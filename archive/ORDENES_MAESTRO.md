> Archivado 2026-09-27: Sesión Maestra sustituida por carriles; ver decisions/2026-09-27-arquitectura-carriles.md

# ORDENES_MAESTRO.md — coordinacion en vivo del sistema multi-agente

> Mantenido por la SESION MAESTRA (unico punto de contacto con Hermes/Oscar).
> Cada ventana de trabajo debe leer este archivo al arrancar y revisarlo cada
> vez que termine una tarea o quede bloqueada, ANTES de considerarse "libre".

## Regla de oro
- Hermes/Oscar hablan UNICAMENTE con la sesion maestra.
- La maestra escribe aqui las instrucciones para cada ventana de trabajo.
- Cada ventana de trabajo escribe aqui su estado (terminado/bloqueado/pregunta)
  en vez de esperar a que alguien le escriba en su terminal.
- Nunca modificar un test para que pase sin arreglar el bug real. Si hay una
  decision de negocio real sin resolver, documentala aqui, no la infieras.

## Directiva global de la sesion maestra (2026-09-24)
- Al terminar la tarea actual o quedar bloqueada, cada ventana debe releer este
  archivo y anadir aqui un estado breve: rama, commit, pruebas, cambios locales,
  resultado y siguiente accion propuesta.
- No iniciar una tarea nueva por cuenta propia si no esta asignada aqui.
- No hacer merge ni deploy a produccion sin confirmacion expresa de Oscar.
- Las preguntas para Oscar se consolidan aqui; la sesion maestra las eleva en
  bloque para no interrumpir varios workstreams a la vez.

## Snapshot de arranque de la sesion maestra (2026-09-24)
- `migrateam-mosq02`: limpio; `a035c569` (sincronizado con `origin/develop`).
- `oscar-hq-auditoria-ramas`: limpio; `b56778a1`. Es el mismo worktree para
  "Fusiona ramas" y "Audita ramas"; tratarlo como un unico frente.
- `oscar-hq-signal-corridas`: limpio; `e069cb5b` (proteccion de estado terminal).
- `oscar-hq-crews`: limpio y alineado con remoto; `1100436d`.
- `oscar-hq-triaje-h`: limpio; `4fefb8ce`.
- `oscar-hq-scraper-crm`: limpio; aun en el commit base `4cb354fa`.
- `Scraper`: rama 40 commits por delante del remoto; commit `d6d45a2`; conserva
  tres carpetas `.pytest-tmp-h118*` no versionadas, que no deben borrarse sin
  verificar antes si siguen en uso.
- `pildora-handbook`: limpio y alineado con remoto; `2139927`.

## Sesiones de trabajo activas (2026-09-24, tarde)
| Ventana | Worktree | Rol actual |
|---|---|---|
| MOSQ-02 publicacion y monitoreo | migrateam-mosq02 | Integradora MigraTeam |
| Fusiona ramas prioritarias en master | oscar-hq-auditoria-ramas | Fusion 3 ramas nuevas + 11 sueltas |
| Audita ramas sueltas | oscar-hq-auditoria-ramas | (verificar si duplica la anterior) |
| Analisis de cierre de ejecucion en crew runner | oscar-hq-signal-corridas | Fix timeout falso, Fast-Track aplicado |
| Herramienta MCP huerfana en Crews | oscar-hq-crews | Verificacion end-to-end en navegador |
| Retoma el triaje de hallazgos | oscar-hq-triaje-h | Cierre de lote H-094 a H-119 |
| Revisa y commitea H-118 | Scraper | Cerrado, libre |
| Prepara propuesta comercial | tattoo-web | Cerrado, libre (propuesta entregada) |
| (nueva, Claude) | pildora-handbook | Cerrar drift handbook<->oscar-hq |

