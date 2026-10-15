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
| **P** (antes D) | Configuración en panel + BD, **sin variables de Railway ni SQL manual**: `ohq_people` (fuente única de personas), `ohq_tool_audit`, topes diarios, rutas por tema, destinatarios de avisos y `/permisos`; `role_routes.tools` pasa a exclusiones; pantallas en el panel de Oscar HQ. Fundida con la antigua fase D | `claude-oscarhq` | **primero** |
| **A** | Carriles: motivos de exclusión en lenguaje llano con botones (🔧 arreglar falso positivo / 🔁 rebasar), `/falta <proyecto>` (checklist ordenado de la salida), texto libre del DM contestado | `claude-hub` | en marcha |
| **B** | Herramientas de lectura del asistente (§4: handbook, Brand Brain, web, estado de tarjetas) | `claude-oscarhq` | tras P |
| **C** | `ejecutar_crew` y `crear_tarjeta` con tope y botones | `claude-oscarhq` | tras B |
| **F** | **Estado operativo visible desde la nube.** El hub (productor) empuja cada pocos minutos a la tabla `ohq_ops_state` (solo lectura para el resto) un resumen sin secretos: tarjetas, lotes con PRs incluidos/excluidos y motivo en lenguaje llano, PRs abiertos, pasos pendientes de una salida y última auditoría. Consumidores: herramienta `estado_operativo` del asistente y página "Operaciones" del panel, siempre con la antigüedad del dato | productor `claude-hub`; consumidores `claude-oscarhq` | tras P y B |
| **E** | Topología de bots: quién contesta dónde (Hermes en grupos, mudo del bot de Carriles, evitar doble respuesta) | decide Oscar | con los hallazgos de `t_dddcc232` y el bug `t_22de53c2` |

Orden: **P, A (en marcha), B, C, F, E**.

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

## 9. Infraestructura actual y objetivo

Marcas: **V** = verificado en esta revisión (fichero, comando o consulta de solo lectura); **S** = supuesto o tomado de documentación sin comprobar.

### 9.1 Inventario actual

| Componente | Dónde corre | Quién lo usa | Depende de | Con el PC apagado | Cómo se configura hoy |
|---|---|---|---|---|---|
| **Runner de carriles** (`agent-lanes`, integrador, bot @pildora_carriles_bot) | PC de Oscar, tarea programada "agent-lanes runner" (V) | Oscar, María | Hermes (CLI y kanban local), `claude -p`, git, gh, `.env` local | **Parado**: nadie reclama tarjetas, no hay lotes ni botones (V: la decisión de carriles lo dice) | `lanes.yaml`, `.env` y `.state/` en disco |
| **Gateway de Hermes** (bot de Telegram del agente personal) | PC de Oscar, tarea "Hermes_Gateway" (V; estado `running`, 2 plataformas conectadas) | Oscar | `config.yaml`, modelos externos | **Parado** | `config.yaml` de Hermes + scripts `apply_*.py` de `tools/hermes-config` |
| **Kanban de Hermes** (4 tableros: default, oscarhq, migrateam, personal) | SQLite local `kanban.db` en el PC (V) | Oscar, runner, panel | Ficheros locales | **No accesible** | Hermes CLI |
| **Panel del kanban** `kanban.pildoradigital.com` | PC (tarea "Hermes Dashboard") publicado con **túnel de Cloudflare** `pildora-design` (V por `instalar-panel-y-tunel.ps1`) | Oscar y quien tenga enlace | PC encendido + `cloudflared` | **Caído** | `config-design.yml` en `~/.cloudflared` |
| **Monitor / auditor / brief diario** | PC, tareas "Monitor Oscar (alertas)", "Auditor diario carriles", "Resumen diario Hermes" (V) | Oscar | PC | Parado | Scripts en `tools/monitor` y `agent-lanes/brief` |
| **Snapshot del kanban a la nube** | PC, tarea "Kanban snapshot Oscar HQ" ejecuta `scripts/push_kanban_snapshot.py` (V) | El bot de roles (lee la tabla de snapshots en Supabase, V) | PC encendido y `.env` de oscar-hq | **Deja de actualizarse**; el bot responde con datos viejos sin avisar (S sobre la ausencia de aviso) | Script + tarea programada |
| **Oscar HQ API + bot de roles + crews** | Railway, proyecto "Oscar HQ", entorno production (V) | Equipo (Andrea, Ingerber, María), Oscar | Supabase, proveedores de modelos | Funciona | Variables de Railway + tablas (`ohq_telegram_configs.role_routes`, `ohq_system_settings`) + código |
| **Panel de Oscar HQ (dashboard)** | Next.js; despliegue Vercel o Railway (S) | Oscar y equipo | API de Railway | Funciona (S) | `/admin/settings` (clave-valor), página de Telegram (token, chats, webhook) |
| **Supabase** (proyectos: Oscar HQ, MigraTeam, MigraTeam-staging, cartera) | Nube (V) | Todo | — | Funciona | Migraciones SQL (`supabase/migrations`; Alembic en MigraTeam) |
| **Webs** pildoradigital.com, recursos.…, migrateam.com | Vercel / Railway (V por CLAUDE.md) | Público | Git push | Funciona | Repos en GitHub |
| **MigraTeam** backend | Railway por push a `master`; frontend Vercel (V) | Clientes | GitHub | Funciona | Env en Railway/Vercel |
| **Handbook** | Repo privado GitHub, PR-only (V) | Humanos; copia manual a `memory/brands` | — | Funciona | Git |
| **Mini PC Minisforum** | Comprado el 24-09 para túneles, cron y CI (V: decisión escrita). **Sin evidencia de que ya aloje algo** (S) | — | — | — | — |

