# Plan de reorganización del kanban de Hermes — 2026-09-28

> Rol: Coordinador (solo lectura del kanban). Nada de esto se ha aplicado. Oscar lo aprueba en bloque y el
> coordinador lo aplica con `tools/agent-lanes/aplicar_reorganizacion.py` (dry-run por defecto).
> Acciones: `references/2026-09-28-plan-reorganizacion-kanban.json`. Foto del tablero: 28-09 ~15:50.

## Resumen

- **84 tarjetas no terminadas** en los 4 tableros (oscarhq 51, migrateam 10, default 22, personal 1). Otras dos
  (t_18ee0a4e y t_6d70f882) terminaron mientras se hacía el análisis y ya no figuran.
- **Qué pasa con cada tarjeta:**

  | Acción | Nº |
  |---|---|
  | Se quedan igual (con épica y prioridad) | 30 |
  | Pasan a Oscar como `[DECISIÓN]` | 15 |
  | Se archivan | 13 |
  | Esperan una respuesta de Oscar (P2–P14) | 15 |
  | Se reasignan a un carril | 6 |
  | Paraguas antiguos que pasan a ser épicas | 4 |
  | Se recrean en otro tablero | 1 |

- **Épicas:** 10 en total, 6 nuevas y 4 que reutilizan paraguas que ya existían. Ver §2.
- **Tarjetas done con más de 14 días: 0.** Hay 47 done y la más antigua es t_a4f9b3f2, completada el 24-09 (hace 4 días).
- **JSON:** 189 acciones:
  - 6 crear épica
  - 20 cambiar título
  - 57 cambiar prioridad
  - 17 desenlazar
  - 54 enlazar
  - 21 asignar
  - 1 recrear
  - 13 archivar

  Sin `--con-unlink`, el script salta 43 de ellas: todo lo que toca a los 4 paraguas antiguos (17 desenlaces,
  19 enlaces, 4 títulos y 3 cambios de dueño). Así, si Oscar elige P1 = B, esos paraguas se quedan exactamente
  como están hoy.

## 1. Dependencias en Hermes: por qué cada épica es la hija de sus tarjetas

En Hermes, `hermes kanban link <padre> <hijo>` es una **dependencia**, no una agrupación. Lo he comprobado en
`hermes_cli/kanban_db.py: link_tasks` y en `agent_lanes/deps.py`:

- Si el padre no está done ni archivado, el hijo `ready` pasa a `todo` y ningún carril lo coge.
- El Integrador no ofrece «Fusionar» a un hijo hasta que el padre tiene `INTEGRADO`.

Si pusiéramos cada épica como padre de sus tarjetas, todas las tarjetas de los carriles se pararían y no se
podrían integrar. De hecho, eso ya pasa hoy: los 4 paraguas del 24-09 (t_f90da954 LLC, t_55600b16 MIGRATEAM-360,
t_63a9b340 PILDORA-VENTAS y t_992e42ac SCRAPER-CRM-LEADS) son padres de sus 19 subtareas. Por eso esas
subtareas llevan 4 días en `todo` y no aparecen en `/decisiones`, que solo muestra tarjetas `ready`/`blocked`.

**Cómo lo resuelve el plan:**

- Cada épica es la **hija** de sus tarjetas (`link <tarjeta> <épica>`). Es el mismo patrón que usaba el
  auto-decomposer con t_f22e073f.
- La épica queda en `todo` y se despierta cuando terminan todas sus tarjetas. No bloquea a nadie.
- Todas las épicas van asignadas a `oscar`, para que ningún carril las reclame, y su título no lleva la
  etiqueta de decisión, así que no entran en la bandeja.
- Los enlaces solo funcionan dentro de un mismo tablero, porque cada tablero tiene su propio `kanban.db`. Por
  eso hay una épica por tablero, y t_9ccfdf80 y t_102f03b6 (orquestación, en oscarhq) se quedan sin enlazar.
- Liberar las 17 subtareas atrapadas exige `unlink`, que no estaba entre las acciones previstas. Va aparte,
  detrás de `--con-unlink` (ver P1).

## 2. Épicas propuestas

Recordatorio de qué tablero lee cada carril:

- **oscarhq:** claude-oscarhq, claude-scraper y claude-ops
- **migrateam:** claude-migrateam
- **default:** claude-nextjobs

