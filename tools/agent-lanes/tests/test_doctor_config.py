"""F1-02 (3a): OHQ_API_URL por defecto, allowlist derivada de people.yaml + lanes.yaml y `lanes.py doctor-config`
(solo nombres, nunca valores). Ids de prueba inventados: ninguno real."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from agent_lanes import config, doctor_config, telegram
from agent_lanes.people import load_people
from agent_lanes.sync_usuarios import ApiOhq

SECRETO = "valor-secreto-que-no-debe-salir-123"
OWNER = "111"
LANES_YAML = {
    "ohq": {"api_url": "https://api.example.test"},
    "telegram": {"generic_origins": [{"chat": 111}], "final_only": [{"chat": -1009000000001}],
                 "destinos": {"decisiones": {"chat": -1009000000002, "thread": 7}}},
    "integration_telegram": {"chat": -1009000000002, "thread": 9},
    "hub_panel": {"enabled": True, "chat": -1009000000001, "thread": 3},
    "lanes": {"claude-hub": {"github": "acme/hub", "telegram": {"chat": -1009000000001, "thread": 4}},
              "claude-x": {"telegram": {"chat": -1009000000003, "thread": 5}}},
    "integrator": {"lanes": {"claude-x": {"deploy": "dokploy", "dokploy_url": "http://x", "dokploy_application_id": "",
                                          "dokploy_token_env": "DOKPLOY_API_TOKEN"}}},
    "monitor_alerts": {"vercel": {"token_env": "VERCEL_TOKEN", "projects": ["web"]}},
}
PEOPLE = {"people": {"ana": {"name": "Ana", "user_id": "222", "role": "dev", "operador": True},
                     "sin_id": {"name": "Nadie", "user_id": None, "role": "dev"},
                     "susp": {"name": "Susp", "user_id": "333", "role": "dev", "suspendido": True}}}


def _files(tmp_path):
    lanes = tmp_path / "lanes.yaml"
    lanes.write_text(yaml.safe_dump(LANES_YAML), encoding="utf-8")
    people = tmp_path / "people.yaml"
    people.write_text(yaml.safe_dump(PEOPLE), encoding="utf-8")
    return lanes, people


def test_ohq_api_url_env_manda_y_si_no_lanes_yaml(tmp_path):
    lanes, _ = _files(tmp_path)
    assert config.ohq_api_url({"OHQ_API_URL": "https://otra.test"}, lanes) == "https://otra.test"
    assert config.ohq_api_url({}, lanes) == "https://api.example.test"
    (tmp_path / "vacio.yaml").write_text("lanes: {}\n", encoding="utf-8")
    assert config.ohq_api_url({}, tmp_path / "vacio.yaml") == ""


def test_el_lanes_yaml_real_trae_la_url_por_defecto():
    assert config.ohq_api_url({}).startswith("https://")


def test_api_ohq_sin_clave_sigue_apagada_aunque_haya_url_por_defecto():
    try:
        ApiOhq.desde_entorno({})
    except SystemExit as e:
        assert "OHQ_API_KEY" in str(e)
    else:
        raise AssertionError("sin OHQ_API_KEY no debe arrancar")
    api = ApiOhq.desde_entorno({"OHQ_API_KEY": "k"})
    assert api.base.startswith("https://")


def test_chats_de_confianza_salen_de_los_destinos_de_lanes_yaml(tmp_path):
    lanes, _ = _files(tmp_path)
    assert config.chats_de_confianza(lanes) == {"-1009000000001", "-1009000000002", "-1009000000003"}
    assert config.chats_de_confianza(tmp_path / "no-existe.yaml") == set()


def test_allowlist_derivada_une_env_people_y_lanes(tmp_path):
    lanes, people_path = _files(tmp_path)
    people = load_people(OWNER, people_path)
    got = telegram.allowlist({"TELEGRAM_ALLOWED_CHATS": " -1009999, ,444 "}, people=people, lanes_path=lanes)
    assert got == {"-1009999", "444", "222", "-1009000000001", "-1009000000002", "-1009000000003"}
    assert "333" not in got  # suspendido: falla cerrado


def test_allowlist_con_people_roto_se_queda_en_env_y_lanes(tmp_path):
    lanes, _ = _files(tmp_path)
    roto = tmp_path / "roto.yaml"
    roto.write_text("people: [", encoding="utf-8")
    got = telegram.allowlist({"TELEGRAM_ALLOWED_CHATS": "444"}, people=load_people(OWNER, roto), lanes_path=lanes)
    assert "444" in got and "222" not in got


def test_notifier_from_env_usa_la_allowlist_derivada(monkeypatch):
    seen = {}
    monkeypatch.setattr(telegram, "allowlist", lambda env, people=None, lanes_path=None: seen.setdefault("x", {"-1"}))
    n, _ = telegram.notifier_from_env({"CARRILES_BOT_TOKEN": "1:t", "TELEGRAM_CHAT_ID": "-100"},
                                      {"generic_origins": set()})
    assert "-1" in n.allowed_chats and "-100" in n.allowed_chats


def test_un_origen_del_grupo_de_lanes_yaml_vuelve_a_su_tema(tmp_path):
    lanes, people_path = _files(tmp_path)
    allowed = telegram.allowlist({}, people=load_people(OWNER, people_path), lanes_path=lanes)
    got = telegram.resolve_target(("-1009000000003", "5"), None, allowed_chats=allowed, generic_origins=set())
    assert got == ("-1009000000003", "5")


def test_doctor_config_sin_ohq_api_url_ni_clave_lo_dice_y_no_imprime_valores(tmp_path):
    lanes, _ = _files(tmp_path)
    data = yaml.safe_load(lanes.read_text(encoding="utf-8"))
    data.pop("ohq")
    env = {"CARRILES_BOT_TOKEN": SECRETO, "TELEGRAM_CHAT_ID": SECRETO, "VERCEL_TOKEN": SECRETO,
           "OHQ_API_KEY": SECRETO}
    text = doctor_config.render(doctor_config.diagnostico(env, data, publico=False, lanes_path=tmp_path / "x.yaml"))
    assert SECRETO not in text
    assert "sync_usuarios (suspensiones del panel): desactivado (falta OHQ_API_URL" in text
    assert "bot_carriles (botones y escucha): activo" in text
    assert "deploy por botón (claude-x, Dokploy): desactivado (falta integrator.lanes.claude-x." \
           "dokploy_application_id, DOKPLOY_API_TOKEN)" in text
    assert "prodwatch Vercel (despliegues de producción fallidos): activo" in text
    assert "vigilancia de Supabase (monitor): desactivado (falta SUPABASE_SERVICE_ROLE_KEY)" in text


def test_doctor_config_con_url_por_defecto_y_clave_activa_sync(tmp_path):
    lanes, _ = _files(tmp_path)
    data = yaml.safe_load(lanes.read_text(encoding="utf-8"))
    filas = {f.funcion: f for f in doctor_config.diagnostico({"OHQ_API_KEY": SECRETO}, data, lanes_path=lanes)}
    sync = filas["sync_usuarios (suspensiones del panel)"]
    assert sync.estado == "activo" and "lanes.yaml" in sync.motivo and SECRETO not in sync.motivo


def test_doctor_config_avisa_si_el_hub_es_publico(tmp_path):
    lanes, _ = _files(tmp_path)
    data = yaml.safe_load(lanes.read_text(encoding="utf-8"))
    pub = doctor_config.render(doctor_config.diagnostico({}, data, publico=True, lanes_path=lanes))
    assert "🟠 repo del hub: aviso (es PÚBLICO" in pub
    unknown = doctor_config.render(doctor_config.diagnostico({}, data, publico=None, lanes_path=lanes))
    assert "visibilidad desconocida" in unknown


def test_repo_publico_lee_gh_y_nunca_lanza():
    ok = lambda args: SimpleNamespace(returncode=0, stdout=json.dumps({"private": False}))  # noqa: E731
    priv = lambda args: SimpleNamespace(returncode=0, stdout=json.dumps({"private": True}))  # noqa: E731
    fail = lambda args: SimpleNamespace(returncode=1, stdout="")  # noqa: E731

    def boom(args):
        raise OSError("sin gh")

    assert doctor_config.repo_publico("acme/hub", ok) is True
    assert doctor_config.repo_publico("acme/hub", priv) is False
    assert doctor_config.repo_publico("acme/hub", fail) is None
    assert doctor_config.repo_publico("acme/hub", boom) is None
    assert doctor_config.repo_publico("", ok) is None
    assert doctor_config.hub_slug(LANES_YAML) == "acme/hub"


def test_publicar_al_arrancar_nunca_lanza():
    sent = []
    assert doctor_config.publicar_al_arrancar(sent.append, informe_fn=lambda: "🩺 ok") is True
    assert sent == ["🩺 ok"]

    def roto():
        raise NameError("x")

    assert doctor_config.publicar_al_arrancar(sent.append, informe_fn=roto) is False


def test_cli_doctor_config_sin_gh(capsys, monkeypatch):
    monkeypatch.setattr(doctor_config, "entorno", lambda env_files=None: {"OHQ_API_KEY": SECRETO})
    assert doctor_config.main(["--sin-gh"]) == 0
    out = capsys.readouterr().out
    assert "doctor-config" in out and SECRETO not in out and "visibilidad desconocida" in out


def test_monitor_resume_las_vigilancias_sin_valores(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(config.ROOT).parent / "monitor"))
    import prodwatch
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    monkeypatch.delenv("DOKPLOY_API_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"VERCEL_TOKEN={SECRETO}\n", encoding="utf-8")
    pw = prodwatch.ProdWatch({"token_env": "VERCEL_TOKEN", "projects": ["a", "b"]},
                             {"claude-x": {"health_url": "https://h", "dokploy_url": "http://d",
                                           "dokploy_token_env": "DOKPLOY_API_TOKEN"}}, env_files=[env])
    line = " · ".join(pw.estado_vigilancias())
    assert SECRETO not in line
    assert "Vercel: activo (2 proyectos)" in line and "health_url: 1 activos" in line
    assert "Dokploy x: desactivado (falta dokploy_application_id, DOKPLOY_API_TOKEN)" in line