### 9.2 Diagrama actual

```
   Equipo (Andrea, Ingerber, María)                Oscar
        │  Telegram                                  │ Telegram / Claude Code / panel
        ▼                                            ▼
 ┌──────────────────┐   lee kanban (copia)   ┌──────────── PC DE OSCAR (debe estar ENCENDIDO) ────────────┐
 │  Bot de roles    │◄───────────────────────│  runner carriles ─ integrador ─ bot @carriles               │
 │  (Railway)       │   snapshot (tarea PC)  │  Hermes gateway ─ kanban.db (SQLite) ─ panel kanban         │
 │  crews, Brand    │                        │  monitor / auditor / brief ─ cloudflared (túnel)            │
 │  Brain, panel    │                        └────────────────────────────┬────────────────────────────────┘
 └───────┬──────────┘                                                      │ git push, gh, railway up
         ▼                                                                 ▼
   Supabase (Oscar HQ)  ◄──────────────────────────────────────  GitHub ─► Railway / Vercel
```

### 9.3 Dependencias del PC encendido y duplicidades (hallazgos)

1. **Todo el plano de control vive en un PC**: carriles, integrador, botones, kanban, panel y túnel. Si se apaga, el equipo no ve nada ni puede pedir nada (V).
2. **El bot del equipo lee una copia del kanban** que sólo se refresca mientras el PC está encendido; no hay indicador de antigüedad en la respuesta (S).
3. **Personas duplicadas**: `tools/telegram/people.yaml` (hub) y `role_routes.people` (BD de oscar-hq, inexistente hoy, V).
4. **Configuración repartida en cinco sitios**: `lanes.yaml`, `.env` del hub, `config.yaml` de Hermes, variables de Railway y `role_routes`/`ohq_system_settings`; sólo la última tiene panel y aun así no cubre rutas ni personas (V).
5. **Constantes en el código**, p. ej. el destino de las alertas de leads (V: `telegram_leads.py`).
6. **El mini PC no aloja nada verificado**, aunque se compró para sacar estos servicios del portátil.

### 9.4 Infraestructura objetivo

Principio: **la nube es la fuente de verdad del estado y de la configuración; el PC es sólo un trabajador que ejecuta y empuja estado.**