| Épica (clave) | Tablero | Id | Dueño | Prioridad | Por qué esa prioridad | Tarjetas |
|---|---|---|---|---|---|---|
| Signal y outreach (`signal`) | oscarhq | reutiliza t_992e42ac | oscar | 9 | Es la prioridad declarada en CONTEXT: cerrar signal+pildora. El outreach ya funciona con cola de revisión (t_1411984a, PR #40). | t_35be06d8, t_eb0c3483, t_b2bf3bdc |
| MigraTeam producto y cobro (`mgproducto`) | migrateam | nueva | oscar | 9 | Mosquera es el único cliente que paga y MigraTeam es la siguiente prioridad. | t_815b87e5, t_905e3371, t_985109b9, t_c6ecfad3, t_3db189f4, t_775dd051 |
| LLC y legal (`llc`) | oscarhq | reutiliza t_f90da954 | oscar | 8 | Bloquea el contrato con Multiverse y la facturación. Además hay riesgo de disolución en Florida (t_a80a33a4). | t_a80a33a4, t_000ace0f, t_46aeb833, t_f3211374, t_0d94921e |
| Orquestación y agentes (`orquestacion`) | default | nueva | oscar | 8 | Es la condición del plan D para cerrar sesiones de Claude Code: 3 días sin intervención. | t_bdee05fe, t_974c7bc4, t_9a2e17fb, t_ad46ae34, t_c0d5a0fd, t_ebcee1f8, t_22de53c2, t_dc1b30fb (+ t_9ccfdf80 y t_102f03b6, en oscarhq y sin enlazar) |
| Ventas Píldora (`ventas`) | oscarhq | reutiliza t_63a9b340 | oscar | 7 | Son ingresos a corto plazo, pero todavía no hay oferta definida. | t_c3fde440, t_e7811e26, t_7a0ba60a, t_9b90d162 |
| Oscar HQ plataforma (`plataforma`) | oscarhq | nueva | oscar | 7 | Todo el código de oscar-hq en claude-oscarhq: seguridad, crews programados y deuda. | t_39532b42, t_4880088a, t_49d9ad5c, t_a2972b36, t_dc43939b, t_f371a1a8, t_1af88ebf, t_3a4f024e, t_a383b0ca, t_0077852a, t_c8b575a6 |
| Docketwise (`docketwise`) | migrateam | nueva | oscar | 7 | Plan t_761326d7 aprobado, con foco en familiar/humanitario desde el 28-09. Va después del cobro de Mosquera. | t_c3e7b48d, t_addbf275, t_dc1e9f0c |
| MigraTeam negocio (`mgnegocio`) | oscarhq | reutiliza t_55600b16 | oscar | 6 | Decisiones que solo puede tomar Oscar: TOS, pipeline, cobro y posicionamiento. | t_75e9bd0f, t_f1203e25, t_6ca8b65c, t_37ff8faf, t_8ce343e6, t_cb91e9fb |
| Marketing (`marketing`) | oscarhq | nueva | oscar | 6 | Deja a Andrea trabajar con comandos y accesos. | t_fccc736d, t_6d85a746 |
| Operaciones personales (`ops`) | oscarhq | nueva | oscar | 3 | Trabajo de claude-ops. Útil, pero ni genera ingresos ni desbloquea clientes. | t_08f90331, t_e8e76d7c, t_190f057a, t_6a2fdf4d, t_6b7b66ec (se recrea en oscarhq) |

Tarjetas sin épica, a propósito:

- t_9ccfdf80 y t_102f03b6: son del otro tablero.
- t_7daeacc9 y t_11fd6811: no encajan en ninguna épica.
- t_98f58389: queda como referencia.
- NextJobs (t_551843f3, t_6d1eb9e0): el proyecto está en pausa.
- El árbol t_f22e073f: pendiente de P5.

## 3. Tabla tarjeta por tarjeta

Cómo leer la tabla:

- **Prioridad:** escala 1-10, donde 10 es lo más urgente. «a→b» quiere decir que la prioridad cambia de a a b.
- **⚡ lanza trabajo:** la tarjeta es `ready` y pasa a un carril, así que el runner la reclama en la siguiente
  pasada (≤60 s; `max_workers: 1`, así que van en cola).
- **Arrancan en cuanto se aplica:** t_35be06d8, t_a383b0ca, t_3db189f4 y la copia de t_6b7b66ec.
- **Arrancan solo con `--con-unlink`:** t_0d94921e, t_7a0ba60a y t_eb0c3483.

| Id | Tablero | Título | Estado | Dueño actual | Acción | Prioridad |
|---|---|---|---|---|---|---|
| t_b889c49a | oscarhq | [SEMANA · DECISIÓN] Kill-switch del outreach de Signal: ¿reanudar s… | ready | oscar | hija→ épica `signal`; **sin cambio de dueño hasta P11**; La decisión (reanudar) ya se ejecutó en código (t_c70e4221 → t_1411984a, PR #40), pero es la única tarjeta que sigue la activación en producción. | 9 |
| t_fccc736d | oscarhq | Comandos de marketing en Telegram con preguntas y crews (/post /ree… | triage | claude-oscarhq | hija→ épica `marketing`; Oscar aprobó aplicar (Integrador). | 9→8 |
| t_39532b42 | oscarhq | Integrar rama rescatada rescue/triaje-h-batch-20260924 (H-094…H-119) | ready | — | hija→ épica `plataforma`; **sin cambio de dueño hasta P2**; Trío con t_c8b575a6/t_c0d5a0fd. | 7→6 |
| t_4880088a | oscarhq | Programar crews: sales_pipeline_review (lunes 8:00), content_creati… | blocked | claude-oscarhq | hija→ épica `plataforma`; ver P4; Solapa con t_49d9ad5c. | 6→5 |
| t_49d9ad5c | oscarhq | Spec: programar sales_pipeline_review semanal + demo_follow_up post… | blocked | claude-oscarhq | hija→ épica `plataforma`; ver P4; Spec de lo mismo que t_4880088a. | 5 |
| t_a2972b36 | oscarhq | Spec: crews programados extra: digest semanal de Signal, KPIs de ma… | blocked | claude-oscarhq | hija→ épica `plataforma` | 5→4 |
| t_dc43939b | oscarhq | Página de estado de carriles en app.pildoradigital.com (lee ohq_con… | blocked | claude-oscarhq | hija→ épica `plataforma`; Bloqueo resuelto: W4 fusionado (#37 32f11427, #39). El coordinador debe desbloquearla (unblock no entra en este script). | 5→4 |
| t_f371a1a8 | oscarhq | Spec: incident_response alimentado por alertas de Railway/Sentry | blocked | claude-oscarhq | hija→ épica `plataforma` | 4→3 |
| t_35be06d8 | oscarhq | Signal: descubrir contactos sin página de equipo (página de contact… | ready | — | **reasignar a claude-scraper**; hija→ épica `signal`; Código del Scraper (Signal Engine). Spec-Lite primero: el cuerpo ya lo exige.; ⚡ lanza trabajo al aplicar | 3→4 |
| t_a383b0ca | oscarhq | Auditoría de negocio de las 46 páginas restantes del dashboard (de 85) | ready | — | **reasignar a claude-oscarhq**; hija→ épica `plataforma`; Auditoría de solo lectura del dashboard de oscar-hq.; ⚡ lanza trabajo al aplicar | 3→2 |
| t_0077852a | oscarhq | Archivar crew fantasma output_compactor | blocked | claude-oscarhq | hija→ épica `plataforma`; **sin cambio de dueño hasta P3**; Contradicción: t_b0f386a8 comprobó 1 uso real (no es fantasma). | 3→2 |
| t_1af88ebf | oscarhq | Spec: H-122 fusionar strategic_review + full_business_audit | blocked | claude-oscarhq | hija→ épica `plataforma` | 3 |
| t_2a58b9e1 | oscarhq | Limpieza de ramas fusionadas y worktrees en C:/tmp (REQUIERE OK de … | ready | — | **archivar**: Sustituida por t_3a4f024e (comentario del coordinador 28-09 12:58). | — |
| t_c8b575a6 | oscarhq | Triar y corregir 10 hallazgos S pendientes (H-094,099,105,106,108,1… | blocked | claude-oscarhq | hija→ épica `plataforma`; **sin cambio de dueño hasta P2**; Trabajo ya en rescue/triaje-h-batch-20260924; integrarlo es t_39532b42. | 1 |
| t_f90da954 | oscarhq | [SEMANA] [LEGAL-LLC] Pildora Digital LLC — cumplimiento fiscal y le… | ready | oscar | **reutilizar como épica** `llc` (título «[ÉPICA] LLC y legal: Pildora Digital LLC (fiscal, firma; bloquea el contrato Multiverse)», dueño oscar) | 0→8 |
| t_0d94921e | oscarhq | Reunir todos los docs societarios (Articles of Organization, Operat… | todo | — | **reasignar a claude-ops**; desenlazar de t_f90da954 (P1); hija→ épica `llc`; Operativo sin repo (carpetas en E:). Solo arranca si se aprueba desenlazar (P1).; ⚡ lanza trabajo al aplicar | 0→6 |
| t_46aeb833 | oscarhq | Confirmar con gestor/CPA obligaciones fiscales US de la LLC (Florid… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_f90da954 (P1); hija→ épica `llc`; Requiere gestor/CPA: solo Oscar. | 0→7 |
| t_f3211374 | oscarhq | Confirmar situacion fiscal en Espana de Oscar por ingresos via LLC … | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_f90da954 (P1); hija→ épica `llc`; Asesor fiscal en España: solo Oscar. | 0→6 |
| t_a80a33a4 | oscarhq | Verificar estado de registered agent y annual report de Florida (ev… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_f90da954 (P1); hija→ épica `llc`; URGENTE (a verificar): si el annual report de Florida no se presentó, la disolución administrativa suele aplicarse a finales de septiembre. Comprobar hoy en sunbiz.org. | 0→10 |
| t_000ace0f | oscarhq | Definir circuito de firma: que puede firmar Oscar como Authorized R… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_f90da954 (P1); hija→ épica `llc`; Firma Authorized Rep vs Sole Member: bloquea contratos (Multiverse, Mosquera). | 0→8 |
| t_55600b16 | oscarhq | [MIGRATEAM-360] Revision integral: producto, ventas, legal, admin, … | ready | — | **reutilizar como épica** `mgnegocio` (título «[ÉPICA] MigraTeam negocio: ventas, legal y administración (ex MIGRATEAM-360)», dueño oscar) | 0→6 |
| t_8ce343e6 | oscarhq | Producto: cerrar las 3 decisiones manuales pendientes (MOSQ-02 idio… | todo | — | **[DECISIÓN] → oscar**; título «[DECISIÓN] MigraTeam MOSQ-01: limpieza de datos de Mosquera (MOSQ-02 y»; desenlazar de t_55600b16 (P1); hija→ épica `mgnegocio`; 2 de las 3 decisiones ya hechas (t_ba6bc31f, done 25-09). | 0→3 |
| t_f1203e25 | oscarhq | Ventas: definir pipeline y estado real de leads/despachos (Mosquera… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_55600b16 (P1); hija→ épica `mgnegocio` | 0→5 |
| t_75e9bd0f | oscarhq | Legal: contrato/TOS de servicio para despachos, proteccion de datos… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_55600b16 (P1); hija→ épica `mgnegocio`; Contrato/TOS y protección de datos: bloqueante legal antes de más despachos. | 0→6 |
| t_6ca8b65c | oscarhq | Administracion: facturacion, cobro, alta de clientes -- flujo opera… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_55600b16 (P1); hija→ épica `mgnegocio`; Relacionada con t_815b87e5 (E2E Stripe) y t_db8507a0 (done). | 0→4 |
| t_cb91e9fb | oscarhq | Documentacion: actualizar HANDOFF_PROXIMA_SESION.md y ledger con es… | todo | — | desenlazar de t_55600b16 (P1); hija→ épica `mgnegocio`; **sin cambio de dueño hasta P8**; Doc de MigraTeam en el tablero equivocado; probablemente obsoleta. | 0→2 |
| t_37ff8faf | oscarhq | Marketing: como se va a vender MigraTeam mas alla de Mosquera (posi… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_55600b16 (P1); hija→ épica `mgnegocio` | 0→4 |
| t_63a9b340 | oscarhq | [PILDORA-VENTAS] Agencia -- foco en venta de servicios digitales | ready | — | **reutilizar como épica** `ventas` (título «[ÉPICA] Ventas Píldora: agencia de servicios digitales», dueño oscar) | 0→7 |
| t_c3fde440 | oscarhq | Definir oferta de servicios digitales a vender (que se vende exacta… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_63a9b340 (P1); hija→ épica `ventas`; Sin oferta no hay venta: primera pieza de la épica. | 0→7 |
| t_e7811e26 | oscarhq | Pipeline de prospeccion activa: quien contacta, con que cadencia, q… | todo | — | **[DECISIÓN] → oscar**; desenlazar de t_63a9b340 (P1); hija→ épica `ventas` | 0→5 |
| t_7a0ba60a | oscarhq | Revisar caso Multiverse Computing como referencia/caso de venta (co… | todo | — | **reasignar a claude-ops**; desenlazar de t_63a9b340 (P1); hija→ épica `ventas`; Leer docs de E:/Pildora Digital 2026/Multiverse y resumirlos: operativo.; ⚡ lanza trabajo al aplicar | 0→3 |
| t_9b90d162 | oscarhq | Preparar propuesta para el cliente de la web de tattoo (proyecto he… | todo | — | desenlazar de t_63a9b340 (P1); hija→ épica `ventas`; **sin cambio de dueño hasta P9** | 0→3 |
| t_992e42ac | oscarhq | [SCRAPER-CRM-LEADS] Verificar captacion activa y gestion real de leads | ready | — | **reutilizar como épica** `signal` (título «[ÉPICA] Signal y outreach: captación → CRM → secuencias», dueño oscar) | 0→9 |
| t_eb0c3483 | oscarhq | Auditar Scraper: esta corriendo de forma constante/programada o sol… | todo | — | **reasignar a claude-scraper**; desenlazar de t_992e42ac (P1); hija→ épica `signal`; Auditoría de solo lectura del Scraper (t_9947adb1 dejó pendiente el reproceso de personas).; ⚡ lanza trabajo al aplicar | 0→4 |
| t_55ac04d8 | oscarhq | Auditar Oscar HQ CRM: confirmar si hay secuencia de email activa en… | todo | — | **archivar**: Respondida: comentario de t_992e42ac (24-09): 8 secuencias en pausa, 0 matrículas; PR #12. | — |
| t_275db333 | oscarhq | Conectar el flujo: leads de Scraper entran automaticamente al CRM d… | todo | — | **archivar**: Hecha: PR #12 (sync Scraper→CRM cada 15 min) fusionado el 24-09. | — |
| t_b2bf3bdc | oscarhq | Definir cadencia de outreach real (email/otro canal) para los leads… | todo | — | **[DECISIÓN] → oscar**; título «[DECISIÓN] Signal: cadencia y volumen diario del outreach (con cola de»; desenlazar de t_992e42ac (P1); hija→ épica `signal` | 0→6 |
| t_2f07828a | oscarhq | PROYECTO-AGENTES: rescate del sistema de Crews + captura de patrone… | ready | — | **archivar**: PR #11 fusionado; su propio comentario la da por cerrada; sustituida por la auditoría de crews del 27-09 (t_dbd74152 y sus tarjetas). | — |
| t_7daeacc9 | oscarhq | [PILDORA-HANDBOOK] Cerrar drift handbook <-> oscar-hq | ready | — | **sin cambio de dueño hasta P10**; pildora-handbook no tiene carril; último commit 10-09. | 0→2 |
| t_70c748be | oscarhq | Orquestación F2-W1b: adaptador Codex + carriles + lanes status + ar… | ready | oscar | **archivar**: Hecha: comentario de cierre W1b (hub 87a83b4/9dfe9fc/28ca285, 127 tests). | — |
| t_6ded7e3e | oscarhq | Orquestación F2-W3b: temas de grupos como carriles directos + hilo … | ready | oscar | **archivar**: Hecha: W3b en hub 1f5cf8d + addendum 0d383f9. | — |
| t_d2fccb2a | oscarhq | Orquestación F2-W4: bots por rol (role_routes) + carriles crew:<rol… | ready | oscar | **archivar**: Hecha: W4 fusionado en oscar-hq (#37 32f11427, #39 51449e7d). Comandos de Gestión pendientes: P12. | — |
| t_9ccfdf80 | oscarhq | Orquestación W5: métricas + retro semanal + autoaprendizaje por PR | ready | oscar | mantener; Orquestación (W5), pero en oscarhq: no enlazable a la épica de default. | 0→3 |
| t_c70e4221 | oscarhq | Reanudar outreach Signal (Scraper→CRM) tras kill-switch, con calent… | triage | oscar | **archivar**: Sustituida por t_1411984a (done); comentario del coordinador 28-09. | — |
| t_102f03b6 | oscarhq | Vigilancia de lanzamientos de modelos + panel de cambio de modelo p… | blocked | oscar | mantener; Aparcada por Oscar. Orquestación, en oscarhq: no enlazable. | 0→1 |
| t_e8e76d7c | oscarhq | Migrar vault Obsidian de disco D: a local + túnel con URL propia | triage | claude-ops | hija→ épica `ops` | 0→3 |
| t_190f057a | oscarhq | Recuperar 'buzón' de agentes de disco E: (extraíble) | blocked | claude-ops | hija→ épica `ops` | 0→3 |
| t_08f90331 | oscarhq | Calendario personal de Oscar organizado por áreas | blocked | claude-ops | hija→ épica `ops` | 0→4 |
| t_6d85a746 | oscarhq | Accesos para Andrea: Drive, GitHub y Canva (marca Píldora) | blocked | claude-ops | hija→ épica `marketing`; Desbloquea el trabajo de Andrea. | 0→6 |
| t_6a2fdf4d | oscarhq | Definir y mejorar gestión de ClickUp | blocked | claude-ops | hija→ épica `ops` | 0→3 |
| t_3a4f024e | oscarhq | Auditoría de ramas lane/* fusionadas y worktrees huérfanos (sin bor… | blocked | claude-oscarhq | hija→ épica `plataforma` | 0→3 |
| t_815b87e5 | migrateam | [SEMANA] E2E de cobro Stripe con Mosquera (claves de test) | ready | oscar | hija→ épica `mgproducto`; Ya es tarjeta de decisión ([SEMANA]): faltan las claves de test de Stripe. | 10 |
| t_905e3371 | migrateam | Spec (solo diseño): verificar en código los 12 puntos de correccion… | triage | claude-migrateam | hija→ épica `mgproducto` | 8 |
| t_c3e7b48d | migrateam | Docketwise #1 · Spec: OCR de documentos de EEUU (I-94, EAD/I-766, I… | blocked | claude-migrateam | hija→ épica `docketwise` | 8→7 |
| t_addbf275 | migrateam | Docketwise #3 · Spec: publicar el catálogo de EEUU ya construido (6… | blocked | claude-migrateam | hija→ épica `docketwise` | 7 |
| t_53c184e6 | migrateam | MigraTeam: tipos 'pago de matrícula' y 'carta de patrocinio', alert… | ready | oscar | **archivar**: Sustituida por t_c6ecfad3 (creada al aprobarla Oscar el 28-09, cita sus comentarios). | — |
| t_dc1e9f0c | migrateam | Docketwise #4 · Spec: alertas de plazos que se usen de verdad (flag… | blocked | claude-migrateam | hija→ épica `docketwise`; Oscar: 'revisar alcance antes'. Relacionada con t_c6ecfad3 (alerta de plazo). | 6→5 |
| t_3db189f4 | migrateam | Crear proyecto Sentry para el frontend de MigraTeam | ready | — | **reasignar a claude-migrateam**; hija→ épica `mgproducto`; Código del frontend; el DSN en Vercel lo pone Oscar (el carril termina en needs_input).; ⚡ lanza trabajo al aplicar | 5→4 |
| t_775dd051 | migrateam | Explicación de visados y procedimientos de extranjería (conocimient… | ready | — | hija→ épica `mgproducto`; **sin cambio de dueño hasta P7** | 4→3 |
| t_c6ecfad3 | migrateam | Diagnosticar alerta de fuera de plazo que no salta + tipos 'pago ma… | blocked | claude-migrateam | hija→ épica `mgproducto`; ver P13 | 0→7 |
| t_985109b9 | migrateam | MigraTeam Fase 1: catálogo docs + formulario Estudios + antecedente… | triage | claude-migrateam | hija→ épica `mgproducto`; ver P13 | 0→8 |
| t_ba5085cc | default | CRM leads: remitente hola@migrateam + arreglar cola de revisión (no… | ready | oscar | **archivar**: Duplicado de t_c70e4221 (Hermes buscó solo en default). | — |
| t_1a5138f2 | default | Carril Integrador: fusionar y desplegar SOLO tarjetas aprobadas por… | ready | oscar | **archivar**: Hecha: integrador en hub 78c59c3 + endurecido 6938e82 (security-auditor). | — |
| t_bdee05fe | default | agent-lanes: reinicio ordenado (drain) + 'lanes status' sin falsos … | ready | oscar | hija→ épica `orquestacion`; Plan D §7.4. | 8→7 |
| t_631cf0bc | default | Resumen diario 8:00 L-V en Gestión·General (determinista, sin LLM) | ready | oscar | **archivar**: Hecha: hub 331bd3b (brief_diario.py) y t_ffaf1078 la da por cubierta. | — |
| t_9a2e17fb | default | agent-lanes: avisar también en el DM las tareas pedidas por DM + qu… | ready | oscar | hija→ épica `orquestacion`; Puede estar cubierta en parte por la bandeja única (15e166f): verificar antes de trabajarla. | 7→4 |
| t_551843f3 | default | [SEMANA] NextJobs: conectar sender, rellenar perfil vacío y corregi… | ready | — | **sin cambio de dueño hasta P6**; NextJobs está pausado. | 6→2 |
| t_ffaf1078 | default | [SIN TRATAR] Peticiones del 24 al 26-09 sin rastro | ready | oscar | **archivar**: Auditoría cerrada: repartida en t_102f03b6, t_e8e76d7c, t_190f057a, t_08f90331, t_6d85a746, t_32f2b188, t_a0790dfc, t_6a2fdf4d. El punto #2 no tiene tarjeta: P14. | — |
| t_6d1eb9e0 | default | NextJobs opción C (híbrida, aprobación por Telegram): implementar | ready | — | depende de t_551843f3; ver P6; Su cuerpo dice que depende de t_551843f3: se enlaza la dependencia. | 5→2 |
| t_ebcee1f8 | default | Respaldo de modelo para Hermes (DeepSeek/OpenRouter) si se agota Cl… | ready | oscar | **etiquetar [DECISIÓN]**; hija→ épica `orquestacion`; Requiere crédito en OpenRouter: solo Oscar. | 5→3 |
| t_ad46ae34 | default | agent-lanes: presupuesto por TAREA (hoy max_budget_usd es por invoc… | ready | oscar | hija→ épica `orquestacion` | 5→4 |
| t_22de53c2 | default | Hermes: bug de clarify en temas de foro (la respuesta escrita se to… | ready | oscar | hija→ épica `orquestacion`; Mitigado en la skill. | 5→2 |
| t_c0d5a0fd | default | Runner: t_0077852a (git worktree add falló) y t_c8b575a6 ('no tiene… | ready | oscar | hija→ épica `orquestacion`; ver P2 | 5→4 |
| t_dc1b30fb | default | Espejo de solo lectura de las tareas de carriles en ClickUp | ready | oscar | hija→ épica `orquestacion`; Necesita token de ClickUp de Oscar. | 4→2 |
| t_974c7bc4 | default | Hermes: consolidar USER.md (1366/1375 caracteres, error 'Memory exc… | ready | oscar | **etiquetar [DECISIÓN]**; hija→ épica `orquestacion`; USER.md tiene datos personales: lo revisa Oscar. Hermes ya no puede escribir memoria. | 4→5 |
| t_11fd6811 | default | Recordatorio: recargar fal.ai para reactivar el vídeo (video_produc… | ready | oscar | **etiquetar [DECISIÓN]**; Recordatorio que solo puede hacer Oscar (recargar fal.ai). | 3→2 |
| t_f22e073f | default | MigraTeam: Extender "variantes por nacionalidad" a país de residenc… | todo | oscar | **sin cambio de dueño hasta P5**; Código de MigraTeam en el tablero default (ningún carril lo ve); subtareas del auto-decomposer (desactivado). |  |
| t_07ceb685 | default | Fix nacionalidad root cause: ISO-2 codes + migration + wire matching | blocked | oscar | **sin cambio de dueño hasta P5**; Código de MigraTeam en el tablero default (ningún carril lo ve); subtareas del auto-decomposer (desactivado). |  |
| t_17214b1e | default | Add residence_country_code axis to audience match model | todo | oscar | **sin cambio de dueño hasta P5**; Código de MigraTeam en el tablero default (ningún carril lo ve); subtareas del auto-decomposer (desactivado). |  |
| t_f91fe646 | default | Add consulado_id axis to audience match model | blocked | oscar | **sin cambio de dueño hasta P5**; Código de MigraTeam en el tablero default (ningún carril lo ve); subtareas del auto-decomposer (desactivado). |  |
| t_af191449 | default | Define and implement priority resolution across the 3 audience axes | todo | oscar | **sin cambio de dueño hasta P5**; Código de MigraTeam en el tablero default (ningún carril lo ve); subtareas del auto-decomposer (desactivado). |  |
| t_4de85509 | default | Extend catalog editor UI and TS types for the 3 override axes | todo | oscar | **sin cambio de dueño hasta P5**; Código de MigraTeam en el tablero default (ningún carril lo ve); subtareas del auto-decomposer (desactivado). |  |
| t_98f58389 | default | MigraTeam — Consolidar spec de motor de fechas único (DeadlineAncho… | ready | oscar | **retitular** «[REF] MigraTeam — Spec motor de fechas único (DeadlineAnchor…»; Ficha de referencia (lote cerrado 27-09). No se archiva: t_985109b9 la cita y aún no ha terminado. | 0→1 |
| t_6b7b66ec | personal | Organizar carpetas MigraTeam/Píldora (E: y local) y comparar por ha… | ready | — | **recrear** en oscarhq → claude-ops y archivar la original. Operativo; claude-ops solo lee el tablero oscarhq. Se recrea allí y se archiva la original.; ⚡ lanza trabajo al aplicar | 2→3 |

## 4. Duplicadas y sustituidas (evidencia)

| Tarjeta | Relación | Evidencia |
|---|---|---|
| t_ba5085cc (default) | duplicado de t_c70e4221 | Comentario del coordinador (28-09 12:45): Hermes la creó porque buscó solo en el tablero default. |
| t_c70e4221 (oscarhq) | sustituida por t_1411984a (done) | Comentario del coordinador: «Sustituida por la tarea nueva de revisión de código (misma rama)». t_1411984a aprobada y PR #40 (a50b2902) en master. |
| t_2a58b9e1 (oscarhq) | sustituida por t_3a4f024e | Comentario del coordinador (28-09 12:58). |
| t_b889c49a (oscarhq) | **no se archiva: pendiente de P11** | Comentario de Oscar (28-09): «REANUDAR». El código ya se hizo (t_c70e4221 → t_1411984a), pero es la única tarjeta que sigue la activación en producción. |
| t_53c184e6 (migrateam) | sustituida por t_c6ecfad3 | El cuerpo de t_c6ecfad3 dice «Decisión aprobada 28-09 (ver comentarios de t_53c184e6)». |
| t_1a5138f2 (default) | ya hecha | Commits del hub 78c59c3 (carril Integrador) y 6938e82 (endurecido tras security-auditor), los dos citan t_1a5138f2. |
| t_631cf0bc (default) | ya hecha | Commit del hub 331bd3b (`brief/brief_diario.py`, cita t_631cf0bc). t_ffaf1078 la da por cubierta. |
| t_70c748be (oscarhq) | ya hecha | Comentario de cierre de W1b (hub 87a83b4, 9dfe9fc, 28ca285; 127 tests). |
| t_6ded7e3e (oscarhq) | ya hecha | «W3b listo (commit 1f5cf8d)» y addendum 0d383f9. |
| t_d2fccb2a (oscarhq) | ya hecha | oscar-hq master: 32f11427 «bots por rol… (W4) (#37)» y 51449e7d (#39). |
| t_2f07828a (oscarhq) | cerrada | Su propio comentario: «PR #11 fusionado… se puede considerar cerrado». La sustituye la auditoría de crews del 27-09 (t_dbd74152). |
| t_ffaf1078 (default) | ya repartida | Su comentario reparte los puntos en 8 tarjetas. El punto #2 se queda sin tarjeta (P14). |
| t_55ac04d8 (oscarhq) | ya respondida | Comentario en t_992e42ac (24-09): 8 secuencias en pausa y 0 matrículas. |
| t_275db333 (oscarhq) | ya hecha | PR #12 (sync Scraper→CRM cada 15 min), fusionado el 24-09 (comentario en t_992e42ac). |

**Solapes que no se archivan**

- **t_4880088a / t_49d9ad5c:** cubren lo mismo, sales_pipeline_review y demo_follow_up. P4.
- **t_c6ecfad3 / t_985109b9:** las dos quieren dar de alta «pago de matrícula» y «carta de patrocinio». P13.
- **t_c8b575a6 / t_39532b42 / t_c0d5a0fd:** el trabajo de t_c8b575a6 ya está en la rama
  `rescue/triaje-h-batch-20260924`. t_39532b42 consiste en integrarla, y t_c0d5a0fd es el diagnóstico del
  runner. P2.
- **t_dc1e9f0c / t_c6ecfad3:** las dos tratan de alertas de plazo. Quedan en épicas distintas y se ven en la
  revisión de alcance que pidió Oscar.
- **t_6b7b66ec / t_0d94921e / t_190f057a:** las tres trabajan sobre el disco E:. Conviene hacerlas en una
  sola sesión de ops con el disco conectado.

## 5. Tarjetas done con más de 14 días

**Recuento: 0. Lista: ninguna.** Hay 47 tarjetas done y la más antigua es t_a4f9b3f2, completada el
2026-09-24 14:00, hace 4 días. El kanban se usa desde el 24-09. No he rebajado el umbral.

## 6. Preguntas para Oscar

Se contestan en bloque. En cada pregunta, **(rec)** marca la opción recomendada. Salvo en P1, el script no
hace nada con estas tarjetas hasta que el coordinador añada al JSON las acciones que salgan de las respuestas.

- **P1 · Liberar las 17 subtareas de los paraguas antiguos** (LLC, MIGRATEAM-360, PILDORA-VENTAS y
  SCRAPER-CRM-LEADS). Hoy están en `todo` porque el paraguas es su padre.
  - A) (rec) Aplicar con `--con-unlink`: se desenlazan y se vuelven a enlazar al revés (tarjeta → épica).
    - Las 12 `[DECISIÓN]` pasan a `ready` y entran en tu bandeja.
    - t_0d94921e, t_7a0ba60a y t_eb0c3483 arrancan en sus carriles.
    - t_cb91e9fb y t_9b90d162 quedan en `ready` sin dueño.
  - B) No desenlazar: los 4 paraguas se quedan como están (mismo título, mismo dueño, sin enlaces nuevos) y sus
    subtareas siguen dormidas. Solo cambian sus prioridades.
  - C) Épicas sin ningún enlace, solo el prefijo `[ÉPICA]`.
- **P2 · Triaje H-094…H-119.** El trabajo de t_c8b575a6 ya está en `rescue/triaje-h-batch-20260924`.
  - A) (rec) Archivar t_c8b575a6 como sustituida por t_39532b42 y asignar t_39532b42 a claude-oscarhq (PR agrupado, revisión). t_c0d5a0fd se queda solo con el fallo del worktree de t_0077852a.
  - B) Reencolar t_c8b575a6 tal cual.
  - C) Dejar las tres como están.
- **P3 · t_0077852a «archivar crew fantasma output_compactor».** t_b0f386a8 comprobó que tuvo 1 uso real en 30 días.
  - A) (rec) Mantenerla: la decisión del PR #31 de sacarlo del menú sigue siendo válida. Solo falló el worktree.
  - B) Archivar la tarjeta y conservar el crew.
- **P4 · t_49d9ad5c (spec) y t_4880088a (programar), los dos sobre sales_pipeline_review y demo_follow_up.**
  - A) (rec) Enlazar la spec como padre de la programación, para que la programación espere a la spec.
  - B) Archivar t_49d9ad5c como absorbida por t_4880088a.
  - C) Dejarlas como están.
- **P5 · Árbol t_f22e073f (6 tarjetas en default: variantes por país de residencia y consulado).** Es código
  de MigraTeam en un tablero que no lee ningún carril, y las subtareas las generó en inglés el auto-decomposer,
  que ya está desactivado.
  - A) (rec) Archivar las 6 y recrear una sola Spec-Lite en migrateam para claude-migrateam, enlazada después
    de t_905e3371 para que no arranque antes.
  - B) Meterlo como punto de la spec t_905e3371 y archivar las 6.
  - C) Dejarlo como está.
- **P6 · NextJobs (t_551843f3 [SEMANA] y t_6d1eb9e0).** El proyecto está en pausa.
  - A) Asignarlas a claude-nextjobs ya.
  - B) (rec) Dejarlas sin dueño con prioridad 2 (el plan solo enlaza t_551843f3 → t_6d1eb9e0).
  - C) Archivarlas.
- **P7 · t_775dd051 «Explicación de visados» (tablero migrateam).**
  - A) (rec) claude-migrateam la entrega en `docs/kb/` del repo.
  - B) Recrearla en oscarhq para claude-ops, con destino `references/` del hub.
  - C) Archivarla.
- **P8 · t_cb91e9fb «Actualizar HANDOFF/ledger post-auditoría» (del 24-09, en el tablero oscarhq).**
  - A) (rec) Archivarla, porque la superaron t_761326d7 y t_905e3371.
  - B) Recrearla en migrateam para claude-migrateam.
- **P9 · t_9b90d162 «Propuesta web de tattoo».** ¿Sigue vivo ese cliente?
  - A) Sí: claude-ops prepara un borrador con la plantilla de propuesta-comercial.
  - B) La haces tú.
  - C) Archivarla.
- **P10 · t_7daeacc9 «Drift handbook ↔ oscar-hq».** El último commit del handbook es del 10-09 y no hay carril para ese repo.
  - A) (rec) Pasa a `[DECISIÓN]` para ti: la opción A de SSOT dice que el sync lo hace Oscar a mano.
  - B) Crear un carril claude-handbook.
  - C) Archivarla.
- **P11 · t_b889c49a y la activación de Signal en producción.** Es la única tarjeta que sigue la activación de
  `OHQ_SCRAPER_OUTREACH_ENABLED=true` con `AUTO_SEND=false` en Railway (pasos post-deploy de t_1411984a).
  - A) (rec) Retitularla «[DECISIÓN] Signal: activar outreach en producción (pasos post-deploy de t_1411984a)».
    No hace falta tarjeta nueva.
  - B) Archivarla, porque la activación ya está hecha.
- **P12 · Comandos de Gestión** (/pipeline /caja /kpis) que pediste en W4 (t_d2fccb2a). No los cubre t_fccc736d.
  - A) Crear una tarjeta para claude-oscarhq (p5).
  - B) (rec) Esperar a ver cómo se usan los comandos de Marketing.
- **P13 · «Pago de matrícula» y «carta de patrocinio».** Las dos están en t_c6ecfad3 y en t_985109b9.
  - A) (rec) Que los tipos vayan solo en t_985109b9 (visado de estudios en España) y que t_c6ecfad3 se quede
    con la alerta de plazo y la caducidad de los custom fields. El coordinador lo deja comentado en las dos
    tarjetas.
  - B) Dejarlo como está.
- **P14 · Punto #2 de t_ffaf1078 (comparativa de modelos por calidad, tiempo y coste).** No tiene tarjeta.
  - A) Crearla en default para oscar (p2).
  - B) (rec) Descartarla por ahora: t_102f03b6 está aparcada.

**Aviso urgente (no es una pregunta):** t_a80a33a4, sobre el annual report y el registered agent de la LLC,
pasa a prioridad 10. Está por verificar, pero si el annual report de Florida no se presentó antes del 1 de mayo,
la disolución administrativa suele aplicarse a finales de septiembre. Hay que comprobarlo hoy en sunbiz.org.
Ojo: esta tarjeta solo aparece en la bandeja con P1 = A. Con P1 = B sigue dormida en `todo`.

## 7. Notas para el coordinador (fuera del script)

- **t_dc43939b** está bloqueada esperando a W4, que ya se fusionó (#37, #39). Hay que desbloquearla; `unblock`
  no está entre las acciones de este plan.
- **Tarjetas `blocked` en las que Oscar ya ha contestado** (t_4880088a, t_49d9ad5c, t_a2972b36, t_f371a1a8,
  t_1af88ebf, t_c3e7b48d, t_addbf275, t_dc1e9f0c, t_08f90331, t_6d85a746, t_6a2fdf4d, t_190f057a,
  t_3a4f024e): comprobar que el runner las ha retomado, porque siguen `blocked`.
- **t_9a2e17fb:** puede que la bandeja única (15e166f) ya cubra el aviso al DM. Hay que verificarlo antes de
  lanzarla.
- **t_98f58389** se queda como `[REF]`. Archivarla cuando termine t_985109b9.

## 8. Cómo aplicar

```bash
py -3.12 tools/agent-lanes/aplicar_reorganizacion.py                          # dry-run (solo list/show)
py -3.12 tools/agent-lanes/aplicar_reorganizacion.py --apply                  # sin P1
py -3.12 tools/agent-lanes/aplicar_reorganizacion.py --apply --con-unlink     # con P1 = A
```

- **Idempotente:** se puede relanzar y salta lo que ya está hecho. Las épicas nuevas llevan `--idempotency-key reorg-20260928-<clave>`.
- **Deriva:** si una tarjeta cambió desde esta foto, esa acción se salta y queda en el log. `--forzar` lo ignora.
- **Tarjetas en curso:** nunca toca tarjetas `running` o `review`.
- **Carriles:** se niega a asignar un carril a una tarjeta de un tablero que ese carril no lee.
- **Archivar:** antes de archivar deja un comentario con el motivo.
- **Log:** `tools/agent-lanes/.state/reorganizacion/<fecha>-<modo>.log`.
- **Dry-run del 28-09 (16:20):**
  - Sin flag: 146 acciones planificadas, 43 saltadas por requerir `--con-unlink`, 0 errores.
  - Con `--con-unlink`: 189 planificadas, 0 errores.
- **Antes de `--apply`:** repetir el dry-run justo antes y leer las líneas `DERIVA`, porque la foto es de ~15:50.
