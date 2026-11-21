# Segundo ordenador como worker de agent-lanes

Estado: **borrador (19-nov-2026)**. Esta guía y `scripts/instalar_worker_remoto.ps1` dejan un segundo PC Windows 11
**listo para trabajar**, pero hay un bloqueo conocido del runner (sección 7) que impide arrancarlo a la vez que el
equipo principal. Hasta que ese bloqueo se arregle, todo lo de aquí es preparación sin riesgo.

Este fichero es público (el repo del hub lo es): no contiene secretos, cuentas, correos ni ids de chat, y no debe
llegar a tenerlos. Donde hace falta un dato privado, dice **de dónde sale**, no cuál es.

## 0. Qué se consigue y qué no

- El segundo equipo ejecuta un runner de agent-lanes con **su propia cuenta de Claude** (`claude -p`, su propia cuota)
  y reclama tarjetas del **mismo kanban** que el equipo principal. Dos equipos = el doble de plazas y cuotas separadas.
- Hace falta que el kanban sea común: **backend `supabase`** (tablas `hub.*`). El kanban `hermes` es un SQLite local
  y no se puede compartir. En el `.env` del equipo principal, a 19-nov, no hay `KANBAN_BACKEND` (es decir, `hermes`
  salvo que lo fije el entorno del servicio): **antes de nada hay que confirmar que el corte F2 a supabase está hecho**
  (`decisions/2026-11-02-kanban-supabase-espejo.md`, pasos 1 a 3). Si no lo está, esto no se puede usar todavía.
- El segundo equipo **no** es un segundo cerebro: no lleva bots de Telegram, ni integrador, ni despliegues. Solo
  reclama tarjetas, trabaja en worktrees `lane/<id>` y empuja esas ramas. Fusionar, desplegar y las migraciones siguen
  siendo del equipo principal y de Oscar (CLAUDE.md global, sección 1).

## 1. Requisitos del equipo

Windows 11, 16 GB de RAM como mínimo (ver sección 5), disco con 20 GB libres, internet estable y **la sesión de Windows
abierta** (el runner es una tarea «al iniciar sesión», sin servicio de sistema).

| Herramienta | Versión | Cómo se comprueba |
|---|---|---|
| Git for Windows | reciente | `git --version`; identidad (`git config --global user.name` y `user.email`) puesta con la de los carriles |
| GitHub CLI | reciente | `gh auth status` debe dar sesión con una cuenta con **escritura** en los repos de la sección 3 |
| Node | 24.x | `node --version` |
| Python | 3.12 con el lanzador `py` | `py -3.12 --version`; paquetes `pyyaml` y `psycopg2-binary` (`py -3.12 -m pip install pyyaml psycopg2-binary`) |
| pnpm | 10.18.3 por corepack | ver sección 2 |
| Claude Code | reciente | `claude --version`, sesión iniciada con la **cuenta de este equipo** |

`claude -p` disponible quiere decir: `claude -p "responde solo: ok"` contesta sin pedir login ni aprobar nada. El
script lo hace con `-ProbarClaude` (gasta una llamada mínima). No pongas `ANTHROPIC_API_KEY` en el entorno: los
workers facturarían por API en lugar de por el plan (el script avisa si la ve, sin mostrarla).

## 2. Instalar pnpm (una vez)

```powershell
corepack enable
corepack prepare pnpm@10.18.3 --activate
```

Es el mismo pin que usa el runner (`agent_lanes/pnpm_setup.py`). El runner instala las dependencias de cada worktree
solo (`pnpm install --frozen-lockfile`).

## 3. Repos, carpetas y worktrees

El runner lee **`lanes.yaml`**, que está versionado y trae las rutas de este PC principal (`C:/Users/oscar/...`). No
hay un `lanes.yaml` por equipo: la forma más simple de que el segundo equipo funcione sin tocar nada es que **use el
mismo nombre de usuario de Windows (`oscar`)** y las mismas rutas. Si no puede ser, mira la sección 5 (edición local).

