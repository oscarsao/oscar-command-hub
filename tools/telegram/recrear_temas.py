"""Recrea con el bot de Trabajos los temas que creó Hermes (28-09) y archiva los viejos.

Por qué: Hermes 0.21.4 trata cada mensaje de un tema que ÉL creó como "respuesta a Hermes"
(`_is_reply_to_bot` ve el mensaje de creación del tema como reply_to_message), así que se salta
require_mention y contesta a todo. `ignored_threads` no sirve: es global y los IDs 5/6/7 existen en
ambos grupos. Un tema creado por otro bot no dispara esa regla.

Uso:  py -3.12 recrear_temas.py [--apply]
Los viejos se renombran "🗄 Archivado · <nombre>" y se cierran (reversible con reopenForumTopic);
no se borra ningún mensaje. Los IDs nuevos se guardan en .state/temas_nuevos.json.
"""
import argparse
import json
import sys
from pathlib import Path

from organizar_temas import GESTION, MARKETING, STATE, _call, _ok, _token

NEW_STATE = STATE.with_name("temas_nuevos.json")

# (chat, thread viejo, nombre)
RECREATE = [
    (GESTION, 148, "💶 Finanzas · CFO"),
    (MARKETING, 7, "🗓️ Planificación y calendario"),
    (MARKETING, 5, "🛂 MigraTeam · Contenido"),
    (MARKETING, 6, "💊 Píldora · Contenido"),
    (MARKETING, 54, "📚 Recursos"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    done = json.loads(NEW_STATE.read_text(encoding="utf-8")) if NEW_STATE.exists() else {}
    if not a.apply:
        for chat, old, name in RECREATE:
            print(f"{chat} t{old} -> nuevo '{name}' (ya hecho: {done.get(f'{chat}:{old}')})")
        print("DRY-RUN. Repite con --apply.")
        return 0
    tok = _token()
    for chat, old, name in RECREATE:
        key = f"{chat}:{old}"
        if key not in done:
            r = _call(tok, "createForumTopic", chat_id=chat, name=name)
            if not r.get("ok"):
                print(key, "crear:", _ok(r))
                continue
            done[key] = r["result"]["message_thread_id"]
            NEW_STATE.parent.mkdir(exist_ok=True)
            NEW_STATE.write_text(json.dumps(done, indent=1), encoding="utf-8")
        r1 = _call(tok, "editForumTopic", chat_id=chat, message_thread_id=old, name=f"🗄 Archivado · {name.split(' ', 1)[1]}")
        r2 = _call(tok, "closeForumTopic", chat_id=chat, message_thread_id=old)
        print(f"{key} -> nuevo t{done[key]} · viejo renombrado {_ok(r1, ('TOPIC_NOT_MODIFIED',))} · "
              f"cerrado {_ok(r2, ('TOPIC_NOT_MODIFIED', 'TOPIC_CLOSED'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
