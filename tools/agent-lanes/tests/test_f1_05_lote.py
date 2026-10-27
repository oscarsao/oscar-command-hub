"""F1-05 (plan del hub 25-10): el lote guarda la salida del gate que falla, juzga los tests del panel por diferencia
frente a la base (D25-A) y actualiza con la base (update-branch, HTTP simulado) las ramas aprobadas que van detrás.
Mismo montaje que test_batch: git real en tmp_path; gh, Hermes y Telegram simulados."""
from __future__ import annotations

import json

import pytest

from agent_lanes.batch import failure_signatures
from agent_lanes.integrator import Policy
from tests.test_batch import Env, sh
from tests.test_falta import notices
from tests.test_integrator import buttons, press

# El «vitest» simulado: siempre falla un test viejo (rojo en la base) y, si existe frontend/NEW, uno nuevo.
FRONT = ('py -3.12 -c "import os,sys;print(\' FAIL  src/old.test.ts > viejo\');'
         'print(\' FAIL  src/new.test.ts > nuevo\' if os.path.exists(\'frontend/NEW\') else \'\');sys.exit(1)"')


def front_env(tmp_path) -> Env:
    pol = Policy(lane="claude-oscarhq", batch="daily", batch_brand="oscarhq", deploy="none",
                 batch_frontend_paths=("frontend/",), batch_frontend_cmd=FRONT)
    e = Env(tmp_path, pol)
    e.created_cards = []
    e.h.create = lambda title, body, assignee, **kw: e.created_cards.append(
        {"title": title, "body": body, "assignee": assignee, "key": kw.get("key")}) or f"t_{len(e.created_cards):08x}"
    return e


def test_failure_signatures_reads_vitest_and_tsc():
    out = ("\x1b[31m FAIL \x1b[39m src/a.test.ts > grupo > caso 12ms\n × src/b.test.tsx > otro\n"
           "src\\c.ts(10,4): error TS2345: Argument of type\nTests  1 failed | 3 passed\n")
    assert failure_signatures(out) == {"src/a.test.ts > grupo > caso", "src/b.test.tsx > otro", "src/c.ts TS2345"}
    assert failure_signatures("Error: algo raro") == set()


def test_branch_without_new_frontend_failures_is_member(tmp_path):
    e = front_env(tmp_path)
    e.branch("t_ok", {"frontend/x.ts": "export const x = 1\n"})
    e.queue()
    out = e.integ.batches.assemble(e.lane, e.policy)
    assert "PR #100" in out
    b = e.integ.batches.all()[0]
    assert [m["tid"] for m in b["members"]] == ["t_ok"]
    assert any("sin fallos nuevos (1 ya presentes en main)" in g for g in b["rec"]["gates"])


def test_new_frontend_failure_excludes_and_gate_output_is_saved(tmp_path):
    e = front_env(tmp_path)
    e.branch("t_new", {"frontend/NEW": "x\n"})
    e.queue()
    out = e.integ.batches.assemble(e.lane, e.policy)
    assert "no se pudo montar" in out and e.gh.created == []
    [b] = e.integ.batches.all()
    assert b["status"] == "failed" and "frontend falla" in b["reasons"][0]
    [g] = b["gate_failures"]
    assert g["exit"] == 1 and "src/new.test.ts > nuevo" in g["salida"] and "Fallos nuevos" in g["salida"]
    assert "src/old.test.ts" not in g["salida"].split("\n\n")[0]  # lo nuevo, separado de lo que ya fallaba


def _move_base(e: Env) -> None:
    e.write("src/b.py", "b = 9\n")
    sh(e.repo, "add", "-A")
    sh(e.repo, "commit", "-m", "base avanza")
    sh(e.repo, "push", "-q", "origin", "main")


def test_branch_behind_is_updated_and_approval_kept(tmp_path):
    e = Env(tmp_path)
    n = e.branch("t_a", {"src/a.py": "a = 2\n"})
    e.queue()
    old = e.gh.prs[n]["headRefOid"]
    _move_base(e)
    out = e.integ.batches.assemble(e.lane, e.policy)
    assert e.gh.updates == [n] and "PR #100" in out
    new = e.gh.prs[n]["headRefOid"]
    assert new != old
    api = next(a for a in e.gh.log if a[1] == "api")
    assert "PUT" in api and f"expected_head_sha={old}" in api
    approval = json.loads((tmp_path / "state" / "approvals" / "t_a.json").read_text(encoding="utf-8"))
    assert approval["head_sha"] == new  # la aprobación pasa a la cabeza que hizo GitHub
    b = e.integ.batches.all()[0]
    assert b["members"][0]["head_sha"] == new
    assert any("actualizada con main" in c[1] for c in e.h.comments if c[0] == "t_a")


def test_branch_up_to_date_is_not_updated(tmp_path):
    e = Env(tmp_path)
    e.branch("t_a", {"src/a.py": "a = 2\n"})
    e.queue()
    e.integ.batches.assemble(e.lane, e.policy)
    assert e.gh.updates == []


@pytest.mark.parametrize("label", ["🔁 Crear tarjeta de rebase", "🧑‍💻 Lo resuelvo en consola", "🗑 Descartar PR"])
def test_update_conflict_leaves_ficha_with_three_buttons(tmp_path, label):
    e = front_env(tmp_path)
    n = e.branch("t_a", {"src/a.py": "a = 2\n"})
    e.queue()
    _move_base(e)
    e.gh.update_mode = "conflict"
    out = e.integ.batches.assemble(e.lane, e.policy)
    assert "No hay nada" in out and e.gh.created == []
    [ficha] = notices(e)
    assert buttons(ficha["markup"]) == ["🔁 Crear tarjeta de rebase", "🧑‍💻 Lo resuelvo en consola",
                                        "🗑 Descartar PR", "💬 Explícame más"]
    row = next(r for r in ficha["markup"]["inline_keyboard"] if r[0]["text"] == label)
    press(e.desk, e.tg, markup={"inline_keyboard": [row]})
    if label.startswith("🔁"):
        [card] = e.created_cards
        assert card["assignee"] == "claude-oscarhq" and "Rebasar #" in card["title"]
        assert e.integ._state("t_a")["status"] == "batch_replaced"
    elif label.startswith("🧑‍💻"):
        [card] = e.created_cards
        assert card["assignee"] == "oscar-e2" and "Cierra: t_a" in card["body"]
        assert e.integ._state("t_a")["status"] == "batch_pending"
    else:
        assert e.gh.prs[n]["state"] == "CLOSED" and e.integ._state("t_a")["status"] == "batch_discarded"