Rutas esperadas (el script las lee de `lanes.yaml`, no están escritas en él):

| Carril | Carpeta | Repo en GitHub | Rama base |
|---|---|---|---|
| claude-hub (y el runner mismo) | `C:\Users\oscar\oscar-command-hub` | oscarsao/oscar-command-hub | main |
| claude-oscarhq | `C:\Users\oscar\dev\oscar-hq` | oscarsao/oscar-hq | master |
| claude-migrateam | `C:\Users\oscar\dev\migrateam` | PildoraDigital/OCR-PDF-and-images | develop |
| claude-scraper | `C:\Users\oscar\dev\Scraper` | oscarsao/icp-prospect-engine | main |
| claude-nextjobs | `C:\Users\oscar\nextjobs\autoapply_v5` | oscarsao/nextjobs-autoapply | master |
| worktrees de todos los carriles | `C:\Users\oscar\dev\_lanes` | (carpeta, no repo) | |

- **Los clones son «de fábrica»**: nadie trabaja en ellos a mano. Cada tarjeta crea su worktree en `_lanes\lane-<id>`
  a partir de `origin/<base>`. El clon del hub debe estar en `main` y limpio: el runner se ejecuta desde él y los hooks
  de los workers salen de ahí. Actualízalo con `git -C C:\Users\oscar\oscar-command-hub pull --ff-only`.
- **`--all` necesita todos los repos**: el runner recorre todos los carriles de `lanes.yaml`. Un repo que falte hace
  que las tarjetas de ese carril se bloqueen en este equipo. Si no quieres uno (p. ej. NextJobs, pausado), sigue la
  opción de edición local de la sección 5.
- El runner solo reclama si `git remote get-url origin` de cada repo coincide con el `github:` de `lanes.yaml`
  (el script lo comprueba).

El script hace todo esto por ti:

```powershell
cd C:\Users\oscar\oscar-command-hub      # o donde hayas clonado el hub
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\scripts\instalar_worker_remoto.ps1 -Comprobar   # solo lectura
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\scripts\instalar_worker_remoto.ps1              # crea carpetas y clona lo que falte
```

El hub hay que clonarlo antes a mano (`gh repo clone oscarsao/oscar-command-hub C:\Users\oscar\oscar-command-hub`),
porque el script vive dentro de él. Es idempotente: repetirlo no cambia nada que ya esté bien, y **no borra nunca nada**.

## 4. El `.env` del equipo (solo nombres)

Fichero: `tools\agent-lanes\.env` (ya está en `.gitignore`; el script comprueba que git lo ignora). Lo creas tú a mano;
el script solo lo lee y dice qué nombres están rellenos, nunca imprime un valor.

| Variable | Obligatoria | Valor y de dónde sale |
|---|---|---|
| `KANBAN_BACKEND` | sí | `supabase` (no es un secreto) |
| `HUB_DB_DSN` | sí | Cadena de conexión Postgres del rol `hub_core` del proyecto Supabase de Oscar HQ. **Sale del gestor de contraseñas de Oscar** (o del panel de Supabase, apartado de conexión; el rol `hub_core` viene del esquema F2-01 y su contraseña la maneja Oscar). No está en el repo, ni en lanes.yaml ni en ningún chat. Pásala por un canal privado y pégala directamente en el `.env` |
| `HUB_WORKER_ID` | sí | Nombre **único** de este equipo (p. ej. `pc2-<lugar>`). No es secreto. Es el dueño que queda escrito en cada reserva (`lock=` en el evento `claimed`). Sin él se usa el nombre del equipo; ponlo explícito para que no cambie si renombras el PC |
| `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` | no | Solo si quieres que este equipo **envíe** avisos de inicio/fin. Salen del `.env` del equipo principal (mismo canal privado). Sin ellos, este equipo trabaja en silencio y el estado se ve en el kanban y en el panel de Telegram del principal |

