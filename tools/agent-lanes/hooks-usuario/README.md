# hooks-usuario · hook «por destino» (propuesta, sin aplicar)

Estado (14-11-2026): **instalado en `~/.claude` por orden de Oscar («aplica 1»), verificación en vivo PENDIENTE** (esa sesión no cargó los hooks:
hay que probar en una sesión nueva, ver paso 6). Los **lotes de PR preautorizados** (sección más abajo) están hechos y probados pero NO instalados. Origen y datos: `decisions/2026-11-14-politica-interrupciones.md`.

## Qué resuelve
Las reglas «siempre confirma» (push/merge a master/main, despliegues, migraciones, envíos) estaban en CLAUDE.md pero los
permisos las contradecían, y el hook anterior miraba el texto del comando. Huecos verificados el 14-11:
`git push` a secas en master **no preguntaba**, `gh pr merge` no tenía hook y estaba en `allow`, y `railway up`/`apply_migration`
también estaban en `allow` (Railway ya no existe: sus reglas se retiraron el 14-11 y el hook vigila ahora la API de Dokploy).

`destino.mjs` mira el **destino real** (`@{push}`, rama actual, refspec, base del PR con `gh pr view`):
- **ask** si el destino es `master`/`main`, no se puede resolver, o la herramienta/comando está en `politica-permisos.json`.
- **allow** solo para `gh pr merge` hacia una rama no protegida (permite quitar el `allow` ancho sin más avisos).
- **nada** para el resto (sigue el flujo normal de permisos). `lane/*` y `develop` pasan sin avisar.
- SQL de Supabase: pasa solo si es claramente de solo lectura; cualquier otra cosa pregunta.

La política (ramas protegidas, comandos y herramientas que preguntan) vive en `politica-permisos.json`: se edita ahí, no en el código.

## Pruebas
```
node --test tools/agent-lanes/hooks-usuario/destino.test.mjs tools/agent-lanes/hooks-usuario/lote.test.mjs   # 47 pruebas (35 + 12 de lotes), ejecutan el archivo real con JSON por stdin
```
Incluyen los 3 casos que fallaban (push a secas, `gh pr merge`) y el despliegue por la API de Dokploy (producción pregunta, staging pasa), el falso positivo del `echo "git push origin master"`
y rutas con espacios. Comprobado con mutación: con la política vaciada fallan 18 casos.

## Cómo instalarlo (lo haces tú, o dime «aplica» y lo hago con copia de seguridad)
1. Copia de seguridad: `cp ~/.claude/settings.json ~/.claude/settings.json.bak-AAAAMMDD`.
2. Copiar `destino.mjs` y `politica-permisos.json` a `~/.claude/hooks/`.
3. En `~/.claude/settings.json`:
   - **Quitar de `permissions.allow`:** las 4 variantes de `gh pr merge` y
     `mcp__claude_ai_Supabase__apply_migration`. Se mantienen `mcp__claude_ai_Supabase__*`, Gmail, etc.: el hook filtra lo delicado.
   - **Hooks `PreToolUse`:** sustituir el primer comando del matcher `Bash` (el que mira el texto del push) por
     `node "C:/Users/oscar/.claude/hooks/destino.mjs"`, cambiar el matcher a `Bash|PowerShell` (si tu build tiene la herramienta PowerShell)
     y añadir otra entrada con el mismo comando y matcher
     `^mcp__(claude_ai_(Supabase|Gmail|Apollo_io|Vercel|ClickUp|Google_Drive|Google_Calendar)|plugin_resend_resend)__`
     (solo los servidores de la política; un `mcp__.*` ejecutaría node en cada llamada de Chrome, ≈780 en la Integradora).
     Se conserva `validate-docs-on-push.mjs`.
     El hook viejo añadía un recordatorio de `db-migration-checker` en cada push; ese recordatorio pasa a CLAUDE.md.
   - Ya hecho el 14-11 (con copia `.bak-20261114*`): quitadas las reglas de Railway de `settings.json` y de `oscar-hq/.claude/settings.local.json`.
     `migrateam/.claude/settings.local.json` puede mantener `git push *` **solo cuando el hook esté instalado**.
   - **Emparejar con CLAUDE.md (obligatorio, si no se duplican los avisos):** hoy el modelo pregunta con botones porque
     CLAUDE.md dice «SIEMPRE confirma» y luego el `allow` deja ejecutar en silencio. Con el hook instalado, la misma acción
     recibiría los botones **y** el aviso de permisos. Regla nueva en `~/.claude/CLAUDE.md` (AUTONOMÍA): *para las acciones que el
     hook vigila (push/merge a master/main, despliegues, migraciones, envíos, SQL de escritura), no preguntar antes con botones:
     mostrar la evidencia como texto y lanzar la acción; el aviso de permisos es la confirmación.*
