"""Clasificador: las reglas de bloqueo solo miran la sección «Hacer» (t_f6511420). Sin red, sin hermes."""
from __future__ import annotations

from agent_lanes import clasificador

from .test_clasificador import CFG, HUB, LANES, ids, task

# Cuerpos con la forma de las tarjetas reales que el clasificador mandó al integrador por frases de Límites/Contexto
# (t_bca8ab62, t_e59e779d y la del editor de sitio). Se reconstruyen con las frases que disparaban las reglas.
BCA8 = """Origen: decisión de Oscar (19-11).
Rama-origen: lane/t_aaaa1111
Contexto: el aviso hoy sale tras el despliegue a producción de Dokploy; cambia las variables de entorno del bot.
Hacer:
1. Ajusta el texto del aviso en notices.py.
2. Añade el test correspondiente.
Criterio de hecho: el aviso sale una sola vez. Ejecuta alembic upgrade head en staging para comprobarlo.
Pruebas: tests de notices.
Límites: rama lane propia; escribe en ~/.hermes/config.yaml solo si Oscar lo pide.
Relacionadas: t_1."""

E59E = """Contexto: Oscar corrige las env vars en Vercel y edita ~/.claude/settings.json a mano.
Hacer: renombra la columna en el panel y añade su test.
Límites: instala gitleaks con winget install gitleaks si falta; despliega a producción cuando acabes."""

SITIO = """## Contexto
Actualiza las variables de entorno de pildora-website y configura Dokploy antes.
## Hacer
- Cambia el componente del editor de sitio para que muestre el borrador.
## Límites
Ejecuta execute_sql en Supabase con la migración nueva; modifica pildora-handbook."""


def _sin_hacer(body):
    return body.replace("Hacer:", "Resumen:").replace("## Hacer", "## Resumen")


def test_tres_cuerpos_reales_ya_no_bloquean():
    for body in (BCA8, E59E, SITIO):
        assert clasificador.evaluar(HUB, task(body), lanes=LANES, cfg=CFG).integrador == [], body
        # Prueba de que las frases ignoradas SÍ bloqueaban antes: sin sección «Hacer» se evalúa todo el texto.
        assert clasificador.evaluar(HUB, task(_sin_hacer(body)), lanes=LANES, cfg=CFG).integrador, body


def test_peticion_real_en_hacer_sigue_bloqueando():
    for body, rid in (("Contexto: nada.\nHacer:\n1. Despliega a producción el runner.\nLímites: rama propia.", "produccion_env"),
                      ("Hacer: ejecuta alembic upgrade head contra Supabase.\nLímites: nada.", "sql_remoto"),
                      ("## Hacer\n- Escribe la config en ~/.hermes/config.yaml\n## Límites\nnada", "fuera_worktree"),
                      ("Hacer:\n1. Cambia el código.\n2. Modifica pildora-handbook y añade la página.", "otro_repo")):
        assert rid in ids(clasificador.evaluar(HUB, task(body), lanes=LANES, cfg=CFG)), body


def test_sin_hacer_se_comporta_como_hoy():
    body = "Despliega a producción el runner. Límites: rama propia."
    assert ids(clasificador.evaluar(HUB, task(body), lanes=LANES, cfg=CFG)) == ["produccion_env"]
    assert clasificador.texto_accion(body) is None
    assert clasificador.texto_accion("Hacer lo que toque con el runner.") is None  # no es una cabecera


def test_rama_origen_y_decision_dentro_de_hacer_se_ignoran():
    body = "Hacer:\n1. Ajusta el texto.\nDecisión: despliega a producción tras revisar.\nRama-origen: lane/t_bbbb2222\n2. Tests."
    assert clasificador.texto_accion(body) == "1. Ajusta el texto.\n2. Tests."
    assert clasificador.evaluar(HUB, task(body), lanes=LANES, cfg=CFG).integrador == []


def test_clasificador_omitir_sigue_siendo_salida_de_emergencia():
    body = "Clasificador: omitir\nHacer:\n1. Despliega a producción el runner."
    assert clasificador.evaluar(HUB, task(body), lanes=LANES, cfg=CFG).integrador == []
