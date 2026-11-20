"""Pruebas de tools/estado/estado_global.py: salidas de ejemplo guardadas, sin red ni kanban real."""
import importlib.util
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]
FX = Path(__file__).parent / "fixtures" / "estado_global"
spec = importlib.util.spec_from_file_location("estado_global", RAIZ / "tools" / "estado" / "estado_global.py")
eg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eg)

AHORA = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
TARJETAS = json.loads((FX / "kanban_default.json").read_text(encoding="utf-8"))
PRS = json.loads((FX / "gh_pr_list.json").read_text(encoding="utf-8"))


def en_base(sha):
    return {"1" * 40: True, "2" * 40: False}.get(sha)


def fake_run(cmd, timeout=120):
    if "list" in cmd and "kanban" in cmd:
        return json.dumps(TARJETAS)
    if "stats" in cmd:
        return json.dumps({"triage": 1, "ready": 1, "blocked": 1, "done": 3})
    if "pr" in cmd:
        return json.dumps(PRS)
    raise RuntimeError("sin respuesta")  # Vercel y git: no disponibles


def test_recuento_por_estado_y_carril():
    assert eg.por_estado(TARJETAS) == {"ready": 1, "blocked": 1, "done": 3, "triage": 1}
    assert eg.por_carril(TARJETAS)["claude-hub"] == {"ready": 1, "blocked": 1, "done": 1}


def test_hecha_no_integrada():
    fuera, sin_dato = eg.hechas_sin_fusionar(TARJETAS, en_base)
    assert [t["id"] for t, _ in fuera] == ["t_aaaaaaa4"]
    assert sin_dato == 1


def test_vars_sin_constar_ignora_conocidas():
    falta = eg.vars_sin_constar(TARJETAS, {"YA_CONSTA_Z"})
    assert falta == {"FOO_TOKEN_X": ["t_aaaaaaa2"]}


def test_informe_completo():
    inf = eg.construir({"default": TARJETAS}, {"default": {"triage": 1, "ready": 1, "blocked": 1, "done": 3}},
                       {"repo/x": PRS, "repo/y": "sin red"}, "vercel caído", set(), en_base, AHORA)
    assert "### Tablero `default` — 6 tarjetas" in inf
    assert "coincide." in inf
    assert "toca rutas vetadas" in inf
    assert "t_aaaaaaa4" in inf and "t_aaaaaaa3" not in inf.split("## 4.")[0].split("## 3.")[1]
    assert "#10 PR en borrador antiguo — borrador, 10 d" in inf
    assert "`repo/y`: no disponible" in inf and "No disponible: vercel caído" in inf
    assert "`FOO_TOKEN_X`" in inf


def test_contraste_con_stats_detecta_diferencia():
    inf = eg.construir({"default": TARJETAS}, {"default": {"done": 99}}, {}, None, set(), en_base, AHORA)
    assert "done: informe 3 ≠ stats 99" in inf


def test_main_escribe_solo_el_informe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eg.shutil, "which", lambda n: n)
    monkeypatch.setattr(eg, "contiene_sha", lambda s, run=None, base="origin/main": en_base(s))
    salida = tmp_path / "informe.md"
    assert eg.main(["--tableros", "default", "--repos", "repo/x", "--salida", str(salida)], run=fake_run) == 0
    assert "# Estado global" in salida.read_text(encoding="utf-8")
    assert "Estado global" in capsys.readouterr().out


def test_comandos_son_de_solo_lectura(monkeypatch):
    vistos = []
    monkeypatch.setattr(eg.shutil, "which", lambda n: n)
    eg.main(["--tableros", "default", "--repos", "repo/x"], run=lambda cmd, timeout=120: (vistos.append(cmd), fake_run(cmd))[1])
    prohibidos = {"create", "complete", "comment", "block", "unblock", "archive", "assign", "merge", "close", "edit"}
    assert vistos and not any(prohibidos & set(c) for c in vistos)


def test_falla_con_claridad_si_falta_herramienta(monkeypatch, capsys):
    monkeypatch.setattr(eg.shutil, "which", lambda n: None)
    monkeypatch.setattr(eg, "HERMES_EXE", Path("/no/existe/hermes.exe"))
    assert eg.main([], run=fake_run) == 2
    assert "falta la herramienta" in capsys.readouterr().err


def test_sin_gh_falla_con_claridad(monkeypatch, capsys):
    monkeypatch.setattr(eg.shutil, "which", lambda n: None if n == "gh" else n)
    assert eg.main([], run=fake_run) == 2
    assert "`gh`" in capsys.readouterr().err