## Correccion de rol (2026-09-24, tarde)
Antes se designaron 3 ventanas como "lider por proyecto" hablando directo con Hermes.
Eso se revierte: NINGUNA ventana de trabajo habla con Hermes/Oscar directamente.
Solo la SESION MAESTRA (esta, en oscar-command-hub) habla con Hermes. Las 3 ventanas
de "MOSQ-02 publicacion", "Fusiona ramas" y "Analisis de cierre de ejecucion" pasan a
ser ventanas de trabajo normales bajo tu coordinacion.

## PEDIDO DE OSCAR (2026-09-24, tarde) -- URGENTE, responder en este archivo
Oscar quiere ver "la pelicula completa": un PLAN INTEGRAL por proyecto (MigraTeam,
Oscar HQ, Pildora, Signal, Scraper-CRM) con la lista larga de tareas cortas y largas,
priorizando Codex por eficiencia de tokens/agentic, reservando Claude Code sobre todo
para integracion/publicacion y trabajo que requiera mas "juicio" sostenido.

ACCION: escribe tu plan en una seccion nueva mas abajo "## PLAN MAESTRO (tu respuesta)"
con: (1) lista de tareas por proyecto, marcando cada una CORTA/LARGA y CODEX/CLAUDE
recomendado, (2) que se puede lanzar ya vs que necesita decision de Oscar primero,
(3) que ventanas actuales se pueden cerrar porque ya terminaron. Hermes revisara este
archivo, no tu terminal directamente.

## Movimientos de Hermes (2026-09-24, tarde, fuera de esta sesion)
- Lanzada `oscar-hq-scraper-crm` (Codex, tarea larga: pipeline Scraper->CRM->Outreach),
  estaba preparada desde la manana pero nunca arranco. Anadela a tu snapshot.
- Lanzada `dev/propuestas/pildora-ventas` (Codex, tarea larga: oferta servicios,
  pipeline prospeccion, caso Multiverse, propuesta tattoo). Sin repo git, carpeta
  de trabajo de negocio. Confirma en tu plan si esto se cruza con `pildora-admin`
  o `pildora-signal-console` (repos que existen pero no se han revisado hoy).

## Cierres confirmados por Hermes/Oscar (2026-09-24, 19:xx)
- MOSQ-02: CERRADO. Promocion a produccion confirmada (master@3160ae6d).
- Signal: PR #10 fusionado a master.
- PR #11 (Crews): FUSIONADO a master (19:42) por confirmacion de Oscar.
- PR #12 (Scraper-CRM): FUSIONADO a master (19:42) por confirmacion de Oscar.
  Kill-switch de outreach sigue activo -- no envia nada real todavia.

## Tareas Kanban listas para delegar (board oscarhq, status=ready, sin asignar)
- PROYECTO-AGENTES (t_2f07828a): rescate sistema de Crews + patrones de sesiones
- AUDITORIA-RAMAS (t_2c710d6a): 11 ramas sueltas (ya en marcha en oscar-hq-auditoria-ramas)
- SCRAPER-CRM-LEADS (t_992e42ac): captacion/CRM leads (ya en marcha en oscar-hq-scraper-crm)
- PILDORA-VENTAS (t_63a9b340): oferta de servicios digitales, pipeline prospeccion
- MIGRATEAM-360 (t_55600b16): revision integral producto/ventas/legal/admin/marketing
- LEGAL-LLC (t_f90da954): fuera de alcance para agentes -- requiere CPA/Sunbiz, solo Oscar

## Pendiente de la maestra al arrancar
1. Leer este archivo y el estado de cada worktree (git log/status).
2. Confirmar con cada ventana de trabajo (via este archivo, no via Hermes) que
   siga esta convencion de ahora en adelante.
3. Reportar a Hermes un resumen consolidado, no ventana por ventana.

## RECORDATORIO DE HERMES (2026-09-25, madrugada) — han pasado ~11h sin tu plan
Sigues sin escribir la seccion "## PLAN MAESTRO (tu respuesta)" pedida en la seccion
de arriba ("PEDIDO DE OSCAR"). Si estas bloqueada por algo, escribelo aqui ahora mismo
en una seccion "## BLOQUEO MAESTRA" en vez de seguir en silencio. Si ya lo tienes listo
pero no lo has volcado aqui, hazlo ya. Oscar esta esperando ver la pelicula completa.

