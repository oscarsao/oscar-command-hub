"""Deriva de la zona de una tarjeta (preflight.py + runner): repos git temporales, sin red ni hermes ni claude."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent_lanes.config import Lane, load_preflight_settings
from agent_lanes.preflight import (DERIVA_DEFAULTS, calcular_deriva, load_deriva_settings, parse_deriva,
                                   revisar_deriva)
from agent_lanes.runner import LaneRunner

from .test_runner_state import FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome

DIA = 86400.0
AJUSTES = dict(DERIVA_DEFAULTS)


def git(repo, *args):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # simula origin/main sin red
    return git(repo, "rev-parse", "HEAD")


def put(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    put(repo, "zona/a.py", "".join(f"linea {i}\n" for i in range(30)))
    put(repo, "zona/b.py", "".join(f"otra {i}\n" for i in range(30)))
    put(repo, "fuera/c.py", "x\n")
    return repo, commit(repo, "base")


def deriva(repo, sha, max_lineas=200):
    return calcular_deriva(str(repo), sha, "origin/main", ["zona"], max_lineas, subprocess.run)


# --- parser ---------------------------------------------------------------------------------------------------------

def test_parser_extrae_sha_y_zonas_con_comas_y_mayusculas():
    body = "Texto\nSHA base de comprobación: 2DED67C9ea4\nzona: `tools/agent-lanes`, agent_lanes/runner.py ,  tests\nmás"
    assert parse_deriva(body) == ("2ded67c9ea4", ["tools/agent-lanes", "agent_lanes/runner.py", "tests"])


def test_parser_sin_linea_no_comprueba_ni_falla():
    assert parse_deriva("Zona: tools/x") == (None, [])
    assert parse_deriva("SHA base de comprobación: abcdef1") == (None, [])
    assert parse_deriva(None) == (None, [])
    assert parse_deriva("nada") == (None, [])


# --- cálculo --------------------------------------------------------------------------------------------------------

def test_sin_cambios_en_la_zona_no_hay_deriva_aunque_cambie_lo_de_fuera(tmp_path):
    repo, sha = make_repo(tmp_path)
    put(repo, "fuera/c.py", "y\n" * 500)
    commit(repo, "fuera")
    d = deriva(repo, sha)
    assert not d.derivada and not d.aviso


def test_fichero_borrado_marca_deriva(tmp_path):
    repo, sha = make_repo(tmp_path)
    git(repo, "rm", "-q", "zona/b.py")
    commit(repo, "borra")
    d = deriva(repo, sha)
    assert d.derivada and "borrados" in d.motivos[0] and "zona/b.py" in d.motivos[0]


def test_fichero_movido_marca_deriva(tmp_path):
    repo, sha = make_repo(tmp_path)
    git(repo, "mv", "zona/a.py", "zona/nuevo.py")
    commit(repo, "mueve")
    d = deriva(repo, sha)
    assert d.derivada and any("movidos" in m and "zona/a.py → zona/nuevo.py" in m for m in d.motivos)


def test_umbral_de_lineas(tmp_path):
    repo, sha = make_repo(tmp_path)
    put(repo, "zona/a.py", "".join(f"cambiada {i}\n" for i in range(30)))  # 30 borradas + 30 añadidas = 60
    commit(repo, "reescribe")
    assert not deriva(repo, sha, max_lineas=60).derivada
    d = deriva(repo, sha, max_lineas=59)
    assert d.derivada and "60 líneas" in d.motivos[0]


def test_sha_inexistente_avisa_y_no_marca_deriva(tmp_path):
    repo, _ = make_repo(tmp_path)
    d = deriva(repo, "0" * 40)
    assert not d.derivada and "no existe" in d.aviso


def test_fallo_de_git_no_marca_deriva(tmp_path):
    d = calcular_deriva(str(tmp_path / "no-hay-repo"), "abcdef1", "origin/main", ["zona"], 200, subprocess.run)
    assert not d.derivada and d.aviso


def test_runner_de_git_que_revienta_no_marca_deriva():
    def boom(*a, **k):
        raise OSError("sin git")
    d = calcular_deriva("x", "abcdef1", "origin/main", ["z"], 200, boom)
    assert not d.derivada and "sin git" in d.aviso


# --- antigüedad -----------------------------------------------------------------------------------------------------

def tarjeta(sha, dias, ahora):
    return {"id": "t_1", "title": "T", "body": f"SHA base de comprobación: {sha}\nZona: zona", "created_at": ahora - dias * DIA}


def test_6_dias_con_deriva_bloquea_y_2_dias_solo_avisa(tmp_path):
    repo, sha = make_repo(tmp_path)
    git(repo, "rm", "-q", "zona/b.py")
    commit(repo, "borra")
    ahora = 1_800_000_000.0
    accion, texto = revisar_deriva(tarjeta(sha, 6, ahora), str(repo), "origin/main", AJUSTES, ahora, subprocess.run)
    assert accion == "bloquear" and "6 días" in texto and sha in texto and "zona/b.py" in texto
    accion, texto = revisar_deriva(tarjeta(sha, 2, ahora), str(repo), "origin/main", AJUSTES, ahora, subprocess.run)
    assert accion == "aviso" and sha in texto


def test_sin_linea_de_sha_pasa_sin_error(tmp_path):
    repo, _ = make_repo(tmp_path)
    t = {"id": "t_1", "body": "Sin nada", "created_at": 1.0}
    assert revisar_deriva(t, str(repo), "origin/main", AJUSTES, 1e10, subprocess.run) == ("ok", "")


def test_deriva_activa_false_apaga_todo(tmp_path):
    repo, sha = make_repo(tmp_path)
    git(repo, "rm", "-q", "zona/b.py")
    commit(repo, "borra")
    ahora = 1_800_000_000.0
    off = {**AJUSTES, "deriva_activa": False}
    assert revisar_deriva(tarjeta(sha, 30, ahora), str(repo), "origin/main", off, ahora, subprocess.run) == ("ok", "")


def test_sha_inexistente_en_la_tarjeta_solo_avisa_aunque_sea_vieja(tmp_path):
    repo, _ = make_repo(tmp_path)
    ahora = 1_800_000_000.0
    accion, _ = revisar_deriva(tarjeta("0" * 40, 30, ahora), str(repo), "origin/main", AJUSTES, ahora, subprocess.run)
    assert accion == "aviso"


def test_dias_y_umbral_se_leen_de_lanes_yaml(tmp_path):
    yml = tmp_path / "lanes.yaml"
    yml.write_text("preflight:\n  dias_revalidar: 1\n  deriva_max_lineas: 5\n  deriva_activa: true\n", encoding="utf-8")
    cfg = load_preflight_settings(yml)
    assert cfg == {"deriva_activa": True, "dias_revalidar": 1, "deriva_max_lineas": 5}
    repo, sha = make_repo(tmp_path)
    put(repo, "zona/a.py", "".join(f"c {i}\n" for i in range(4)))  # >5 líneas cambiadas
    commit(repo, "cambia")
    ahora = 1_800_000_000.0
    assert revisar_deriva(tarjeta(sha, 2, ahora), str(repo), "origin/main", cfg, ahora, subprocess.run)[0] == "bloquear"
    assert revisar_deriva(tarjeta(sha, 2, ahora), str(repo), "origin/main", AJUSTES, ahora, subprocess.run)[0] == "ok"


def test_ajustes_por_defecto_si_faltan_o_son_invalidos(tmp_path):
    assert load_deriva_settings(None) == DERIVA_DEFAULTS
    assert load_deriva_settings({"dias_revalidar": "x", "deriva_max_lineas": -3, "deriva_activa": "si"}) == DERIVA_DEFAULTS
    yml = tmp_path / "lanes.yaml"
    yml.write_text("runner: {}\n", encoding="utf-8")
    assert load_preflight_settings(yml) == DERIVA_DEFAULTS
    assert load_preflight_settings(tmp_path / "no-existe.yaml") == DERIVA_DEFAULTS


# --- runner ---------------------------------------------------------------------------------------------------------

def runner(repo, tarea, ahora):
    lane = Lane(name="claude-x", board="x", repo=str(repo), base="main", worktree_root=str(repo.parent / "wt"))
    h = FakeHermes(tasks=[tarea])
    w = FakeWorker([ok_outcome()])
    r = LaneRunner(lane, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier(),
                   wall_clock=lambda: ahora, deriva_settings=dict(AJUSTES))
    return r, h, w


def test_runner_no_lanza_una_tarjeta_vieja_con_deriva_y_deja_motivo(tmp_path):
    repo, sha = make_repo(tmp_path)
    git(repo, "rm", "-q", "zona/b.py")
    commit(repo, "borra")
    ahora = 1_800_000_000.0
    r, h, w = runner(repo, tarjeta(sha, 6, ahora), ahora)
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert w.runs == []
    assert any(c[0] == "comment" and "zona/b.py" in c[2] and sha in c[2] for c in h.calls)
    assert any(c[0] == "block" and c[2] == "needs_input" and "zona/b.py" in c[3] for c in h.calls)


def test_runner_con_tarjeta_reciente_con_deriva_solo_avisa(tmp_path):
    repo, sha = make_repo(tmp_path)
    git(repo, "rm", "-q", "zona/b.py")
    commit(repo, "borra")
    ahora = 1_800_000_000.0
    r, h, w = runner(repo, tarjeta(sha, 2, ahora), ahora)
    assert r.run_once() == {"t_1": "review"}
    assert w.runs == ["run"]
    assert any(c[0] == "comment" and "aviso previo" in c[2] and "zona/b.py" in c[2] for c in h.calls)


def test_runner_sin_linea_de_sha_lanza_como_siempre(tmp_path):
    repo, _ = make_repo(tmp_path)
    ahora = 1_800_000_000.0
    r, h, w = runner(repo, {"id": "t_1", "title": "T", "body": "Corrige un typo", "created_at": ahora - 30 * DIA}, ahora)
    assert r.run_once() == {"t_1": "review"}
    assert w.runs == ["run"]
