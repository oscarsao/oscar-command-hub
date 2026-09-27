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
- No es donde se desarrolla código de producto (eso vive en migrateam/oscar-hq/pildora/etc). Única
  excepción: `tools/agent-lanes`.
- **No contiene secrets** nunca. Solo referencias a dónde están los `.env`.

## Reglas de edición
- `CONTEXT.md` y `README.md` se actualizan cuando cambian prioridades/estado de forma estable.
- Un proyecto nuevo → archivo en `projects/<slug>.md`.
- Una decisión tomada → `decisions/<fecha>-<slug>.md` (una decisión por archivo).
- Un traspaso de sesión → `handoffs/<fecha>-<proyecto>.md`.

## Fuentes de verdad y carriles (2026-09-27)
- **Ejecución:** kanban de Hermes. **Negocio:** kanban de Píldora. **Personal:** ClickUp. **Conocimiento:** este hub.
- **Reglas de agentes:** mandan desde `~/.claude/CLAUDE.md`; excepciones en el AGENTS.md de cada repo.
- `tools/agent-lanes` es el único código de este repo: el runner de carriles. Tiene sus tests y su `.env`
  (ignorado por git).
- Hermes = Coordinador: registra, decide y avisa, pero no implementa. La Sesión Maestra está archivada (`archive/`).

## Mantenimiento
- Oscar es el CEO y decisor. Los deploys/merges a producción requieren confirmación suya.
- Hermes y las sesiones coordinadoras consolidan aquí el contexto estable (decisiones, referencias).

## Formato
- Markdown plano. Fechas ISO. Español (España) salvo que Oscar indique lo contrario.