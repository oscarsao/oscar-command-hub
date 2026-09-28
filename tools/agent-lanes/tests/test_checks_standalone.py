import subprocess
import sys
from pathlib import Path

CHECKS = Path(__file__).resolve().parents[1] / "agent_lanes" / "checks.py"


def test_checks_runs_as_standalone_script():
    # El runner lo lanza por ruta con cwd = worktree (no como paquete): un import relativo lo rompe (27-09).
    repo = Path(__file__).resolve().parents[3]
    cp = subprocess.run([sys.executable, str(CHECKS), "HEAD"], cwd=repo, capture_output=True, text=True)
    assert "ImportError" not in cp.stderr and cp.returncode == 0, cp.stderr[-500:]


def _repo(tmp_path: Path) -> Path:
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("config", "core.autocrlf", "false")
    (tmp_path / "a.yaml").write_bytes(b"x: 1\r\n")
    git("add", "a.yaml")
    git("commit", "-qm", "base")
    git("tag", "base")
    return tmp_path


def _check(repo: Path, content: bytes) -> subprocess.CompletedProcess:
    (repo / "a.yaml").write_bytes(content)
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "x"], check=True, capture_output=True)
    return subprocess.run([sys.executable, str(CHECKS), "base"], cwd=repo, capture_output=True, text=True)


def test_diff_check_accepts_crlf_files(tmp_path):
    # claude-hub: lanes.yaml/config.py/runner.py son CRLF; una línea nueva con \r\n no es un espacio sobrante.
    cp = _check(_repo(tmp_path), b"x: 1\r\ny: 2\r\n")
    assert cp.returncode == 0, cp.stderr[-500:]


def test_diff_check_still_catches_trailing_spaces_in_crlf(tmp_path):
    cp = _check(_repo(tmp_path), b"x: 1\r\ny: 2  \r\n")
    assert cp.returncode == 1 and "trailing whitespace" in cp.stderr
