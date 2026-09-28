# agent-lanes: carriles de agentes

Runner que consume las tarjetas `ready` del kanban de Hermes asignadas a un carril (`assignee` = nombre del carril),
lanza un `claude -p` acotado por hooks y deja el resultado en `review` para que lo valide Oscar.
Contrato y arquitectura: `decisions/2026-09-27-arquitectura-carriles.md`. Configuración: `lanes.yaml`.

## Qué va a cada carril

| Assignee | Para qué | Resultado |
|---|---|---|
| `claude-<repo>` (`claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs`) | Código de ESE repo: bugfix, feature, spec, docs versionados | Rama `lane/<id>` empujada, `test_cmd` del carril, carril `review` y PR con [✅ Aprobar] |
| `claude-ops` | Trabajo operativo sin repo: inventariar o copiar carpetas (vault/buzón del disco E:), leer calendario, Drive o ClickUp y proponer cambios, preparar accesos, planes e informes | Carpeta `C:/Users/oscar/dev/_lanes/ops/<id>` con evidencias; siempre a `review` con [✅ Validar / 🔁 Pedir cambios] |
| `oscar` | Lo que exige a Oscar: firmar, pagar, decisiones de negocio, credenciales, ejecutar una acción externa ya aprobada (v1), cualquier cosa en producción | Lo hace Oscar (también es donde acaba el botón 🗄 Aparcar) |

Regla rápida: si el entregable es un commit, va a `claude-<repo>`. Si toca cuentas, discos o servicios y se puede
preparar sin efectos irreversibles, va a `claude-ops`. Si necesita a Oscar o no se puede deshacer, va a `oscar`
(o a `claude-ops` para que lo prepare y lo proponga).

## Carril ops (`kind: ops`)

### Cómo se escribe la tarea
En el cuerpo de la tarjeta, además del objetivo y los criterios, una ruta por línea:

```
Origen-Ops: E:/Obsidian/Vault            # solo lectura (se pasa con --add-dir)
Destino-Ops: C:/Users/oscar/Obsidian     # se puede escribir aquí, además del workspace
```

Los destinos los valida el runner, no el worker: deben ser absolutos, colgar de `dest_roots` de `lanes.yaml`
(hoy `C:/Users/oscar`), no ser rutas protegidas (sistema, `AppData`, cualquier `~/.*` como `~/.claude.json` o
`~/.local/bin`, perfiles de PowerShell, el hub, `dev/_lanes`, `dev/_hub-wt` y los `repo` de todos los carriles) y
no solaparse con un origen. Un destino inválido bloquea la tarea antes de lanzar el worker.

### Límites de seguridad (`contract/ops_guard.py`, hook PreToolUse con matcher `*`, lista blanca, fail closed)
- Herramientas: Bash, Read/Grep/Glob, Edit/Write/MultiEdit y las internas inocuas (TodoWrite, ToolSearch,
  StructuredOutput…). PowerShell, Agent, WebFetch, WebSearch, Skill, NotebookEdit y cualquier otra: bloqueadas.
- Bash: solo comandos de lectura (`ls`, `cat`, `find` sin `-delete/-exec`, `grep`, `sha256sum`, `git`/`gh` de
  lectura, `cloudflared tunnel list|info`…) y de copia (`cp`, `mkdir`, `touch`, `tee`, `robocopy`/`xcopy` sin
  `/MIR /MOV /MOVE /PURGE`), con el destino dentro del workspace o de un `Destino-Ops`. Las redirecciones `>` se
  comprueban igual. Bloqueado: borrar, mover, `format`, `diskpart`, `reg`, `schtasks`, `sc`, `netsh`, `setx`,
  `bcdedit`, `git push`, `gh pr merge`, `railway`, `vercel`, `alembic`, `supabase`, intérpretes y shells anidados,
  `curl`/`wget`, variables `$X` y `$(...)`.
- Secretos: cualquier referencia a `.env`, tokens, claves o credenciales se bloquea (Bash, Read, Grep, Glob, Write).
  Además, raíces protegidas en lectura: `~/AppData` (credenciales de gh/vercel/railway), `~/.claude*`,
  `~/.cloudflared`, `~/.config`, `~/.ssh`, `~/.docker`, `~/.npmrc`, `.env`/`.state` de agent-lanes. Un comando
  recursivo (`grep -r`, `rg`, `cp -r`, `robocopy`) tampoco puede partir de una carpeta que las contenga (`~`).
  El túnel se consulta con `cloudflared tunnel list|info`.
- Flags con valor pegado (`--output=`, `-fcampo=`, `--input=`) cuentan igual; `robocopy /JOB /SAVE` y `cp -l/-s`
  (enlaces al original) bloqueados.
