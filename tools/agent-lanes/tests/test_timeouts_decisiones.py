"""t_cce5da81 (23-10): timeouts de hermes/gh como fallo transitorio con reintento, y decisiones de Oscar que no se
pierden (cola persistente que se aplica en la pasada siguiente). Sin red, sin hermes, sin gh."""
from __future__ import annotations

import json
import subprocess

import pytest

from agent_lanes import proc
from agent_lanes.hermes import HermesCLI, HermesError
from tests.test_decisions import FakeGH, DeskHermes, _desk, _done_markup, _press


@pytest.fixture(autouse=True)
def _limpio():
    proc.take_transient()
    yield
    proc.take_transient()


def _cli(script, backoff=(5.0, 20.0)):
    """HermesCLI con un runner guionizado: cada elemento es 'timeout' o (rc, stdout, stderr)."""
    calls, sleeps = [], []

    def run(cmd, **kw):
        calls.append(cmd)
        step = script[min(len(calls) - 1, len(script) - 1)]
        if step == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, *step)

    return HermesCLI("default", exe="hermes", runner=run, sleep=sleeps.append, timeout_backoff=backoff), calls, sleeps


# --- 1. HermesCLI ---------------------------------------------------------------------------------------------------

def test_hermes_lectura_con_timeout_se_reintenta_con_espera_progresiva():
    h, calls, sleeps = _cli(["timeout", "timeout", (0, "[]", "")])
    assert h.list_status("claude-hub", "ready") == []
    assert len(calls) == 3 and sleeps == [5.0, 20.0]
    assert proc.take_transient() is None  # salió bien: nada que anotar


def test_hermes_timeout_agotado_no_lanza_y_anota_transitorio():
    h, calls, sleeps = _cli(["timeout"])
    with pytest.raises(HermesError, match="timeout"):  # un HermesError normal, no TimeoutExpired
        h.show("t_1")
    assert len(calls) == 3 and sleeps == [5.0, 20.0]
    assert "sin respuesta" in (proc.take_transient() or "")


def test_hermes_escritura_con_timeout_no_se_repite():
    """Un timeout en una escritura es ambiguo (pudo aplicarse): no se repite, se devuelve fallo."""
    h, calls, sleeps = _cli(["timeout", (0, "", "")])
    assert h.comment("t_1", "hola") is False
    assert len(calls) == 1 and sleeps == []
    assert proc.take_transient()


def test_hermes_timeout_configurable_en_lanes_yaml():
    proc.configure_cli(timeout=7, backoff=(1.0,))
    try:
        seen = []

        def run(cmd, **kw):
            seen.append(kw["timeout"])
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        h = HermesCLI("default", exe="hermes", runner=run, sleep=lambda s: None)
        assert h.heartbeat("t_1") is False
        assert seen == [7, 7]  # 1 intento + 1 reintento (backoff de 1 espera)
    finally:
        proc.configure_cli()


# --- 1b. gh -----------------------------------------------------------------------------------------------------------

def test_run_retrying_gh_lectura_y_escritura():
    calls, sleeps = [], []

    def run(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, 120)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    cp = proc.run_retrying(["gh", "pr", "list"], runner=run, sleep=sleeps.append, backoff=(5, 20),
                           retry_timeout=proc.gh_is_read(["pr", "list"]))
    assert cp.returncode == 0 and len(calls) == 2 and sleeps == [5]
    assert not proc.gh_is_read(["pr", "create"]) and not proc.gh_is_read(["pr", "merge", "1"])
    assert proc.gh_is_read(["api", "repos/x/y/deployments"]) and not proc.gh_is_read(["api", "-X", "POST", "x"])