4. **Camino automatizado (comprobado en el código, 14-11):** el `gh pr merge` del lote (`batch.py:775`), `git merge` y los
   despliegues del integrador (`integrator.py`, hoy aún con rutas de Railway: ver tarjeta) son **subprocesos directos de Python**: el hook no los ve, así
   que lo aprobado desde Telegram sigue funcionando. Los `claude -p` con herramienta Bash son los workers, ops y revisión (un `ask`
   allí equivale a denegar, y solo ocurre si intentan empujar a master/main); triaje, consulta y explicaciones usan `Read,Grep,Glob`
   o ninguna herramienta. Las skills `integrar-lote` y `cerrar-tarea` mandan merges/pushes por Bash en sesión interactiva: ahí
   saldrá el aviso de permisos, con Oscar delante.
5. **Antes de instalar:** comprobar que el runner esté vacío (`lanes.py status`) y después que un push de lane sigue pasando
   (`git push origin lane/<id>` no debe avisar).
6. **Verificación en vivo** (el comportamiento de `ask` forzado frente a un `allow` de settings está documentado solo de forma indirecta):
   en un repo de prueba con remoto, en `master`, pedir a Claude `git push`. Debe salir el aviso de permisos. Si no sale, desinstalar y avisar.

## Reversión
Restaurar `settings.json.bak-*` y borrar los dos archivos de `~/.claude/hooks/`. No hay estado que limpiar.

## Límites conocidos (honestos)
- El análisis es de la línea de comandos: sustituciones raras (`$(…)`, `eval`) con push/merge pasan a **ask** por prudencia, pero
  no se garantiza cubrir todo lo que un shell puede expresar. No sustituye a la protección de rama del repo en GitHub.
- En `claude -p` (carriles) los hooks de usuario también corren y un `ask` se deniega: un worker que intente empujar a master recibe el motivo.
- El hook no puede saber si la sesión es interactiva (no está documentado).
- `protegidas_por_repo` está vacío: si `develop` de MigraTeam debe ser protegida (staging con usuarios reales), se añade ahí.

## Lotes: un solo «sí» para varios PR (hecho y probado; NO instalado)
Problema: con el hook, cada `gh pr merge` a master pide su aviso. Para un lote de N PR aprobados, N avisos es el mismo cuello que se quiere evitar.

Diseño (`destino.mjs` + `preautoriza.mjs` + `lote.test.mjs`):
1. Oscar aprueba el lote (botones o texto) con la lista de PR.
2. `node preautoriza.mjs --repo owner/nombre [--horas 24] [--nota "lote"] 482 483` **imprime** el JSON (no lo escribe): por cada PR lee su SHA de cabeza con
   `gh pr view`, rechaza los que no apuntan a rama protegida o no están abiertos, conserva las entradas vigentes y descarta las caducadas (máx. 72 h).
3. La herramienta Write guarda ese JSON en `~/.claude/hooks/pr-preautorizados.json`. **Ese Write dispara el aviso de permisos** (el hook protege el propio fichero):
   ahí Oscar ve repo, números, SHA y caducidad y da el único «sí».
4. `gh pr merge N` a master/main devuelve `allow` solo si hay entrada con el **mismo repo, número y SHA** y sin caducar. Un push posterior al OK cambia el SHA e invalida la preautorización.
   Fichero ausente, corrupto, PR no resoluble o comando mezclado con un push a master → `ask`.

Para instalarlo: copiar `destino.mjs` y `preautoriza.mjs` a `~/.claude/hooks/` y añadir en `settings.json`, dentro de `PreToolUse`, una entrada con matcher `Write|Edit|MultiEdit`
y el mismo comando `node "C:/Users/oscar/.claude/hooks/destino.mjs"` (la entrada actual de `Write|Edit` del escáner de secretos se conserva).

Límites honestos del lote: el guardián del fichero mira el nombre `pr-preautorizados` en el comando o la ruta; no frena a quien construya el nombre en tiempo de ejecución
(protege contra descuidos y flujos normales, no contra un agente que quiera esquivarlo). Tampoco cubre que la sesión escriba el fichero con un `ask` que Oscar acepte sin leer.

## Refuerzo del 14-11 (instalado y verificado en sesión nueva)
Tras la revisión de los CLAUDE.md (hallazgos del subagente):
- **Force push de cualquier tipo -> ask**, también a ramas propias (`-f`, `--force`, `--force-with-lease`, `-fu`, `+refspec`). Antes solo lo frenaba el texto.
- **`gh workflow run|enable|disable` -> ask** (puede lanzar un despliegue o cambiar CI).
- **`vercel env add|rm|update|pull` -> ask** (`pull` vuelca secretos a un fichero; `env ls` pasa). Límite: la forma `node .../vercel/dist/index.js env add` no empieza por `vercel` y no se ve.
- **`settings.json`:** deny con la forma `git -C <ruta>` (`reset --hard`, `push --force`, `push -f`, `branch -D`, `clean -f`), que los deny antiguos no cubrían.
- Pruebas: `node --test refuerzo.test.mjs destino.test.mjs lote.test.mjs` -> 52. Mutación sin la detección de force push -> 2 fallos.
- Sigue SIN cubrir: `alembic upgrade` remoto, push a `develop` (decisión de Oscar: no protegida), `psql` por Bash y borrados de ficheros.

