# AGENTS.md — Oscar Command Hub

> Reglas para CUALQUIER agente (Hermes, Claude Code, Codex, OpenCode) que conecte a este repo.

## Objetivo
Este repo es el **cerebro compartido** de gestión de Oscar: proyectos, empresas, decisiones y
situaciones. NO es un repo de código de producto. Aquí se versiona contexto y estado.

## Rol de este repo
- **LEER** `CONTEXT.md` primero — es el mapa maestro.
- Las carpetas son registros vivos: `projects/`, `decisions/`, `handoffs/`, `references/`.
- Es la fuente que cualquier agente puede usar para ponerse al día sin depender de una sesión
  concreta de Claude.

## Lo que NO es este repo
- No es donde se desarrolla código de producto (eso vive en migrateam/oscar-hq/pildora/etc).
- **No contiene secrets** nunca. Solo referencias a dónde están los `.env`.

## Reglas de edición
- `CONTEXT.md` y `README.md` se actualizan cuando cambian prioridades/estado de forma estable.
- Un proyecto nuevo → archivo en `projects/<slug>.md`.
- Una decisión tomada → `decisions/<fecha>-<slug>.md` (una decisión por archivo).
- Un traspaso de sesión → `handoffs/<fecha>-<proyecto>.md`.

## Este hub lo mantiene Hermes como asistente integral
- Oscar es el CEO y decisor. Los deploys/merges a producción requieren confirmación suya.
- Hermes consolida el contexto aquí y lo mantiene sincronizado con la memoria local.

## Formato
- Markdown plano. Fechas ISO. Español (España) salvo que Oscar indique lo contrario.