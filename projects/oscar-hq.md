# oscar-hq — estado

> Última actualización: 2026-09-24. Sprint continuado desde 2026-09-22/23 (sesión de planificación).

## Qué es
Plataforma central (dashboard + API) de Oscar HQ. Aloja agentes/crews (CrewAI), brands
(brandbrain), CRM/ventas, email marketing, signal integration, budgets.

## Estado general
- Deploy: manual (railway up) **no conectado a GitHub**.
- Rama principal: `master`. (En oscar-hq se usa master; hay PRs a feature ramas.)
- Se está cerrando la tanda de **signal + pildora** antes de pasar a MigraTeam.

## Frentes activos / hallazgos (de la sesión de planificación)
- **Agentes/crews mal cableados en la plataforma** (CRM → leads, clasificación, propuestas).
  Idea: caparazón que sirva para migrateam, pildora o cualquier marca; poblarlo luego.
- **Signal**: worker_signal ramifica `discover` por brand_code; 2 ICPs de pildora ahora sí se
  ejecutan (antes nunca se llamaban desde el job). Apollo enrich por lotes (2.101 empresas,
  resumidas en 1.000 por página). Crédito/contador H-092.
- **Brandbrain**: contexto central; muchas páginas duplicadas, la primera no editable. Simplificar
  con 80/20 sin perder funcionalidad.
- **Budget duro**: $10-25 por marca en `/api/crews/run` (budget_guard / ohq_brand_budget) cortan
  en duro y de repente. Revisar el fallback global que corta brands activas.
- **Emails transaccionales**: plantillas con diseño (adaptar la de migrateam → logo pildora),
  + una simple tipo firma/ invitación sin HTML pesado. render_transactional con raw_vars (escapado).
  Algunas plantillas aparecen en texto plano cuando ya tenían HTML.
- **Finder H-096/H-098/H-102**: encontrar persistencia de brands (write OK, read OK tras deploy,
  crear/leer/borrar cerrado), tablas de Empresas con estructuración, asistente de punta a punta,
  propuestas con Stripe cobrando 100% (sin anticipo — revisar).
- **57 hallazgos** pendientes de consolidar/triar/decidir/despachar.

## Preguntas/decisiones abiertas para Oscar
- Simplificar brandbrain.
- Standby de runs automáticos.
- Mover todo a una sola consola.

## Próximo paso
Cerrar signal/pildora, mergear a master, dejar documentado, pasar foco a **MigraTeam**.