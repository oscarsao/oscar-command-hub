"""Nombres, descripciones y mensaje fijado de cada tema de Gestión y Marketing (aprobado por Oscar 28-09).

Uso:  py -3.12 organizar_temas.py            -> dry-run (solo imprime)
      py -3.12 organizar_temas.py --apply    -> renombra, describe grupos y fija la guía en cada tema

Usa el bot de Trabajos (CARRILES_BOT_TOKEN de agent-lanes/.env, admin con can_manage_topics).
El token nunca se imprime. Idempotente: renombrar al mismo nombre da "TOPIC_NOT_MODIFIED" y se ignora;
la guía guarda el message_id en .state/guias_temas.json y la edita en vez de duplicarla.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = HERE.parent / "agent-lanes" / ".env"
STATE = HERE / ".state" / "guias_temas.json"

GESTION, MARKETING = -1003530490339, -1004277259008

GROUP_DESCRIPTIONS = {
    GESTION: "Gestión interna de Píldora Digital (holding: MigraTeam + Píldora). "
             "Dirección y C-level por tema; Operaciones = Hermes y carriles de agentes. "
             "Comandos del bot Trabajos: /hoy /decisiones /aprobar /tareas.",
    MARKETING: "Marketing de MigraTeam y Píldora Digital. El CMO (bot Oscar HQ Avisos) responde en cada "
               "tema y entrega piezas listas para usar. Un tema por marca + planificación + recursos.",
}

# (chat, thread, nombre, guía fijada). thread=None -> tema General.
# 355/74/77/80/83 los creó el bot de Trabajos (recrear_temas.py): los que creó Hermes le hacían contestar a todo.
TOPICS = [
    (GESTION, None, "🏛️ Dirección · CEO",
     "🏛️ Dirección · CEO\n\nPrioridades, estrategia y decisiones transversales del holding (MigraTeam + Píldora).\n"
     "Responde: CEO (bot Oscar HQ Avisos).\n"
     "Resumen diario de las 8:00 y avisos generales llegan aquí."),
    (GESTION, 5, "🛠️ Operaciones · General",
     "🛠️ Operaciones · General\n\nTrabajo técnico que no es de una marca concreta (Oscar HQ, Scraper, NextJobs, infraestructura).\n"
     "Responde: Hermes. Pide algo y lo convierte en tarjeta para el carril de agentes.\n"
     "Botones de aprobar/pedir cambios: bot Pildora - Trabajos. /hoy /decisiones /tareas."),
    (GESTION, 230, "🛂 Operaciones · MigraTeam",
     "🛂 Operaciones · MigraTeam\n\nDesarrollo y operación de MigraTeam (carril claude-migrateam).\n"
     "Responde: Hermes. Inicio, avance, preguntas y fin de cada tarea se publican aquí.\n"
     "Nada llega a producción sin tu botón explícito."),
    (GESTION, 231, "💊 Operaciones · Píldora",
     "💊 Operaciones · Píldora\n\nDesarrollo de Píldora Digital y Oscar HQ (carril claude-oscarhq).\n"
     "Responde: Hermes. Aprobar = PR; Fusionar/Desplegar = botones del Integrador."),
    (GESTION, 7, "📣 Marketing · Decisiones",
     "📣 Marketing · Decisiones\n\nDecisiones de marketing que no se discuten en el grupo de Marketing (presupuesto, posicionamiento, prioridades).\n"
     "Responde: CMO (bot Oscar HQ Avisos). La ejecución con Andrea va en el grupo de Marketing."),
    (GESTION, 6, "💼 Ventas · CSO",
     "💼 Ventas · CSO\n\nPipeline, propuestas, clientes y alianzas de ambas marcas.\n"
     "Responde: CSO (bot Oscar HQ Avisos). No da cifras que no tenga: si falta el dato, lo dice."),
    (GESTION, 355, "💶 Finanzas · CFO",
     "💶 Finanzas · CFO\n\nCaja, costes, facturación y fiscalidad.\n"
     "Responde: CFO (bot Oscar HQ Avisos). Privado: nada de esto se comparte en Marketing."),
    (MARKETING, None, "💬 General · CMO",
     "💬 General · CMO\n\nConversación general de marketing. El CMO (bot Oscar HQ Avisos) responde a cada mensaje "
     "y entrega piezas terminadas (copys, guiones, posts, emails, ideas).\n"
     "Para una marca concreta, usa su tema. Hermes solo responde si le mencionas."),
    (MARKETING, 74, "🗓️ Planificación y calendario",
     "🗓️ Planificación y calendario\n\nCalendario editorial, fechas clave y campañas de ambas marcas.\n"
     "Responde: CMO. Pídele un calendario o una semana de publicaciones y te la entrega en tabla."),
    (MARKETING, 77, "🛂 MigraTeam · Contenido",
     "🛂 MigraTeam · Contenido\n\nContenido de MigraTeam (LegalTech de extranjería para despachos).\n"
     "Responde: CMO con el contexto y el tono de MigraTeam."),
    (MARKETING, 80, "💊 Píldora · Contenido",
     "💊 Píldora · Contenido\n\nContenido de Píldora Digital (marketing + IA para pymes).\n"
     "Responde: CMO con el contexto y el tono de Píldora."),
    (MARKETING, 83, "📚 Recursos",
     "📚 Recursos\n\nMaterial de referencia: plantillas, briefs, enlaces, imágenes y piezas aprobadas.\n"
     "Responde: CMO si le preguntas; úsalo sobre todo como archivo."),
]


def _token() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("CARRILES_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("FAIL: falta CARRILES_BOT_TOKEN en agent-lanes/.env")


def _call(tok: str, method: str, **params) -> dict:
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        return json.load(e)


def _ok(r: dict, benign: tuple[str, ...] = ()) -> str:
    if r.get("ok"):
        return "OK"
    d = r.get("description", "")
    return "OK (sin cambios)" if any(b in d for b in benign) else f"FALLO: {d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not a.apply:
        for chat, thread, name, guide in TOPICS:
            print(f"{chat} t{thread or 'General'} -> {name}")
        print("DRY-RUN. Repite con --apply.")
        return 0

    tok = _token()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    for chat, desc in GROUP_DESCRIPTIONS.items():
        print(chat, "descripción:", _ok(_call(tok, "setChatDescription", chat_id=chat, description=desc),
                                         ("not modified",)))
    for chat, thread, name, guide in TOPICS:
        if thread is None:
            r = _call(tok, "editGeneralForumTopic", chat_id=chat, name=name)
        else:
            r = _call(tok, "editForumTopic", chat_id=chat, message_thread_id=thread, name=name)
        line = f"{chat} t{thread or 'General'} '{name}': nombre {_ok(r, ('TOPIC_NOT_MODIFIED',))}"
        key = f"{chat}:{thread or 0}"
        mid = state.get(key)
        if mid:
            r = _call(tok, "editMessageText", chat_id=chat, message_id=mid, text=guide)
            line += f" · guía editada {_ok(r, ('not modified',))}"
        else:
            p = {"chat_id": chat, "text": guide, "disable_notification": True}
            if thread is not None:
                p["message_thread_id"] = thread
            r = _call(tok, "sendMessage", **p)
            if r.get("ok"):
                mid = r["result"]["message_id"]
                state[key] = mid
                r = _call(tok, "pinChatMessage", chat_id=chat, message_id=mid, disable_notification=True)
            line += f" · guía fijada {_ok(r)}"
        print(line)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
