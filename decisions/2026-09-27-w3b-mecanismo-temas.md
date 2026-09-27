# W3b (27-09-2026): cómo se calla Hermes fuera de Gestión t5

Tarjeta: `t_6ded7e3e` (board `oscarhq`). Implementa el punto 3 de "Cambios técnicos" en `2026-09-27-telegram-grupos-temas.md`.

## Objetivo
- (a) En **Gestión · Operaciones-Código** (chat `-1003530490339`, thread `5`) Hermes actúa normal: intake a tareas.
- (b) En el resto de temas **no responde**, salvo si le mencionan (o si responden a un mensaje suyo).
- (c) Memoria separada por tema.

## Mecanismo elegido: filtro nativo del adaptador de Telegram, sin perfil nuevo
Se añaden dos claves al bloque `platforms.telegram` del `config.yaml` del perfil default:

```yaml
platforms:
  telegram:
    enabled: true
    require_mention: true
    free_response_topics: "-1003530490339:5"
```

- `require_mention: true`: en los grupos, Hermes solo procesa un mensaje si hay @mención, `/comando@bot`, respuesta a un mensaje suyo o palabra clave.
- `free_response_topics`: excepción para Gestión t5, donde procesa todo.
- **Los DM no se filtran nunca.**

### Evidencia (hermes-agent 0.21.4)

**Filtro del adaptador:** `plugins/platforms/telegram/adapter.py:6353-6390` (`_should_process_message`).
- 6366-6367: DM → siempre pasa.
- 6380-6381: si el par chat:tema está en `free_response_topics`, pasa. Esto se evalúa **antes** del filtro de mención.
- 6386-6390: con `require_mention`, solo pasa si responden al bot o lo mencionan (o si hay palabra clave).

**Formato de las claves:**
- `adapter.py:5733-5735`: `require_mention` (env `TELEGRAM_REQUIRE_MENTION`, por defecto false).
- `adapter.py:5761-5773`: `free_response_topics` con el formato `<chat_id>:<thread_id>`. El tema General se escribe `1`.

**Cómo llega el YAML al adaptador:**
- `adapter.py:7208-7221`: el hook `_apply_yaml_config` pasa el YAML a env + `extra`. **El env gana sobre el YAML.** Hoy el `.env` no define `TELEGRAM_REQUIRE_MENTION` ni `TELEGRAM_FREE_RESPONSE_TOPICS`; se comprobó por nombre, sin leer valores.
- `adapter.py:7238-7240` y `7251`, `7270`: el hook trata ambas claves.
- `gateway/config_loader.py:182-191`: el bloque anidado `platforms.telegram` es válido cuando no hay un `telegram:` de nivel superior.
- `gateway/platforms/_shared.py:151-167`: las listas se unen con comas. Aun así se usa un string para no depender de eso.

### Por qué no `gateway.profile_routes` hacia un perfil `grupos`

**Qué hace `profile_routes`:** `gateway/profile_routing.py`.
- 1-11 y 55-101: enruta un mensaje ya aceptado a otro perfil, por `user_id`/`thread_id`/`chat_id`/`guild_id`. Gana la especificidad mayor: 16/8/4/2 (70-72, 169).
- Requiere `gateway.multiplex_profiles: true`, que ya está activo. Si la ruta apunta a un perfil inexistente, se rechaza el mensaje (`website/docs/user-guide/multi-profile-gateways.md:841-845`).

**Por qué no sirve aquí:**
- **No resuelve (b).** El filtro de mención se aplica en el adaptador del bot que recibe, con su propio `config.extra` (`adapter.py:6353`), **antes** de enrutar. El bot de Hermes es el adaptador del perfil default, así que un `require_mention` puesto en un perfil `grupos` no se evaluaría nunca.
- **Duplica cosas.** Un perfil nuevo necesitaría su propio `.env` de modelo, una copia de la skill y del `SOUL.md`, y su propia `memories/`.
- **Complica los avisos del kanban.** La entrega a perfiles enrutados tiene reglas extra (`website/docs/user-guide/features/kanban.md:1299-1309`).

Conclusión: no se crea perfil. `gateway.profile_routes` sigue siendo `[]`.

### (c) Memoria por tema

**Historial de conversación:** ya va separado por tema, sin tocar nada.
- `gateway/session.py:673-714` (`build_session_key`): la clave incluye `chat_id` y `thread_id` (`agent:main:telegram:group:<chat>:<thread>`).
- 699-701 y `gateway/config.py:596-597`:
  - `group_sessions_per_user: true` (ya está en `config.yaml`) aísla por participante solo fuera de hilos.
  - `thread_sessions_per_user: false` (por defecto) hace que un tema sea **una sesión compartida** por sus participantes.
- Resultado: Gestión t5, Marketing t5 y el DM son tres conversaciones distintas.

**Memoria persistente:** es del perfil, no del tema.
- `MEMORY.md` y `USER.md` viven en `<HERMES_HOME>/memories` (`tools/memory_tool.py:38-40`) y se comparten entre el DM y todos los temas.
- Límite conocido y aceptado. La skill ordena guardar lo propio de un tema en la tarjeta del kanban, no en la memoria.

