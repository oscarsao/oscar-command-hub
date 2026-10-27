"""F1-08 (CF-15): skills_sync.py --check compara las skills del hub con las instaladas, sin copiar nada."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("skills_sync", Path(__file__).resolve().parents[1] / "skills_sync.py")
skills_sync = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("skills_sync", skills_sync)
_spec.loader.exec_module(skills_sync)


def _skill(base: Path, name: str, text: str, extra: dict | None = None) -> None:
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(text.encode("utf-8"))
    for rel, body in (extra or {}).items():
        (d / rel).write_bytes(body.encode("utf-8"))


def _hub(tmp_path: Path) -> Path:
    hub = tmp_path / "hub"
    _skill(hub / "skills", "barrido", "a")
    _skill(hub / "tools/agent-lanes/skills", "tomar-tarea", "nueva", {"ref.md": "x"})
    (hub / "skills" / "README.md").write_text("no es una skill", encoding="utf-8")
    return hub


def test_iguales_sale_0(tmp_path, capsys):
    hub, inst = _hub(tmp_path), tmp_path / "inst"
    _skill(inst, "barrido", "a")
    _skill(inst, "tomar-tarea", "nueva", {"ref.md": "x"})
    _skill(inst, "spec-lite", "solo instalada")  # no versionada en el hub: no se mira
    assert skills_sync.main(["--check", "--hub", str(hub), "--instaladas", str(inst)]) == 0
    assert "2 skills iguales" in capsys.readouterr().out


def test_difiere_o_falta_sale_1_y_no_copia(tmp_path, capsys):
    hub, inst = _hub(tmp_path), tmp_path / "inst"
    _skill(inst, "tomar-tarea", "vieja")  # SKILL.md distinto y sin ref.md
    antes = sorted(p.as_posix() for p in inst.rglob("*"))
    assert skills_sync.main(["--check", "--hub", str(hub), "--instaladas", str(inst)]) == 1
    out = capsys.readouterr().out
    assert "falta instalada" in out and "barrido" in out
    assert "difiere" in out and "SKILL.md" in out and "ref.md" in out
    assert sorted(p.as_posix() for p in inst.rglob("*")) == antes  # solo lectura
    assert (inst / "tomar-tarea" / "SKILL.md").read_text(encoding="utf-8") == "vieja"


def test_sin_carpeta_instalada_no_revienta(tmp_path):
    assert skills_sync.main(["--check", "--hub", str(_hub(tmp_path)), "--instaladas", str(tmp_path / "no")]) == 1


def test_hub_sin_skills_sale_2(tmp_path):
    assert skills_sync.main(["--check", "--hub", str(tmp_path), "--instaladas", str(tmp_path)]) == 2


def test_el_hub_real_tiene_las_skills_versionadas():
    nombres = set(skills_sync.skills_del_hub(skills_sync.HUB))
    assert {"tomar-tarea", "cerrar-tarea", "barrido-carriles"} <= nombres
