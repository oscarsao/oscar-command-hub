"""Acciones de producción del carril integrador (fases 1-2): lista blanca, validadores, redacción, auditoría. Sin red."""
import json

import pytest

from agent_lanes import acciones_prod as ap

CFG = {"servicios_logs": frozenset({"oscar-hq"}), "repos_pr": frozenset({"pildora-mcp"}),
       "proyectos_sql": frozenset({"proj1"}), "hosts_health": frozenset({"pildoradigital.com"}),
       "bots_webhook": frozenset({"avisos"})}


def _ok(salida="hola", rc=0):
    llamadas = []

    def runner(argv, timeout):
        llamadas.append(argv)
        return salida, rc
    runner.llamadas = llamadas
    return runner


def _registros(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("q", [
    "DROP TABLE x", "select 1; drop table x", "select 1 -- c", "delete from t", "update t set a=1",
    "select * from t into outfile", "with a as (delete from t returning *) select * from a",
    "select pg_sleep(10)", "select set_config('a','b',false)", "", "select 1 /* x */", "truncate t", None,
])
def test_select_rechaza_escrituras(q):
    with pytest.raises(ap.AccionRechazada):
        ap.validar_select(q)


def test_select_acepta_lectura():
    assert ap.validar_select("  select id, name from public.t where x = 1 ").startswith("select")
    assert ap.validar_select("WITH a AS (SELECT 1) SELECT * FROM a")


def test_redactar_secretos():
    # Valores ficticios partidos en dos para que ningún escáner de secretos vea un token completo en el fuente
    # (el test los junta en tiempo de ejecución y comprueba que `redactar` los elimina).
    t = ("SUPABASE_KEY=abc123xyz token: " + "ghp_" + "ABCDEFGHIJKLMNOP1234 jwt " + "eyJhbGciOiJIUzI1" + ".eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4 "
         "bot " + "123456789" + ":AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0 Authorization: Bearer abcdefghijklmnop1234")
    r = ap.redactar(t)
    for fuga in ("abc123xyz", "ghp_ABCDEF", "eyJhbGci", "AAHdqTcvCH1", "abcdefghijklmnop1234"):
        assert fuga not in r
    assert "[REDACTADO]" in r and ap.redactar("todo normal 200 OK") == "todo normal 200 OK"


def test_config_falla_cerrado(tmp_path):
    assert all(not v for v in ap.cargar_config(tmp_path / "no_existe.yaml").values())
    mal = tmp_path / "m.yaml"
    mal.write_text("servicios_logs: oscar-hq\nrepos_pr: [a, 3]\n", encoding="utf-8")
    assert all(not v for v in ap.cargar_config(mal).values())
    assert "oscar-hq" in ap.cargar_config()["servicios_logs"]   # el yaml real carga


def test_logs_construye_argv_fijo():
    _, argv = ap.preparar("logs", "oscar-hq", {"lineas": 50}, CFG)
    assert argv[0] == "ssh" and argv[-1] == "docker logs --tail 50 oscar-hq" and argv[-2] == "pildora-01"


@pytest.mark.parametrize("tipo,obj,params", [
    ("logs", "oscar-hq; rm -rf /", {}), ("logs", "otro", {}), ("logs", "oscar-hq", {"lineas": 501}),
    ("logs", "oscar-hq", {"lineas": "5; ls"}), ("rm", "x", {}), ("shell", "ls", {}), (None, "x", {}),
    ("sql_select", "proj_ajeno", {"consulta": "select 1"}), ("sql_select", "proj1", {"consulta": "delete from t"}),
    ("health", "evil.com", {}), ("webhook_info", "otro", {}),
    ("abrir_pr", "repo-ajeno", {"rama": "x"}), ("abrir_pr", "pildora-mcp", {"rama": "../x"}),
    ("abrir_pr", "pildora-mcp", {"rama": "x", "base": "develop"}), ("abrir_pr", "pildora-mcp", {}),
    ("logs", "oscar-hq", "no-dict"),
])
def test_rechaza_lo_que_no_encaja(tipo, obj, params):
    with pytest.raises(ap.AccionRechazada):
        ap.preparar(tipo, obj, params, CFG)


def test_pr_sin_shell_ni_inyeccion_en_titulo():
    _, argv = ap.preparar("abrir_pr", "pildora-mcp", {"rama": "lane/t_1", "titulo": "a\nb; rm -rf"}, CFG)
    assert argv[:3] == ["gh", "pr", "create"] and "--repo" in argv and "\n" not in argv[argv.index("--title") + 1]


def test_ejecutar_nivel_a_audita_y_redacta(tmp_path):
    aud, r = tmp_path / "a.jsonl", _ok("linea\nAPI_TOKEN=supersecreto1234 fin")
    res = ap.ejecutar("sql_select", "proj1", {"consulta": "select email from users"}, tarjeta="t_1",
                      carril="claude-integrador", cfg=CFG, runner=r, audit_path=aud)
    assert res["ok"] and "supersecreto" not in res["salida"]
    regs = _registros(aud)
    assert [x["resultado"] for x in regs] == ["iniciada", "ok"]
    assert "select email" not in aud.read_text(encoding="utf-8")   # solo hash y longitud
    assert regs[0]["consulta_len"] == len("select email from users") and regs[0]["nivel"] == "A"


def test_ejecutar_recorta_y_marca_error(tmp_path):
    r = ap.ejecutar("logs", "oscar-hq", {}, cfg=CFG, runner=_ok("x" * 20000, 2), audit_path=tmp_path / "a.jsonl")
    assert not r["ok"] and len(r["salida"]) < ap.MAX_SALIDA + 100 and "recortado" in r["salida"]


def test_ejecutar_herramienta_ausente_no_revienta(tmp_path):
    def runner(argv, timeout):
        raise FileNotFoundError("ssh")
    r = ap.ejecutar("health", "pildoradigital.com", {}, cfg=CFG, runner=runner, audit_path=tmp_path / "a.jsonl")
    assert not r["ok"] and "FileNotFoundError" in r["salida"]


@pytest.mark.parametrize("tipo", ["fusionar", "desplegar", "migracion", "variable"])
def test_nivel_b_nunca_se_ejecuta_aqui(tmp_path, tipo):
    r = _ok()
    with pytest.raises(ap.AccionRechazada):
        ap.ejecutar(tipo, "oscar-hq", {}, cfg=CFG, runner=r, audit_path=tmp_path / "a.jsonl")
    assert r.llamadas == []
    assert _registros(tmp_path / "a.jsonl")[-1]["resultado"] == "rechazada"


def test_rechazo_queda_auditado(tmp_path):
    with pytest.raises(ap.AccionRechazada):
        ap.ejecutar("drop_all", "x", {}, cfg=CFG, runner=_ok(), audit_path=tmp_path / "a.jsonl")
    assert _registros(tmp_path / "a.jsonl")[0]["resultado"] == "rechazada"


def test_sin_auditoria_no_se_ejecuta(tmp_path):
    bloqueado = tmp_path / "es_un_dir"
    bloqueado.mkdir()
    r = _ok()
    with pytest.raises(OSError):
        ap.ejecutar("health", "pildoradigital.com", {}, cfg=CFG, runner=r, audit_path=bloqueado)
    assert r.llamadas == []


def test_hash_params_estable_y_sensible():
    assert ap.hash_params("a", {"x": 1, "y": 2}) == ap.hash_params("a", {"y": 2, "x": 1})
    assert ap.hash_params("a", {"x": 1}) != ap.hash_params("a", {"x": 2})