**No pongas en este equipo**: `CARRILES_BOT_TOKEN` (dos procesos escuchando al mismo bot se pisan), `INTEGRATOR_ENABLED=true`
(el integrador solo corre en el principal), `BOARD_GUARD_APPLY=true`, ni claves de Oscar HQ (`OHQ_API_KEY`). El script
marca como FALTA las dos primeras.

Prueba de la conexión, solo lectura: `-ProbarConexion` (ejecuta `runner.py --check`, que comprueba el contrato del
esquema `hub.*`, sin reclamar nada).

## 5. `lanes.yaml` y la RAM de este equipo

No hay un `lanes.yaml` propio por equipo. Lo que **sí** se ajusta por equipo sin editarlo:

- **Tope global de workers:** `install-service.ps1 -MaxWorkers N` (o `runner.py --max-workers N`). Esa opción **pisa**
  `runner.max_workers` de `lanes.yaml` (hoy 6, calibrado para 32 GB). Orientación por RAM (cada `claude` con su
  `pnpm`/`tsc` ronda varios GB; hubo un OOM el 27-09): **16 GB → 2**, 32 GB → 3 (el script imprime la cifra de tu
  equipo). Empieza en **1** durante la prueba.
- `max_parallel` por carril (oscarhq 4, hub 2, review 2) no hace falta tocarlo: manda el tope global.

Lo que **no** se puede por equipo hoy (no existe en el código): elegir qué carriles corren. `runner.py --all` los
corre todos; `runner.py --lane X` corre uno solo, pero el candado `.state/runner.lock` impide dos procesos. Si quieres
limitar carriles, la salida provisional es una **edición local** de `lanes.yaml` (rutas, o quitar carriles) marcada con
`git update-index --skip-worktree tools/agent-lanes/lanes.yaml`; cuesta un conflicto en cada `git pull` que toque ese
fichero, así que es mejor evitarlo con el mismo usuario `oscar` y todos los repos. Mejora propuesta (no hecha): una
variable `LANES_ONLY` en el `.env` del equipo.

