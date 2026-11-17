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
INICIOS_PERMITIDOS = {"Documentación", "Arreglos", "Escribir", "Tarjetas"}
TERMINOS_PROHIBIDOS_EN_DECIDE_SOLO = ("push", "force", "alembic", "variable", "desplieg", "despleg", "fusion", "merge", "migraci",
                                      "envío", "envio", "borrar", "producción", "master", "main", "stamp", "downgrade", "credencial", "clave")
LENGUAJE_DE_CONCESION = ("autorizó", "autorizado", "autoriza", "sin confirm", "sin preguntar", "sin pedir")


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
    # 1) Falla cerrado: cada línea de decide_solo empieza por un verbo de la lista blanca (solo trabajo en rama propia).
    for linea in d["decide_solo"]:
        assert linea.split()[0] in INICIOS_PERMITIDOS, f"decide_solo con inicio no permitido: {linea[:60]!r}"
    # 2) Ningún término de §1 ni lenguaje de concesión en decide_solo, nunca ni pregunta_siempre.
    solo = " ".join(d["decide_solo"]).lower()
    for t in TERMINOS_PROHIBIDOS_EN_DECIDE_SOLO:
        assert t not in solo, f"decide_solo menciona {t!r}"
    for campo in ("decide_solo", "nunca", "pregunta_siempre"):
        texto = " ".join(d[campo]).lower()
        for t in LENGUAJE_DE_CONCESION:
            assert t not in texto, f"{campo} contiene lenguaje de concesión {t!r}"
    # 3) Toda ficha con rama de producción pregunta siempre antes de fusionar a ella.
    pq = " ".join(d["pregunta_siempre"]).lower()
    assert "fusionar" in pq and d["rama_produccion"] in pq, "pregunta_siempre debe cubrir fusionar a la rama de producción"