Estado real verificado por Hermes esta madrugada (git, no ventanas):
- MOSQ-02: CERRADO por Hermes/Oscar (06:xx). Working tree limpio, HEAD d0806966ea,
  claim y alcance completos. Sesion instruida a terminar.
- oscar-hq-auditoria-ramas: descarto 8 ramas ya integradas en master, verificando
  inventario exacto antes de borrar (correcto, no forzar). Sigue viva.
- oscar-hq-signal-corridas: PR #10 fusionado hace horas, avisado que puede cerrar.
  Ventana sigue abierta, sin nueva tarea asignada -- revisar si cerro sola.
- oscar-hq-crews (Herramienta MCP): PR #13 fusionado a master por Hermes/Oscar
  (2026-09-25 06:42, squash+delete-branch). B3.4 verificado end-to-end completo.
- tattoo-web: CERRADO. Propuesta comercial entregada (PROPUESTA_TATTOO_WEB.md),
  la ventana termino la sesion sola.
- oscar-hq-triaje-h: CERRADO. Batch H-094 a H-119 confirmado (4fefb8ce, "close
  triaged H batch"), la ventana termino la sesion sola.
- pildora-ventas: causa raiz encontrada -- el briefing de Hermes de anoche se
  trunco por compresion de contexto, nunca llego completo. Reenviado corto y
  claro a las 06:2x (propuesta comercial Pildora + pipeline prospeccion + caso
  Multiverse). Ahora trabajando de verdad.
- PILDORA-VENTAS/PROYECTO-AGENTES/AUDITORIA-RAMAS/SCRAPER-CRM: siguen activas,
  sin bloqueo nuevo detectado.

## Ramas confirmadas como descartables (Auditoria-ramas, 2026-09-25 08:50)
Estas ocho ramas estan completamente contenidas en `master`. Cada una entro primero en
`integ/2026-09-22` mediante el commit indicado y el conjunto completo llego a `master`
mediante `71c8676cde0caeac1f7ee97cf8d8094f62cf1677` (merge: 8 ramas - coste real de crews,
presupuestos, Brand Brain, Signal, emails, CRM). Sus referencias local/remota se pueden
borrar sin perder codigo:
1. `feature/budget-guard-hard-stop` — via `9116461d`
2. `feature/admin-budgets-page` — via `a01d823f`
3. `feature/coste-real-crews` — via `7c36389f`
4. `feature/brand-brain-editable` — via `51aad5fe`
5. `feature/signal-fuentes-configurables` — via `fbd43f05`
6. `feature/emails-diseno-y-variables` — via `5b44b123`
7. `feature/crm-editar-campos-contacto` — via `5ac5dff8`
8. `feature/agentes-escritura-crm` — via `20714461`

Nota: el intento de borrado automatico de estas ramas fallo por permisos del sandbox
(no puede escribir referencias .git ni usar SSH/HTTPS para push --delete). No es un
riesgo de perdida de codigo -- son solo referencias sueltas, se pueden borrar a mano
mas tarde con `git push origin --delete <rama>` + `git branch -d <rama>` desde fuera
del sandbox cuando convenga.

## PENDIENTE REAL DE REVISION (2026-09-25, manana) -- PRs sin fusionar
- MigraTeam (OCR-PDF-and-images): #74 "Tanda 21" (22-sep), #71 "Tanda 18" (22-sep),
  #16 draft Vercel Analytics (3-sep) -- ninguno tocado hoy, requieren revision de Oscar.
- Pildora Handbook: 8 PRs abiertos, el mas viejo del 28-jul -- acumulacion real,
  necesita sesion dedicada, no es urgente hoy.
- Pildora Website/Recursos: 3 PRs menores (Vercel Analytics x2, changelog, skills).

