---
name: alta-persona-equipo
description: Da de alta (o cambia el acceso de) una persona del equipo en todo el sistema de Oscar - bots de Telegram (carriles, Hermes, Avisos de Oscar HQ), grupos y temas, panel de Oscar HQ y, si aplica, Drive. Úsala cuando Oscar diga «añade a X», «X no puede entrar» o cambie el rol o las marcas de alguien.
---

# Alta de persona del equipo

Cada paso que cambie configuración de Hermes, variables de entorno o accesos se pregunta antes a Oscar con botones. Nunca manejes contraseñas: la persona fija la suya.

## Datos a reunir (preguntar con botones lo que falte)
Nombre · user_id de Telegram (de los logs `Blocked unauthorized user <id>` o con `getChat`) · rol (coo/cmo/marketing/cto…) · marcas (Píldora, MigraTeam, Signal, `*`) · ¿operador? (aprueba en el bot de carriles) · grupo de trabajo (Gestión -1003530490339, Marketing -1004277259008, Desarrollo -1003731090094) · correo (panel y Drive).

## Sitios (todos: si falta uno, la persona «no puede hacer nada» en algún canal)
1. **Hub** `people.yaml` REAL (fuera del repo: `tools/agent-lanes/.state/people.yaml` o `AGENT_LANES_PEOPLE_FILE`; ver `docs/people-yaml-fuera-del-repo.md`): `user_id`, `role`, `brands`, `operador`, `grupo_tareas`. Los ids reales no se commitean; solo la plantilla `tools/telegram/people.example.yaml` va por rama + PR. Después, Oscar añade el id a `TELEGRAM_ALLOWED_CHATS` de `tools/agent-lanes/.env` y la persona envía `/start` al bot de carriles.
2. **Hermes** (`%LOCALAPPDATA%\hermes\.env`): `TELEGRAM_ALLOWED_USERS` (DM) y/o `TELEGRAM_GROUP_ALLOWED_USERS` (solo grupos); `config.yaml` `free_response_chats` si debe responder sin mención. Copia `.bak-<fecha>` y `hermes gateway restart`.
3. **Avisos (Oscar HQ)**: `ohq_telegram_configs` de la marca → `allowed_chat_ids` y `role_routes` (rol y marca por chat o persona), por el panel o la API; nunca tocar a mano el secreto del webhook.
4. **Panel Oscar HQ**: usuario en `ohq_users` (admin/editor/viewer y marcas) desde la UI de usuarios; el inicio con Google usa el correo.
5. **Grupos**: un bot no puede añadir personas. Enlace de invitación de un solo uso (`createChatInviteLink`, `member_limit=1`) solo si Oscar lo pide; si no, que la añada Oscar.
6. **Drive** (si aplica): carpeta de la marca con el rol de la tabla de `docs/infra/drive-carpetas-marcas.md`, con OK.

## Verificación
La persona escribe en su grupo y por DM; comprueba en los logs de cada bot que se reconoce con su rol y marca (skill verificar-bot-telegram).
