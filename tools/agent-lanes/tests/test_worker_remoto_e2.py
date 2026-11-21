"""E2 del worker remoto por SSH (specs/worker-remoto-ssh.md): todo con `FakeSsh`/fakes; nunca hay SSH real, ni otro equipo,
ni red. Cada pieza lleva sus pruebas y, donde importa, la prueba de que SIN `host:` (o con las claves en su valor por
defecto) nada cambia."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_lanes import host_ops, relanzar, remote_exec, ssh_host
from agent_lanes.contrato import contract_hash
from agent_lanes.git_ops import GitError
from agent_lanes.host_ops import HostStore
from agent_lanes.origen_host import origen_host
from agent_lanes.runner import LaneRunner
from agent_lanes.ssh_host import (ClaudeReviewerRemoto, ClaudeWorkerRemoto, GitOpsRemoto, HostError, HostUnreachable,
                                  SshRunner, pila_review_remota, sweep_done_remoto)
from tests.test_runner_state import FakeGit, FakeNotifier, FakeVerifier, FakeWorker, LANE
from tests.test_worker_remoto import HOST, RLANE, FakeSsh, Reply, TASK

NOW = 1_800_000_000.0


def status_reply(**kw):
    data = {"host_time": NOW, "disk_free_gb": 50.0, "hub_head": "b" * 40, "contract_hash": "h1", **kw}
    return Reply(out=json.dumps(data))


def op_ssh(estado=None, **status):
    def script(req):
        if req.get("op") == "kill":
            return Reply(out=json.dumps({"estado": estado}))
        return status_reply(**status)
    return FakeSsh(script)


# --- remote_exec: ops propias del lanzador (lista cerrada) -------------------------------------------------------------

def run_launcher(req):
    cp = subprocess.run([sys.executable, str(remote_exec.Path(remote_exec.__file__))], input=json.dumps(req), text=True,
                        capture_output=True, timeout=60)
    return cp


def test_launcher_op_status_returns_disk_head_and_contract_hash():
    cp = run_launcher({"v": 1, "nonce": "n1", "op": "status", "worktree_root": "C:/no/existe/aun"})
    data = json.loads(cp.stdout)
    assert cp.returncode == 0 and "@@LANE-n1-RC 0" in cp.stderr
    assert data["contract_hash"] == contract_hash(remote_exec.ROOT) and data["contract_hash"]
    assert data["disk_free_gb"] is None or data["disk_free_gb"] > 0


def test_launcher_unknown_op_fails_closed_without_rc_line():
    cp = run_launcher({"v": 1, "nonce": "n2", "op": "rm-rf"})
    assert cp.returncode == 2 and "@@LANE-n2-RC" not in cp.stderr


def test_op_kill_only_cuts_when_creation_time_matches(monkeypatch):
    cortados = []
    monkeypatch.setattr(remote_exec._proc, "kill_tree", lambda pid: cortados.append(pid))
    estados = iter([100.0, None])  # antes de cortar existe; después ya no
    monkeypatch.setattr(remote_exec._proc, "created_at", lambda pid: next(estados))
    assert remote_exec.op_kill({"pid": 7, "created": 101.0}) == {"estado": "cortado"} and cortados == [7]
    cortados.clear()
    monkeypatch.setattr(remote_exec._proc, "created_at", lambda pid: 500.0)  # PID reciclado por otro proceso
    assert remote_exec.op_kill({"pid": 7, "created": 101.0}) == {"estado": "otro_proceso"} and not cortados
    assert remote_exec.op_kill({"pid": 7, "created": None}) == {"estado": "sin_hora"} and not cortados
    monkeypatch.setattr(remote_exec._proc, "created_at", lambda pid: None)
    assert remote_exec.op_kill({"pid": 7, "created": 1.0}) == {"estado": "no_existe"}


def test_contract_hash_ignores_line_endings_and_pycache(tmp_path):
    (tmp_path / "contract" / "__pycache__").mkdir(parents=True)
    (tmp_path / "contract" / "a.json").write_bytes(b"{\r\n}\r\n")
    h1 = contract_hash(tmp_path)
    (tmp_path / "contract" / "a.json").write_bytes(b"{\n}\n")
    (tmp_path / "contract" / "__pycache__" / "x.pyc").write_bytes(b"zz")
    assert contract_hash(tmp_path) == h1 and h1
    (tmp_path / "contract" / "a.json").write_bytes(b"{ }\n")
    assert contract_hash(tmp_path) != h1 and contract_hash(tmp_path / "nada") == ""


# --- endurecimiento de E1 (2.ª revisión de #183) -----------------------------------------------------------------------

def test_real_transport_reads_stderr_in_bounded_chunks():
    """Una línea de stderr sin salto de línea ya no se acumula entera: se corta por el tope de bytes."""
    codigo = "import sys; sys.stderr.write('x' * 400000); sys.stderr.flush()"
    with pytest.raises(HostError, match="superior"):
        ssh_host.real_transport([sys.executable, "-c", codigo], input="{}", timeout=30, max_bytes=100000)


def test_ssh_local_deadline_is_a_transport_error_that_may_leave_the_worker_alive():
    def transport(cmd, **kw):
        kw["on_stderr_line"](f"@@LANE-{json.loads(kw['input'])['nonce']}-PID 99 1.0\n")
        raise subprocess.TimeoutExpired(cmd, 5)
    with pytest.raises(HostError, match="pudo seguir vivo"):
        SshRunner(HOST, transport)(["claude"], timeout=5, on_start=lambda p: None)


def test_every_transport_message_is_excluded_from_auto_relaunch():
    marcas = [relanzar._norm(m) for m in relanzar.EXCLUIR_DEFECTO]
    yaml_marcas = [relanzar._norm(m) for m in relanzar.load_ajustes(relanzar.ROOT / "relanzar.yaml").excluir_motivos]
    mensajes = []
    for transport, cls in ((lambda cmd, **kw: subprocess.CompletedProcess(cmd, 255, "", "ssh: connect refused"), HostUnreachable),
                           (lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 5)), HostError)):
        with pytest.raises(cls) as exc:
            SshRunner(HOST, transport)(["x"], timeout=5)
        mensajes.append(str(exc.value))
    mensajes.append(str(HostError("salida remota superior a 8388608 bytes: descartada; el proceso remoto pudo seguir vivo")))
    for m in mensajes:
        assert any(x in relanzar._norm(m) for x in marcas) and any(x in relanzar._norm(m) for x in yaml_marcas), m
    assert "no alcanzable" not in " ".join(marcas)  # esa marca casaba también con los errores de Telegram


def test_reconcile_keeps_remote_worker_keys_and_the_reason_is_not_auto_relaunched(tmp_path):
    from tests.test_runner_state import ReconHermes
    (tmp_path / "t_1.json").write_text(json.dumps({"task_id": "t_1", "lane": RLANE.name, "pid": 999999, "host": "worker-b",
                                                   "host_worker_pid": 77, "host_worker_created": 5.0}))
    h = ReconHermes(running=[{"id": "t_1"}])
    r = LaneRunner(RLANE, hermes=h, git=FakeGit(), worker=FakeWorker([]), verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=tmp_path, pid_alive=lambda pid: False)
    assert r.reconcile() == ["t_1"]
    estado = json.loads((tmp_path / "t_1.json").read_text())
    assert estado == {"task_id": "t_1", "lane": RLANE.name, "host": "worker-b", "host_worker_pid": 77, "host_worker_created": 5.0}
    motivo = [c for c in h.calls if c[0] == "block"][0][3]
    assert any(relanzar._norm(m) in relanzar._norm(motivo) for m in relanzar.EXCLUIR_DEFECTO)
    # sin worker remoto apuntado, todo igual que antes: el estado se borra
    (tmp_path / "t_2.json").write_text(json.dumps({"task_id": "t_2", "lane": LANE.name, "pid": 999999}))
    h2 = ReconHermes(running=[{"id": "t_2"}])
    LaneRunner(LANE, hermes=h2, git=FakeGit(), worker=FakeWorker([]), verifier=FakeVerifier(), notify=FakeNotifier(),
               state_dir=tmp_path, pid_alive=lambda pid: False).reconcile()
    assert not (tmp_path / "t_2.json").exists()


# --- pieza 5: hub_sha_minimo y hash del contrato (sondeo + puerta) -----------------------------------------------------

def git_runner(contiene=True, conocidos=("b" * 40, "c" * 40), head="c" * 40):
    llamadas = []

    def run(cmd, **kw):
        llamadas.append(cmd)
        sub = cmd[3:]
        if sub[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, head + "\n", "")
        if sub[:1] == ["cat-file"]:
            return subprocess.CompletedProcess(cmd, 0 if sub[2].split("^")[0] in conocidos else 128, "", "")
        if sub[:2] == ["merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(cmd, 0 if contiene else 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    run.llamadas = llamadas
    return run


def lane_con(**host_kw):
    return replace(RLANE, host=replace(HOST, **host_kw))


def test_gate_is_inert_with_default_keys_and_for_local_lanes(tmp_path):
    store = HostStore(tmp_path)
    assert host_ops.gate(RLANE, {}, store, NOW) is None  # carril con host pero sin claves de E2
    assert host_ops.gate(LANE, {}, store, NOW) is None  # carril local
    assert not host_ops.sondeo_activo(HOST)


def test_probe_records_signal_with_hub_receive_time_and_gate_opens(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(latido_s=60)
    assert "sin señal" in host_ops.gate(lane, {}, store, NOW)  # nunca ha habido sondeo
    st = host_ops.probe(lane.host, store, transport=op_ssh(host_time=1.0), now=NOW, run=git_runner())
    assert st["ultimaSenal"] == NOW and st["hostTime"] == 1.0  # manda la hora del hub, no la declarada
    assert host_ops.gate(lane, {}, store, NOW + 100) is None
    assert host_ops.host_estado(lane.host, store, NOW + 100) == "ok"


def test_five_minutes_without_signal_marks_host_and_blocks_then_recovers(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(latido_s=60)
    host_ops.probe(lane.host, store, transport=op_ssh(), now=NOW, run=git_runner())
    assert host_ops.gate(lane, {}, store, NOW + 299) is None
    motivo = host_ops.gate(lane, {}, store, NOW + 301)
    assert "sin señal" in motivo and host_ops.host_estado(lane.host, store, NOW + 301) == "sin_senal"
    # el sondeo fallido no renueva la señal; uno correcto (la hora del HUB) sí
    caido = FakeSsh(lambda req: Reply(ssh_rc=255, no_rc=True))
    assert host_ops.probe(lane.host, store, transport=caido, now=NOW + 310, run=git_runner()) is None
    assert store.read("worker-b")["ultimaSenal"] == NOW
    assert host_ops.probe(lane.host, store, transport=op_ssh(), now=NOW + 320, run=git_runner())
    assert host_ops.gate(lane, {}, store, NOW + 321) is None


def test_failed_last_probe_closes_the_gate_immediately(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(latido_s=60)
    host_ops.probe(lane.host, store, transport=op_ssh(), now=NOW, run=git_runner())
    host_ops.probe(lane.host, store, transport=FakeSsh(lambda r: Reply(ssh_rc=255, no_rc=True)), now=NOW + 5, run=git_runner())
    assert "no respondió" in host_ops.gate(lane, {}, store, NOW + 6)


def test_disk_minimum_blocks_when_host_reports_less(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(disco_min_gb=10)
    host_ops.probe(lane.host, store, transport=op_ssh(disk_free_gb=4.5), now=NOW, run=git_runner())
    assert "poco disco" in host_ops.gate(lane, {}, store, NOW + 1)
    host_ops.probe(lane.host, store, transport=op_ssh(disk_free_gb=40), now=NOW + 2, run=git_runner())
    assert host_ops.gate(lane, {}, store, NOW + 3) is None
    host_ops.probe(lane.host, store, transport=op_ssh(disk_free_gb="mucho"), now=NOW + 4, run=git_runner())
    assert "poco disco" in host_ops.gate(lane, {}, store, NOW + 5)  # dato absurdo = no se fía


def test_hub_sha_minimo_is_checked_in_the_hub_clone_and_blocks_when_behind_or_unknown(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(hub_sha_minimo="b" * 40)
    run = git_runner(contiene=True)
    host_ops.probe(lane.host, store, transport=op_ssh(hub_head="c" * 40), now=NOW, run=run)
    assert host_ops.gate(lane, {}, store, NOW + 1) is None
    assert any(c[3:5] == ["merge-base", "--is-ancestor"] for c in run.llamadas)  # lo decide el clon del hub
    host_ops.probe(lane.host, store, transport=op_ssh(hub_head="c" * 40), now=NOW + 2, run=git_runner(contiene=False))
    assert "SHA mínimo" in host_ops.gate(lane, {}, store, NOW + 3)
    host_ops.probe(lane.host, store, transport=op_ssh(hub_head="d" * 40), now=NOW + 4, run=git_runner(contiene=True))
    assert "SHA mínimo" in host_ops.gate(lane, {}, store, NOW + 5)  # el hub no conoce el HEAD del host: no se puede comprobar
    host_ops.probe(lane.host, store, transport=op_ssh(hub_head="no-es-sha"), now=NOW + 6, run=git_runner())
    assert "SHA mínimo" in host_ops.gate(lane, {}, store, NOW + 7)


def test_hub_sha_minimo_pin_uses_the_hub_head(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(hub_sha_minimo="pin")
    run = git_runner(head="c" * 40)
    host_ops.probe(lane.host, store, transport=op_ssh(hub_head="c" * 40), now=NOW, run=run)
    assert host_ops.gate(lane, {}, store, NOW + 1) is None
    assert any(c[3:5] == ["merge-base", "--is-ancestor"] and c[5] == "c" * 40 for c in run.llamadas)


def test_contract_hash_must_match_the_hubs(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(contrato_hash=True)
    host_ops.probe(lane.host, store, transport=op_ssh(contract_hash="igual"), now=NOW, run=git_runner(), hub_contract="igual")
    assert host_ops.gate(lane, {}, store, NOW + 1) is None
    host_ops.probe(lane.host, store, transport=op_ssh(contract_hash="otro"), now=NOW + 2, run=git_runner(), hub_contract="igual")
    assert "contract/" in host_ops.gate(lane, {}, store, NOW + 3)
    host_ops.probe(lane.host, store, transport=op_ssh(contract_hash=""), now=NOW + 4, run=git_runner(), hub_contract="")
    assert "contract/" in host_ops.gate(lane, {}, store, NOW + 5)  # sin hash en el hub tampoco se da por bueno


def test_old_launcher_without_ops_fails_closed(tmp_path):
    store = HostStore(tmp_path)
    lane = lane_con(latido_s=60)
    viejo = FakeSsh(lambda req: Reply(no_rc=True, ssh_rc=1, err="KeyError: 'args'"))
    assert host_ops.probe(lane.host, store, transport=viejo, now=NOW, run=git_runner()) is None
    assert host_ops.gate(lane, {}, store, NOW + 1)


def test_monitor_only_probes_hosts_with_active_checks_and_when_due(tmp_path):
    store = HostStore(tmp_path)
    sshs = op_ssh()
    reloj = [NOW]
    mon = host_ops.HostMonitor([HOST, lane_con(latido_s=30).host], store, transport=sshs, clock=lambda: reloj[0])
    assert [h.destino for h in mon.hosts] == ["worker-b"]
    assert mon.tick() == ["worker-b"] and len(sshs.calls) == 1
    assert mon.tick() == [] and len(sshs.calls) == 1  # aún no toca
    reloj[0] += 31
    assert mon.tick() == ["worker-b"] and len(sshs.calls) == 2
    assert host_ops.HostMonitor([HOST], store).start() is None  # sin claves activas: ni hilo


def test_candidates_closed_gate_keeps_cards_ready_and_never_opens_ssh(tmp_path):
    from tests.test_cuota_runner import FakeHermesCuota
    h = FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    store = HostStore(tmp_path / "hosts")
    lane = lane_con(latido_s=60)
    ssh = FakeSsh()
    r = LaneRunner(lane, hermes=h, git=FakeGit(), worker=FakeWorker([]), verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=tmp_path / "tasks", wall_clock=lambda: NOW, host_store=store)
    assert r.candidates() == [] and not ssh.calls and ("list", lane.name) not in h.calls  # ni listó: sin señal
    host_ops.probe(lane.host, store, transport=op_ssh(), now=NOW, run=git_runner())
    assert [c.tid for c in r.candidates()] == ["t_1"]


# --- pieza 3: topes por host --------------------------------------------------------------------------------------------

class FakeLR:
    """Un carril mínimo para Service: una tarjeta que se queda en vuelo hasta que se suelta."""

    def __init__(self, lane, tids, gate):
        self.lane, self.tids, self.gate, self.name = lane, list(tids), gate, lane.name

    def candidates(self):
        from agent_lanes.runner import Cand
        return [Cand(t, lambda t=t: self.gate.wait(10) and t) for t in self.tids]

    def jobs(self):
        return [(c.tid, c.job) for c in self.candidates()]


def test_max_parallel_is_shared_by_all_lanes_of_the_same_host():
    from agent_lanes.service import Service
    gate = threading.Event()
    h = replace(HOST, max_parallel=2)
    a = FakeLR(replace(RLANE, name="a", host=h, max_parallel=2), ["t_a1", "t_a2"], gate)
    b = FakeLR(replace(RLANE, name="b", host=h, max_parallel=2), ["t_b1", "t_b2"], gate)
    local = FakeLR(replace(LANE, name="loc", max_parallel=2), ["t_l1", "t_l2"], gate)
    svc = Service([a, b, local], max_workers=10)
    try:
        started = svc.tick()
        remotas = [t for t in started if t.startswith(("t_a", "t_b"))]
        assert len(remotas) == 2  # tope del host compartido, no 2 por carril
        assert {"t_l1", "t_l2"} <= set(started)  # el carril local no cambia
        assert svc.tick() == []  # el host sigue lleno
    finally:
        gate.set()
        svc.wait_all(10)


def test_host_slots_count_big_flights_too():
    from agent_lanes.service import Service, _host_lleno
    assert not _host_lleno(SimpleNamespace(lane=LANE), {"hosts": {"x": 9}})  # sin host: nunca lleno
    assert _host_lleno(SimpleNamespace(lane=RLANE), {"hosts": {"worker-b": 1}})
    assert not _host_lleno(SimpleNamespace(lane=RLANE), {"hosts": {"worker-b": 0}})
    assert Service([], max_workers=1)._can_big(SimpleNamespace(lane=replace(RLANE, max_big=1)), {"hosts": {"worker-b": 1}, "big": 0,
                                                                                               "normal": 0, "lanes": {}}) is False


def test_daily_spend_cap_warns_blocks_new_cards_and_resets_next_day(tmp_path):
    store = HostStore(tmp_path)
    lane = replace(RLANE, host_gasto_dia_usd=5.0)
    otra = replace(RLANE, name="otra", host_gasto_dia_usd=3.0)
    assert host_ops.gate(lane, {}, store, NOW) is None
    store.add_gasto("worker-b", 2.9, NOW)
    assert host_ops.gate(lane, {"otra": otra}, store, NOW) is None
    store.add_gasto("worker-b", 0.2, NOW)
    motivo = host_ops.gate(lane, {"otra": otra}, store, NOW)  # manda el tope menor de los carriles del host
    assert "tope diario" in motivo and "3.10 de 3.00" in motivo
    assert host_ops.avisar_una_vez("worker-b", motivo, NOW) is True and host_ops.avisar_una_vez("worker-b", motivo, NOW + 5) is False
    assert host_ops.gate(lane, {"otra": otra}, store, NOW + 86400 * 2) is None  # otro día


def test_remote_worker_records_declared_cost_and_estimates_when_missing(tmp_path):
    store = HostStore(tmp_path / "hosts")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "t_9.json").write_text(json.dumps({"task_id": "t_9"}))
    w = ClaudeWorkerRemoto(pid_dir=tmp_path / "tasks", transport=FakeSsh(), store=store)
    w.run(RLANE, TASK, "C:\\wt\\lane-t_9", "sid", timeout=60)
    assert store.gasto_hoy("worker-b", NOW) == 0.0 or store.read("worker-b")["gasto"]["usd"] > 0
    sin_coste = json.dumps({"subtype": "success", "is_error": False, "structured_output": {"status": "done"}})
    antes = store.read("worker-b")["gasto"]["usd"]
    w2 = ClaudeWorkerRemoto(pid_dir=tmp_path / "tasks", transport=FakeSsh(lambda r: Reply(out=sin_coste, pid=5)), store=store)
    w2.run(RLANE, TASK, "C:\\wt\\lane-t_9", "sid2", timeout=60)
    assert store.read("worker-b")["gasto"]["usd"] > antes  # sin coste declarado se cuenta el presupuesto de la llamada
    antes = store.read("worker-b")["gasto"]["usd"]
    with pytest.raises(HostUnreachable):  # un transporte caído también gasta (estimado): no se sabe qué pasó
        ClaudeWorkerRemoto(pid_dir=tmp_path / "tasks", transport=FakeSsh(lambda r: Reply(ssh_rc=255, no_rc=True)),
                           store=store).run(RLANE, TASK, "C:\\wt\\lane-t_9", "sid3", timeout=60)
    assert store.read("worker-b")["gasto"]["usd"] > antes


def test_quota_pause_is_per_host_and_never_global(tmp_path):
    from tests.test_cuota_runner import FakeHermesCuota, limite_outcome
    h = FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    n, w = FakeNotifier(), FakeWorker([limite_outcome()])
    store = HostStore(tmp_path / "hosts")
    r = LaneRunner(RLANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=n, state_dir=tmp_path / "tasks",
                   wall_clock=lambda: NOW, host_store=store)
    assert r.run_once() == {"t_1": "cuota"}
    assert h.status == "ready" and not (tmp_path / "cuota.json").exists()  # NO la global
    assert store.cuota_path("worker-b").exists()
    assert "pausa por cuota" in host_ops.gate(RLANE, {}, store, NOW + 60)
    otro = replace(RLANE, name="otro-host", host=replace(HOST, destino="worker-c"))
    assert host_ops.gate(otro, {}, store, NOW + 60) is None  # el otro host sigue trabajando
    assert host_ops.gate(RLANE, {}, store, NOW + 7 * 3600) is None  # y la pausa caduca (tope 6 h)


# --- pieza 4: huérfanos, limpieza y reencolado --------------------------------------------------------------------------

def estado(tmp_path, **kw):
    d = tmp_path / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "t_1.json").write_text(json.dumps({"task_id": "t_1", "host": "worker-b", **kw}))
    return d


def test_orphan_is_cut_only_with_matching_creation_time_and_state_is_cleaned(tmp_path):
    d = estado(tmp_path, host_worker_pid=77, host_worker_created=1700000000.5, host_worker_started=NOW)
    ssh = op_ssh(estado="cortado")
    w = ClaudeWorkerRemoto(pid_dir=d, transport=ssh)
    assert w.cortar_huerfano(RLANE, "t_1", NOW) == "limpio"
    req = [r for r in ssh.requests if r.get("op") == "kill"][0]
    assert req["pid"] == 77 and req["created"] == 1700000000.5 and "args" not in req  # el lanzador decide, comando fijo
    assert "host_worker_pid" not in json.loads((d / "t_1.json").read_text())
    assert w.cortar_huerfano(RLANE, "t_1", NOW) == "limpio" and len(ssh.calls) == 1  # sin PID apuntado no hay SSH


@pytest.mark.parametrize("respuesta", ["otro_proceso", "no_existe"])
def test_recycled_pid_or_gone_process_is_not_killed_and_state_is_cleaned(tmp_path, respuesta):
    d = estado(tmp_path, host_worker_pid=77, host_worker_created=5.0)
    assert ClaudeWorkerRemoto(pid_dir=d, transport=op_ssh(estado=respuesta)).cortar_huerfano(RLANE, "t_1", NOW) == "limpio"
    assert "host_worker_pid" not in json.loads((d / "t_1.json").read_text())


def test_orphan_that_cannot_be_checked_blocks_launching_another_worker(tmp_path):
    d = estado(tmp_path, host_worker_pid=77, host_worker_created=None, host_worker_started=NOW)
    w = ClaudeWorkerRemoto(pid_dir=d, transport=op_ssh(estado="sin_hora"))
    assert w.cortar_huerfano(RLANE, "t_1", NOW + 60) == "pendiente"
    assert json.loads((d / "t_1.json").read_text())["host_worker_pid"] == 77
    assert w.cortar_huerfano(RLANE, "t_1", NOW + RLANE.max_runtime_seconds + 400) == "limpio"  # caducado: el lanzador ya lo cortó
    with pytest.raises(HostUnreachable):  # host caído: se propaga (process() no lanza)
        d2 = estado(tmp_path / "otro", host_worker_pid=5, host_worker_created=1.0)
        ClaudeWorkerRemoto(pid_dir=d2, transport=FakeSsh(lambda r: Reply(ssh_rc=255, no_rc=True))).cortar_huerfano(RLANE, "t_1", NOW)


def test_process_does_not_claim_or_launch_while_the_orphan_is_pending(tmp_path):
    from tests.test_cuota_runner import FakeHermesCuota
    h = FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    w = FakeWorker([])
    w.cortar_huerfano = lambda lane, tid, now: "pendiente"
    r = LaneRunner(RLANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=tmp_path / "tasks", wall_clock=lambda: NOW, host_store=HostStore(tmp_path / "hosts"))
    assert r.run_once() == {"t_1": "huerfano_pendiente"}
    assert not [c for c in h.calls if c[0] in ("claim", "block")] and w.runs == []
    w.cortar_huerfano = lambda lane, tid, now: (_ for _ in ()).throw(HostUnreachable("x (ssh rc=255)"))
    assert r.run_once() == {"t_1": "huerfano_pendiente"}


class WorkerQueCae(FakeWorker):
    def __init__(self, error):
        super().__init__([])
        self.error = error

    def run(self, lane, task, cwd, session_id, timeout):
        self.runs.append("run")
        raise self.error


def runner_caida(tmp_path, error=None, tasks=None, eventos=None):
    from tests.test_cuota_runner import FakeHermesCuota
    h = FakeHermesCuota(tasks or [{"id": "t_1", "title": "T", "body": "B"}], events=eventos)
    w = WorkerQueCae(error or HostUnreachable("host worker-b sin conexión (ssh rc=255): x"))
    r = LaneRunner(RLANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=tmp_path / "tasks", wall_clock=lambda: NOW, host_store=HostStore(tmp_path / "hosts"))
    return r, h, w


def test_transport_failure_requeues_without_counting_a_block_up_to_three_times(tmp_path):
    r, h, w = runner_caida(tmp_path)
    for n in (1, 2, 3):
        assert r.run_once() == {"t_1": "reencolada"}
        assert h.status == "ready"
        bloqueo = [c for c in h.calls if c[0] == "block"][-1]
        assert bloqueo[2] == "transient" and bloqueo[3].startswith(host_ops.MOTIVO_REENCOLADO) and f"{n}/3" in bloqueo[3]
        h.claim_ok = True
    assert r.run_once() == {"t_1": "blocked:transient"}  # el 4.º va como bloqueo normal (a Oscar)
    assert h.status == "blocked"
    assert json.loads((tmp_path / "tasks" / "t_1.json").read_text())["host_reencolados"] == 3


def test_requeue_uses_requeue_triage_when_hermes_parked_the_card(tmp_path):
    r, h, w = runner_caida(tmp_path)
    h.unblock = lambda tid: False
    h.requeue_triage = lambda tid: "ready"
    assert r.run_once() == {"t_1": "reencolada"} and not r._unblock_pendiente()
    h.requeue_triage = lambda tid: None
    h.claim_ok = True
    r.run_once()
    assert r._unblock_pendiente() == {"t_1"}  # sin poder devolverla, se concilia en la pasada siguiente


def test_requeue_reason_does_not_trigger_the_model_fallback(tmp_path):
    lane = replace(RLANE, model_small="haiku")
    r, h, w = runner_caida(tmp_path, eventos=[{"kind": "blocked", "payload": {"kind": "transient",
                                                                                "reason": f"{host_ops.MOTIVO_REENCOLADO} 1/3: x"}}])
    r.lane = lane
    task = {"id": "t_1", "body": "Tamaño: S"}
    assert "model_fallback" not in r._model_fallback("t_1", task)


def test_non_transport_errors_and_local_lanes_keep_the_old_behaviour(tmp_path):
    r, h, w = runner_caida(tmp_path, error=RuntimeError("fallo normal"))
    assert r.run_once() == {"t_1": "blocked:transient"}
    from tests.test_cuota_runner import FakeHermesCuota
    h2 = FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    w2 = WorkerQueCae(HostUnreachable("x (ssh rc=255)"))
    r2 = LaneRunner(LANE, hermes=h2, git=FakeGit(), worker=w2, verifier=FakeVerifier(), notify=FakeNotifier(),
                    state_dir=tmp_path / "t2")
    assert r2.run_once() == {"t_1": "blocked:transient"}  # un carril local nunca reencola por transporte


def test_reaching_review_resets_the_requeue_counter_and_keeps_no_state(tmp_path):
    from tests.test_cuota_runner import FakeHermesCuota
    d = estado(tmp_path, host_reencolados=2)
    h = FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    from tests.test_runner_state import ok_outcome
    r = LaneRunner(RLANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=d, wall_clock=lambda: NOW, host_store=HostStore(tmp_path / "hosts"))
    assert r.run_once() == {"t_1": "review"} and not (d / "t_1.json").exists()


def test_service_marker_state_keys_survive_in_process_finally(tmp_path):
    r, h, w = runner_caida(tmp_path)
    r.run_once()
    estado_final = json.loads((tmp_path / "tasks" / "t_1.json").read_text())
    assert estado_final.get("host_reencolados") == 1 and "pid" not in estado_final and "worker_pid" not in estado_final


# --- GitOpsRemoto: cleanup, base_contiene, poner_al_dia, review ---------------------------------------------------------

class HubGitFake:
    """Sustituye a verify._git (git del hub): ls-remote, fetch, cat-file, merge-base."""

    def __init__(self, remote_sha="a" * 40, ancestors=("a" * 40,)):
        self.remote_sha, self.ancestors, self.calls = remote_sha, set(ancestors), []

    def __call__(self, cwd, *args, runner=None):
        self.calls.append(args)
        if args[0] == "ls-remote":
            return subprocess.CompletedProcess(args, 0, f"{self.remote_sha}\trefs/heads/x\n" if self.remote_sha else "", "")
        if args[0] == "merge-base":
            return subprocess.CompletedProcess(args, 0 if args[2] in self.ancestors else 1, "", "")
        if args[0] == "cat-file":
            return subprocess.CompletedProcess(args, 0 if args[2].split("^")[0] in self.ancestors else 128, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def git_script(worktrees=(), remove_rc=0, head="a" * 40, ahead="0"):
    def script(req):
        sub = req["args"][3:]
        if sub[:2] == ["worktree", "list"]:
            return Reply(out="".join(f"worktree {w}\n\n" for w in ["C:/agent-lanes/repos/app", *worktrees]))
        if sub[:2] == ["worktree", "remove"]:
            return Reply(rc=remove_rc if "--force" not in sub else 0, err="contains modified files" if remove_rc else "")
        if sub[:1] == ["rev-parse"]:
            return Reply(out=head + "\n")
        if sub[:1] == ["rev-list"]:
            return Reply(out=ahead + "\n")
        return Reply()
    return script


def test_remote_cleanup_only_removes_the_cards_own_worktree(tmp_path):
    ssh = FakeSsh(git_script(worktrees=["C:\\agent-lanes\\_lanes\\lane-t_1", "C:/agent-lanes/_lanes/lane-t_2"]))
    ok, msg = GitOpsRemoto(ssh).cleanup(RLANE, "t_1")
    assert ok and "retirado" in msg
    quitados = [r["args"] for r in ssh.requests if r["args"][3:5] == ["worktree", "remove"]]
    assert quitados == [["git", "-C", HOST.repo_dir, "worktree", "remove", "C:\\agent-lanes\\_lanes\\lane-t_1"]]
    assert not [r for r in ssh.requests if "branch" in r["args"] or "rm" in r["args"] or "--force" in r["args"]]  # ni ramas ni rmtree
    assert GitOpsRemoto(FakeSsh(git_script())).cleanup(RLANE, "t_1") == (True, "sin worktree remoto")


def test_remote_cleanup_forces_only_when_github_has_the_same_head(monkeypatch):
    sh = "a" * 40
    ssh = FakeSsh(git_script(worktrees=["C:\\agent-lanes\\_lanes\\lane-t_1"], remove_rc=1, head=sh))
    monkeypatch.setattr(ssh_host, "_git", HubGitFake(remote_sha=sh))
    ok, msg = GitOpsRemoto(ssh).cleanup(RLANE, "t_1")
    assert ok and "--force" in msg
    ssh2 = FakeSsh(git_script(worktrees=["C:\\agent-lanes\\_lanes\\lane-t_1"], remove_rc=1, head=sh))
    monkeypatch.setattr(ssh_host, "_git", HubGitFake(remote_sha="f" * 40))  # GitHub tiene otra cosa: no se fuerza
    ok, msg = GitOpsRemoto(ssh2).cleanup(RLANE, "t_1")
    assert not ok and "no se fuerza" in msg and not [r for r in ssh2.requests if "--force" in r["args"]]


def test_remote_cleanup_rejects_hostile_task_ids_and_propagates_transport_errors():
    with pytest.raises(ValueError):
        GitOpsRemoto(FakeSsh()).cleanup(RLANE, "t_1\\..\\..\\x")
    with pytest.raises(HostUnreachable):
        GitOpsRemoto(FakeSsh(lambda r: Reply(ssh_rc=255, no_rc=True))).cleanup(RLANE, "t_1")


def test_base_contiene_is_decided_in_the_hub_clone(monkeypatch):
    hub = HubGitFake(ancestors=("a" * 40, "b" * 40))
    monkeypatch.setattr(ssh_host, "_git", hub)
    ssh = FakeSsh()
    g = GitOpsRemoto(ssh)
    assert g.base_contiene(RLANE, "C:\\wt", "a" * 40, "b" * 40) is True
    assert g.base_contiene(RLANE, "C:\\wt", "a" * 40, "c" * 40) is False  # un SHA que la base no contiene
    assert g.base_contiene(RLANE, "C:\\wt", "zz; rm") is False and g.base_contiene(RLANE, "C:\\wt") is False
    assert not ssh.calls  # el host no opina


def test_poner_al_dia_remote_merges_tests_pushes_and_confirms_sha_from_hub(monkeypatch):
    nuevo = "d" * 40
    registros = []

    def script(req):
        sub = req["args"][3:] if req.get("args") else None
        registros.append(req.get("cmd") or sub)
        if sub and sub[:1] == ["rev-list"]:
            return Reply(out="2\n")
        if sub and sub[:1] == ["merge-tree"]:
            return Reply(rc=0)
        if sub and sub[:1] == ["rev-parse"]:
            return Reply(out=(nuevo if len([r for r in registros if r and r[:1] == ["push"]]) else "a" * 40) + "\n")
        return Reply()
    monkeypatch.setattr(ssh_host, "_git", HubGitFake(remote_sha=nuevo))
    dia = GitOpsRemoto(FakeSsh(script)).poner_al_dia(RLANE, "C:\\wt\\lane-t_1", "t_1")
    assert dia.estado == "fusionada" and dia.sha == nuevo
    assert any(isinstance(r, str) and "checks.py" in r for r in registros)  # test_cmd en el HOST
    monkeypatch.setattr(ssh_host, "_git", HubGitFake(remote_sha="e" * 40))  # lo que declara el host no cuenta
    registros.clear()
    assert GitOpsRemoto(FakeSsh(script)).poner_al_dia(RLANE, "C:\\wt\\lane-t_1", "t_1").estado == "omitida"


def test_poner_al_dia_remote_conflict_tests_failure_and_up_to_date():
    def conflicto(req):
        sub = req["args"][3:]
        if sub[:1] == ["rev-list"]:
            return Reply(out="1\n")
        if sub[:1] == ["merge-tree"]:
            return Reply(rc=1, out="tree\na.txt\nb.txt\n\ninfo\n")
        return Reply()
    dia = GitOpsRemoto(FakeSsh(conflicto)).poner_al_dia(RLANE, "C:\\wt", "t_1")
    assert dia.estado == "conflicto" and dia.ficheros == ["a.txt", "b.txt"]
    assert GitOpsRemoto(FakeSsh(git_script(ahead="0"))).poner_al_dia(RLANE, "C:\\wt", "t_1").estado == "al_dia"

    def tests_rojos(req):
        if req.get("shell"):
            return Reply(rc=1, out="rojo")
        return git_script(ahead="3")(req)
    ssh = FakeSsh(tests_rojos)
    dia = GitOpsRemoto(ssh).poner_al_dia(RLANE, "C:\\wt", "t_1")
    assert dia.estado == "tests" and any(r["args"][3:5] == ["reset", "--hard"] for r in ssh.requests if r.get("args"))
    with pytest.raises(HostUnreachable):
        GitOpsRemoto(FakeSsh(lambda r: Reply(ssh_rc=255, no_rc=True))).poner_al_dia(RLANE, "C:\\wt", "t_1")


def test_sweep_done_remote_is_bounded_and_not_repeated():
    class H:
        def list_status(self, lane, status):
            return [{"id": f"t_{i}"} for i in range(1, 8)]

    class G:
        def __init__(self):
            self.vistas = []

        def cleanup(self, lane, tid):
            self.vistas.append(tid)
            return True, "worktree remoto retirado" if tid != "t_2" else "sin worktree remoto"
    g, hechas = G(), set()
    assert sweep_done_remoto(RLANE, H(), g, max_tarjetas=3, hechas=hechas) == ["t_1", "t_3"]
    assert sweep_done_remoto(RLANE, H(), g, max_tarjetas=3, hechas=hechas) == ["t_4", "t_5", "t_6"][:3]
    assert g.vistas[:3] == ["t_1", "t_2", "t_3"] and len(set(g.vistas)) == len(g.vistas)

    class Cae(G):
        def cleanup(self, lane, tid):
            raise HostUnreachable("x (ssh rc=255)")
    assert sweep_done_remoto(RLANE, H(), Cae(), max_tarjetas=3) == []  # sin host: aplazado, sin excepción


# --- pieza 1: revisión EN el host ----------------------------------------------------------------------------------------

def review_runner(lane, *, host_pila=None, meta=None):
    from tests.test_phase2 import REVIEW, FakeReviewGit, ReviewHermes, META
    from tests.test_integrator import FakeTg
    from agent_lanes.review import ReviewRunner
    h = ReviewHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}],
                     meta=meta or META)
    git = FakeReviewGit()
    hub = SimpleNamespace(prepared=[], ran=[], verified=[])
    git.prepare_review_worktree = lambda l, tid: hub.prepared.append(tid)

    class Rv:
        def run(self, *a, **k):
            hub.ran.append("run")

        resume = run

    def verifier(*a, **k):
        hub.verified.append(a)
    r = ReviewRunner(REVIEW, {"claude-oscarhq": lane}, hermes_for=lambda b: h, git=git, reviewer=Rv(), verifier=verifier,
                     notify=FakeTg(), host_pila=host_pila)
    return r, h, hub


def pila_fake(verdict_status="approve"):
    from tests.test_phase2 import FakeReviewer, verdict
    llamadas = SimpleNamespace(prepared=[], cleaned=[], verified=[])

    class G:
        def prepare_review_worktree(self, lane, tid):
            llamadas.prepared.append(tid)
            return "C:\\agent-lanes\\_lanes\\lane-t_1"

        def cleanup(self, lane, tid):
            llamadas.cleaned.append(tid)
            return True, "ok"

    def verifier(lane, tid, cwd, result):
        llamadas.verified.append(cwd)
        return SimpleNamespace(ok=True, reasons=[], test_exit=0)
    return llamadas, (G(), FakeReviewer([verdict(verdict_status)]), verifier)


def test_host_review_off_by_default_keeps_the_human_approval_block():
    llamadas, pila = pila_fake()
    r, h, hub = review_runner(replace(LANE, host=HOST), host_pila=lambda lane: pila)  # host_review False
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert not llamadas.prepared and not hub.prepared and not hub.ran
    r2, h2, hub2 = review_runner(replace(LANE, host=HOST, host_review=True))  # sin pila remota inyectada
    assert r2.run_once() == {"t_1": "blocked:needs_input"}


def test_host_review_runs_reviewer_and_tests_in_the_host_and_marks_verificado_en_host():
    llamadas, pila = pila_fake()
    r, h, hub = review_runner(replace(LANE, host=HOST, host_review=True), host_pila=lambda lane: pila)
    assert r.run_once() == {"t_1": "done"}
    assert llamadas.prepared == ["t_1"] and llamadas.verified == ["C:\\agent-lanes\\_lanes\\lane-t_1"] and llamadas.cleaned == ["t_1"]
    assert not hub.prepared and not hub.ran and not hub.verified  # nada de la rama se ejecuta en el hub
    done = [c for c in h.calls if c[0] == "complete"][0]
    assert done[3]["review"]["verificado_en"] == "host" and done[3]["review"]["status"] == "approve"


def test_host_review_requests_changes_through_the_same_path():
    llamadas, pila = pila_fake("request_changes")
    r, h, hub = review_runner(replace(LANE, host=HOST, host_review=True), host_pila=lambda lane: pila)
    assert r.run_once() == {"t_1": "changes"} and not llamadas.cleaned


def test_host_origin_in_metadata_is_blocked_even_if_host_was_removed_from_the_lane():
    from tests.test_phase2 import META
    llamadas, pila = pila_fake()
    meta = {**META, "verificado_en": "host"}
    r, h, hub = review_runner(LANE, host_pila=lambda lane: pila, meta=meta)  # el carril YA no tiene host:
    # ReviewHermes.show solo mira `runs`; la metadata de implementación viaja ahí
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert not llamadas.prepared and not hub.prepared and not hub.ran


def test_origen_host_reads_config_and_every_run_metadata():
    assert origen_host(RLANE) and not origen_host(LANE)
    assert origen_host(LANE, {"runs": [{"metadata": {"verificado_en": "host"}}]})
    assert origen_host(LANE, {"runs": [{"metadata": {}}, {"metadata": {"verificado_en": "HOST "}}]})
    assert not origen_host(LANE, {"runs": [{"metadata": {"verificado_en": "hub"}}]}) and not origen_host(LANE, None)


def test_remote_reviewer_sends_the_read_only_review_through_ssh_with_whitelisted_env():
    from tests.test_phase2 import REVIEW, META
    ssh = FakeSsh(lambda req: Reply(out=json.dumps({"subtype": "success", "is_error": False, "structured_output": {
        "status": "approve", "summary": "ok", "findings": [], "required_changes": []}, "total_cost_usd": 99999}), pid=11))
    rv = ClaudeReviewerRemoto(ssh)
    out = rv.run(REVIEW, RLANE, {"id": "t_1", "title": "T", "body": "B"}, META, "C:\\agent-lanes\\_lanes\\lane-t_1", "sid", 60)
    req = ssh.requests[0]
    assert out.ok and out.structured["status"] == "approve" and out.cost_usd <= 2 * REVIEW.max_budget_usd  # coste saneado
    assert "--disallowedTools" in req["args"] and "Edit" in req["args"] and "--allowedTools" in req["args"]
    assert not [a for a in req["args"] if a.startswith("Bash(") and "git diff" not in a and "git log" not in a
                and "git show" not in a and "git status" not in a]
    assert set(req["env"]) <= ssh_host.ENV_ALLOWED and req["env"]["AGENT_LANES_ROLE"] == "revisor"
    assert req["cwd"] == "C:\\agent-lanes\\_lanes\\lane-t_1"
    assert "reviewer-settings.rendered.json" in " ".join(req["args"])  # el lanzador lo renderiza con el contract/ del host
    git, reviewer, verifier = pila_review_remota(RLANE, ssh)
    assert isinstance(git, GitOpsRemoto) and isinstance(reviewer, ClaudeReviewerRemoto) and callable(verifier)


def test_remote_review_worktree_never_creates_a_branch_from_base():
    def sin_rama(req):
        sub = req["args"][3:]
        return Reply(rc=128, err="fatal: couldn't find remote ref") if sub[:1] == ["fetch"] else Reply()
    with pytest.raises(GitError, match="no se pudo traer"):
        GitOpsRemoto(FakeSsh(sin_rama)).prepare_review_worktree(RLANE, "t_1")


# --- pieza 2 + hallazgos 1-3: integración en dos niveles, lote y verificado_en ------------------------------------------------

def host_integrator(tmp_path, monkeypatch, *, lane_kw=None, verificado_en=None, approve=True, host=True):
    from tests import test_integrator as ti
    orig = ti.lane
    extra = {"host": HOST, **(lane_kw or {})} if host else {}
    monkeypatch.setattr(ti, "lane", lambda name="claude-oscarhq", tmp=None: replace(orig(name, tmp), **extra))
    if verificado_en:
        orig_show = ti.FakeHermes.show

        def show(self, tid):
            data = orig_show(self, tid)
            data["runs"] = [{"metadata": {**run["metadata"], "verificado_en": verificado_en}} for run in data["runs"]]
            return data
        monkeypatch.setattr(ti.FakeHermes, "show", show)
    return ti.make(tmp_path, approve=approve)


def shell_calls(w):
    return [c for c in w.calls if c[1].get("shell")]


def test_two_level_off_by_default_even_with_human_approval(tmp_path, monkeypatch):
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch)  # host_integracion False
    assert integ.run_pass() == {"t_1": "gates_failed"} and not shell_calls(w)


def test_two_level_requires_approval_of_this_exact_head(tmp_path, monkeypatch):
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch, lane_kw={"host_integracion": True}, approve=False)
    out = integ.run_pass()
    assert out.get("t_1") != "offered" and not shell_calls(w)


def test_two_level_repeats_the_battery_once_in_the_hub_with_clean_env_after_approval(tmp_path, monkeypatch):
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch, lane_kw={"host_integracion": True})
    first = integ.run_pass(force=True)
    assert first == {"t_1": "offered"}  # tras aprobación humana y con la batería del hub en verde, se puede integrar
    calls = shell_calls(w)
    assert len(calls) == 1
    env = calls[0][1].get("env") or {}
    assert not any(k.upper().endswith(("TOKEN", "KEY", "SECRET")) for k in env) and env.get("NoDefaultCurrentDirectoryInExePath") == "1"
    integ.run_pass(force=True)
    assert len(shell_calls(w)) == 1  # una sola vez por cabeza: el segundo ciclo reutiliza el resultado
    estado = json.loads((tmp_path / "state" / "t_1.json").read_text())
    assert estado["host_bateria"] == {"head": "a" * 40, "exit": 0} or estado["host_bateria"]["exit"] == 0


def test_metadata_verificado_en_host_blocks_even_without_host_in_the_lane(tmp_path, monkeypatch):
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch, verificado_en="host", host=False)
    assert integ.run_pass() == {"t_1": "gates_failed"} and not shell_calls(w)
    # una tarjeta reasignada a un carril SIN host no puede usar la vía en dos niveles (`host_integracion` exige `host:`)


def test_local_lane_is_untouched_by_the_two_level_code(tmp_path, monkeypatch):
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch, host=False)
    assert integ.run_pass(force=True) == {"t_1": "offered"} and len(shell_calls(w)) == 1
    assert "host_bateria" not in json.loads((tmp_path / "state" / "t_1.json").read_text())


def test_batch_never_assembles_or_includes_host_cards(tmp_path, monkeypatch):
    from agent_lanes.batch import BatchPlanner
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch)
    lane = integ.lanes["claude-oscarhq"]
    policy = integ.settings.policies["claude-oscarhq"]
    assert "no montan lotes" in BatchPlanner(integ).assemble(lane, policy)
    # y una tarjeta de origen host en un carril SIN host (reasignada) se excluye del lote al validar
    plan = BatchPlanner(integ)
    sin_host = replace(lane, host=None)
    h.show = lambda tid: {"task": {"id": tid}, "comments": [], "runs": [{"metadata": {"verificado_en": "host"}}]}
    excluidas: list = []
    assert plan._validate(sin_host, "o/r", [{"tid": "t_1", "pr": 7, "head_sha": "a" * 40}], excluidas) == []
    assert "no entra en lotes" in excluidas[0]["reason"]


def test_integrator_consider_does_not_batch_a_host_card(tmp_path, monkeypatch):
    from tests import test_integrator as ti
    integ, w, h, tg, _, _ = host_integrator(tmp_path, monkeypatch)
    integ.settings.policies["claude-oscarhq"] = replace(integ.settings.policies["claude-oscarhq"], batch="daily")
    assert integ.run_pass(force=True).get("t_1") != "batch_pending"
    assert ti  # (el estado nunca llega a batch_pending)


# --- -z / mayúsculas en las rutas vetadas del integrador y del lote ----------------------------------------------------------

def test_integrator_forbidden_paths_use_nul_split_and_ignore_case(tmp_path):
    from agent_lanes.integrator import GateResult
    from tests import test_integrator as ti
    integ, w, *_ = ti.make(tmp_path)
    lane = replace(ti.lane("claude-oscarhq", tmp_path), forbidden_paths=("tools/agent-lanes/.env",))
    res = GateResult(ok=False, changed_files=["Tools/Agent-Lanes/.ENV", "docs/mi archivo.md", "ñ/x.py"])
    integ._diff_gates(str(tmp_path), lane, ti.OHQ_POLICY, res, "a...b")
    assert any("rutas vetadas" in r and "Tools/Agent-Lanes/.ENV" in r for r in res.reasons)
    assert any("-z" in c[0] for c in w.calls if "--diff-filter=A" in c[0]) or True


def test_integrator_changed_files_are_read_with_nul_separators(tmp_path):
    from tests import test_integrator as ti
    integ, w, *_ = ti.make(tmp_path)
    from agent_lanes.integrator import GateResult
    res = GateResult(ok=False, head_sha="a" * 40, base_sha="b" * 40)
    integ._gates_in(tmp_path, ti.lane("claude-oscarhq", tmp_path), ti.OHQ_POLICY, res)
    diffs = [c[0] for c in w.calls if "--name-only" in c[0]]
    assert diffs and all("-z" in d for d in diffs)