1. **Estado operativo en la nube (fase F).** El hub (productor, carril `claude-hub`) empuja cada pocos minutos un resumen **sin secretos** a la tabla de Supabase `ohq_ops_state` de oscar-hq, de solo lectura para el resto: tarjetas, lotes con PRs incluidos y excluidos y su motivo en lenguaje llano, PRs abiertos, pasos pendientes de una salida y la última auditoría. Ya existe el patrón (`push_kanban_snapshot.py`); se generaliza. Los consumidores viven en oscar-hq (carril `claude-oscarhq`): la herramienta `estado_operativo` del asistente del equipo y la página "Operaciones" del panel. **Siempre con la antigüedad del dato**; si el PC está apagado, lo dicen.
2. **Configuración única en BD + panel (fase P)**: personas, rutas por tema, herramientas (exclusiones), topes, destinatarios de avisos y ajustes de crews, en `ohq_people`, `ohq_system_settings` y `role_routes`, con pantallas de panel y sin variables de Railway ni SQL manual. `people.yaml` y `lanes.yaml` pasan a semilla/defecto.
3. **Secretos sólo en Railway o cifrados en BD** (como ya se hace con `bot_token_encrypted`). Ningún secreto en el panel en claro ni en ficheros del repo.
4. **Ningún componente que el equipo necesite depende del PC.** Lo que sí puede seguir en el PC (ejecutar código con `claude -p`, worktrees, gh) es un trabajador: apagado, el estado dice "carril offline desde hace X" y las peticiones del equipo quedan en cola en la nube.
5. **Mover los servicios no críticos para el equipo al mini PC** (encendido 24/7): runner, integrador, túnel y panel del kanban. Es una migración de hosting; se decide aparte.
6. **Vigilancia**: el monitor publica un latido en `ohq_ops_state`; Railway alerta si se retrasa.

### 9.5 Diagrama objetivo

```
 Equipo ──Telegram──► Asistente del equipo (Railway) ──lee──► Supabase: config + ohq_ops_state (con antigüedad)
                          │ crews · Brand Brain · handbook · webs                  ▲
                          │ pide trabajo (tarjeta a nombre de Oscar)               │ empuja estado + latido (fase F)
 Oscar ──panel / Telegram─┴─► Panel Oscar HQ (config, personas, permisos, "Operaciones")
                                                                          ┌───────┴────────── TRABAJADOR (PC o mini PC) ──────┐
 Oscar ──Telegram──► Bot de control (carriles) ◄──── acciones y botones ──│ runner · integrador · Hermes · kanban local        │
                                                                          └────────────────────────────────────────────────────┘
```

### 9.6 Riesgos de la migración

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Estado en la nube desactualizado y el bot lo da por bueno | Respuestas erróneas | Mostrar siempre "actualizado hace X"; pasado un umbral, decir que el PC está offline |
| Duplicar la fuente de configuración durante la transición | Comportamientos distintos entre hub y oscar-hq | Una sola lectura de BD con respaldo a fichero; retirar el fichero tras verificar |
| Mover el hub al mini PC rompe rutas absolutas (`C:/Users/oscar/...`) | Carriles no arrancan | Parametrizar rutas en `lanes.yaml`; probar con un carril antes de mover el resto |
| Secretos copiados al mini PC | Superficie de ataque mayor | Secretos por carril con acceso mínimo; rotar tokens al migrar |
| Exponer el panel sin el túnel actual | Panel inaccesible o expuesto | Mantener Cloudflare Tunnel con autenticación; no abrir puertos |
| Tablas nuevas sin RLS | Fuga de datos entre marcas | RLS activo sin políticas y acceso por service_role, como el resto de tablas `ohq_*` |
| El resumen empujado filtra información sensible | Fuga a la nube | Productor con lista blanca de campos; sin cuerpos de tarjeta ni rutas locales; revisión en el PR de la fase F |

### 9.7 Orden recomendado

P (config en BD y panel; incluye personas y permisos), A (en marcha), B, C, F, E. Detalle en §6.
