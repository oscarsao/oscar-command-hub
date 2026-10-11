"""tools/hermes-config/apply_dm_policy.py contra un HERMES_HOME temporal (nunca el real): dry-run por defecto,
backup, --apply idempotente y --revert exacto."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "hermes-config" / "apply_dm_policy.py"
CONFIG = ("model: x\ngateway:\n  allow_all_users: false\n  multiplex_profiles: true\n"
          "platforms:\n  telegram:\n    enabled: true\n")


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for n in ("GATEWAY_UNAUTHORIZED_DM_BEHAVIOR", "UNAUTHORIZED_DM_BEHAVIOR"):
        monkeypatch.delenv(n, raising=False)
    spec = importlib.util.spec_from_file_location("apply_dm_policy", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8", newline="\n")
    return m


def _cfg(tmp_path):
    return (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_dry_run_is_default_and_writes_nothing(mod, tmp_path):
    assert mod.main([]) == 0
    assert _cfg(tmp_path) == CONFIG and not list(tmp_path.glob("*.bak*"))


def test_apply_backs_up_and_only_adds_two_keys(mod, tmp_path, capsys):
    assert mod.main(["--apply"]) == 0
    data = yaml.safe_load(_cfg(tmp_path))
    assert data["gateway"]["unauthorized_dm_behavior"] == "decline"
    assert data["gateway"]["unauthorized_dm_decline_message"]
    assert data["gateway"]["allow_all_users"] is False and data["platforms"]["telegram"]["enabled"] is True
    assert (tmp_path / "config.yaml.bak-20261011-dmpolicy").read_text(encoding="utf-8") == CONFIG
    assert mod.main(["--apply"]) == 0  # idempotente
    assert "ya aplicado" in capsys.readouterr().out


def test_revert_restores_exact_text(mod, tmp_path):
    mod.main(["--apply"])
    assert mod.main(["--revert"]) == 0
    assert _cfg(tmp_path) == CONFIG


def test_aborts_without_writing_when_checks_fail(mod, tmp_path):
    (tmp_path / "config.yaml").write_text("model: x\n", encoding="utf-8")
    assert mod.main(["--apply"]) == 1 and _cfg(tmp_path) == "model: x\n"
    (tmp_path / "config.yaml").write_text(CONFIG.replace("gateway:\n", "gateway:\n  unauthorized_dm_behavior: pair\n"),
                                          encoding="utf-8", newline="\n")
    before = _cfg(tmp_path)
    assert mod.main(["--apply"]) == 1 and _cfg(tmp_path) == before and not list(tmp_path.glob("*.bak*"))


def test_does_not_print_config_values(mod, tmp_path, capsys):
    (tmp_path / "config.yaml").write_text(CONFIG + "secreto: SUPERSECRETO123\n", encoding="utf-8", newline="\n")
    mod.main([])
    mod.main(["--apply"])
    assert "SUPERSECRETO123" not in capsys.readouterr().out
