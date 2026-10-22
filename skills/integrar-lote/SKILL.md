---
name: integrar-lote
description: Integra uno o varios PR aprobados por Oscar (de carriles o propios) en la rama de producción - revisión, suite combinada, merge con la identidad correcta, cierre de tarjetas y despliegue. Úsala cuando Oscar apruebe con botón integrar o fusionar PR concretos.
---

# Integrar un lote de PR

Rol: Integrador. Merge a master/main y despliegue solo con OK de Oscar (botón). Una rama nunca se comparte entre conversaciones.

## 1. Preparar (sin tocar el checkout raíz)
- `git -C <repo> fetch origin` y worktree propio:
  `git -C <repo> worktree add -b integ/<fecha>-<tema> C:/Users/oscar/dev/_wt/<repo>-integ-<fecha> origin/<base>`
- Muestra repo, rama, SHA base y `git status --short` antes de escribir.
- Por PR: `gh pr view N --json mergeable,statusCheckRollup,files` y la tarjeta `t_*` asociada.

## 2. Revisar
- Subagente `code-reviewer` en los PR de código; `security-auditor` si tocan auth, endpoints públicos, secretos o borrados; `db-migration-checker` si hay migraciones.
- Arregla fallos menores en la rama del lote. Si algo es grave, sácalo del lote y dilo.

## 3. Combinar y probar (una sola vez; nunca suites en paralelo contra la misma BD)
- `git merge --no-ff origin/<rama-PR>` por cada PR; resuelve conflictos conservando la intención de ambos lados.
- oscar-hq: backend `py -3.12 -m pytest -q`; dashboard `corepack pnpm@10.18.3 install --frozen-lockfile`, `tsc --noEmit`, `vitest run`, build. Nunca `npm ci`: el dashboard usa pnpm (19 h de tarjetas de rebase falsas el 21-22 oct).
- MigraTeam: reglas del repo (ruff F,E9, py_compile, tests).
- Migraciones SQL nuevas: no se aplican aquí; lístalas para la skill `migracion-supabase`.

## 4. Fusionar
- Identidad de commit = la de Oscar (config global). **Nunca** `agent-lanes@localhost`: Vercel bloquea el despliegue (incidente recursos, 22-10).
- PR del lote → `gh pr merge --merge`. Comenta en los PR originales «incluido en #X» y ciérralos.
- Tarjetas: `hermes kanban --board <b> comment <id> 'Integrada en <SHA> (#X)'` (comillas simples) y pásalas a done.

## 5. Desplegar y verificar
- Backend: skill `desplegar-dokploy`. Frontends: Vercel READY del commit del merge.
- Prueba en producción de cada cosa integrada.

## 6. Informar
SHA de merge, hallazgos de revisión, pruebas, migraciones pendientes, despliegue y «cómo lo pruebas tú» (URL + pasos). Borra el worktree solo si está limpio y fusionado.