### Qué ve Hermes del origen (contrato `Origen-Telegram`)

**En el prompt:**
- La línea `Source` muestra el **nombre del grupo**, no el `-100…`, más `thread: N` (`gateway/session.py:115-121` y 395-402).
- `redact_pii` está desactivado: comentado en `config.yaml`. Telegram además está en `_PII_SAFE_PLATFORMS` (`session.py:188-196`).
- Por eso la skill trae una tabla fija de nombre de grupo → `chat_id` y prohíbe adivinar.

**Comprobación cruzada para W1b:**
- `kanban_create` suscribe automáticamente la sesión de origen a los eventos de la tarea, con los IDs exactos de chat y tema (`tools/kanban_tools.py:1073-1116`, `_resolve_notify_target`).
- **Riesgo de coordinación con W1b:** el notificador de Hermes y el runner pueden publicar **los dos** el aviso de fin o bloqueo en el mismo hilo.
- Si molesta, hay dos salidas:
  - desactivar `kanban.auto_subscribe_on_create`;
  - que el runner solo publique inicio y avance.

## Cambios hechos (W3b)
- **Skill** `hermes/skills/autonomous-ai-agents/oscar-multi-agent-orchestration/SKILL.md` (backup `.bak-20260927-w3b`): sección nueva "Protocolo de intake por temas".
  - Qué mensajes llegan.
  - Tabla grupo → `chat_id`.
  - Tabla repo → `board` + `assignee`.
  - Primera línea del cuerpo: `Origen-Telegram: chat=<chat_id> thread=<thread_id>`. Desde el DM, se usa Gestión t5.
  - `idempotency_key`.
  - Ack en el hilo.
  - `clarify` con opciones si el repo es ambiguo.
  - Límites de memoria.
  - Se quitó la regla antigua "otro repo → sin assignee": el schema de `kanban_create` exige `assignee` (`tools/kanban_tools_schemas.py:505`).
- **`hermes/SOUL.md`** (backup `.bak-20260927-w3b`): dos líneas, sobre el filtro del gateway y el contrato `Origen-Telegram`.
- **Script** `tools/hermes-config/apply_w3b_config.py`:
  - una sustitución exacta sobre el ancla `platforms:/telegram:/enabled: true` (debe aparecer 1 vez);
  - comprueba que ni el `.env` ni el entorno pisan las claves (solo por nombre);
  - compara el YAML completo: antes = después menos las 2 claves;
  - hace backup `config.yaml.bak-20260927-w3b` y escritura atómica;
  - imprime solo OK/FAIL;
  - es idempotente y tiene `--revert`.
  - **No se ejecutó contra el `config.yaml` real** (lo hace Oscar). Se probó en un config sintético: dry-run, apply, re-apply idempotente, revert y aborto si el `.env` pisa el valor.

## Cómo se aplica (Oscar)
```
! ~/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe ~/oscar-command-hub/tools/hermes-config/apply_w3b_config.py
! ~/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe ~/oscar-command-hub/tools/hermes-config/apply_w3b_config.py --apply
! ~/AppData/Local/hermes/bin/hermes gateway restart
```
El primero es un dry-run: todo debe salir `OK`.

## Cómo se prueba
1. **Gestión t5**, sin mención: "¿qué carriles están libres?" → Hermes responde.
   - Con un encargo de código: "arregla X en oscar-hq" → `apuntado t_xxx en carril claude-oscarhq (board oscarhq)`.
   - La tarjeta empieza por `Origen-Telegram: chat=-1003530490339 thread=5`.
2. **Marketing t5 (MigraTeam)**, sin mención: "hola" → Hermes **calla** (solo responde el bot de roles).
3. **Marketing t5**, con `@<bot de Hermes> ¿qué hay en el carril de migrateam?` → Hermes responde en ese hilo.
4. **DM:** sigue igual, sin filtro.
5. **Log:** con el gateway en debug, los mensajes ignorados no generan turno. Se puede comprobar en `state.db`: no hay sesión nueva `...:-1004277259008:5` tras el paso 2.

## Cómo se revierte
- `! <python del venv> ~/oscar-command-hub/tools/hermes-config/apply_w3b_config.py --revert` y después `hermes gateway restart`.
- Alternativa: restaurar `config.yaml.bak-20260927-w3b`. Se pierde cualquier cambio posterior del config.
- Skill y SOUL: restaurar sus `.bak-20260927-w3b`.

## Pendiente / fuera de alcance
- **Andrea no puede activar a Hermes** ni mencionándolo: `TELEGRAM_ALLOWED_USERS` solo incluye a Oscar. Si se quiere, lo decide Oscar (cambio en `.env`, pendiente de su `user_id`).
- **Bot de roles:** que en Gestión t5 solo responda si se le menciona. Es W4 (repo oscar-hq).
- **Opcional:** `bots_require_mention: true`, si algún día el bot de roles y Hermes se contestan en bucle. Hoy no hace falta.
- **Opcional:** `group_topics`, para cargar la skill automáticamente en t5 (`adapter.py:6989-7011`). No se activa en esta iteración, para mantener el cambio de config al mínimo. Si Hermes en t5 no aplica el protocolo de intake, es el siguiente paso.
