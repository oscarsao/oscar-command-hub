"""/cola (en curso + siguientes por carril) y /prioridad (reordenar desde Telegram, solo operadores)."""
from __future__ import annotations

from agent_lanes.commands import COMMANDS, NOT_OWNER
from tests.test_commands import KanbanHermes, center, command

RUN, LOW, HIGH, OHQ_T, DONE, MISSING = ("t_a0000001", "t_b0000001", "t_b0000002", "t_b0000003", "t_c0000001",
                                        "t_d0000000")


class PrioHermes(KanbanHermes):
    def set_priority(self, tid, priority):
        self.calls.append(("prio", tid, priority))
        self.shows[tid]["task"]["priority"] = priority
        return True


def task(tid, assignee, status, **extra):
    return {"task": {"id": tid, "title": f"Titulo {tid}", "assignee": assignee, "status": status, **extra}}


def setup(tmp_path):
    shows = {
        RUN: task(RUN, "claude-migrateam", "running", started_at=1000.0 + 30 * 3600 - 135 * 60),
        LOW: task(LOW, "claude-migrateam", "ready", priority=1),
        HIGH: task(HIGH, "claude-migrateam", "ready", priority=9),
        OHQ_T: task(OHQ_T, "claude-oscarhq", "ready", priority=3),
        DONE: task(DONE, "claude-migrateam", "done"),
    }
    cc, desk, bot, _, _ = center(tmp_path, shows)
    h = PrioHermes(shows)
    cc.hermes_for = lambda b: h
    return cc, desk, bot, h


def test_cola_muestra_en_curso_y_siguientes_por_prioridad(tmp_path):
    cc, desk, bot, h = setup(tmp_path)
    command(desk, "/cola")
    text = bot.sent[-1]["text"]
    assert RUN in text and "desde hace 2 h 15 min" in text
    assert text.index(HIGH) < text.index(LOW)  # prioridad 9 antes que 1
    assert "prioridad 9" in text
    assert "claude-nextjobs" in text and "▶️ libre" in text
    command(desk, "/cola migrateam", uid=2)
    assert "claude-oscarhq" not in bot.sent[-1]["text"]
    command(desk, "/cola marte", uid=3)
    assert bot.sent[-1]["text"].startswith("Marca desconocida")


def test_prioridad_numero_sube_baja_y_limites(tmp_path):
    cc, desk, bot, h = setup(tmp_path)
    command(desk, f"/prioridad {LOW} 7")
    assert ("prio", LOW, 7) in h.calls and "1 → 7" in bot.sent[-1]["text"]
    command(desk, f"/prioridad {LOW} sube", uid=2)
    assert ("prio", LOW, 8) in h.calls
    command(desk, f"/prioridad {LOW} baja", uid=3)
    assert ("prio", LOW, 7) in h.calls
    command(desk, f"/prioridad {LOW} 500", uid=4)
    assert ("prio", LOW, 99) in h.calls


def test_prioridad_rechaza_entradas_malas_y_cerradas(tmp_path):
    cc, desk, bot, h = setup(tmp_path)
    for i, bad in enumerate(("", LOW, f"{LOW} alta", "--board x 5", f"{LOW} -3", f"{LOW} 5 6"), 1):
        command(desk, f"/prioridad {bad}", uid=i)
        assert bot.sent[-1]["text"].startswith("Uso:")
    command(desk, f"/prioridad {DONE} 5", uid=10)
    assert "cerrada" in bot.sent[-1]["text"]
    command(desk, f"/prioridad {MISSING} 5", uid=11)
    assert "No encuentro" in bot.sent[-1]["text"]
    assert not [c for c in h.calls if c[0] == "prio"]


def test_solo_operadores_reordenan(tmp_path):
    cc, desk, bot, h = setup(tmp_path)
    command(desk, f"/prioridad {LOW} 9", user=555, chat=555)
    assert bot.sent[-1]["text"] == NOT_OWNER and not h.calls
    command(desk, "/cola", user=555, chat=555, uid=2)
    assert bot.sent[-1]["text"] == NOT_OWNER


def test_comandos_registrados():
    assert {"cola", "prioridad"} <= {c for c, _ in COMMANDS}
