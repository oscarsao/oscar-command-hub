---
name: tomar-tarea
description: Toma una tarea del kanban de Hermes desde una consola interactiva de Claude Code (claim + worktree lane/<id>), igual que haría el runner de agent-lanes. Úsala cuando Oscar diga "/tomar-tarea t_xxxx" o "coge la tarea t_xxxx".
---

# /tomar-tarea <task_id>

Trabajas como **Implementador de UNA tarea**, igual que un worker de carril pero con Oscar delante.

1. Reclama la tarea y crea su worktree (nunca a mano):
   ```bash
   py -3.12 C:/Users/oscar/oscar-command-hub/tools/agent-lanes/lanes.py take <task_id>
   ```
   - Imprime JSON con `worktree`, `branch` (`lane/<task_id>`), `base` y `lease_minutes`.
   - Si falla (no está en `ready`, o su assignee no es un carril), para y díselo a Oscar. No reintentes con otra vía.
2. Trabaja **solo en ese worktree** (`cd` al path del JSON). Nunca en el checkout raíz del repo.
3. Lee `AGENTS.md`/`CLAUDE.md` del repo y `tools/agent-lanes/roles/implementador.md`. Aplican igual:
   - commit + push SOLO de `lane/<task_id>`;
   - nunca merge, push a master/main, deploy, Alembic remoto ni `.github/workflows/`.
4. El lease dura `lease_minutes`. El heartbeat de la CLI no lo alarga, así que, si vas a pasarte, avisa a Oscar.
5. Para terminar, usa `/cerrar-tarea <task_id>`.
