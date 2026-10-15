"""Presupuesto por TAREA (t_ad46ae34): run + resumes comparten `max_budget_usd`, no uno por invocación."""
import json
import subprocess

from agent_lanes.worker import ClaudeWorker
from tests.test_runner_state import GOOD, LANE

TASK = {"id": "t_9", "title": "T", "body": ""}


def make(costs, ok=False):
    calls = []

    def fake_run(args, **kw):
        calls.append(float(args[args.index("--max-budget-usd") + 1]))
        payload = {"subtype": "success" if ok else "error_max_budget_usd", "is_error": not ok,
                   "total_cost_usd": costs[len(calls) - 1]}
        if ok:
            payload["structured_output"] = GOOD
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    return ClaudeWorker(runner=fake_run), calls


def test_budget_is_split_and_total_never_exceeds_lane_cap():
    w, calls = make([1.8, 0.6, 0.6])
    o = w.run(LANE, TASK, "C:/wt", "s", timeout=60)
    assert calls == [1.8] and o.cost_usd == 1.8          # 60 % del tope en la 1ª llamada
    o = w.resume(LANE, "C:/wt", "s", timeout=60, task_id="t_9")
    assert calls[1] == 0.6 and o.cost_usd == 2.4         # 1,2 restantes entre 2 resumes
    o = w.resume(LANE, "C:/wt", "s", timeout=60, task_id="t_9")
    assert calls[2] == 0.6 and o.cost_usd == 3.0         # el último se lleva lo que queda
    assert sum(calls) <= LANE.max_budget_usd


def test_exhausted_budget_does_not_call_claude():
    w, calls = make([3.0])
    w._spent["s"] = 2.95
    o = w.resume(LANE, "C:/wt", "s", timeout=60, task_id="t_9")
    assert calls == [] and not o.ok and o.subtype == "error_max_budget_usd" and o.cost_usd == 2.95


def test_lane_without_resumes_keeps_full_budget_and_sessions_are_independent():
    w, calls = make([0.5, 0.5], ok=True)
    lane = type(LANE)(**{**LANE.__dict__, "max_resumes": 0})
    w.run(lane, TASK, "C:/wt", "a", timeout=60)
    w.run(lane, TASK, "C:/wt", "b", timeout=60)
    assert calls == [lane.max_budget_usd, lane.max_budget_usd]
