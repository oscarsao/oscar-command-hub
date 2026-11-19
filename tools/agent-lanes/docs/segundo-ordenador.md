# Segundo ordenador como worker de agent-lanes

Estado: **19-nov-2026, el código del hub ya soporta varios equipos** (sección 7). Esta guía y
`scripts/instalar_worker_remoto.ps1` dejan un PC Windows 11 **listo para trabajar** como un equipo más del mismo kanban.
Lo que falta no es código del hub: es el **alta del equipo en `hub.workers`** y los cambios del esquema que lista la
sección 7 (los hace Oscar en oscar-hq). Está pensado para **N equipos** (el de Oscar, el de Ángel, el de María…), no
para uno solo: cada equipo es un `worker` con su id, sus topes y, si hace falta, sus propios carriles.

Este fichero es público (el repo del hub lo es): no contiene secretos, cuentas, correos ni ids de chat, y no debe
llegar a tenerlos. Donde hace falta un dato privado, dice **de dónde sale**, no cuál es.

## 0. Qué se consigue y qué no

- El segundo equipo ejecuta un runner de agent-lanes con **su propia cuenta de Claude** (`claude -p`, su propia cuota)
  y reclama tarjetas del **mismo kanban** que el equipo principal. Dos equipos = el doble de plazas y cuotas separadas.
- Hace falta que el kanban sea común: **backend `supabase`** (tablas `hub.*`). El kanban `hermes` es un SQLite local
  y no se puede compartir. En el `.env` del equipo principal, a 19-nov, no hay `KANBAN_BACKEND` (es decir, `hermes`
  salvo que lo fije el entorno del servicio): **antes de nada hay que confirmar que el corte F2 a supabase está hecho**
  (`decisions/2026-11-02-kanban-supabase-espejo.md`, pasos 1 a 3). Si no lo está, esto no se puede usar todavía.
- Cada equipo reclama con **su id** (`HUB_WORKER_ID`): el kanban sabe de quién es cada reserva, solo el dueño la renueva
  o la cierra, y una reserva sin latido caduca sola (sección 7). Sirve igual para un tercero y un cuarto equipo.
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
| `HUB_WORKER_ID` | sí | Nombre **único** de este equipo (p. ej. `pc2-<lugar>`): **minúsculas, números, `-` o `_`, de 2 a 63 caracteres** (el mismo formato que `hub.workers.id`; el hostname de Windows va en mayúsculas y se pasa a minúsculas, pero ponlo explícito para que no cambie si renombras el PC). No es secreto. Es el dueño que queda escrito en cada reserva (`lock=` en el evento `claimed`) y **debe existir y estar activo en `hub.workers`: el alta la hace Oscar** (si es un PC de otra persona, además `externo = true` y el ajuste `workers_externos` encendido). Sin alta, el kanban rechaza reclamar y el carril se pausa 5 min entre intentos |
| `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` | no | Solo si quieres que este equipo **envíe** avisos de inicio/fin. Salen del `.env` del equipo principal (mismo canal privado). Sin ellos, este equipo trabaja en silencio y el estado se ve en el kanban y en el panel de Telegram del principal |

**No pongas en este equipo**: `CARRILES_BOT_TOKEN` (dos procesos escuchando al mismo bot se pisan), `INTEGRATOR_ENABLED=true`
(el integrador solo corre en el principal), `BOARD_GUARD_APPLY=true`, ni claves de Oscar HQ (`OHQ_API_KEY`). El script
marca como FALTA las dos primeras.

Prueba de la conexión, solo lectura: `-ProbarConexion` (ejecuta `runner.py --check`, sin reclamar nada). Comprueba, en
este orden: el contrato del esquema `hub.*` (sale 3 si falla), el **guard de los workers** (sello y sonda, sale 4) y si el
kanban **aceptaría hoy reclamar** a este equipo (alta y activo en `hub.workers`, interruptor de externos y sello
aprobado, sale 5).

## 5. `lanes.yaml` y la RAM de este equipo

No hay un `lanes.yaml` propio por equipo. Lo que **sí** se ajusta por equipo sin editarlo:

- **Tope global de workers:** `install-service.ps1 -MaxWorkers N` (o `runner.py --max-workers N`). Esa opción **pisa**
  `runner.max_workers` de `lanes.yaml` (hoy 6, calibrado para 32 GB). Orientación por RAM (cada `claude` con su
  `pnpm`/`tsc` ronda varios GB; hubo un OOM el 27-09): **16 GB → 2**, 32 GB → 3 (el script imprime la cifra de tu
  equipo). Empieza en **1** durante la prueba.
