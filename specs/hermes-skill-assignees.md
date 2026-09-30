# Propuesta de cambio en la skill de Hermes (NO aplicada)

Archivo: `C:/Users/oscar/AppData/Local/hermes/skills/autonomous-ai-agents/oscar-multi-agent-orchestration/SKILL.md`
(fuera del repo: Oscar o Hermes lo aplican a mano). Fuente de verdad de los carriles: `tools/agent-lanes/lanes.yaml`.

## Problemas
1. `:83` dice "Valores válidos, solo estos: claude-oscarhq, claude-migrateam, claude-scraper, claude-nextjobs u oscar" y
   `:87` habla de "los cinco valores válidos", pero `:177` manda el trabajo operativo a `claude-ops`, que `:83` no
   permite. Tampoco aparece `claude-hub` (el sistema de carriles). Las dos líneas se contradicen.
2. Falta qué assignee es válido en qué board. t_cb53e597 se creó con `claude-oscarhq` en el board `default`: el runner
   de `claude-oscarhq` solo vigila `oscarhq`, y nadie la reclamó durante 36 h.

## Tabla canónica (según lanes.yaml)

| assignee | board en el que se le crea la tarea |
|---|---|
| `claude-oscarhq` | `oscarhq` (SOLO ahí) |
| `claude-scraper` | `oscarhq` |
| `claude-ops` | `oscarhq` |
| `claude-migrateam` | `migrateam` |
| `claude-nextjobs` | `default` |
| `claude-hub` | `default` |
| `oscar` | el board del repo/marca |

## Diff propuesto

```diff
@@ :83
-- **Valores válidos, solo estos:** `claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs` u `oscar`.
+- **Valores válidos, solo estos:** `claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs`,
+  `claude-ops`, `claude-hub` u `oscar`. Cada carril solo lo recoge el runner si la tarea está en SU board:
+  `claude-oscarhq`, `claude-scraper` y `claude-ops` → board `oscarhq`; `claude-migrateam` → `migrateam`;
+  `claude-nextjobs` y `claude-hub` → `default`. `claude-oscarhq` en el board `default` no lo recoge nadie
+  (t_cb53e597 esperó 36 h): si el board no casa con el carril, corrige el board antes de crear.
@@ :87
-- **Antes de `kanban_create`,** comprueba que el assignee es uno de los cinco valores válidos. Si no lo es, no la crees y pregunta con `clarify`.
+- **Antes de `kanban_create`,** comprueba que el assignee es uno de los siete valores válidos Y que el `board` es el de la tabla. Si no, no la crees y pregunta con `clarify`.
@@ tabla de :68-74 (añadir filas)
+| Operativo (PC, cuentas, calendario, Drive, accesos) | `oscarhq` | `claude-ops` |
+| Sistema de carriles (agent-lanes, monitor, bot de Telegram) | `default` | `claude-hub` |
@@ :139-140 (obsoleto: "Hoy existe claude-oscarhq / Previstos…")
-- Hoy existe **`claude-oscarhq`**.
-- Previstos: `claude-migrateam`, `claude-scraper`, `claude-nextjobs`, `review` y `crew:<rol>`.
+- Carriles activos: `claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs`, `claude-ops`,
+  `claude-hub` y `review` (la lista y los boards salen de `lanes.yaml`).
```

`:177` ya es correcto; con el cambio de `:83` deja de contradecirse.
