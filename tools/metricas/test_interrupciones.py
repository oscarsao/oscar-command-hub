"""Pruebas de interrupciones.py con transcripciones sinteticas.  python -m pytest tools/metricas -q"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import interrupciones as m  # noqa: E402


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _sesion(ruta, base, preguntas, mensajes_user=()):
    """preguntas: lista de (label_opciones, respuesta, minutos_de_espera)."""
    lineas = []
    for i, (ops, resp, esp) in enumerate(preguntas):
        t0 = base + timedelta(minutes=10 * i)
        q = f"pregunta {i}"
        lineas.append({"type": "assistant", "timestamp": _ts(t0), "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "AskUserQuestion",
             "input": {"questions": [{"question": q, "options": [{"label": o} for o in ops]}]}}]}})
        lineas.append({"type": "user", "timestamp": _ts(t0 + timedelta(minutes=esp)), "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": f'The user answered: "{q}"="{resp}". You can now continue.'}]}})
    for j, txt in enumerate(mensajes_user):
        lineas.append({"type": "user", "timestamp": _ts(base + timedelta(hours=1 + j)), "message": {"content": txt}})
    with open(ruta, "w", encoding="utf-8") as f:
        for l in lineas:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")


def test_cuenta_acepta_espera_y_recuerda(tmp_path):
    base = datetime.now(timezone.utc) - timedelta(days=2)
    c = tmp_path / "C--Users-oscar-dev"
    c.mkdir()
    _sesion(c / "a.jsonl", base, [
        (["Hazlo (Recomendada)", "Otra"], "Hazlo (Recomendada)", 2),
        (["Hazlo (Recomendada)", "Otra"], "Otra", 4),
        (["Sí", "No"], "Sí", 6),                       # sin recomendada: no entra en el %
    ], mensajes_user=["recuerda que no uso Railway", "otra cosa"])
    P, R, ses = m.recorre(str(tmp_path), 7)
    f = m.resumen(P, R, ses)
    assert len(f) == 1
    f = f[0]
    assert f["preguntas"] == 3 and f["tandas"] == 3
    assert f["pct_recomendada_aceptada"] == 50          # 1 de 2 con recomendada
    assert f["espera_mediana_min"] == 4.0
    assert f["recuerda_repetidos"] == 1
    assert f["sesiones"] == 1


def test_excluye_carriles_y_scratchpad_y_antiguos(tmp_path):
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for nombre in ("C--Users-oscar-dev--lanes-lane-t-0001", "C--Users-oscar-AppData-scratchpad-x"):
        d = tmp_path / nombre
        d.mkdir()
        _sesion(d / "a.jsonl", base, [(["A (Recomendada)"], "A (Recomendada)", 1)])
    d = tmp_path / "C--Users-oscar"
    d.mkdir()
    _sesion(d / "vieja.jsonl", base - timedelta(days=60), [(["A"], "A", 1)])
    os.utime(d / "vieja.jsonl", (time.time() - 60 * 86400,) * 2)
    P, R, ses = m.recorre(str(tmp_path), 28)
    assert P == []


def test_no_guarda_texto_de_preguntas(tmp_path):
    base = datetime.now(timezone.utc) - timedelta(days=1)
    d = tmp_path / "C--Users-oscar"
    d.mkdir()
    _sesion(d / "a.jsonl", base, [(["SECRETO-XYZ (Recomendada)"], "SECRETO-XYZ (Recomendada)", 1)])
    P, R, ses = m.recorre(str(tmp_path), 7)
    assert "SECRETO" not in json.dumps(P) + json.dumps(m.resumen(P, R, ses))


def test_semana_iso():
    assert m.semana("2026-11-15") == "2026-W46"
