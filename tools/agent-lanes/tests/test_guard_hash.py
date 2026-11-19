"""H11: el worker de otro equipo lleva el mismo guard y contrato (sello, sonda y `runner.py --check`)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runner  # noqa: E402
from agent_lanes import guard_hash as gh  # noqa: E402


@pytest.fixture
def copia(tmp_path):
    """Un checkout mínimo con los ficheros del guard y su manifest (para estropearlo sin tocar el repo)."""
    for rel in (*gh.GUARD_FILES, gh.MANIFEST.as_posix()):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)
    return tmp_path


def test_el_sello_del_repo_esta_al_dia():
    """Falla si alguien cambia el guard (o su contrato) sin regenerar contract/guard-manifest.json en el mismo commit."""
    assert gh.verify_manifest() == [], "py -3.12 -m agent_lanes.guard_hash --write"


def test_el_sello_cubre_el_hook_los_settings_y_la_politica():
    sellados = json.loads((ROOT / gh.MANIFEST).read_text(encoding="utf-8"))["files"]
    for rel in ("contract/guard.py", "contract/worker-settings.json", "hooks-usuario/politica-permisos.json",
                "hooks-usuario/destino.mjs", "contract/result.schema.json", "roles/implementador.md"):
        assert sellados.get(rel), rel


def test_los_finales_de_linea_no_cambian_el_sello(copia):
    antes = gh.compute(copia)["digest"]
    for rel in gh.GUARD_FILES:  # un git con autocrlf en el otro PC
        f = copia / rel
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert gh.compute(copia)["digest"] == antes and gh.verify_manifest(copia) == []


def test_fichero_editado_ausente_o_sin_sellar_rompe_el_sello(copia):
    (copia / "contract/guard.py").write_text("# guard debilitado\nraise SystemExit(0)\n", encoding="utf-8")
    assert any("contract/guard.py" in p and "cambió" in p for p in gh.verify_manifest(copia))
    (copia / "contract/guard.py").unlink()
    assert any("falta el fichero del guard contract/guard.py" in p for p in gh.verify_manifest(copia))
    shutil.copyfile(ROOT / "contract/guard.py", copia / "contract/guard.py")
    assert gh.verify_manifest(copia) == []
    manifest = copia / gh.MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["digest"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert any("no coincide" in p for p in gh.verify_manifest(copia))
    manifest.unlink()
    assert any("sello del guard" in p for p in gh.verify_manifest(copia))


def test_regenerar_el_sello_tras_un_cambio_intencionado(copia):
    (copia / "roles/ops.md").write_text("otro rol\n", encoding="utf-8")
    assert gh.verify_manifest(copia)
    gh.write_manifest(copia)
    assert gh.verify_manifest(copia) == []
    assert gh.current_digest(copia) == json.loads((copia / gh.MANIFEST).read_text(encoding="utf-8"))["digest"]
    (copia / "roles/ops.md").unlink()
    assert gh.current_digest(copia) is None  # sin un fichero del guard no hay sello que anotar en hub.workers


def test_la_sonda_ejecuta_el_hook_real_y_el_guard_del_repo_la_pasa():
    assert gh.hook_command()[0] == "py" and gh.hook_command()[-1].endswith("contract/guard.py")
    assert gh.probe() == []


def test_la_sonda_falla_cerrada_si_el_guard_deja_pasar_o_no_se_ejecuta(copia):
    def deja_pasar(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "", "")  # un guard que siempre contesta «no hay objeción»

    problemas = gh.probe(copia, run=deja_pasar)
    assert len(problemas) == 2 and all("exit 0, esperado 2" in p for p in problemas)  # push a master y alembic remoto

    def siempre_bloquea(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, "", "")  # roto en el otro sentido: ni el `git status` pasa

    assert any("orden inocua" in p for p in gh.probe(copia, run=siempre_bloquea))

    def sin_python(cmd, **kw):
        raise FileNotFoundError("py")

    assert "sin él un worker no es seguro" in gh.probe(copia, run=sin_python)[0]
    (copia / "contract/worker-settings.json").write_text("{}", encoding="utf-8")
    assert "no declara el hook" in gh.probe(copia)[0]


def test_la_sonda_no_hereda_el_entorno_de_la_tarea(monkeypatch):
    visto = {}

    def espia(cmd, **kw):
        visto.update(kw["env"])
        return subprocess.CompletedProcess(cmd, 2, "", "")

    monkeypatch.setenv("AGENT_LANES_PUSH_OK", "lane/t_1")  # con esto el guard dejaría empujar: la sonda no debe llevarlo
    gh.probe(run=espia)
    assert not any(k.startswith("AGENT_LANES_") for k in visto)


def test_problems_incluye_el_id_de_worker(copia):
    assert gh.problems(copia, worker="Mi PC", run=lambda c, **k: subprocess.CompletedProcess(c, 2, "", ""))[0].startswith(
        "HUB_WORKER_ID='Mi PC' no es válido")


def test_avisos_del_hook_de_usuario(tmp_path):
    assert len(gh.user_hook_warnings(home=tmp_path)) == 2  # nada instalado
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copyfile(ROOT / "hooks-usuario/destino.mjs", hooks / "destino.mjs")
    (hooks / "politica-permisos.json").write_text("{}", encoding="utf-8")
    assert gh.user_hook_warnings(home=tmp_path) == ["~/.claude/hooks/politica-permisos.json difiere de "
                                                    "hooks-usuario/politica-permisos.json del repo"]


# --- runner.py --check ----------------------------------------------------------------------------------------------------------
class _Kanban:
    worker = "pc-b"

    def check_contract(self):
        return []

    def register_worker(self):
        return True


@pytest.fixture
def humo(monkeypatch):
    llamadas = []
    monkeypatch.setattr(runner, "backend_for", lambda board, **kw: _Kanban())
    monkeypatch.setattr(runner, "kanban_backend_name", lambda: "supabase")
    monkeypatch.setattr(gh, "probe", lambda *a, **k: llamadas.append("probe") or [])
    return llamadas


def test_check_supabase_con_guard_aprobado_sale_0(humo):
    assert runner.main(["--check"]) == 0 and humo == ["probe"]


def test_check_supabase_con_guard_distinto_no_arranca(humo, monkeypatch):
    monkeypatch.setattr(gh, "verify_manifest", lambda *a, **k: ["el guard cambió: contract/guard.py (distinto del aprobado)"])
    assert runner.main(["--check"]) == 4


def test_check_supabase_con_hook_que_deja_pasar_no_arranca(humo, monkeypatch):
    monkeypatch.setattr(gh, "probe", lambda *a, **k: ["el guard no responde como debe a «push a master»: exit 0, esperado 2"])
    assert runner.main(["--check"]) == 4


def test_check_supabase_con_id_de_worker_invalido_no_arranca(humo, monkeypatch):
    class Mayusculas(_Kanban):
        worker = "DESKTOP ABC"

    monkeypatch.setattr(runner, "backend_for", lambda board, **kw: Mayusculas())
    assert runner.main(["--check"]) == 4


def test_check_con_hermes_no_toca_el_guard(monkeypatch):
    """Paridad: con el backend hermes (equipo único) el arranque es el de siempre, sin sello ni sonda."""
    class Hermes:
        def check_contract(self):
            return []

    monkeypatch.setattr(runner, "backend_for", lambda board, **kw: Hermes())
    monkeypatch.setattr(runner, "kanban_backend_name", lambda: "hermes")
    monkeypatch.setattr(gh, "problems", lambda *a, **k: pytest.fail("hermes no comprueba el guard"))
    assert runner.main(["--check"]) == 0


def test_lanes_por_equipo_filtran_lo_que_corre_y_un_carril_inexistente_no_arranca(humo, monkeypatch):
    monkeypatch.setattr(runner, "load_runner_settings", lambda: {
        "max_workers": 6, "workers": {"pc-b": {"max_workers": 2, "lanes": ["no-existe"]}}})
    assert runner.main(["--check", "--all"]) == 3  # ningún carril de este equipo existe: no arranca callado


def test_check_supabase_sin_alta_en_el_kanban_no_arranca(humo, monkeypatch):
    class SinAlta(_Kanban):
        def admission_report(self):
            return ["worker 'pc-b' sin alta en hub.workers (la da de alta Oscar)"]

    monkeypatch.setattr(runner, "backend_for", lambda board, **kw: SinAlta())
    assert runner.main(["--check"]) == 5


def test_check_supabase_con_alta_sale_0(humo, monkeypatch):
    class ConAlta(_Kanban):
        def admission_report(self):
            return []

    monkeypatch.setattr(runner, "backend_for", lambda board, **kw: ConAlta())
    assert runner.main(["--check"]) == 0
