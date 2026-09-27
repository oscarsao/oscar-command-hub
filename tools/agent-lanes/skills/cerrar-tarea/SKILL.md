---
name: cerrar-tarea
description: Cierra una tarea tomada con /tomar-tarea. Hace la misma verificación mecánica que el runner de agent-lanes (rama empujada, SHA, test_cmd del carril) y la pasa a review; el carril review la revisa después. Úsala cuando Oscar diga "/cerrar-tarea t_xxxx".
---

# /cerrar-tarea <task_id>

1. En el worktree de la tarea (`C:/Users/oscar/dev/_lanes/lane-<task_id>`):
   - todo commiteado (`git status --short` vacío);
   - `git push -u origin lane/<task_id>` hecho.
2. Pásala a review con un resumen de 1-3 frases: qué cambiaste y cómo lo verificaste.
   ```bash
   py -3.12 C:/Users/oscar/oscar-command-hub/tools/agent-lanes/lanes.py close <task_id> "<resumen>"
   ```
   El script comprueba, sin fiarse de ti:
   - que `lane/<task_id>` existe en origin con el mismo SHA que tu HEAD;
   - que tiene commits sobre la base;
   - que no toca rutas vetadas del carril;
   - que el `test_cmd` del carril pasa.
   Si algo falla, **la tarea sigue en running**: arregla y repite. No uses `hermes kanban complete` para saltártelo.
3. Si en lugar de terminar necesitas una decisión de Oscar:
   ```bash
   C:/Users/oscar/AppData/Local/hermes/bin/hermes kanban --board <board> block <task_id> --kind needs_input "<pregunta con opciones y recomendación>"
   ```
4. **Nunca** merge ni borrado de la rama remota: eso lo hace Oscar o el Integrador después de la review.
