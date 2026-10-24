# Spec-Lite: Hermes de equipo (sin terminal ni ficheros)

Tarjeta t_f77fc36c · Origen: Oscar 24-10 (botón) · Estado: **propuesta, pendiente de OK de Oscar** (no implementada).

## Problema
Hermes (@oscarsaoBot) corre en el PC de Oscar con terminal, ficheros y navegador. En Telegram no se puede limitar por
persona, así que Ingerber y Andrea quedaron con `hermes: false` en `tools/telegram/people.yaml` (hub #131).
Objetivo: que hablen con un Hermes que NO pueda ejecutar comandos ni leer ficheros.

## Opciones
| | A. Mismo bot, enrutado por usuario | B. Bot aparte con perfil propio (recomendada) |
|---|---|---|
| Cómo | Un solo gateway; toolsets distintos según `user_id` | Segundo bot de Telegram + segundo perfil/instancia de Hermes (`HERMES_HOME` propio) |
| Seguridad | Depende de que Hermes soporte toolsets por usuario. **No verificado**; hoy `platform_toolsets.telegram` es global. Un fallo de enrutado da terminal a un no operador | Aislamiento por construcción: el proceso no tiene el toolset, ni el `.env` de Oscar, ni su SOUL/memoria |
| Coste | Bajo si existiera la función | Crear bot en @BotFather (Oscar), un proceso más, un `.env` nuevo |
| Contexto privado | Comparte memoria y SOUL con Oscar: riesgo de fuga | SOUL y memoria propios, sin contexto de Oscar |

**Recomendación: B.** Una barrera de permisos en un solo proceso compartido es justo lo que ya falló como idea.

## Diseño (B)
1. **Perfil `equipo`** (HERMES_HOME separado, p. ej. `%LOCALAPPDATA%\hermes-equipo`), con:
   - `platform_toolsets.telegram`: solo `kanban`, `todo`, `web`, `memory`. Sin `terminal`, `file`, `browser`, `code_execution`, `cronjob`.
   - `SOUL.md` propio: rol de asistente de equipo, sin el contexto privado de Oscar; crea tareas solo por la vía oficial
     (tarjetas `[IDEA]` asignadas a `oscar`, como el DM de ideas); español de España.
   - Memoria propia, vacía al inicio.
   - Kanban: solo crear tarjeta `[IDEA]`; nunca asignar a carriles ejecutables (igual que `can: []` hoy).
2. **Bot nuevo** (token en el `.env` del perfil, nunca en el repo). Oscar lo crea en @BotFather.
3. **Temas 🤖 Asistente**: el bot nuevo se añade al grupo y responde solo en esos temas (config de temas del perfil).
4. **Permisos**: el `TELEGRAM_ALLOWED_USERS` del perfil equipo = personas con `hermes: equipo` en people.yaml.
   - Cambio de esquema: `hermes` pasa de booleano a `true | false | "equipo"` (por defecto, como hoy: `true` = Hermes de Oscar;
     `false` = ninguno; `"equipo"` = Hermes de equipo). Ingerber y Andrea → `hermes: equipo`.
   - `sincronizar_permisos.py` aprende a escribir el `.env`/SOUL de **cada** perfil (`--hermes-home` ya existe) con su lista;
     el Hermes de Oscar deja de contener a quien sea `equipo`.
5. **Arranque**: segundo gateway (`hermes --profile equipo gateway`) y su servicio/tarea programada. Eso es un `.ps1`/tarea
   programada: lo monta Oscar o un carril ops, no este carril.

## Pasos (estimación 1-2 días, 3 PR)
1. (Oscar) Confirmar opción B y crear el bot en @BotFather; pasar el token por vía segura (no por chat).
2. PR hub: esquema `hermes: equipo` en `people.py`/`sincronizar_permisos.py` + tests (dobles, `tmp_path`).
3. Config del perfil: `tools/hermes-config/apply_hermes_equipo.py` (genera config.yaml/SOUL.md del perfil con toolsets acotados,
   idempotente, con modo seco) + test que verifica que `terminal`/`file`/`browser` NO están en los toolsets.
4. (Oscar/ops) Alta del servicio, reinicio y prueba.

## Hecho cuando
- Ingerber y Andrea hablan con el Hermes de equipo en 🤖 Asistente.
- Pedirle que ejecute un comando o lea un fichero falla (herramienta no disponible); prueba manual documentada.
- people.yaml los incluye con `hermes: equipo` y el Hermes de Oscar sigue sin contestarles.

## Riesgos / límites
- Hay que confirmar con la versión instalada de Hermes que (a) existen perfiles/`HERMES_HOME` separados y (b) un toolset
  omitido no se puede reactivar desde el chat. Si (a) falla, la alternativa es otra cuenta de Windows o contenedor.
- `web` permite leer páginas: sin datos privados en el SOUL y sin acceso a la red local sensible no hay fuga, pero es una
  superficie de prompt-injection; se mantiene solo si Oscar lo quiere (decisión abajo).
- «Todo configurable desde la UI»: toolsets y lista de personas deberían poder cambiarse desde el panel; en la v1 quedan en
  YAML (people.yaml) y se anota como seguimiento.

## Decisiones para Oscar
1. Opción A o B (recomendada B).
2. ¿`web` en el toolset de equipo? (recomendado sí).
3. ¿Quién monta el servicio del segundo gateway? (recomendado: Oscar con la instalación guiada).
