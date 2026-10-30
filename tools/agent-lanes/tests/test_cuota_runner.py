"""Grupo 1 del hub v1: error real del worker visible, pausa por cuota, lock caducado y modelo por tamaño. Solo fakes."""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from types import SimpleNamespace

from agent_lanes import bloqueos, cuota, grandes
from agent_lanes.config import Lane
from agent_lanes.runner import LaneRunner
from agent_lanes.service import Service, acquire_single_instance
from agent_lanes.worker import ClaudeWorker, WorkerOutcome, choose_model, parse_result, run_claude
from tests.test_runner_state import (LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, GOOD,
                                     ok_outcome)

LIMITE = "You've hit your usage limit · resets 6pm"
AHORA = datetime(2026, 10, 30, 14, 0).timestamp()


def limite_outcome(text=LIMITE):
    return WorkerOutcome(False, "success", None, 0.1, f"subtype=success is_error=True result={text}")


# --- 1. error real visible -----------------------------------------------------------------------------------------

def test_parse_result_incluye_el_texto_del_error():
    out = parse_result(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": LIMITE}))
    assert not out.ok and LIMITE in out.raw_error and "is_error=True" in out.raw_error


def test_parse_result_recorta_el_texto_largo():
    out = parse_result(json.dumps({"subtype": "error", "is_error": True, "result": "x" * 2000}))
    assert len(out.raw_error) < 600


def test_run_claude_anade_stderr_aunque_stdout_no_este_vacio():
    payload = json.dumps({"subtype": "error_max_turns", "is_error": True})

    def fake(args, **kw):
        return subprocess.CompletedProcess(args, 0, payload, "boom: cuota agotada")

    out = run_claude(fake, ["claude"], "p", "C:/wt", 60, {})
    assert "stderr=boom: cuota agotada" in out.raw_error


# --- 2. clasificación ----------------------------------------------------------------------------------------------

def test_bloqueos_clasifica_la_cuota_antes_que_el_tiempo():
    d = bloqueos.clasificar(f"el worker no terminó (tras 0 resume(s)): subtype=success is_error=True result={LIMITE}",
                            bloqueos.load_config("no-existe.yaml"))
    assert d.clase == bloqueos.TRANSITORIO and d.resumen == "se agotó la cuota de uso de Claude"
    assert bloqueos.clasificar("claude superó 7200s", bloqueos.load_config("no-existe.yaml")).resumen != d.resumen


# --- 3. pausa por cuota ----------------------------------------------------------------------------------------------

def test_es_cuota_y_hora_de_reset():
    assert cuota.es_cuota(LIMITE) and not cuota.es_cuota("subtype=error_max_budget_usd")
    assert cuota.hora(cuota.pausa_hasta("resets 6pm", AHORA)) == "18:00"
    assert cuota.hora(cuota.pausa_hasta("usage limit, resets at 18:30", AHORA)) == "18:30"
    assert cuota.hora(cuota.pausa_hasta("limit reached, resets 9am", AHORA)) == "09:00"  # ya pasó hoy: mañana
    assert cuota.pausa_hasta("resets in 2 hours", AHORA) == AHORA + 7200
    assert cuota.pausa_hasta("usage limit", AHORA, 45) == AHORA + 45 * 60  # sin hora legible: ahora + minutos


class FakeHermesCuota(FakeHermes):
    def __init__(self, tasks, events=None):
        super().__init__(tasks=tasks)
        self.status = "ready"
        self.events = events or []

    def block(self, task_id, kind, reason):
        self.status = "blocked"
        return super().block(task_id, kind, reason)

    def unblock(self, task_id):
        self.calls.append(("unblock", task_id))
        self.status = "ready"
        return True

    def show(self, task_id):
        return {"task": {"status": self.status}, "events": self.events}


def runner_cuota(tmp_path, outcomes, *, lane=LANE, hermes=None):
    h = hermes or FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    n, w = FakeNotifier(), FakeWorker(outcomes)
    r = LaneRunner(lane, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=n,
                   state_dir=tmp_path / "tasks", wall_clock=lambda: AHORA)
    return r, h, w, n


def test_worker_con_cuota_agotada_devuelve_la_tarea_a_ready(tmp_path):
    r, h, w, n = runner_cuota(tmp_path, [limite_outcome()])
    assert r.run_once() == {"t_1": "cuota"}
    assert h.status == "ready" and "unblock" in h.kinds()          # no queda blocked
    assert w.runs == ["run"]                                       # sin resumes contra la misma cuota
    data = json.loads((tmp_path / "cuota.json").read_text(encoding="utf-8"))
    assert cuota.hora(data["paused_until"]) == "18:00" and data["desde"] == AHORA
    assert any("cuota" in m for m in n.msgs + [e[1] for e in n.edits])  # un aviso con el mecanismo de siempre


def test_segunda_tarea_con_la_pausa_ya_vigente_no_repite_el_aviso(tmp_path):
    r, h, w, n = runner_cuota(tmp_path, [limite_outcome()])
    r.run_once()
    avisos = len(n.msgs) + len(n.edits)
    h2 = FakeHermesCuota([{"id": "t_2", "title": "T2", "body": "B"}])
    r2 = LaneRunner(LANE, hermes=h2, git=FakeGit(), worker=FakeWorker([limite_outcome()]), verifier=FakeVerifier(),
                    notify=n, state_dir=tmp_path / "tasks", wall_clock=lambda: AHORA + 60)
    assert r2.run_once() == {"t_2": "cuota"}
    assert len(n.msgs) + len(n.edits) == avisos + 1  # solo el «en curso» de t_2: ningún aviso de cuota nuevo


class LaneFalsa:
    name = "falsa"

    def __init__(self):
        self.lanzadas = []

    def jobs(self):
        return [("t_9", lambda: self.lanzadas.append("t_9") or "ok")]


def test_con_pausa_vigente_no_se_lanza_ningun_worker(tmp_path):
    path = tmp_path / "cuota.json"
    cuota.escribir(path, AHORA + 3600, LIMITE, AHORA)
    lane = LaneFalsa()
    svc = Service([lane], max_workers=2, cuota_pausada=lambda: cuota.vigente(path, AHORA + 60) is not None)
    assert svc.tick() == [] and svc.run_pass() == {} and svc.reserved_pass() == []
    svc.wait_all(5)
    assert lane.lanzadas == []


def test_con_pausa_vencida_se_lanza_y_se_borra_la_pausa(tmp_path):
    path = tmp_path / "cuota.json"
    cuota.escribir(path, AHORA + 3600, LIMITE, AHORA)
    lane = LaneFalsa()
    svc = Service([lane], max_workers=2, cuota_pausada=lambda: cuota.vigente(path, AHORA + 3601) is not None)
    assert svc.tick() == ["t_9"]
    svc.wait_all(5)
    assert lane.lanzadas == ["t_9"] and not path.exists()


def test_status_muestra_la_pausa_vigente(tmp_path):
    path = tmp_path / "cuota.json"
    assert cuota.texto_estado(path, AHORA) == ""
    cuota.escribir(path, AHORA + 3600, LIMITE, AHORA)
    assert cuota.texto_estado(path, AHORA) == "pausado por cuota hasta 15:00"


# --- 4. lock caducado ------------------------------------------------------------------------------------------------

def test_lock_de_un_pid_inexistente_se_toma_con_aviso(tmp_path, caplog):
    lock = tmp_path / "runner.lock"
    lock.write_text(json.dumps({"pid": 99999999, "started": 1}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        assert acquire_single_instance(lock, pid_alive=lambda pid: False)
    assert "lock caducado del PID 99999999: lo tomo" in caplog.text


def test_lock_de_un_runner_vivo_no_se_toma(tmp_path, caplog):
    lock = tmp_path / "runner.lock"
    lock.write_text(json.dumps({"pid": 99999999, "started": 1}), encoding="utf-8")
    assert not acquire_single_instance(lock, pid_alive=lambda pid: True)
    assert "caducado" not in caplog.text


# --- 5. modelo por tamaño --------------------------------------------------------------------------------------------

LANE_S = Lane(**{**LANE.__dict__, "model_small": "haiku"})
PEQUENA = {"id": "t_1", "title": "T", "body": "Hazlo\n\nTamaño: S\n"}


def test_es_pequena():
    assert grandes.es_pequena(PEQUENA)
    assert grandes.es_pequena({"body": "- **Tamaño:** S"})
    assert not grandes.es_pequena({"body": "Tamaño: L"}) and not grandes.es_pequena({"body": "Tamaño: Small-ish"})
    assert not grandes.es_pequena({"body": "sin tamaño"}) and not grandes.es_pequena({"title": "[GRANDE]", "body": "Tamaño: S"})


def test_choose_model_barato_para_s_y_fallback_tras_un_fallo(caplog):
    assert choose_model(LANE_S, PEQUENA) == "haiku"
    assert choose_model(LANE_S, {**PEQUENA, "model_fallback": True}) == LANE.model
    assert choose_model(LANE_S, {"id": "t_2", "body": "Tamaño: M"}) == LANE.model
    assert choose_model(LANE, PEQUENA) == LANE.model  # sin model_small configurado
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        choose_model(LANE_S, {**PEQUENA, "model_fallback": True})
    assert "ya falló con haiku" in caplog.text


def test_el_worker_lanza_claude_con_el_modelo_elegido_tambien_en_el_resume():
    modelos = []

    def fake_run(args, **kw):
        modelos.append(args[args.index("--model") + 1])
        return subprocess.CompletedProcess(args, 0, json.dumps({"subtype": "error_max_budget_usd", "is_error": True}), "")

    w = ClaudeWorker(runner=fake_run)
    w.run(LANE_S, PEQUENA, "C:/wt", "s", timeout=60)
    w.resume(LANE_S, "C:/wt", "s", timeout=60, task_id="t_1")
    w.run(LANE_S, {**PEQUENA, "model_fallback": True}, "C:/wt", "s2", timeout=60)
    assert modelos == ["haiku", "haiku", LANE.model]


def test_runner_marca_el_fallback_solo_con_fallos_reales(tmp_path):
    bloqueo = {"kind": "blocked", "payload": {"kind": "transient", "reason": "verificación mecánica fallida"}}
    cuota_ev = {"kind": "blocked", "payload": {"kind": "transient", "reason": f"{cuota.MOTIVO_PREFIX} hasta 18:00"}}
    pregunta = {"kind": "blocked", "payload": {"kind": "needs_input", "reason": "¿cuál?"}}
    casos = [([], False), ([cuota_ev, pregunta], False), ([bloqueo], True), ([cuota_ev, bloqueo], True)]
    for events, esperado in casos:
        h = FakeHermesCuota([PEQUENA], events=events)
        r, *_ = runner_cuota(tmp_path, [ok_outcome()], lane=LANE_S, hermes=h)
        assert bool(r._model_fallback("t_1", PEQUENA).get("model_fallback")) is esperado, events
    r, *_ = runner_cuota(tmp_path, [ok_outcome()], lane=LANE)  # sin model_small: ni mira el historial
    assert r._model_fallback("t_1", PEQUENA) is PEQUENA
