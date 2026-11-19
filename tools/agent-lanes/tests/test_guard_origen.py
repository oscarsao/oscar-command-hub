"""Guard: `git checkout`/`git restore` de rutas desde ramas de `Rama-origen:` (t_f6511420). Sin red ni git real."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
import guard  # noqa: E402

from agent_lanes.worker import origin_refs  # noqa: E402
from agent_lanes.config import Lane  # noqa: E402

ORIGIN = json.dumps(["origin/lane/t_aaaa1111", "origin/main", "origin/feat/ui"])


def bash(cmd, origin=ORIGIN):
    env = {"AGENT_LANES_ORIGIN_OK": origin} if origin else {}
    return guard.check({"tool_name": "Bash", "tool_input": {"command": cmd}}, env=env)


@pytest.mark.parametrize("cmd", [
    "git checkout origin/lane/t_aaaa1111 -- src/App.tsx",
    "git checkout origin/feat/ui -- src/a.ts docs/b.md",
    "git -C C:/wt checkout origin/feat/ui -- src",
    "git restore --source=origin/feat/ui src/a.ts",
    "git restore --source origin/feat/ui --staged --worktree -- src/a.ts",
    "git status && git commit -m 'restore y checkout de x'",
    "git log --grep checkout",
])
def test_permitidos(cmd):
    assert bash(cmd) is None


@pytest.mark.parametrize("cmd", [
    "git checkout origin/main -- src/a.ts",           # nunca main (aunque se colara en la lista)
    "git checkout origin/master -- src/a.ts",
    "git checkout origin/otra -- src/a.ts",           # rama no citada
    "git checkout main",
    "git checkout lane/t_1",
    "git checkout -b nueva",
    "git checkout -f origin/feat/ui -- src/a.ts",
    "git checkout origin/feat/ui",
    "git checkout origin/feat/ui --",
    "git checkout origin/feat/ui -- ../fuera/a.ts",
    "git checkout origin/feat/ui -- /abs/a.ts",
    "git checkout origin/feat/ui -- .github/workflows/ci.yml",
    "git checkout origin/feat/ui -- .",
    "git checkout -- src/a.ts",                        # descartar cambios locales
    "git restore src/a.ts",
    "git restore --staged src/a.ts",
    "git restore --source=origin/main src/a.ts",
    "git restore --source=origin/otra src/a.ts",
    "git restore -p --source=origin/feat/ui src/a.ts",
    "git restore --source=origin/feat/ui --ours src/a.ts",
    "git restore --source=origin/feat/ui",
    "git switch main",
    "git -c core.x=1 checkout origin/feat/ui -- a.ts",
])
def test_denegados(cmd):
    assert bash(cmd), cmd


@pytest.mark.parametrize("origin", [None, "no-json", "[]"])
def test_sin_ramas_declaradas_falla_cerrado(origin):
    assert bash("git checkout origin/feat/ui -- a.ts", origin=origin)
    assert bash("git restore --source=origin/feat/ui a.ts", origin=origin)


def test_main_o_master_en_la_lista_se_descartan_en_el_guard():
    env = {"AGENT_LANES_ORIGIN_OK": json.dumps(["origin/main", "origin/master"])}
    assert guard.check({"tool_name": "Bash", "tool_input": {"command": "git checkout origin/main -- a"}}, env=env)


def test_origin_refs_solo_de_lineas_rama_origen_y_nunca_main():
    lane = Lane(name="claude-hub", board="default", repo="C:/hub", base="main", worktree_root="C:/wt")
    body = "Rama-origen: lane/t_aaaa1111\nRama-origen: origin/feat/ui\nRama-origen: main\nRama-origen: master\n" \
           "Mira lane/t_bbbb2222 en el texto."
    assert origin_refs(lane, {"body": body}) == ["origin/lane/t_aaaa1111", "origin/feat/ui"]
    assert origin_refs(lane, {"body": "sin línea"}) == []
