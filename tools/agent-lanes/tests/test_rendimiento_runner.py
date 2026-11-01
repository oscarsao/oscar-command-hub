"""t_4d64e8d5: caché de `hermes kanban list` por pasada, contador de procesos e intervalo configurable."""
import subprocess

from agent_lanes import proc
from agent_lanes.config import load_runner_settings
from agent_lanes.hermes import HermesCLI


class Fake:
    def __init__(self, out="[]"):
        self.calls, self.out = [], out

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[4:])
        return subprocess.CompletedProcess(cmd, 0, self.out, "")


def cli(fake, ttl=60.0):
    return HermesCLI("default", exe="hermes", runner=fake, list_cache=ttl)


def test_misma_lectura_se_sirve_de_cache():
    f = Fake('[{"id": "t_1"}]')
    h = cli(f)
    assert h.list_status("claude-hub", "ready") == h.list_status("claude-hub", "ready") == [{"id": "t_1"}]
    assert len(f.calls) == 1


def test_cada_lector_recibe_su_copia():
    h = cli(Fake('[{"id": "t_1"}]'))
    h.list_status("a", "ready")[0]["id"] = "roto"
    assert h.list_status("a", "ready") == [{"id": "t_1"}]


def test_lecturas_distintas_no_se_mezclan():
    f = Fake()
    h = cli(f)
    h.list_status("a", "ready")
    h.list_status("a", "running")
    h.list_status("b", "ready")
    h.list_status_all("ready")
    assert len(f.calls) == 4


def test_una_escritura_invalida_la_cache():
    f = Fake()
    h = cli(f)
    h.list_status("a", "ready")
    h.comment("t_1", "hola")
    h.list_status("a", "ready")
    assert [c[0] for c in f.calls] == ["list", "comment", "list"]


def test_show_no_invalida():
    f = Fake("{}")
    h = cli(f)
    h.list_status("a", "ready")
    h.show("t_1")
    h.list_status("a", "ready")
    assert [c[0] for c in f.calls] == ["list", "show"]


def test_caduca_con_el_ttl(monkeypatch):
    f, t = Fake(), [100.0]
    monkeypatch.setattr("agent_lanes.hermes.time.monotonic", lambda: t[0])
    h = cli(f, ttl=20)
    h.list_status("a", "ready")
    t[0] += 19
    h.list_status("a", "ready")
    t[0] += 2
    h.list_status("a", "ready")
    assert len(f.calls) == 2


def test_ttl_cero_desactiva():
    f = Fake()
    h = cli(f, ttl=0)
    h.list_status("a", "ready")
    h.list_status("a", "ready")
    assert len(f.calls) == 2


def test_un_fallo_no_se_cachea():
    class Mal(Fake):
        def __call__(self, cmd, **kw):
            self.calls.append(cmd[4:])
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
    f = Mal()
    h = cli(f)
    for _ in range(2):
        try:
            h.list_status("a", "ready")
        except Exception:
            pass
    assert len(f.calls) == 2


def test_doble_de_pruebas_sin_ttl_explicito_no_cachea():
    f = Fake()
    h = HermesCLI("default", exe="hermes", runner=f)
    h.list_status("a", "ready")
    h.list_status("a", "ready")
    assert len(f.calls) == 2


def test_ttl_de_lanes_yaml(monkeypatch):
    proc.configure_cli(list_cache=7)
    try:
        assert proc.cli_settings()["list_cache"] == 7.0
    finally:
        proc.configure_cli()


def test_intervalo_y_estadisticas_configurables_en_lanes_yaml(tmp_path):
    p = tmp_path / "lanes.yaml"
    p.write_text("runner:\n  interval_seconds: 120\n  stats_log_minutes: 2\n", encoding="utf-8")
    s = load_runner_settings(p)
    assert s["interval_seconds"] == 120 and s["stats_log_minutes"] == 2


def test_contador_de_procesos_por_ejecutable(monkeypatch):
    monkeypatch.setattr(proc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    proc.stats_delta()
    proc.run(["C:/x/hermes.exe", "kanban"])
    proc.run(["git", "status"])
    proc.run(["git", "log"])
    assert proc.stats_delta() == {"hermes": 1, "git": 2}
    assert proc.stats_delta() == {}
