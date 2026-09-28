# MigraTeam — estado

> Última actualización: 2026-09-24. Es la PRÓXIMA prioridad de foco tras cerrar signal/pildora.

## Qué es
Plataforma para despachos de abogados (inmigración). Flujo: portal-cliente, despacho, leads,
plantillas, secuencias, email, etiquetado.

## Estado general
- **Ramas (28-09, decisión de Oscar; CLAUDE.md del repo):** todo entra por `develop` = **staging**
  (Railway staging `migrateam-backend-staging-staging.up.railway.app` + Vercel develop, Supabase
  `rfkfpnppxunviedctdkk`). `master` = **producción** (push despliega solo en minutos) y solo recibe una
  promoción `release/<fecha>` con OK de Oscar, o un hotfix puntual que **hay que devolver a develop enseguida**
  (el monitor avisa si develop se queda por detrás). El carril `claude-migrateam` parte de `develop` y su PR va
  contra `develop`; el Integrador solo ofrece "Fusionar en develop (despliega staging)".
- 28-09: develop sincronizado con master (PR #91, 20 commits de hotfix que no se habían devuelto); staging en
  `37c59086965d`.
- Backend start: `alembic upgrade head && uvicorn` (en staging corre contra la BD de staging).
- Sesiones de Claude Code activas en worktrees (`C--Users-oscar-dev-migrateam`).
- Tune/emails: se probó enviar cada plantilla a oscarsao20@gmail.com. Etiquetado para generar contenido.

## Peticiones de Oscar (de la sesión de planificación)
- Revisar flujo e2e desde que contacta hasta que contrata.
- Plantillas con estilo (traer la de pildora/migrateam interna) para transaccionales y una simple
  tipo firma para invitación/captación sin caer en promociones.
- Panel lateral: botón de correo abre modal (nuevo correo/secuencia/plantilla), no solo mailto;
  elegir contacto o empresa. Vista cliente/empresa independiente.
- Aprovechar features/branch ya listas (feat/gtm-emit-demo-requested b04a6a47 → verificar
  npm install + vitest + tsc sobre esa rama para mergear a master).

## Próximo paso
Tras cerrar oscar-hq, retomar MigraTeam con foco total.