"""Bucles de preguntas (0a): respuestas ligadas a la TAREA (no al texto exacto), notas del coordinador al worker,
re-pregunta de algo ya respondido y limitaciones permanentes del guard ("requiere consola")."""
from __future__ import annotations

from agent_lanes.decisions import keyboard_spec
from agent_lanes.hermes import (ANSWER_PREFIX, OSCAR_AUTHOR, answer_progress, answered_pairs, coordinator_notes_from,
                                find_answered, is_guard_limit, looks_like_guard_limit, oscar_answers_from, similar_text)
from agent_lanes.hermes_answers import HermesAnswers
from agent_lanes.worker import build_prompt
from tests.test_runner_state import GOOD, LANE, FakeHermes, ok_outcome
from tests.test_runner_state import make as make_runner


def ans(q, a, t, author=OSCAR_AUTHOR):
    return {"author": author, "body": f"{ANSWER_PREFIX} {q} → {a}", "created_at": t}


Q_OLD = "¿Instalo la dependencia pytest-asyncio para poder ejecutar los tests asíncronos?"
Q_NEW = "¿Puedo instalar pytest-asyncio para ejecutar los tests asíncronos?"


def test_similar_text_matches_reformulations_not_different_topics():
    assert similar_text(Q_OLD, Q_NEW)
    assert similar_text("¿Publicar ya?", "  ¿PUBLICAR ya? ")
    assert not similar_text("¿Publicar ya?", "¿Precio del plan Pro?")


def test_oscar_answers_keep_only_latest_for_a_reformulated_question():
    show = {"comments": [ans(Q_OLD, "Sí", 1), ans("¿Canal?", "Email", 2), ans(Q_NEW, "No, usa mocks", 3)]}
    out = oscar_answers_from(show)
    assert len(out) == 2 and out[-1].endswith("No, usa mocks") and not any("→ Sí" in a for a in out)


def test_answer_progress_counts_a_reformulated_question_answer():
    qs = [{"question": Q_NEW}]
    show = {"events": [{"kind": "blocked", "created_at": 10, "payload": {"kind": "needs_input"}}],
            "comments": [ans(Q_OLD, "Sí", 20)]}
    answers, nxt = answer_progress(show, qs)
    assert answers == {0: "Sí"} and nxt is None


def test_find_answered_requires_every_question_to_be_answered():
    show = {"comments": [ans(Q_OLD, "Sí", 1)]}
    assert find_answered(show, [{"question": Q_NEW}]) == [(Q_NEW, "Sí")]
    assert find_answered(show, [{"question": Q_NEW}, {"question": "¿Y el precio del plan?"}]) is None
    assert answered_pairs({"comments": []}) == []


def test_coordinator_notes_exclude_hermes_answers_and_workers():
    show = {"comments": [
        {"author": "coordinador", "body": "NO vuelvas a preguntar por pytest-asyncio: ya está instalado."},
        {"author": "default", "body": "RESPUESTA-OSCAR (id=100000001): sí"},
        {"author": "default", "body": "RESPUESTA-OSCAR: sin id (P07: no vale y tampoco es nota)"},
        {"author": "agent-lanes", "body": "LANE claim"},
        {"author": "default", "body": "Oscar decide: usa mocks."}]}
    assert coordinator_notes_from(show) == ["NO vuelvas a preguntar por pytest-asyncio: ya está instalado.",
                                            "Oscar decide: usa mocks."]


def test_prompt_has_coordinator_notes_and_last_answer_wins():
    p = build_prompt({"id": "t_1", "title": "T", "body": "B", "oscar_answers": ["Respuesta de Oscar: ¿X? → no"],
                      "coordinator_notes": ["NO vuelvas a preguntar X"]}, LANE)
    assert "Notas del coordinador" in p and "NO vuelvas a preguntar X" in p
    assert "vale la ÚLTIMA" in p and "otras palabras" in p


# --- runner: pregunta ya respondida -------------------------------------------------------------------------

def needs(q):
    return ok_outcome({**GOOD, "status": "needs_input", "summary": "pregunto",
                       "questions": [{"question": q, "options": ["Sí", "No"], "recommended": 0}]})


class AnsweredHermes(FakeHermes):
    def __init__(self, shows, **kw):
        super().__init__(**kw)
        self.show_data = shows

    def answered_for(self, tid, questions):
        return find_answered(self.show_data, questions)


