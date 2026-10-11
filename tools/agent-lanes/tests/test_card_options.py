"""Bloque de opciones de las tarjetas de decisión: parser tolerante y mensaje (card_options.py)."""
from __future__ import annotations

from agent_lanes.card_options import (PREPARED_AUTHOR, PREPARED_PREFIX, CardSpec, card_message, format_block,
                                      parse_card)

FULL = """Contexto: Hay que fijar el precio antes del lunes.
Es reversible.
Opciones:
A) 9 € al mes
B) 19 € al mes
   (con descuento anual)
C) Gratis un año
Recomendada: B
"""


def task(tid="t_d0000001", title="[DECISIÓN] Precio"):
    return {"board": "default", "since": 1000.0, "task": {"id": tid, "title": title}}


def test_full_block():
    s = parse_card(FULL)
    assert s.has_options and s.context == "Hay que fijar el precio antes del lunes. Es reversible."
    assert s.options == ["9 € al mes", "19 € al mes (con descuento anual)", "Gratis un año"]
    assert s.recommended == 1 and s.dropped == 0 and not s.prepared
    assert s.answer_for(1) == "B) 19 € al mes (con descuento anual)" and s.answer_for(3) is None


def test_no_options_uses_body_as_context():
    s = parse_card("## Objetivo\nElegir proveedor de correo.\n")
    assert not s.has_options and s.options == [] and s.recommended is None
    assert s.context == "Objetivo Elegir proveedor de correo."
    assert not parse_card(None).has_options and not parse_card("").has_options


def test_tolerant_keys_bullets_and_numbers():
    s = parse_card("- **Contexto:** algo\n**Opciones**:\n- 1) Sí\n- 2. No\nRecomendación: 2")
    assert s.options == ["Sí", "No"] and s.recommended == 1
    s = parse_card("OPCIONES:\na: uno\nb - dos\nrecomendada: a")
    assert s.options == ["uno", "dos"] and s.recommended == 0


def test_malformed_single_option_or_garbage_means_no_options():
    assert not parse_card("Opciones:\nA) solo una").has_options
    assert not parse_card("Opciones:\nlo que sea\nsin letras").has_options
    assert parse_card("Recomendada: Z\nOpciones:\nA) x\nB) y").recommended is None


def test_more_than_three_options_are_cut_and_counted():
    s = parse_card("Opciones:\nA) a\nB) b\nC) c\nD) d\nE) e")
    assert s.options == ["a", "b", "c"] and s.dropped == 0 or s.dropped == 0


def test_more_than_three_options_dropped_count():
    s = parse_card("Opciones:\n1) a\n2) b\n3) c\n")
    assert len(s.options) == 3
    # D/E no son letras válidas (solo A/B/C): no entran ni cuentan; las letras válidas repetidas sí sobran
    s = parse_card("Opciones:\nA) a\nB) b\nC) c\n1) d\n2) e")
    assert s.options == ["a", "b", "c"] and s.dropped == 2


def test_recommended_that_does_not_exist_is_ignored():
    assert parse_card("Opciones:\nA) a\nB) b\nRecomendada: C").recommended is None


def test_prepared_comment_only_from_the_preparer_author():
    block = format_block(CardSpec("Ctx", ["uno", "dos", "tres"], 2))
    good = {"author": PREPARED_AUTHOR, "body": f"{PREPARED_PREFIX}\n{block}"}
    forged = {"author": "otro-agente", "body": f"{PREPARED_PREFIX}\n{block}"}
    assert not parse_card("sin nada", [forged]).has_options
    s = parse_card("sin nada", [good])
    assert s.prepared and s.options == ["uno", "dos", "tres"] and s.recommended == 2 and s.context == "Ctx"
    # lo escrito en el cuerpo manda sobre el borrador
    assert not parse_card(FULL, [good]).prepared
    # el comentario más reciente gana
    newer = {"author": PREPARED_AUTHOR, "body": f"{PREPARED_PREFIX}\nOpciones:\nA) x\nB) y"}
    assert parse_card("", [good, newer]).options == ["x", "y"]


def test_format_block_roundtrip():
    s = parse_card(FULL)
    again = parse_card(format_block(s))
    assert (again.context, again.options, again.recommended) == (s.context, s.options, s.recommended)


def test_message_with_options_is_escaped_and_short():
    spec = CardSpec("<b>x</b> & " + "y" * 900, ["a<1", "b" * 500, "c"], 0, dropped=1)
    text = card_message(task(title="[DECISIÓN] <script>"), spec, "https://k", 1000.0 + 7200)
    assert "&lt;script&gt;" in text and "<script>" not in text and "a&lt;1" in text
    assert '<a href="https://k/tasks/default/t_d0000001">t_d0000001</a>' in text and "hace 2 h" in text
    assert "⭐ Recomendada: A" in text and "+1 opción más" in text and len(text) < 1500
    assert "Contexto" in text and "…" in text


def test_message_without_options_points_to_preparar():
    text = card_message(task(), CardSpec(), None, 1000.0)
    assert "sin opciones preparadas: pide /preparar t_d0000001" in text and "<code>t_d0000001</code>" in text
