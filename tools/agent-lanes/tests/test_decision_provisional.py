"""Regla «decisión provisional»: el texto de instrucciones del worker la incluye con la lista de lo irreversible."""
from __future__ import annotations

from agent_lanes import worker

from .test_clasificador import HUB


def test_prompt_contiene_regla_y_lista_irreversible():
    prompt = worker.build_prompt({"id": "t_1", "title": "T", "body": "x"}, HUB)
    assert worker.DECISION_PROVISIONAL_MARCA in prompt
    assert "recomendada" in prompt
    assert "needs_input" in prompt
    for item in worker.IRREVERSIBLE:
        assert item in prompt
    assert len(worker.IRREVERSIBLE) == 8