## 6. Comprobar antes de arrancar

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\scripts\instalar_worker_remoto.ps1 -Comprobar -ProbarConexion -ProbarClaude
```

Debe terminar en `Resumen: LISTO` (o, mientras exista el bloqueo de la sección 7, en `PREPARADO ... bloqueo conocido`;
nunca en `NO LISTO`). Sin `-Comprobar` escribe además `tools\agent-lanes\.state\informe-segundo-worker.txt`. Códigos de
salida: 0 listo; 1 hay FALTA o BLOQ.

## 7. Bloqueo conocido del runner: `reconcile()` (hay que arreglarlo antes de arrancar)

Al arrancar, cada carril ejecuta `LaneRunner.reconcile()` (`agent_lanes/runner.py`): bloquea toda tarjeta en `running`
de su carril que **no tenga un PID vivo en el `.state/` local**, porque da por hecho que «solo este runner consume el
carril». Con dos equipos eso es falso: el segundo runner vería las tarjetas que está ejecutando el principal, no
encontraría su PID y las **bloquearía** («runner reiniciado: la tarea quedó en running sin proceso vivo»); y al revés,
cada reinicio del principal bloquearía las del segundo. El diccionario de tarjeta del backend supabase tampoco expone
`lease_owner`, así que hoy no hay forma de distinguirlas.

Arreglo propuesto (tarjeta aparte, carril claude-hub, con pruebas; **no incluido en esta rama**):

1. `SupabaseKanban._task_dict` expone `lease_owner` (ya se guarda en `hub.tasks`).
2. `reconcile()` (y el equivalente del carril review, si lo tiene) solo tratan como huérfanas las `running` cuyo `lease_owner` es **este**
   `HUB_WORKER_ID` (o `None`).
3. Revisar el resto de lectores de `list_status(..., "running")` y de `.state/tasks/<id>.json` por si asumen un solo runner
   (`status`, `drain`, `atascos`, el panel).
4. Prueba con dos `HUB_WORKER_ID` contra el Postgres desechable (`HUB_TEST_DSN`, ver README, «Backend del kanban»).

Hasta que esté fusionado, el script marca `BLOQ` y devuelve 1. Mientras tanto, solo se puede hacer la **fase A**: todo
lo de las secciones 1 a 6 (no reclama nada).

## 8. Arrancar y comprobar que reclama una tarjeta de prueba

Solo con la sección 7 resuelta y el script en `LISTO`.

1. **Una pasada en primer plano, un solo worker** (no registra nada):
   ```powershell
   cd C:\Users\oscar\oscar-command-hub\tools\agent-lanes
   py -3.12 runner.py --all --once --max-workers 1
   ```
   Debe escribir `kanban: backend=supabase` y `contrato hermes OK` (el mensaje es histórico: es el contrato del
   kanban), y salir sin bloquear ninguna tarjeta ajena.
2. **Tarjeta de prueba** (la crea Oscar o el integrador con la skill `tarjeta-carril`): carril `claude-hub`, título
   `[PRUEBA segundo worker]`, tamaño S, cuerpo: «No cambies ningún fichero. Comenta el valor de HUB_WORKER_ID de tu
   entorno si lo conoces, y termina». Crea 2 o 3 iguales: reclama el primer runner que las vea, así que verás repartirse
   entre equipos sin necesidad de pausar el principal.
3. **Comprobación:** en la tarjeta, el evento `claimed` lleva `lock=<HUB_WORKER_ID>`; debe aparecer el id de este
   equipo en alguna. En este equipo, `.state\runner.log` muestra la tarjeta y `py -3.12 lanes.py status` la
   muestra. Al cerrarse, se mira que la rama `lane/<id>` existe en GitHub y que el worktree se limpia.
4. **Ya con servicio** (cuando la prueba esté bien): `powershell -ExecutionPolicy Bypass -File tools\agent-lanes\install-service.ps1 -MaxWorkers <N>`.
   Registra la tarea «agent-lanes runner» al iniciar sesión, sin privilegios de administrador.

Si la cuota de la cuenta de este equipo se agota, el runner pausa solo (`cuota_pausa_minutos`); las tarjetas siguen
disponibles para el principal.

## 9. Parar y volver atrás

Todo es reversible y no borra datos de nadie:

- **Parar ordenadamente:** `py -3.12 tools\agent-lanes\lanes.py restart --drain` deja de reclamar y espera a los workers.
  Nunca `schtasks /End` a pelo (incidente 27-09). Con el servicio instalado, para quitarlo:
  `powershell -ExecutionPolicy Bypass -File tools\agent-lanes\install-service.ps1 -Uninstall`.
- **Una tarjeta mal reclamada por este equipo:** se bloquea o se reasigna desde el kanban/Telegram como cualquier otra;
  su rama `lane/<id>` queda en GitHub.
- **Volver al estado anterior del equipo:** basta con quitar la tarea programada y el `.env`. Los clones y `_lanes`
  pueden quedarse (no molestan). Borrarlos es decisión de Oscar (no lo hace el script).
- **Volver atrás del kanban compartido** (si el problema es el backend, no este equipo): `KANBAN_BACKEND=hermes` en el
  principal y reinicio, según el plan de corte del 2-nov (`decisions/2026-11-02-kanban-supabase-espejo.md`).
- **Una credencial filtrada o equipo perdido:** Oscar rota la contraseña del rol `hub_core` (afecta a los dos equipos)
  o crea un rol por equipo; y revoca la sesión de GitHub y la de Claude de ese PC.

## 10. Lo que queda por decidir (para Oscar)

- Confirmar que el corte F2 a supabase está hecho (sección 0) y, si no, cuándo se hace.
- Autorizar la tarjeta del bloqueo de la sección 7 (tamaño M, tocan `runner.py` y `kanban_api.py`).
- Si el segundo equipo ha de tener rol de base de datos propio (`hub_core_<equipo>`) en vez de compartir `hub_core`.
