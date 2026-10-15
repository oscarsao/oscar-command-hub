# Arquitectura de los bots del equipo — revisión y propuesta (15-10-2026)

Estado: **propuesta para decidir**. Revisión pedida por Oscar tras leer las conversaciones reales de Andrea e Ingerber
(6–15 oct) y el estado de los tres bots. Complementa la auditoría `t_dddcc232` (flujo de aprobaciones), que sigue en
marcha: cuando termine, sus hallazgos se integran en §6 sin rehacer este mapa.

## 1. Qué hay hoy (verificado)

| Bot | Dónde corre | Quién lo usa | Qué sabe y qué puede hacer |
|---|---|---|---|
| **Carriles** (`@pildora_carriles_bot`, hub) | PC de Oscar | Oscar, María | Solo comandos y botones (`/estado`, `/lote`, `/decisiones`…). El texto libre del DM se ignora o abre una tarjeta `[IDEA]`; en el grupo de Marketing ignora el texto. No tiene LLM conversacional salvo "Explícame más" en avisos de decisión. |
| **Rol / "CMO"** (oscar-hq) | Railway | Andrea, Ingerber, Oscar | Responde texto libre. Contexto: solo títulos de kanban, ejecuciones, último despliegue y commits (`telegram_context.py`). Herramientas: `generar_imagen`, y tras el lote 2 `redactar_texto`, `avisar_a_oscar`. **No** ejecuta crews, **no** abre webs, **no** consulta el Brand Brain, **no** lee el handbook en vivo (solo copia de `memory/brands`, sincronizada a mano). Memoria: en proceso 2 h (arreglado en #110). |
| **Hermes** (gateway, PC de Oscar) | PC de Oscar | Oscar | El más capaz: `file`, `terminal`, `web`, `browser`, `kanban`, `memory`, `skills`; pero `no_mcp`. En grupos exige mención (`require_mention`); solo responde libre en 3 temas del grupo de Gestión. Rechaza DMs de quien no está autorizado (Andrea/Ingerber). |

Consecuencia: **el equipo habla con el bot que menos puede**, y el que más puede no les contesta. De ahí las quejas
("solo un bot responde", "no tienen acceso al handbook, a los crews, a las webs, al Brand Brain").

## 2. Fallos de arquitectura (no de código)

1. **Capacidades en el bot equivocado.** Crews, Brand Brain, webs y handbook viven en oscar-hq pero el bot de oscar-hq
   no los llama. Hermes los podría alcanzar pero no atiende al equipo.
2. **Personas duplicadas.** `tools/telegram/people.yaml` (hub) y `role_routes.people` (oscar-hq) describen a las mismas
   personas y no se leen entre sí. En producción `role_routes` **no tiene `people`**: el bot no sabía quién escribía.
3. **Permisos como prohibición, no como política.** `role_routes[...].tools` es una lista blanca por tema y los roles
   de `people.yaml` tienen capacidades fijas en código. Bloquea antes de que haya un riesgo real.
4. **El bot cuenta, no guía.** Los avisos listan el motivo en crudo ("posible secreto…", "choca con master") sin
   saber si es un falso positivo ni qué botón pulsar. Nada encadena los pasos de una salida (fusionar → SQL →
   variables → desplegar).
5. **Sin estado compartido.** El bot del equipo no ve el estado de integración (lote, PR excluido, SQL pendiente), que
   vive en `.state/` del PC de Oscar.

## 3. Principios propuestos

- **Una puerta por audiencia.** Equipo → *un* asistente conversacional (el de oscar-hq). Oscar/María → control
  (Carriles) y Hermes. Ningún bot "se hace el mudo": si algo no es suyo, lo dice y lo deriva con `avisar_a_oscar`.
- **Abierto por defecto, con dos raíles.** Todo miembro conocido puede *leer* todo y *generar* contenido. Solo se
  frena con botón de Oscar lo que tiene **efecto externo o irreversible**: enviar correo, publicar, desplegar,
  fusionar, tocar variables de entorno, borrar, gasto fuera de tope. Lo demás no se bloquea: se **registra**
  (quién, qué, coste) y se acota con **topes diarios** por persona, no con listas de herramientas.
- **Fuente única de personas.** Una tabla `ohq_people` (id Telegram, nombre, rol, marca habitual, tope diario).
  Hub y oscar-hq la leen; `people.yaml` pasa a ser semilla.
- **Decir la verdad sobre lo que se vio.** Ninguna afirmación de verificación sin herramienta en el turno (regla ya en
  #113). Las comprobaciones reales (¿está GTM en la web?) se hacen con una herramienta, no con el modelo.

## 4. Arquitectura objetivo (herramientas del asistente del equipo)

Todas de **solo lectura salvo las marcadas**; se registran en el registro de herramientas de `telegram_tools.py`.

| Herramienta | Qué hace | Cómo | Raíl |
|---|---|---|---|
| `leer_handbook(ruta)` | Lee `pildora-handbook` bajo demanda | API de contenidos de GitHub, token de solo lectura, caché corta | — |
| `consultar_brand_brain(marca, pregunta)` | Estilo, paleta, tono, mensajes | Llamada interna a `services/brand_brain.py` | — |
| `ver_web(url)` | Descarga HTML de dominios propios (pildoradigital.com, migrateam.com, recursos.…) y comprueba cosas concretas (GTM, título, enlaces) | `fetch` con lista de dominios propios | — |
| `estado_tarjeta(id\|texto)` | Dice en lenguaje llano en qué punto está una tarjeta/PR | Lee el panel de kanban | — |
| `ejecutar_crew(nombre, input)` | Lanza un crew existente y devuelve el resultado al hilo | Función interna de `routes/crews.py`, con tope de coste | **gasto** (tope diario) |
| `crear_tarjeta(titulo, cuerpo, marca)` | Pide trabajo a un carril | Hermes; queda a nombre de Oscar para aprobar | **efecto** (botón Oscar) |
| `avisar_a_oscar` | Ya existe (#111) | — | — |

## 5. Permisos y roles flexibles

- Tabla `ohq_people(telegram_id, nombre, rol, marca, tope_diario_usd, activo)` + tabla `ohq_tool_audit` (persona, herramienta,
  coste, resultado). El rol solo da la **marca por defecto** y el **tope**; no recorta herramientas.
- Comando `/permisos` (solo Oscar) para subir/bajar un tope, activar/desactivar a una persona o añadir una nueva en
  segundos, desde Telegram.
- `role_routes[...].tools` deja de ser lista blanca: pasa a ser **lista de exclusiones** (vacía por defecto).
- Los raíles (§3) viven en código, no en configuración, para que no se puedan quitar por error.

## 6. Mapa de tarjetas

| Fase | Contenido | Carril | Cuándo |
|---|---|---|---|
| **A** | Carriles: motivos de exclusión en lenguaje llano con botones (🔧 arreglar falso positivo / 🔁 rebasar), `/falta <proyecto>` (checklist ordenado: fusionar → SQL → variables → desplegar), texto libre del DM contestado con el estado real | `claude-hub` | **ya** |
| **B** | Herramientas de lectura del asistente (§4: handbook, Brand Brain, web, estado) | `claude-oscarhq` | tras el lote 2 (toca `telegram_bot.py`/`telegram_tools.py`) |
| **C** | `ejecutar_crew` y `crear_tarjeta` con tope y botones | `claude-oscarhq` | tras B |
| **D** | `ohq_people` + `ohq_tool_audit` + `/permisos`; `role_routes.tools` pasa a exclusiones | `claude-oscarhq` | tras B (puede ir en paralelo con C si no tocan los mismos ficheros) |
| **E** | Topología de bots: quién contesta dónde (Hermes en grupos, mudo del bot de Carriles, evitar doble respuesta) | decide Oscar | con los hallazgos de `t_dddcc232` y el bug `t_22de53c2` |

Máximo 3 flujos de código activos a la vez (regla del hub).

## 7. Riesgos y decisiones abiertas

- **Coste de `ejecutar_crew`**: sin tope diario por persona un crew mal pedido es caro. Propuesta inicial: tope por
  persona y día, ajustable con `/permisos`.
- **Token del handbook**: de solo lectura y limitado a `oscarsao/pildora-handbook`; el handbook sigue siendo PR-only.
- **Datos de clientes en `ver_web`/crews**: pasan por `_scrub` igual que el resto de textos.
- **Pendiente de decidir (Oscar):** ¿se silencia al bot de Carriles en el grupo de Marketing o responde a texto libre
  con el estado? (fase E).

## 8. Qué NO cambia

Producción (fusionar a `main`/`master`, desplegar, `alembic`/SQL, variables de entorno, borrados) sigue siendo **solo de
Oscar con botón**, como en `~/.claude/CLAUDE.md`. Esta propuesta abre lo demás.
