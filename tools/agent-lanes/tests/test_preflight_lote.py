"""Comprobación previa del lote (preflight_lote.py + paso informativo de batch.py). Git REAL sobre repos temporales;
las ramas simulan las del 19-11-2026 de oscar-hq: dos con el esquema 20261409, una con 20261118 fuera de secuencia, dos
montando rutas en main.py y dos tocando package.json. Sin red, sin GitHub y sin Telegram."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_lanes import ajustes_hub, preflight_lote as pf
from tests.test_batch import Env, sh

MIG = "supabase/migrations/"


def repo_con_base(tmp: Path) -> Path:
    r = tmp / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True, capture_output=True)
    (r / MIG).mkdir(parents=True)
    (r / MIG / "20261407_inicial.sql").write_text("select 1;\n", encoding="utf-8")
    (r / MIG / "20261408_ultima.sql").write_text("select 2;\n", encoding="utf-8")
    (r / "src/api").mkdir(parents=True)
    (r / "src/api/main.py").write_text("app = 1\n", encoding="utf-8")
    (r / "dashboard").mkdir()
    (r / "dashboard/package.json").write_text('{"a": 1}\n', encoding="utf-8")
    sh(r, "add", "-A")
    sh(r, "commit", "-m", "base")
    return r


def rama(r: Path, nombre: str, ficheros: dict) -> None:
    sh(r, "checkout", "-q", "-b", nombre, "main")
    for f, t in ficheros.items():
        p = r / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
    sh(r, "add", "-A")
    sh(r, "commit", "-m", nombre)
    sh(r, "checkout", "-q", "main")


def ramas_del_19_11(r: Path) -> None:
    rama(r, "a", {MIG + "20261409_a.sql": "select 'a';\n", "src/api/main.py": "app = 1\nr_a = 1\n"})
    rama(r, "b", {MIG + "20261409_b.sql": "select 'b';\n", "src/api/main.py": "app = 1\nr_b = 1\n"})
    rama(r, "c", {MIG + "20261118_c.sql": "select 'c';\n", "dashboard/package.json": '{"a": 2}\n'})
    rama(r, "d", {"dashboard/package.json": '{"a": 3}\n'})


def test_informe_con_las_ramas_del_19_11(tmp_path):
    r = repo_con_base(tmp_path)
    ramas_del_19_11(r)
    inf = pf.analizar(str(r), "main", ["a", "b", "c", "d"], prefijos=(MIG,))
    assert inf.error == "" and inf.ultima_base == 20261408
    tipos = {(h.rama, h.tipo, h.bloquea) for h in inf.numeracion}
    assert ("b", "duplicada", True) in tipos                  # la segunda con 20261409 queda fuera
    assert ("a", "duplicada", False) in tipos                 # la primera se conserva, solo se avisa
    assert ("c", "fuera_de_secuencia", True) in tipos         # 20261118 < 20261408
    assert set(inf.bloqueadas) == {"b", "c"}
    assert {s.fichero: s.ramas for s in inf.solapes} == {"src/api/main.py": ["a", "b"],
                                                         "dashboard/package.json": ["c", "d"]}
    txt = inf.render()
    assert "20261409" in txt and "20261118" in txt and "src/api/main.py" in txt and "dashboard/package.json" in txt
    assert pf.main(["--repo", str(r), "--base", "main", "--ramas", "a,b,c,d"]) == 1  # hay choque de numeración


def test_sin_ramas_con_problemas_informa_que_no_hay_choques(tmp_path):
    r = repo_con_base(tmp_path)
    rama(r, "x", {MIG + "20261409_x.sql": "select 'x';\n", "src/otro.py": "1\n"})
    rama(r, "y", {MIG + "20261410_y.sql": "select 'y';\n", "src/mas.py": "2\n"})
    inf = pf.analizar(str(r), "main", ["x", "y"], prefijos=(MIG,))
    assert not inf.hay_choques and inf.bloqueadas == {}
    assert "Sin choques" in inf.render()
    assert pf.main(["--repo", str(r), "--base", "main", "--ramas", "x,y"]) == 0


def test_version_ya_existente_en_la_base_y_solape_con_otros_pr(tmp_path):
    r = repo_con_base(tmp_path)
    rama(r, "x", {MIG + "20261408_otra.sql": "select 'x';\n", "src/api/main.py": "app = 9\n"})
    rama(r, "ajena", {"src/api/main.py": "app = 7\n"})
    inf = pf.analizar(str(r), "main", ["x"], prefijos=(MIG,), otros=["ajena", "no-existe"])
    assert inf.bloqueadas == {"x": f"{MIG}20261408_otra.sql: la versión 20261408 ya existe en la base ({MIG}20261408_ultima.sql)"}
    assert [(s.fichero, s.ramas) for s in inf.solapes] == [("src/api/main.py", ["x", "ajena"])]


def test_rama_inexistente_del_lote_da_error_sin_lanzar(tmp_path):
    r = repo_con_base(tmp_path)
    inf = pf.analizar(str(r), "main", ["no-existe"])
    assert "no encuentro" in inf.render() and not inf.bloqueadas


def test_no_escribe_nada_en_el_repo(tmp_path):
    r = repo_con_base(tmp_path)
    ramas_del_19_11(r)
    antes = (sh(r, "for-each-ref"), sh(r, "status", "--short"))
    pf.analizar(str(r), "main", ["a", "b", "c", "d"], prefijos=(MIG,))
    assert (sh(r, "for-each-ref"), sh(r, "status", "--short")) == antes


def test_fallos_previos_curados_separan_nuevos_de_viejos(tmp_path):
    f = tmp_path / "fp.json"
    f.write_text(json.dumps({"fallos": [
        {"id": "F-1", "repo": "oscar-hq", "patron": "dashboard/src/a.test.ts > viejo", "nota": "flaky"},
        {"id": "F-2", "repo": "otro", "patron": "src/b.ts error TS1"}, {"sin": "patron"}]}), encoding="utf-8")
    cur = pf.cargar_fallos_previos(f, "oscar-hq")
    assert [c["id"] for c in cur] == ["F-1"]
    nuevos, viejos = pf.clasificar_fallos({"dashboard/src/a.test.ts > viejo caso", "src/b.ts error TS1"}, cur)
    assert nuevos == ["src/b.ts error TS1"] and viejos == [("dashboard/src/a.test.ts > viejo caso", "F-1 · flaky")]
    assert pf.cargar_fallos_previos(tmp_path / "no.json") == []
    f.write_text("{roto", encoding="utf-8")
    assert pf.cargar_fallos_previos(f) == []


def test_fichero_de_fallos_previos_del_repo_es_valido():
    assert isinstance(pf.cargar_fallos_previos(), list)
    assert json.loads(pf.FALLOS_PREVIOS.read_text(encoding="utf-8"))["version"] == 1


# --- integración en el lote diario ---------------------------------------------------------------------------

def _base_con_esquema(env: Env) -> None:
    env.write(MIG + "20261408_ultima.sql", "select 2;\n")
    env.write("src/api/main.py", "app = 1\n")
    sh(env.repo, "add", "-A")
    sh(env.repo, "commit", "-m", "esquema")
    sh(env.repo, "push", "-q", "origin", "main")
    sh(env.repo, "fetch", "-q", "origin")


def test_lote_deja_fuera_choque_de_numeracion_y_avisa_de_solapes(tmp_path):
    e = Env(tmp_path)
    _base_con_esquema(e)
    e.branch("t_a", {MIG + "20261409_a.sql": "select 'a';\n", "src/api/main.py": "app = 1\nr_a = 1\n"})
    e.branch("t_b", {MIG + "20261409_b.sql": "select 'b';\n"})   # misma versión que t_a: queda fuera
    e.branch("t_c", {MIG + "20261118_c.sql": "select 'c';\n"})   # fuera de secuencia: queda fuera
    e.branch("t_d", {"src/api/main.py": "app = 1\nr_d = 1\n"}, approve=True)  # solape con t_a (no choca al fusionar)
    ajena = e.branch("t_aj", {"src/api/main.py": "app = 1\nr_x = 1\n"}, approve=False)
    e.gh.others = [ajena]
    e.queue()
    e.integ.batches.assemble(e.lane, e.policy)
    b = e.integ.batches.all()[0]
    reasons = {x["tid"]: x["reason"] for x in b["excluded"]}
    assert "numeración" in reasons["t_b"] and "20261409" in reasons["t_b"]
    assert "numeración" in reasons["t_c"] and "fuera de secuencia" in reasons["t_c"]
    assert "t_a" not in reasons and "t_a" in [m["tid"] for m in b["members"]]
    avisos = b["rec"]["avisos"]
    assert any("src/api/main.py" in a and "(t_a)" in a and f"#{ajena}" in a for a in avisos)  # también con otros PR abiertos
    assert "Avisos:" in e.tg.sent[0]["text"] and "Fuera del lote" in e.tg.sent[0]["text"]
    assert "Comprobación previa" in e.gh.created[0]["body"]


def test_lote_sin_problemas_no_cambia_y_se_puede_apagar(tmp_path, monkeypatch):
    e = Env(tmp_path)
    _base_con_esquema(e)
    e.branch("t_a", {MIG + "20261409_a.sql": "select 'a';\n"})
    e.branch("t_b", {MIG + "20261410_b.sql": "select 'b';\n"})
    e.queue()
    e.integ.batches.assemble(e.lane, e.policy)
    b = e.integ.batches.all()[0]
    assert [m["tid"] for m in b["members"]] == ["t_a", "t_b"] and b["excluded"] == [] and b["rec"]["avisos"] == []
    assert "Sin choques" in e.gh.created[0]["body"]
    # apagado con el ajuste: la rama duplicada ya no se aparta
    e2 = Env(tmp_path / "e2")
    _base_con_esquema(e2)
    e2.branch("t_a", {MIG + "20261409_a.sql": "x\n"})
    e2.branch("t_b", {MIG + "20261409_b.sql": "y\n"})
    monkeypatch.setattr(ajustes_hub, "_default", type("V", (), {"valor": lambda s, k: 0 if k == "lotes.preflight"
                                                                else ajustes_hub.SETTINGS[k].default})())
    e2.queue()
    e2.integ.batches.assemble(e2.lane, e2.policy)
    assert [m["tid"] for m in e2.integ.batches.all()[0]["members"]] == ["t_a", "t_b"]
