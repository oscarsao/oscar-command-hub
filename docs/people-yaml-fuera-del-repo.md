# people.yaml real fuera del repo (t_6ea0d37c)

El repo es público y `tools/telegram/people.yaml` llevaba los ids de Telegram de personas del equipo. Desde este cambio:

- En el repo solo hay **`tools/telegram/people.example.yaml`** (ids ficticios `5000000001`…). Es la plantilla.
- El fichero **real** se lee de la variable de entorno `AGENT_LANES_PEOPLE_FILE` o, si no existe, de
  **`tools/agent-lanes/.state/people.yaml`** (`.state/` está en `.gitignore`).
- Si el fichero real no existe o está roto, el bot **falla cerrado**: solo Oscar tiene acceso. El ejemplo nunca se usa de respaldo.
- Los tests no tocan el real: un fixture de `conftest.py` apunta siempre al ejemplo.

## Cómo copiar el people.yaml real (lo hace Oscar o el integrador, ANTES de pulsar [🔁 Aplicar])

El runner vivo ejecuta el checkout de `main`; cuando se fusione este PR y se aplique, el `people.yaml` antiguo desaparece
de ahí. Hay que dejar el real en su sitio nuevo primero:

```bash
# desde el checkout de main, antes de fusionar (o sacándolo del commit anterior con git show)
cp tools/telegram/people.yaml tools/agent-lanes/.state/people.yaml
# alternativa tras fusionar:  git show <commit-previo>:tools/telegram/people.yaml > tools/agent-lanes/.state/people.yaml
```

Comprobación: `py -3.12 tools/telegram/sincronizar_permisos.py` (sin `--apply`) debe listar a las mismas personas que antes.
Si el fichero vive en otra ruta, define `AGENT_LANES_PEOPLE_FILE` en el entorno del runner y del sincronizador.
Las ediciones futuras (alta/baja, `sync_usuarios --escribir`, `sincronizar_permisos --apply`) se hacen sobre el real.

## Pendiente de Oscar

- El historial de git conserva los ids antiguos. Reescribirlo no se hace aquí (decisión aparte): basta con dejar de
  publicarlos y, si se quiere, hacer el repo privado (punto 4 del paquete).
- Quedan en el repo el id de Oscar (`OWNER_TELEGRAM_ID`) y los ids de grupos/temas de `lanes.yaml`, scripts de temas y
  varios tests: los usa el código en marcha, así que sacarlos es otra tarea (moverlos a configuración local).
- `tools/telegram/people.yaml` queda como stub vacío (la tarea no pudo borrarlo): `git rm tools/telegram/people.yaml`.