class SlowGH(FakeGH):
    """gh que no responde mientras `down`; después, el FakeGH de siempre."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.down = True

    def __call__(self, args, **kw):
        if self.down:
            self.calls.append(args)
            raise subprocess.TimeoutExpired(args, kw.get("timeout"))
        return super().__call__(args, **kw)


def test_gh_de_decisiones_reintenta_la_lectura_y_no_la_escritura(tmp_path):
    gh = SlowGH()
    desk, tg, h = _desk(tmp_path, gh=gh)
    desk._sleep = lambda s: None
    _press(desk, _done_markup(desk), 0)
    kinds = [c[2] for c in gh.calls]
    assert kinds == ["list", "list", "list", "create"]  # pr list: 1 + 2 reintentos; pr create: sin repetir


# --- 2. cola de decisiones ------------------------------------------------------------------------------------------

def _queued_desk(tmp_path):
    gh = SlowGH()
    desk, tg, h = _desk(tmp_path, gh=gh)
    desk._sleep = lambda s: None
    desk.pending.MIN_GAP_SECONDS = 0
    markup = _done_markup(desk)
    _press(desk, markup, 0)
    return desk, tg, h, gh, markup


def test_decision_con_timeout_va_a_la_cola_sin_devolver_botones(tmp_path):
    desk, tg, h, gh, _ = _queued_desk(tmp_path)
    queue = json.loads((tmp_path / "decisiones_pendientes.json").read_text(encoding="utf-8"))
    (token, entry), = queue.items()
    assert entry["kind"] == "act" and entry["rec"]["task_id"] == "t_1" and entry["attempts"] == 0
    assert "⏳" in tg.edits[-1]["text"] and "reintento solo" in tg.edits[-1]["text"]
    assert tg.markups == []  # los botones no vuelven mientras está en cola (no se aplica dos veces)
    assert h.calls == []


def test_decision_en_cola_se_aplica_en_la_pasada_siguiente(tmp_path):
    desk, tg, h, gh, _ = _queued_desk(tmp_path)
    gh.down = False  # GitHub vuelve
    assert desk.retry_pending() == 1
    assert [c[0] for c in h.calls] == ["comment"] and h.calls[0][2].startswith("APROBADO-OSCAR")
    assert "PR #12" in tg.edits[-1]["text"]
    assert desk.pending.all() == {}
    assert desk.retry_pending() == 0  # nada más que hacer


def test_cola_sobrevive_a_un_reinicio_del_runner(tmp_path):
    desk, tg, h, gh, _ = _queued_desk(tmp_path)
    desk2, tg2, h2 = _desk(tmp_path, gh=FakeGH())  # runner nuevo, mismo .state
    desk2.pending.MIN_GAP_SECONDS = 0
    assert desk2.retry_pending() == 1
    assert h2.calls and h2.calls[0][2].startswith("APROBADO-OSCAR")


def test_sigue_transitorio_se_reencola_contando_reintentos(tmp_path):
    desk, tg, h, gh, _ = _queued_desk(tmp_path)
    desk.retry_pending()
    (entry,) = desk.pending.all().values()
    assert entry["attempts"] == 1 and "reintentos: 1" in tg.edits[-1]["text"]


def test_deja_de_ser_transitorio_devuelve_botones_y_dice_cuantos_reintentos(tmp_path):
    desk, tg, h, gh, _ = _queued_desk(tmp_path)
    desk.retry_pending()  # 1º reintento: sigue sin responder
    gh.down, gh.create_rc = False, 1  # ahora falla de verdad (no transitorio)
    desk.retry_pending()  # 2º reintento
    assert desk.pending.all() == {}
    texts = [e["text"] for e in tg.edits]
    assert any("tras 2 reintentos automáticos" in t for t in texts)
    assert tg.markups and tg.markups[-1]  # botones de vuelta para repetir


def test_agotados_los_reintentos_devuelve_botones(tmp_path):
    desk, tg, h, gh, _ = _queued_desk(tmp_path)
    desk.pending.MAX_RETRIES = 2
    for _ in range(3):
        desk.retry_pending()
    assert desk.pending.all() == {}
    assert any("tras 2 reintentos" in e["text"] for e in tg.edits) and tg.markups


def test_fallo_no_transitorio_no_se_encola(tmp_path):
    desk, tg, h = _desk(tmp_path, gh=FakeGH(create_rc=1), hermes=DeskHermes())
    _press(desk, _done_markup(desk), 0)
    assert desk.pending.all() == {} and tg.markups  # como siempre: botones de vuelta al momento


def test_lanes_yaml_trae_el_timeout_configurable():
    proc.configure_cli()
    cfg = proc.cli_settings()
    assert cfg["timeout"] == 120 and cfg["backoff"] == (5.0, 20.0)


def test_commits_ahead_cuenta_sobre_la_base():
    from agent_lanes.config import Lane
    from agent_lanes.git_ops import GitOps
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "0\n" if "rev-list" in cmd else "", "")

    lane = Lane(name="claude-hub", board="default", repo="C:/hub", base="main")
    assert GitOps(runner=run).commits_ahead(lane, "t_1") == 0
    assert calls[-1][-1] == "origin/main..origin/lane/t_1"
    assert GitOps(runner=lambda c, **k: subprocess.CompletedProcess(c, 1, "", "x")).commits_ahead(lane, "t_1") is None
