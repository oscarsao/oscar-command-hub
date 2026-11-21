"""tests_delta.py: elección de tests según el diff, con repos git temporales (sin lanzar pytest de verdad)."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "agent_lanes" / "tests_delta.py"
AL = "tools/agent-lanes"


def _load():
    spec = importlib.util.spec_from_file_location("tests_delta_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for rel in ("agent_lanes/foo.py", "agent_lanes/config.py", "tests/conftest.py", "tests/test_foo.py",
                "tests/test_foo_extra.py", "tests/test_bar.py", "tests/test_readme_lanes.py",
                "tests/test_doctor_config.py"):
        _write(tmp_path, f"{AL}/{rel}")
    _write(tmp_path, f"{AL}/lanes.yaml", "lanes:\n  a:\n    test_cmd: uno\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "lane/x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, **files: str) -> None:
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "cambio")


def _names(files):
    return sorted(Path(f).name for f in files)


FIJOS = ["test_doctor_config.py", "test_readme_lanes.py"]


def test_diff_vacio_solo_los_fijos(repo):
    mode, files = _load().plan("main")
    assert mode == "delta" and _names(files) == FIJOS


def test_un_modulo_lanza_sus_tests_y_los_fijos(repo):
    _commit(repo, **{f"{AL}/agent_lanes/foo.py": "y\n"})
    mode, files = _load().plan("main")
    assert mode == "delta"
    assert _names(files) == sorted(FIJOS + ["test_foo.py", "test_foo_extra.py"])


def test_un_test_cambiado_se_lanza_solo_el(repo):
    _commit(repo, **{f"{AL}/tests/test_bar.py": "y\n"})
    mode, files = _load().plan("main")
    assert mode == "delta" and _names(files) == sorted(FIJOS + ["test_bar.py"])


def test_modulo_sin_tests_propios_suite_completa(repo):
    _commit(repo, **{f"{AL}/agent_lanes/nuevo.py": "y\n"})
    assert _load().plan("main") == ("completo", [])


@pytest.mark.parametrize("rel", [
    "tests/conftest.py", "agent_lanes/config.py", "agent_lanes/runner.py", "agent_lanes/checks.py",
    "agent_lanes/tests_delta.py", "agent_lanes/hermes.py", "agent_lanes/verify.py",
    "lanes.yaml", "contract/result.schema.json", "roles/implementador.md", "tests/fixtures/golden.json",
    "agent_lanes/sub/otro.py",
])
def test_lo_comun_o_no_cubierto_lanza_la_suite_completa(repo, rel):
    _commit(repo, **{f"{AL}/{rel}": "y\n"})
    assert _load().plan("main") == ("completo", [])


def test_mas_de_25_ficheros_suite_completa(repo):
    _commit(repo, **{f"docs/f{i}.md": "y\n" for i in range(26)})
    assert _load().plan("main") == ("completo", [])


def test_docs_y_ficheros_fuera_de_agent_lanes_no_amplian(repo):
    _commit(repo, **{f"{AL}/README.md": "y\n", "decisions/x.md": "y\n", "otra/cosa.txt": "y\n"})
    assert _load().plan("main") == ("delta", [f"{AL}/tests/{n}" for n in FIJOS])


def test_cambio_de_valor_en_lanes_yaml_tambien_es_completo(repo):
    _commit(repo, **{f"{AL}/lanes.yaml": "lanes:\n  a:\n    test_cmd: dos\n"})
    assert _load().plan("main") == ("completo", [])


def test_base_inexistente_suite_completa(repo):
    assert _load().plan("no-existe") == ("completo", [])


def test_git_que_agota_el_tiempo_cae_a_completo(repo, monkeypatch):
    mod = _load()

    def lento(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(mod.subprocess, "run", lento)
    assert mod.plan("main") == ("completo", [])


def test_main_imprime_modo_y_devuelve_el_codigo_de_pytest(repo, monkeypatch, capsys):
    mod = _load()
    llamadas = []

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd[0] == "git":
            return real_run(cmd, **kw)
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    _commit(repo, **{f"{AL}/agent_lanes/foo.py": "y\n"})
    assert mod.main(["main"]) == 3
    assert capsys.readouterr().out.splitlines()[0] == "tests_delta: modo delta, 4 fichero(s) de test"
    assert "-m" in llamadas[0] and "pytest" in llamadas[0] and f"{AL}/tests" not in llamadas[0]
    _commit(repo, **{f"{AL}/tests/conftest.py": "y\n"})
    mod.main(["main"])
    assert capsys.readouterr().out.splitlines()[0].startswith("tests_delta: modo completo")
    assert f"{AL}/tests" in llamadas[1]


def test_main_sin_argumento_da_2(capsys):
    assert _load().main([]) == 2