- Rutas: `realpath` + `normcase` y contención por ruta (`ops/t_1` no cubre `ops/t_10`); `/c/...` de Git Bash y
  relativas al `cwd` real (sigue los `cd` del comando).
- MCP: sin `--strict-mcp-config`, así cargan los conectores de claude.ai en `-p`. El hook solo deja pasar nombres
  exactos de LECTURA de Google Calendar (list/get/search), Google Drive (search/read/metadata/permisos/recientes) y
  ClickUp (get/filter/search); `--allowedTools` los lista y `ops-worker-settings.json` deniega explícitamente las de
  escritura de esos servidores y los servidores enteros de riesgo (deny prevalece sobre allow, por eso no hay
  `deny: mcp__*`). `download_file_content` y `clickup_execute_operator` quedan fuera.
- El workspace y los destinos llegan al hook por variables de entorno que fija el runner
  (`AGENT_LANES_WORKSPACE`, `AGENT_LANES_OPS_DESTS`), nunca por un fichero que el worker pueda editar.

### Resultado y verificación
Schema propio `contract/ops-result.schema.json`: `evidence[]` (`path`, `hash`, `count`, `link`, `note`) y
`proposed_actions[]` (acción exacta: `id`, `kind`, `description`, `tool`, `arguments`). El runner (`agent_lanes/ops.py`)
comprueba que cada ruta de evidencia existe dentro del workspace o de un destino declarado, recalcula los sha256 y
el nº de archivos, y exige al menos una evidencia de ruta. Nunca marca done: `request-review` y aviso con
[✅ Validar] (hace `complete` + comentario `VALIDADO-OSCAR`, sin PR) / [🔁 Pedir cambios] (vuelve al carril con el
comentario) / [🗄 Aparcar]. El carril `review` no revisa ops y `sweep_done` no borra sus workspaces.
Los `needs_input` de ops entran en `/decisiones` y en los recordatorios; la review de ops pendiente todavía no
(siguiente paso: un plan de "review ops" en `renotify.py`, y `/tarea` con sus botones).

### Acciones externas o irreversibles
Compartir, invitar, crear eventos/calendarios/tareas, mover el original, configurar un túnel o servicio: el worker
NO las ejecuta (el hook se lo impide). Las devuelve como `needs_input` con la acción exacta en `proposed_actions`
(queda en la tarjeta) y una pregunta [Aprobar / No]. Al retomar con la respuesta de Oscar, la v1 termina `done` con
cada acción aprobada en `next_steps` ("APROBADA, pendiente de ejecución") y la ejecuta Oscar.

**Siguiente paso (v2, no implementado):** ejecución de solo lo aprobado.
1. Al pulsar [Aprobar] sobre una pregunta ligada a una acción, `DecisionDesk` escribe
   `.state/ops-approvals/<task_id>.json` con `{id, tool, sha256(tool + arguments canónicos), aprobado_por, fecha}`.
   Ese directorio está fuera del workspace y es ruta protegida: el worker no puede escribirlo.
2. En la segunda pasada, `ops_guard` calcula el mismo hash de cada llamada (herramienta MCP o comando) y la deja
   pasar solo si coincide con una aprobación de su `AGENT_LANES_TASK`; cada aprobación se consume una vez.
3. El resultado lleva la evidencia de la ejecución (enlace/id devuelto) y vuelve a review como siempre.
Requiere preguntas con `action_id` en el schema y un tipo de botón nuevo; hasta entonces v1 termina en propuesta.

## Otros
- Tests: `cd tools/agent-lanes && py -3.12 -m pytest -q`.
- Una pasada de un carril: `py -3.12 runner.py --lane claude-ops --once`. Estado: `py -3.12 lanes.py status`
  ("arrancando" = claim recién hecho con el runner vivo; "huérfano" solo con el runner parado).
- Reiniciar el runner: SIEMPRE `py -3.12 lanes.py restart --drain [--timeout 3600]`, nunca `schtasks /End` a pelo
  (incidente 27-09). Deja de reclamar, espera a que acaben workers/deploys, reinicia la tarea "agent-lanes runner".
  Un `.state/drain.json` de más de 2 h se ignora.
- Candado de repos: cada carril de código declara `github: owner/repo`; si el remote del repo no coincide (o falta),
  el carril no reclama y el log dice ERROR una vez. Un carril nuevo sin `github:` queda parado.
- /salud en el bot de carriles: runner, Hermes, servicios y alertas del monitor, develop↔master de MigraTeam, RAM/CPU
  y decisiones pendientes. Solo lecturas locales (sin HTTP: eso lo hace el monitor).
