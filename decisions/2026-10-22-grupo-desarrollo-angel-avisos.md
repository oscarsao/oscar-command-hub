# Grupo de desarrollo de Angel: carriles por marca y avisos limpios (t_7ed3de09)

Estado: **fase 1 implementada en el PR; la activación en producción queda para Oscar** (ver «Qué falta»).
Fecha: 2026-10-22. Relacionado: t_1ddcd298 (alta de Angel, PR#75), `2026-10-15-arquitectura-bots-equipo.md`.

## Cómo se enruta hoy un aviso (sin cambios)
`TaskNotices.publish` → `TelegramNotifier.send` → `telegram.resolve_target`, por prioridad:
1. `Origen-Telegram: chat=<id> thread=<id>` del cuerpo de la tarjeta, si el chat está en `TELEGRAM_ALLOWED_CHATS`
   (`.env` de agent-lanes) y no es un origen genérico (DM de Oscar, Gestión t5).
2. `telegram:` del carril en `lanes.yaml` (tema de la marca en Gestión: t230 MigraTeam, t231 Píldora).
3. `TELEGRAM_CHAT_ID/THREAD_ID` del `.env` (Gestión t5).

**Conclusión: el enrutado por marca no necesita código nuevo.** Quien decide el destino es la línea
`Origen-Telegram` de la tarjeta, no quién la creó. Las 4 tarjetas piloto (t_5029c75c, t_b638089d, t_5cf6e6de,
t_d4baf0ee, board oscarhq) llevan `chat=-1003731090094 thread=1`; en cuanto ese chat esté permitido, sus avisos
salen en el grupo de Angel y dejan de salir en Gestión t5/t231.

Angel trabaja los carriles igual que Oscar/María: mismo sistema, otro chat de salida. Las marcas del grupo son
Píldora/OscarHQ/Signal; **MigraTeam queda fuera**: sus tarjetas no llevan ese origen y el carril `claude-migrateam`
no se ha tocado.

## Filtro «solo resultado final» (fase 1, implementado)
Nuevo bloque en `lanes.yaml`:

```yaml
telegram:
  final_only:
    - {chat: -1003731090094}   # thread omitido = cualquier hilo
```

En un chat así, `LaneRunner.notify` aplica esta regla (`QUIET_STATES` en `agent_lanes/runner.py`):

| Estado | Grupo de Angel | DM de Oscar |
|---|---|---|
| `running` (claim) | sí («se pidió X») | — |
| `review`, `done` | sí («se entregó Y») | según reglas actuales |
| `needs_input`, `blocked`, `stuck`, `changes` | **no** | sí, con sus botones |

Las vueltas de proceso van al DM de Oscar (que es quien decide) y el aviso de inicio del grupo **no se borra**
(`keep_previous`). Sin la clave `final_only` todo funciona como antes; Gestión no cambia.

## Qué falta (lo hace Oscar; yo no toco Telegram ni `.env`)
1. Añadir `-1003731090094` a `TELEGRAM_ALLOWED_CHATS` en el `.env` de agent-lanes (sin esto el origen se ignora y
   los avisos siguen yendo a Gestión).
2. Crear los temas en el grupo (`createForumTopic` exige que el bot de carriles sea admin con «gestionar temas») y
   fijar los mensajes de referencia. Propuesta: `Desarrollo · Píldora/OscarHQ` y `Desarrollo · Signal`. Si se
   crean temas nuevos, hay que cambiar `thread=1` de las tarjetas piloto o dejarlas en el tema 1 (General).
3. Fusionar este PR y pulsar [🔁 Aplicar] (el runner vivo ejecuta `main`).

## Fases siguientes (no incluidas)
- F2: aviso final de fallo definitivo (reintentos agotados) en el grupo, hoy oculto en el DM.
- F2: enrutado por marca para tarjetas creadas con `/nueva` desde el grupo (hoy basta `Origen-Telegram`).
- Botones de decisión en el grupo: hoy `review/done` pueden llevarlos; la autorización de pulsarlos sigue siendo la
  de `decisions.py` (solo Oscar). Verificar con una tarjeta piloto real.
