# Lote diario del integrador

Para Oscar: en vez de un PR y un botón por cada tarea aprobada, cada proyecto con `batch: daily` junta lo aprobado en
**un solo lote al día** y te manda **una ficha 🚦 con un botón "🔀 Fusionar lote"**.

Activado de entrada en `claude-migrateam` y `claude-oscarhq` (`tools/agent-lanes/lanes.yaml`). `claude-hub` y el resto
siguen PR a PR: con `batch` ausente no cambia nada.

## Qué pasa

1. Apruebas una tarea como siempre (✅). Con `batch: daily` no sale ficha suelta ni se pasan gates sueltos: la rama
   queda en cola (`batch_pending`).
2. A la hora `batch_time` (por defecto **08:30**, hora local) o con `/lote migrateam` / `/lote oscarhq`:
   - se crea `release/lote-<marca>-<fecha>` desde `<remote>/<base>` (si ya hay una ese día, `-2`, `-3`…);
   - se fusionan en ella, **en orden de aprobación**, las ramas `lane/*` aprobadas;
   - se abre **un solo PR** hacia la base.
3. Gates:
   - **por rama**: conflicto con la base o con otra rama del lote, rutas vetadas del carril, ejecutables o módulos que
     suplantan la stdlib, `.env`/`.gitattributes`, secretos y un solo head de Alembic;
   - **del lote combinado**: `test_cmd` del carril (la suite que ya usa el carril) y, si el lote toca
     `batch_frontend_paths`, `batch_frontend_cmd` (build/typecheck).
   - Una rama que choca o rompe un gate queda **fuera** con su motivo (en la ficha y en su tarjeta) y **no bloquea** a
     las demás; vuelve a la cola del lote siguiente. Si el combinado falla, cada rama se prueba sola: la culpable sale
     y se reprueba el resto. Si aun así falla, no se abre PR.
4. La ficha lista "Incluye (N)" y "Fuera del lote (M) — motivo", gates, riesgos y nota de deploy.
5. Al pulsar **🔀 Fusionar lote** (solo Oscar): se comprueba que el PR y la base siguen siendo lo probado y se fusiona
   (`--squash --match-head-commit`). Cada rama recibe `INTEGRADO <sha>`, sus PR sueltos se cierran y sigue el flujo del
   carril (verificar staging en MigraTeam, 🚀 Desplegar en Oscar HQ…) a nivel de lote.
   **Migraciones**: se agregan las de todas las ramas del lote; "✅ Migración aplicada" vale para todo el lote.
6. Si la base se mueve entre el montaje y el botón, no se fusiona: `/lote <marca>` cierra el PR viejo y monta uno nuevo.

## Configuración (política del integrador)

```yaml
claude-oscarhq:
  batch: daily                # ausente = PR a PR
  batch_time: "08:30"         # opcional
  batch_brand: oscarhq        # opcional (nombre en la rama y en /lote); por defecto el carril sin "claude-"
  batch_frontend_paths: [frontend/]        # opcional
  batch_frontend_cmd: npm run typecheck    # opcional; se ejecuta en la raíz del worktree del lote
```

Nota de seguridad: el integrador seguía sin hacer `git push` nunca. Ahora hace **un único push**, de la rama
`release/lote-*` que él mismo monta (sin `--force`, sin hooks, validado con regex); a la base y a cualquier otra rama
sigue prohibido.

## Prueba en seco sobre una base de prueba

Solo lectura: `fetch` + worktree temporal; no empuja, no abre PR, no guarda estado, no habla con Telegram ni comenta.

```
py -3.12 -m agent_lanes.integrator --dry-run --lane claude-oscarhq --batch 12,15,18
```

Los números son PR abiertos del repo del carril, en el orden en que se fusionarían. Salida de ejemplo (base de prueba
con un PR que choca con otro y uno limpio):

```
[dry-run] lote oscarhq sobre main @ 3f9a1c0d2b7e: OK
  ✔ incluye #12 (t_a)
  ✔ incluye #18 (t_b)
  ⛔ fuera #15: choca con main o con otra rama del lote (src/a.py)
  gates: sin conflictos con main, sin secretos, tests OK
  botón: 🔀 Fusionar lote
```

Esa misma prueba está automatizada con git real en repos temporales (origen bare local, `gh` simulado):
`py -3.12 -m pytest tools/agent-lanes/tests/test_batch.py -q` — en particular `test_dry_run_builds_without_push_pr_or_state`
(dry-run), `test_assemble_merges_in_approval_order_one_pr_one_button`, `test_excluded_branches_do_not_block_the_rest`,
`test_combined_test_failure_isolates_the_culprit` y `test_merge_button_integrates_members_closes_prs_and_aggregates_migration`.

## Límites conocidos

- Una tarea que depende de otra aún no integrada espera (como antes): si el padre va en el lote de hoy, el hijo entra
  en el de mañana.
- El resumen fijado del tema de Integración no lista aún las ramas en cola/lote (la ficha del lote sí).
- Montar un lote ejecuta la suite del carril (hasta 30 min por ejecución) en el hilo del runner; `/lote` lo hace en un
  hilo aparte y avisa al terminar.
