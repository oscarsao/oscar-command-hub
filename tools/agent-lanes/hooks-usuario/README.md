# hooks-usuario · hook «por destino» (propuesta, sin aplicar)

Estado: **propuesta** (14-11-2026). Nada de esto está instalado. Instalarlo es configuración global y la aplica Oscar
(o con su OK explícito). Origen y datos: `decisions/2026-11-14-politica-interrupciones.md`.

## Qué resuelve
Las reglas «siempre confirma» (push/merge a master/main, despliegues, migraciones, envíos) estaban en CLAUDE.md pero los
permisos las contradecían, y el hook anterior miraba el texto del comando. Huecos verificados el 14-11:
`git push` a secas en master **no preguntaba**, `gh pr merge` no tenía hook y estaba en `allow`, y `railway up`/`apply_migration`
también estaban en `allow`.

`destino.mjs` mira el **destino real** (`@{push}`, rama actual, refspec, base del PR con `gh pr view`):
- **ask** si el destino es `master`/`main`, no se puede resolver, o la herramienta/comando está en `politica-permisos.json`.
- **allow** solo para `gh pr merge` hacia una rama no protegida (permite quitar el `allow` ancho sin más avisos).
- **nada** para el resto (sigue el flujo normal de permisos). `lane/*` y `develop` pasan sin avisar.
- SQL de Supabase: pasa solo si es claramente de solo lectura; cualquier otra cosa pregunta.

La política (ramas protegidas, comandos y herramientas que preguntan) vive en `politica-permisos.json`: se edita ahí, no en el código.

## Pruebas
```
node --test tools/agent-lanes/hooks-usuario/destino.test.mjs     # 34 pruebas, ejecutan el archivo real con JSON por stdin
```
Incluyen los 3 casos que fallaban (push a secas, `gh pr merge`, `railway up`), el falso positivo del `echo "git push origin master"`
y rutas con espacios. Comprobado con mutación: con la política vaciada fallan 18 casos.

## Cómo instalarlo (lo haces tú, o dime «aplica» y lo hago con copia de seguridad)
1. Copia de seguridad: `cp ~/.claude/settings.json ~/.claude/settings.json.bak-AAAAMMDD`.
2. Copiar `destino.mjs` y `politica-permisos.json` a `~/.claude/hooks/`.
3. En `~/.claude/settings.json`:
   - **Quitar de `permissions.allow`:** las 4 variantes de `gh pr merge`, las 4 de `railway … up` y
     `mcp__claude_ai_Supabase__apply_migration`. Se mantienen `mcp__claude_ai_Supabase__*`, Gmail, etc.: el hook filtra lo delicado.
   - **Hooks `PreToolUse`:** sustituir el primer comando del matcher `Bash` (el que mira el texto del push) por
     `node "C:/Users/oscar/.claude/hooks/destino.mjs"`, cambiar el matcher a `Bash|PowerShell` (si tu build tiene la herramienta PowerShell)
     y añadir otra entrada con el mismo comando y matcher
     `^mcp__(claude_ai_(Supabase|Gmail|Apollo_io|Vercel|ClickUp|Google_Drive|Google_Calendar)|plugin_resend_resend)__`
     (solo los servidores de la política; un `mcp__.*` ejecutaría node en cada llamada de Chrome, ≈780 en la Integradora).
     Se conserva `validate-docs-on-push.mjs`.
     El hook viejo añadía un recordatorio de `db-migration-checker` en cada push; ese recordatorio pasa a CLAUDE.md.
   - Por proyecto: quitar `Bash(railway up *)` de `oscar-hq/.claude/settings.local.json` (Oscar HQ ya está en Dokploy).
     `migrateam/.claude/settings.local.json` puede mantener `git push *` **solo cuando el hook esté instalado**.
   - **Emparejar con CLAUDE.md (obligatorio, si no se duplican los avisos):** hoy el modelo pregunta con botones porque
     CLAUDE.md dice «SIEMPRE confirma» y luego el `allow` deja ejecutar en silencio. Con el hook instalado, la misma acción
     recibiría los botones **y** el aviso de permisos. Regla nueva en `~/.claude/CLAUDE.md` (AUTONOMÍA): *para las acciones que el
     hook vigila (push/merge a master/main, despliegues, migraciones, envíos, SQL de escritura), no preguntar antes con botones:
     mostrar la evidencia como texto y lanzar la acción; el aviso de permisos es la confirmación.*
4. **Camino automatizado (comprobado en el código, 14-11):** el `gh pr merge` del lote (`batch.py:775`), `git merge` y el
   despliegue de Railway del integrador (`integrator.py:_railway`) son **subprocesos directos de Python**: el hook no los ve, así
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
