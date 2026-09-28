import subprocess
import sys
from pathlib import Path

CHECKS = Path(__file__).resolve().parents[1] / "agent_lanes" / "checks.py"


def test_checks_runs_as_standalone_script():
    # El runner lo lanza por ruta con cwd = worktree (no como paquete): un import relativo lo rompe (27-09).
    repo = Path(__file__).resolve().parents[3]
    cp = subprocess.run([sys.executable, str(CHECKS), "HEAD"], cwd=repo, capture_output=True, text=True)
    assert "ImportError" not in cp.stderr and cp.returncode == 0, cp.stderr[-500:]
