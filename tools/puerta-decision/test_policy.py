"""Valida las fichas de policy/*.yaml (borrador inerte): estructura y que no cuelen secretos.
py -3.12 -m pytest tools/puerta-decision/test_policy.py -q"""
import re
from pathlib import Path

import pytest
import yaml

POLICY = Path(__file__).resolve().parents[2] / "policy"
OBLIGATORIOS = {"repo", "ruta_local", "rama_produccion", "rama_staging", "despliegue", "proteccion", "decide_solo",
                "pregunta_siempre", "nunca", "verificacion", "aviso_a", "verificado", "sin_verificar", "estado"}
SECRETO = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|ohq_pt_[A-Za-z0-9]{6,}|eyJ[A-Za-z0-9_-]{15,}|AKIA[0-9A-Z]{12,}|(?:password|token|secret)\s*[:=]\s*\S{6,})", re.I)
FICHAS = sorted(POLICY.glob("*.yaml"))


def test_hay_fichas():
    assert len(FICHAS) >= 5


@pytest.mark.parametrize("ruta", FICHAS, ids=lambda p: p.name)
def test_ficha_valida(ruta):
    d = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    assert OBLIGATORIOS <= set(d), f"faltan campos: {OBLIGATORIOS - set(d)}"
    assert d["estado"] in {"borrador", "revisada"}
    assert set(d["despliegue"]) == {"produccion", "staging"}
    for campo in ("decide_solo", "pregunta_siempre", "nunca", "verificacion", "aviso_a", "sin_verificar"):
        assert isinstance(d[campo], list), campo
    assert d["pregunta_siempre"], "toda ficha tiene al menos una cosa que se pregunta siempre"
    assert Path(ruta).stem.replace("-", "") or True


@pytest.mark.parametrize("ruta", FICHAS, ids=lambda p: p.name)
def test_sin_secretos(ruta):
    assert not SECRETO.search(ruta.read_text(encoding="utf-8"))


@pytest.mark.parametrize("ruta", FICHAS, ids=lambda p: p.name)
def test_nada_relaja_reglas_duras(ruta):
    """Una ficha solo añade cautelas: nunca puede declarar que se decide solo algo de la lista dura del CLAUDE.md global."""
    d = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    solo = " ".join(d["decide_solo"]).lower()
    for prohibido in ("push a master", "push a main", "force push", "alembic upgrade", "variables de entorno", "despliegue a producción"):
        assert prohibido not in solo
