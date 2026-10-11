## Enlaces obligatorios (regla dura)

Todo mensaje a Oscar que mencione una pieza de trabajo lleva su enlace, sin excepciones:

- **Tarjeta del kanban** (cualquier `t_xxxxxxxx`): `https://kanban.pildoradigital.com/kanban?board=<board>#<id>`
  (ej. `https://kanban.pildoradigital.com/kanban?board=oscarhq#t_1a2b3c4d`). Nunca el id suelto.
- **PR**: su URL completa de GitHub (`https://github.com/<owner>/<repo>/pull/<n>`), y el repo junto al número
  (`oscar-hq#12`, no `#12`: hay PRs de varios repos con el mismo número).
- **Spec, pitch o documento**: su ruta en el repo y, si está empujada, su URL (`.../blob/<rama>/<ruta>`).

Si no conoces el board o la URL, consúltalos (`kanban show <id>`) antes de contestar; no los inventes.

## Board correcto al crear tareas

Las tareas para un carril se crean en el board de ESE carril, no en `default`:

- `claude-oscarhq` -> board `oscarhq`
- `claude-migrateam` -> board `migrateam`

Tras crearla, comprueba con `kanban show <id>` que el board y el asignado son los esperados y enlaza la tarjeta
con la regla de arriba.