def test_repeated_answered_question_relaunches_once_with_answers():
    h = AnsweredHermes({"comments": [ans(Q_OLD, "Sí", 1)]}, tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    runner, _, w, _ = make_runner([needs(Q_NEW), ok_outcome()], hermes=h)
    prompts = []
    w.resume = lambda *a, prompt=None, **k: (w.runs.append("resume"), prompts.append(prompt), w.outcomes.pop(0))[2]
    assert runner.run_once() == {"t_1": "review"}
    assert w.runs == ["run", "resume"] and "Sí" in prompts[0] and "No vuelvas a preguntarlo" in prompts[0]
    assert "block" not in h.kinds()


def test_repeated_question_insisting_goes_to_oscar_with_previous_answers():
    h = AnsweredHermes({"comments": [ans(Q_OLD, "Sí", 1)]}, tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    runner, _, w, _ = make_runner([needs(Q_NEW), needs(Q_NEW)], hermes=h)
    w.resume = lambda *a, prompt=None, **k: (w.runs.append("resume"), w.outcomes.pop(0))[1]
    assert runner.run_once() == {"t_1": "blocked:needs_input"}
    block = next(c for c in h.calls if c[0] == "block")
    assert "Respuestas previas de Oscar" in block[3] and "-> Sí" in block[3]
    assert len([c for c in h.calls if c[0] == "comment"]) >= 2  # claim + aviso de relanzado, una sola vez


def test_new_question_blocks_normally():
    h = AnsweredHermes({"comments": [ans(Q_OLD, "Sí", 1)]}, tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    runner, _, w, _ = make_runner([needs("¿Qué precio ponemos al plan Pro?")], hermes=h)
    assert runner.run_once() == {"t_1": "blocked:needs_input"}
    assert w.runs == ["run"]


# --- limitación permanente del guard ----------------------------------------------------------------------

def test_structured_guard_limitation_blocks_as_console_without_retry_path():
    out = ok_outcome({**GOOD, "status": "needs_input", "summary": "Necesito instalar un paquete", "limitation": "guard",
                      "questions": [{"question": "¿Instalo pytest-asyncio?", "options": ["Sí", "No"], "recommended": 0}]})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    runner, _, w, _ = make_runner([out], hermes=h)
    assert runner.run_once() == {"t_1": "blocked:limitacion_guard"}
    block = next(c for c in h.calls if c[0] == "block")
    assert block[2] == "needs_input" and is_guard_limit(block[3])


def test_guard_limit_heuristic_and_keyboard():
    assert looks_like_guard_limit("El guard me bloquea pip install", "")
    assert not looks_like_guard_limit("¿Publicar ya?", "precio")
    spec = keyboard_spec("blocked", block_kind="console")
    assert [b["action"] for b in spec[0]] == ["park"]  # ni Reintentar ni responder


def test_heal_does_not_return_guard_limited_task_to_the_lane():
    reason = "LIMITACION-GUARD: el worker no puede hacerlo con el guard del carril:\n- ¿Instalo pytest-asyncio?"
    show = {"task": {"id": "t_1", "status": "blocked", "title": "T"},
            "events": [{"kind": "blocked", "created_at": 10, "payload": {"kind": "needs_input", "reason": reason}}],
            "comments": [ans("¿Instalo pytest-asyncio?", "Sí", 20)]}
    ha = HermesAnswers({}, hermes_for=lambda b: None)
    assert ha.heal(object(), object(), show) is False


def test_retry_button_detects_guard_limited_block():
    from agent_lanes.decisions import DecisionDesk
    reason = "LIMITACION-GUARD: x"
    show = {"events": [{"kind": "blocked", "created_at": 10, "payload": {"kind": "needs_input", "reason": reason}}]}

    class H:
        def show(self, tid):
            return show

    class Desk:
        def _hermes(self, rec):
            return H()

    assert DecisionDesk._guard_limited(Desk(), {"task_id": "t_1"}) is True
    show["events"][0]["payload"]["reason"] = "El worker necesita decisión:\n- ¿X?"
    assert DecisionDesk._guard_limited(Desk(), {"task_id": "t_1"}) is False


def test_renotify_plan_for_guard_limited_block_is_console_card():
    from agent_lanes.renotify import Renotifier
    reason = "LIMITACION-GUARD: x"
    show = {"task": {"id": "t_1", "title": "T", "status": "blocked"},
            "events": [{"kind": "blocked", "created_at": 10, "payload": {"kind": "needs_input", "reason": reason}}],
            "comments": [], "runs": []}
    p = Renotifier._plan_blocked(object(), LANE, show)
    assert p.block_kind == "console" and "/tomar-tarea t_1" in p.status
