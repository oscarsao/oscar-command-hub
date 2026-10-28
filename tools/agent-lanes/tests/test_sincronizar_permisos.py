"""tools/telegram/sincronizar_permisos.py: people.yaml -> listas de acceso y tabla de permisos de Hermes (24-10)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "telegram" / "sincronizar_permisos.py"
spec = importlib.util.spec_from_file_location("sincronizar_permisos", SCRIPT)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

PEOPLE = """people:
  maria: {name: María, user_id: "111", role: coo}
  ingerber: {name: Ingerber, user_id: "222", role: cmo}
  angel: {name: Angel, user_id: "333", role: dev}
  mauredy: {name: Mauredy, user_id: null, role: dev}
"""
_TODO = ["desarrollo.aprobar", "desarrollo.publicar"]
HQ = {"personas": [{"telegram_id": "111", "permisos": {"*": _TODO}},
                   {"telegram_id": "333", "permisos": {"Píldora": _TODO, "Signal": _TODO}}]}


@pytest.fixture(autouse=True)
def _hq(monkeypatch):
    from agent_lanes import people
    monkeypatch.setattr(people, "hq_desde_entorno", lambda *a, **k: (lambda: HQ))
SOUL = "# Hermes\r\n\r\n<!-- PERSONAS-ACCESO:inicio -->\r\n## Personas\r\n- regla a mano\r\n<!-- PERSONAS-ACCESO:fin -->\r\n"
ENV = "OTRA=x\r\nTELEGRAM_ALLOWED_USERS=999\r\nTELEGRAM_GROUP_ALLOWED_USERS=333\r\nTELEGRAM_BOT_TOKEN=secreto\r\n"


def _home(tmp_path, env=ENV, soul=SOUL):
    (tmp_path / "people.yaml").write_text(PEOPLE, encoding="utf-8")
    for name, text in ((".env", env), ("SOUL.md", soul)):
        with open(tmp_path / name, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return ["--hermes-home", str(tmp_path), "--people", str(tmp_path / "people.yaml")]


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def test_dry_run_changes_nothing_and_never_prints_env_values(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    args = _home(tmp_path)
    assert sp.main(args) == 1
    out = capsys.readouterr().out
    assert _read(tmp_path / ".env") == ENV and _read(tmp_path / "SOUL.md") == SOUL
    assert "secreto" not in out and "999" not in out and "Mauredy" in out
    assert not list(tmp_path.glob("*.bak-sync-*"))


def test_apply_writes_owner_plus_people_with_id_and_keeps_crlf_and_other_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    args = _home(tmp_path)
    assert sp.main(args + ["--apply"]) == 0
    env = _read(tmp_path / ".env")
    ids = f"{sp.DEFAULT_OWNER},111,222,333"
    assert env == f"OTRA=x\r\nTELEGRAM_ALLOWED_USERS={ids}\r\nTELEGRAM_GROUP_ALLOWED_USERS={ids}\r\nTELEGRAM_BOT_TOKEN=secreto\r\n"
    soul = _read(tmp_path / "SOUL.md")
    assert "- regla a mano" in soul and "\r\n" in soul and "\n" not in soul.replace("\r\n", "")
    assert soul.index(sp.ANCHOR) < soul.index(sp.MARK_START) < soul.index("## Personas")
    fila = {l.split("|")[1].strip(): l for l in soul.split("\r\n") if l.startswith("| ") and "---" not in l}
    assert "sí (según los permisos de Oscar HQ)" in fila["Angel"] and "Píldora, Signal" in fila["Angel"] and fila["Angel"].endswith("| no |")
    assert fila["María"].endswith("| sí |") and "| no |" in fila["Ingerber"]
    assert "Mauredy" not in fila and "Sin alta" in soul and len(list(tmp_path.glob("*.bak-sync-*"))) == 2
    # idempotente: segunda pasada sin cambios y el bloque no se duplica
    assert sp.main(args) == 0
    assert _read(tmp_path / "SOUL.md").count(sp.MARK_START) == 1


def test_missing_keys_are_appended(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    args = _home(tmp_path, env="OTRA=x\n")
    assert sp.main(args + ["--apply"]) == 0
    assert _read(tmp_path / ".env").splitlines()[1:] == [f"TELEGRAM_ALLOWED_USERS={sp.DEFAULT_OWNER},111,222,333",
                                                           f"TELEGRAM_GROUP_ALLOWED_USERS={sp.DEFAULT_OWNER},111,222,333"]


def test_duplicate_and_export_keys_are_all_updated(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    env = "TELEGRAM_ALLOWED_USERS=1\nexport TELEGRAM_GROUP_ALLOWED_USERS = 2\nTELEGRAM_ALLOWED_USERS=3\n"
    args = _home(tmp_path, env=env)
    assert sp.main(args + ["--apply"]) == 0
    ids = f"{sp.DEFAULT_OWNER},111,222,333"
    assert _read(tmp_path / ".env") == (f"TELEGRAM_ALLOWED_USERS={ids}\nTELEGRAM_GROUP_ALLOWED_USERS={ids}\n"
                                        f"TELEGRAM_ALLOWED_USERS={ids}\n")


def test_broken_markers_abort_without_writing(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_TELEGRAM_ID", raising=False)
    soul = SOUL + sp.MARK_END + "\r\n"
    args = _home(tmp_path, soul=soul)
    assert sp.main(args + ["--apply"]) == 2
    assert _read(tmp_path / "SOUL.md") == soul and _read(tmp_path / ".env") == ENV