- `max_parallel` por carril (oscarhq 4, hub 2, review 2) no hace falta tocarlo: manda el tope global.
- **Ajustes por equipo dentro del propio `lanes.yaml`** (versionado, sin editarlo a mano en cada PC), por id de worker:

  ```yaml
  runner:
    max_workers: 6                     # el del equipo principal (el global)
    workers:
      pc2-casa:   {max_workers: 2, lanes: [claude-hub, claude-scraper]}
      pc-angel:   {max_workers: 1}
  ```

  Orden de prioridad del tope: `--max-workers N` > `runner.workers.<id>.max_workers` > `runner.max_workers`. `lanes`
  limita qué carriles corre ese equipo con `--all` (sin la clave, todos; un carril que no existe se avisa y, si no queda
  ninguno, el runner no arranca). Así un equipo sin el repo de NextJobs, por ejemplo, no tiene que tocar el fichero.
  Cada equipo cuenta SUS workers: con dos PC el límite real de la suma es la suma de los topes, no el global.
- **Segador de reservas caducadas:** cada runner lo ejecuta cada 60 s (`runner.release_stale_seconds`) con tope 3
  (`runner.release_stale_max_attempts`; 0 lo apaga; el `.env` manda con `RELEASE_STALE_MAX_ATTEMPTS` y
  `RELEASE_STALE_SECONDS`). Solo con el backend `supabase`; con `hermes` va apagado (su dispatcher ya lo hacía).

