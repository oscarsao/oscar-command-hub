# Spec-Lite · Reconciliación del kanban (t_d6c82325)

**Problema.** Un PR mergeado, un archivo o una decisión tomada en otro canal (chat del integrador, Telegram, consola)
no llegan al kanban: la tarjeta sigue abierta y el aviso sigue apuntando a Oscar aunque no pueda actuar.

**Alcance** (módulo nuevo `agent_lanes/reconcile.py`; solo botones, nadie archiva sola, merge/deploy/migraciones siguen
siendo de Oscar):
1. **Cierre automático.** Cada pasada del runner (`RECONCILE_MINUTES`, 10 por defecto) lee los PR recientes (`gh pr
   list --state all`). Vínculo *fuerte* con una tarjeta: rama `lane/<id>`, id `t_xxxxxxxx` en el título, o en el cuerpo
   tras `Cierra:`/`Resuelve:`/`Tarjeta:`/`Closes:`. (Un id suelto en el cuerpo, p. ej. «se apoya en t_x», NO cierra.)
   PR mergeado → comentario + `complete` («resuelta en PR #n»); PR cerrado sin merge → comentario + archivar. Un solo
   aviso por tarjeta y PR (estado en `.state/reconcile.json`). Nunca toca tarjetas `running`.
2. **Pasada diaria de obsolescencia.** Tarjetas abiertas con (a) PR mergeado/cerrado, (b) «cubierta por/duplicada de
   t_y» con t_y ya hecha, o (c) >72 h sin movimiento. Se propone con un mensaje por tarjeta y botones
   [🗄 Archivar] [⏳ Mantener 7 d] (máx. 5 por día). Nunca se archiva sola.
3. **Aviso con próximo paso y responsable.** Todo aviso lleva `➡️ Próximo paso: … · Lo da: …` (`notices.next_step`).
   Si Oscar no puede actuar (consola/guard/config) lo da el integrador (oscar-e2) y el aviso lleva el botón
   [🧑‍💻 Que lo resuelva el integrador] (reasigna la tarjeta a `INTEGRATOR_PROFILE`).
4. **Registro de decisiones por el integrador.** `lanes.py tarjeta <comentar|archivar|reasignar|resuelta> <t_id>
   --autor oscar-e2 [--a perfil] "texto"`: actúa en el kanban, deja el comentario `REGISTRO por <autor>: …` y una línea
   en `.state/reconcile-audit.jsonl` (fecha, autor, acción, tarjeta, texto).

**Fuera de alcance:** reiniciar el runner (lo decide Oscar), tocar hooks/contrato.
**Tests:** `tests/test_reconciliacion.py` (a, b, c y el registro).
