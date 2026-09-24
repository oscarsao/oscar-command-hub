# Oscar Command Hub — Cerebro integral de gestión

**Un solo lugar accesible desde cualquier agente** (Hermes, Claude Code, Codex, OpenCode) para
tener el mapa de proyectos, empresas, decisiones y situaciones de Oscar.

## Para cualquier agente que conecte

1. **Empieza leyendo `CONTEXT.md`** — es el mapa maestro: identidad, prioridades, presupuesto,
   estado de cada proyecto, convenciones.
2. **Registros vivos por carpeta** (no dupliques, referencia):
   - `projects/` — estado, ramas, blockers y próximo paso de cada proyecto.
   - `decisions/` — decisiones tomadas (formato: `<fecha> - <tema>.md`).
   - `handoffs/` — traspasos entre sesiones/agentes.
   - `references/` — convenciones, runbooks, credenciales de acceso NO sensibles.
3. **Nunca** pongas secrets/API keys aquí. Secrets van en los `.env` de cada proyecto o en
   el vault.
4. Antes de codear en un proyecto real, lee el `CONTEXT.md` de *ese* proyecto (en su repo) y
   respeta las reglas de bash/railway/alembic de `~/.claude/CLAUDE.md` y `AGENTS.md` global.

## Convención de flujo (adaptada del CLAUDE.md de Claude)

- **Coordinador**: alcance, specs, dependencias, estado. No edita código.
- **Implementador**: una rama/worktree por workstream. Máx. 3 workstreams de código.
- **Integrador**: compone workstreams + gate combinado. Único propietario de Alembic.
- **Revisor**: solo lectura.

Oscar = CEO + decisor final. Hermes = agente de asistencia integral (rango, estado, mentoría y
consolidación); las **decisiones de deploy/merge** las toma Oscar.

## ¿Por qué existe este repo?

Oscar quiere un ecosistema de gestión integral de sus proyectos y empresas. La sesión de
planificación original está en una conversación de Claude Code (oscar-hq). Este repo es la
versión **versionada y compartida** de ese contexto, para que cualquier agente que se conecte
se ponga al día sin depender de una sola sesión.

Fecha de creación: 2026-09-24