## 6. Comprobar antes de arrancar

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\scripts\instalar_worker_remoto.ps1 -Comprobar -ProbarConexion -ProbarClaude
```

Debe terminar en `Resumen: LISTO` (nunca en `NO LISTO`). Sin `-Comprobar` escribe además
`tools\agent-lanes\.state\informe-segundo-worker.txt`. Códigos de salida: 0 listo; 1 hay FALTA. Además de las
herramientas, los repos y el `.env`, comprueba (sin escribir nada) el **sello y la sonda del guard** de este clon
(sección 7, H11) y, con `-ProbarConexion`, la admisión en el kanban.

## 7. Cómo conviven varios equipos en el mismo kanban (resuelto en el código, 19-nov)

La auditoría del 19-nov (`integrador-docs/AUDITORIA_SEGUNDO_WORKER_2026-11-19.markdown`) encontró que el claim era
atómico pero el runner asumía un solo proceso. Lo que hace ahora el código (todo con el backend `supabase`; con `hermes`
el comportamiento es idéntico al de siempre, hay pruebas de paridad):

| Hallazgo | Qué hace ahora |
|---|---|
| H1 `reconcile` | La tarjeta trae `lease_owner`, `lease_until` y la hora del servidor. Al arrancar, un runner solo da por huérfanas **sus** reservas sin proceso vivo; una `running` de otro equipo ni se mira, ni se mata un pid, ni se bloquea. `lanes status` la muestra como `remoto` (`t_xxxx@pc2-casa`), no como huérfana |
| H3 admisión | `claim` comprueba en `hub.workers` que el worker existe y está activo, el interruptor `workers_externos` (si es externo) y el sello del guard. Falla cerrado. No llama a `hub.claim` (elige ella la tarjeta y se saltaría los filtros del runner) |
| H4 reloj | Toda fecha que se guarda (reserva, eventos, comentarios, runs, tarjeta) es `now()` del **servidor**: un PC con el reloj mal puesto no acorta ni alarga reservas |
| H5 dueño | El latido solo renueva la reserva propia. `block`, `complete`, `request_review` y `reopen_review` se rechazan si la reserva **viva** es de otro. Si el dueño perdió la reserva, el latido lo detecta: el runner **corta su `claude`** y abandona sin bloquear, sin pasar a review y sin avisar por Telegram (esa tarjeta ya es de otro). Detección: hasta un latido (240 s) |
| H6 segador | Un ticker del runner llama a `release_stale`: lo caducado (hora del servidor) **vuelve a la cola** (`ready`, como hacía el dispatcher de Hermes con un claim caducado) y a `triage` al 3.er intento para que lo mire una persona, con un evento `lease_expired` y un comentario en la tarjeta. `runner.release_stale_requeue: false` la deja en `blocked` (entonces alguien debe desbloquearla: ningún vigilante anuncia un `blocked` que no nació en el runner). Varios runners a la vez es seguro. Ojo: con el `ttl` de los carriles largos (7500 s) la tarjeta de un equipo muerto tarda hasta ~2 h en liberarse |
| H7 | Las lecturas ya no escriben si no hay nada que promover. Se acepta que `_assign` deje pasar una `running` sin dueño (solo existe en lo importado de Hermes) |
| H9 | Misma rama `lane/<id>` para todos (sin sufijo de equipo, para no romper el flujo ni la revisión): la **guarda** es el dueño de la reserva. Un equipo que la pierde deja de escribir (se le corta el `claude`) y el que la reclama sigue desde `origin/lane/<id>`. Antes de verificar y de poner la rama al día con la base (el único push que hace el runner) se **lee la reserva fresca**: no depende de que el latido, que puede ir 240 s atrasado, lo haya visto. El comentario de claim lleva `equipo=<id>` |
| H10 | `serialize_paths` (migraciones) tiene ahora un cerrojo transaccional entre equipos (`claim_serial`); topes y carriles por equipo en `lanes.yaml` (sección 5); el estado local `.state` sigue siendo por PC (cada checkout el suyo) y se anota el equipo en el estado de cada tarea. Un claim rechazado por admisión pausa el carril 5 min en vez de martillear |
| H11 guard | `runner.py --check` comprueba el **sello** (sha256 de hook, settings, política, esquemas y roles contra `contract/guard-manifest.json`) y una **sonda** que ejecuta el hook real (un push a master y un alembic remoto deben dar exit 2). El sello se anota en `hub.workers.version` y, si existe el ajuste `guard_hash_aprobado`, `claim` lo exige |

**Riesgo abierto: el carril `review` no reserva.** `ReviewRunner` lee las tarjetas en `review` sin reclamarlas (el `claim` de
Hermes solo coge `ready`): dos equipos que corran el carril de revisión revisarían la misma tarjeta a la vez y uno podría
cerrar o reabrir lo que el otro acaba de tocar. **Que solo el equipo principal corra `review`**: en los demás equipos limita
`runner.workers.<id>.lanes` a carriles de implementación (sin `claude-review`). Lo mismo para el integrador
(`INTEGRATOR_ENABLED`, ya prohibido en el instalador). Los hilos que arrancan en todo runner (`HermesAnswers`,
`reasignar_abiertas`) son idempotentes: el segundo equipo los repite sin efecto.

**Si cambias el guard** (`contract/guard.py`, settings, política, esquemas, roles): regenera el sello con
`py -3.12 -m agent_lanes.guard_hash --write` y commitéalo en el mismo cambio; un test falla si se olvida y el runner no
arranca con un sello roto (salida 4).

**Dos equipos con el mismo `HUB_WORKER_ID`** no están protegidos: se verían como el mismo dueño y reabrirían H1 (cada uno
reconciliaría y renovaría las reservas del otro). El id es único por PC; el instalador avisa si coincide con el nombre del equipo.

**Quién actúa sobre una tarjeta `running` de otro equipo.** Oscar desde Telegram, el integrador y el revisor usan el mismo
cliente: una reserva **viva** de otro equipo no se puede cerrar ni bloquear desde fuera (se rechaza con el motivo y queda
en el log); una **caducada** sí (está abandonada y la libera el segador de todos modos). Para parar a un equipo vivo:
`workers_externos` a `false` (kill-switch de los PC de otras personas), `lanes.py restart --drain` en ese PC, o esperar a
que su reserva caduque.

**Lo que este código espera del esquema real de oscar-hq** (no está en este repo; lo hace Oscar en su carril):

- H2: `hub.task_runs.profile/lease_owner/lease_until` y `hub.tasks.lease_ttl/block_recurrences/created_by_worker/extra`
  (la migración 20261108 no los tiene; `check_contract()` lo dice y el corte no debe hacerse sin esto).
- `hub.workers` con el alta de cada equipo (`id`, `activo`, `externo`) y `hub.ajustes` con `workers_externos`; el rol
  `hub_core` ya puede actualizar `last_seen`, `version` y `hostname` (el runner los escribe al arrancar).
- Opcional: la clave `guard_hash_aprobado` en `hub.ajustes` (texto o lista de sellos; sin ella no se exige sello) y darla de
  alta en `hub.ajuste_valido`. Sin esa clave el control queda en `--check`.
- H8: un rol por equipo (`hub_worker_<id>`) con GRANT por columnas en lugar de compartir `hub_core`. Hoy cualquier DSN de
  `hub_core` puede escribir cualquier tarjeta: **no repartas el DSN a un equipo en el que no confíes**.
- Nota: `hub.claim` topa el ttl a 2 h y `claim_ttl_seconds` de los carriles largos es 7500 s; el claim de este backend
  no usa esa función por eso (acepta cualquier ttl > 0).

## 8. Arrancar y comprobar que reclama una tarjeta de prueba

Solo con el script en `LISTO` y el alta del equipo hecha por Oscar (sección 7).

1. **Una pasada en primer plano, un solo worker** (no registra nada):
   ```powershell
   cd C:\Users\oscar\oscar-command-hub\tools\agent-lanes
   py -3.12 runner.py --all --once --max-workers 1
   ```
   Debe escribir `kanban: backend=supabase`, `kanban: worker=<tu id>`, `contrato hermes OK` (el mensaje es histórico: es
   el contrato del kanban), `guard de workers OK`, `segador de reservas activo` y salir sin bloquear ninguna tarjeta
   ajena (verás `reserva de otro equipo (...); no se toca` por cada tarjeta del principal).
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

- Confirmar que el corte F2 a supabase está hecho (sección 0) y, si no, cuándo se hace, junto con los cambios de esquema
  de la sección 7 (H2: columnas; alta de cada equipo en `hub.workers`; ajuste `guard_hash_aprobado`).
- Si cada equipo ha de tener rol de base de datos propio (`hub_worker_<id>`, H8) en vez de compartir `hub_core`: recomendado
  antes de dar un DSN a una persona que no sea Oscar.
- Revisar la PR que cierra el bloqueo de la sección 7 antes de integrarla (toca `runner.py`, `kanban_api.py`, un módulo
  nuevo y el guard; es del carril hub, y integrar es del integrador).
