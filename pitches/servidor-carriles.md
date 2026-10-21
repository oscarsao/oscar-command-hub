# Pitch — Sacar carriles y Hermes del PC de Oscar a un servidor

**Fecha:** 2026-10-21 · **Tarea:** t_bf5cf039 (antes «Fase G») · **Estado:** propuesta, a la espera de decisiones de Oscar (§9)
**Alcance de este documento:** solo escribe el plan. No se contrata, instala ni migra nada. Se ha hecho con lectura del repo
`oscar-command-hub` y búsqueda de precios; no se ha tocado el sistema vivo (ni el PC, ni el mini PC, ni Hermes).

## 0. Problema y appetite

- **Problema:** runner, Hermes, monitor y workers `claude -p` viven en el PC de Oscar. El PC se queda sin RAM y se reinicia
  (ya hubo un OOM el 27-09; por eso `runner.max_workers: 1` en `lanes.yaml`). Cada reinicio corta tareas, deja `runner ciego`
  (`WinError 4551`, App Control) y worktrees a medias.
- **Decisión de Oscar (21-oct, botones):** aprobado sacar carriles y Hermes a un servidor. Condición del 15-10 que se mantiene:
  **el control corre en UNA sola máquina a la vez** y la sesión de Claude se inicia a mano en esa máquina.
- **Appetite:** 2-3 semanas de calendario, ~5-6 días de trabajo repartidos en 4 fases pequeñas (§7), cada una reversible. Si la
  fase 2 (sombra) no pasa, se vuelve al PC sin pérdida.

## 1. Opciones

Precios: búsquedas del 21-oct-2026 sobre fuentes secundarias; **ninguna página oficial pudo leerse**. Todo precio debe
confirmarse en la web del proveedor antes de decidir. Lo no verificable se marca ⚠.

### Dimensionado (estimación, ⚠ sin medir)
Hoy: 2 workers en paralelo (el `lanes.yaml` actual deja `max_workers: 1`; `instalar_servicio.sh` instala 2). Cada `claude -p` más
`pytest`/`git`/`node` puntual: estimo 1,5-3 GB de pico por worker. Más Hermes (gateway + dispatcher), runner, monitor y panel:
~1-2 GB. **Mínimo razonable: 4 vCPU / 8 GB / 80 GB; cómodo para 3-4 workers: 8 vCPU / 16 GB / 160 GB.** Disco: worktrees
de 5 repos + `node_modules`/venvs + logs. **Medir antes de comprar** (tarea de la fase 0: pico real de RAM durante un día normal).

| | VPS Hetzner | VPS OVH | Railway | Mini PC Minisforum (ya comprado) |
|---|---|---|---|---|
| **Coste/mes** | CPX32 (8 GB): ~€35,99 según una fuente de junio-2026; CX33 (8 GB) €8,99 pero **la línea CX figuraba «no disponible» en ago/sep-2026**. 16 GB: sin precio actual verificado ⚠ (la lista de 2025 daba €19,99 y está obsoleta tras las subidas de abril y junio-2026). IPv4 +€0,50. | VPS-3/VPS-4 (8 vCores / 24 GB / 200 GB NVMe en un listado): €13,99-€23,48/mes; las fuentes discrepan en specs y precio (otro listado: 6 vCores / 12 GB a ~$12). ⚠ | Pro $20/mes + uso: RAM $10/GB/mes, CPU $20/vCPU/mes, volumen $0,15/GB-mes. 8 GB + 4 vCPU 24/7 ≈ $80 + $80 = ~$160/mes (cálculo propio ⚠). | €0 recurrente (€1.729 pagado en 4 cuotas, 24-09). Electricidad: ⚠ sin medir (consumo típico de un mini PC bajo carga ligera, decenas de W). |
| **Hardware** | Dimensionable | Dimensionable; las specs/€ son las mejores del lote | Contenedores; tope 24 vCPU/24 GB por réplica | Ryzen AI 9 HX470, **64 GB RAM**, 1 TB SSD: sobrado |
| **24/7** | Sí (SLA de proveedor). Riesgo: disponibilidad de stock y cambios de precio recientes | Sí | Sí | Depende de luz e internet de casa de Oscar; sin SLA |
| **Mantenimiento** | Alto: SO, parches, firewall, backups | Alto, igual | Bajo en SO, pero **filesystem efímero**: worktrees y `claude` login exigen volumen persistente; no encaja con procesos de larga duración con sesión interactiva | Alto, igual que un VPS, más hardware, SAI, BIOS, red |
| **Ventajas** | Datacenter UE, red fiable, snapshots baratos | Barato, mucha RAM | Cero SO que mantener | Sin coste nuevo; 64 GB resuelven el límite de RAM de una vez; ya previsto en la decisión del 24-09 (túneles, cron, servicios internos) |
| **Riesgos** | Subidas de precio en 2026 (+hasta 176% en CPX/CCX nuevos); CX no disponible; dependencia del proveedor | Specs/precios poco claros; VPS-3 US «deprecated» en un listado | Coste alto para un proceso siempre encendido; el modelo de pago por uso no encaja | **Nunca verificado** (¿llegó, arranca, qué SO, red, SAI?); un corte de luz/internet para todo el sistema; coste de oportunidad si falla |

**Fuentes de precios (consultadas 21-oct-2026, secundarias):**
Hetzner — [Northflank: subidas](https://northflank.com/blog/hetzner-cloud-server-price-increases), [WZ-IT: junio 2026](https://wz-it.com/en/blog/hetzner-price-increase-june-2026-cpx-ccx-alternatives/), [Findstack](https://findstack.com/resources/hetzner-price-increase-2026), [Cloud Pricing Comparison 2026-08](https://kimmo.suominen.com/stuff/cpc-2026-08.txt).
OVH — [vpsbenchmarks VPS-3](https://www.vpsbenchmarks.com/hosters/ovhcloud/plans/vps-3), [VPS-4](https://www.vpsbenchmarks.com/hosters/ovhcloud/plans/vps-4), [whtop](https://www.whtop.com/amp/plans/ovhcloud.com/137099).
Railway — [docs de planes](https://docs.railway.com/pricing/plans), [railway.com/pricing](https://www.railway.com/pricing).
Minisforum — `decisions/2026-09-24-compra-minisforum-x1-pro-470.md` (precio y specs de la compra).

### Recomendación
**Principal: el mini PC Minisforum con Ubuntu Server LTS**, *condicionado* a una verificación en solo lectura (§1.1) y a un SAI
básico. Motivos: coste recurrente cero (el dinero ya está gastado), 64 GB eliminan el problema de RAM con margen para 4+ workers,
ya estaba pensado para exactamente este tipo de carga (decisión del 24-09), y no introduce dependencia de un proveedor ni la
exposición a las subidas de precios de 2026. **Alternativa: VPS de 16 GB (OVH VPS-3/4 o Hetzner CPX con 16 GB)**, ~€15-45/mes
según proveedor (⚠ confirmar), si la verificación del mini PC falla o si Oscar prefiere 24/7 con SLA. Railway queda descartado
para este uso (siempre encendido, sesiones de larga duración, caro).

Nota de honestidad: la recomendación tiene un punto débil real — la fiabilidad de casa. Si Oscar valora más «que no se caiga» que
«no pagar», la alternativa VPS gana. La fase 2 (sombra) mide cuántos cortes hay antes de decidir el corte definitivo.

### 1.1 Qué comprobar del mini PC (solo lectura; lo hace Oscar o oscar-bf)
1. ¿Está en casa, encendido y con la RAM/SSD declaradas? (`systeminfo` / BIOS; sin instalar nada).
2. SO actual y si permite instalar Linux (disco libre, Secure Boot, ¿se puede dual/medio disco?).
3. Conexión: cable Ethernet, velocidad real, IP/CGNAT del router (afecta al acceso directo; con Tailscale/Cloudflare no importa).
4. Corte de luz/internet: ¿hay SAI? ¿cuántas veces al mes se va la luz/internet?
5. Temperatura y ruido en carga (¿dónde va a estar?).
6. Autoarranque tras corte de luz (opción de BIOS «AC power recovery»).

## 2. Qué se mueve y qué se queda

| Componente | Hoy (Windows, PC de Oscar) | Destino |
|---|---|---|
| Runner `agent-lanes` (`runner.py`, `lanes.py`) | servicio vía `install-service.ps1` | **Se mueve** → servicio `systemd` |
| Hermes: gateway Telegram + dispatcher | PC de Oscar | **Se mueve** (una sola máquina con polling, §8) |
| Worktrees `dev/_lanes` y `_hub-wt` (+ `_lanes/ops`, `_lanes/_integrator`) | disco local | **Se mueven** (se regeneran con `git clone`/`worktree`; lo no empujado se rescata antes, §5) |
| `kanban.db` y kanban por tablero (`oscarhq`, etc.) | `HERMES_HOME` | **Se mueve** con copia previa y parada del origen |
| Monitor (`tools/monitor`) y `dbwatch` | `LOCALAPPDATA` | **Se mueve** |
| Tareas programadas (resumen diario `.vbs`, auditor, copia del kanban) | `schtasks` | **Se mueven** → `systemd timers`/cron |
| Panel + túnel Cloudflare (`tools/infra/instalar-panel-y-tunel.ps1`) | PC | **Se mueve**: el túnel se reinstala en el servidor (`cloudflared` como servicio) |
| Logs / `.state/` del runner, integrador, monitor y telegram | local | Se mueven (estado) con copia; logs nuevos en el servidor, rotación |
| Sesiones interactivas de Claude Code, repos de trabajo de Oscar, `.env` personales | PC | **Se quedan** |
| Carril `ops` con `dest_roots: [C:/Users/oscar, E:/...LEGAL-LLC]` | accede a carpetas del PC | **Se queda en el PC** o se rediseña: un servidor no puede leer `E:\` del PC. Decisión pendiente (§8) |
| Despliegues Railway/Vercel | CLI en el PC (`railway_dir` en `lanes.yaml`) | Se mueve la CLI; los tokens los pone Oscar (§4) |

## 3. Rutas y plataforma (Windows → Linux)

- `lanes.yaml` tiene ~10 rutas absolutas `C:/Users/oscar/...` (`worktree_root`, `repo:` de 5 carriles, `dest_roots`, integrador y
  `railway_dir`); en total 61 apariciones en 17 archivos de `tools/` (README, skills, `.ps1`, `.vbs`, tests).
  **Propuesta:** una variable `LANES_ROOT` (y `REPOS_ROOT`) con expansión `${LANES_ROOT}` en `agent_lanes/config.py`; los tests
  siguen con `tmp_path`. Se hace en la fase 1 **sin cambiar el comportamiento en Windows** (valor por defecto = rutas actuales).
- Windows hoy: `py -3.12` en `allowed_tools`/`test_cmd`, `pythonw/.pyw`, `schtasks`, `.vbs`, App Control (`WinError 4551`), finales de
  línea CRLF en algunos archivos. En Linux: `python3.12`/`py` desaparece (el `allowed_tools` y el `test_cmd` deben parametrizarse),
  `systemd` sustituye a `install-service.ps1`/`schtasks` (hay ya un `instalar_servicio.sh`, pero llama a PowerShell), desaparece
  `WinError 4551` y el vigilante `ciego` sigue valiendo para otros fallos. Los hooks `contract/*guard*` están en Python:
  hay que revisar rutas con `\` y el `ops_guard.py` en Linux (tarea de la fase 1, con tests).
- Los archivos `.ps1`/`.vbs`/`.bat` no los puede añadir un carril (el integrador bloquea el PR): los instaladores de Linux
  (`.sh`, unidades `systemd`) tampoco son ejecutables permitidos de forma automática; los escribe Oscar o se aprueban a mano.

## 4. Secretos y cuenta de Claude

Inventario por servicio (**solo nombres/ubicación, sin valores**; lista no exhaustiva, ⚠ lo completa quien haga la fase 0 con
un `grep` de variables en solo lectura):

| Servicio | Secretos que necesita |
|---|---|
| Hermes gateway | token del bot de Telegram (Hermes), `TELEGRAM_ALLOWED_CHATS`, configuración en `HERMES_HOME` (`config.yaml`) |
| Runner/avisos | token de bot de Telegram del carril (se guarda con `guardar_token_bot.py`), `TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID` |
| Workers `claude -p` | **sesión de Claude de Oscar** (login interactivo, no una API key) |
| Git/PR | credencial de GitHub (`gh auth`) con permisos de push a `lane/*` |
| Integrador/deploy | tokens de Railway y Vercel, claves de Supabase (`SERVICE_ROLE_KEY` aparece en tests) |
| Monitor/`dbwatch` | credenciales de lectura de BD |
| Túnel y panel | token de Cloudflare Tunnel |

- **Dónde viven:** fichero `.env` con permisos `600` propiedad del usuario del servicio, fuera del repo, en el servidor (o gestor de
  secretos del proveedor). Nada en el repo, nada por Telegram/chat/correo.
- **Quién los pone:** Oscar, a mano, por SSH (copiando desde su gestor de contraseñas). Los workers y Hermes no los ven ni los escriben.
- **Cuenta de Claude — riesgo de dos máquinas:** el login de Oscar usa tokens de refresco de un solo uso (nota ya en el
  `config.yaml` de Hermes). Si dos máquinas comparten la misma sesión, una invalida a la otra y los workers fallan por autenticación.
  **Regla: el control corre en UNA sola máquina a la vez.** Cómo se inicia en el servidor: Oscar abre `claude` en el servidor por SSH y
  hace el login a mano (flujo de dispositivo/URL), una vez; no se copian ficheros de credenciales del PC. En el corte (fase 3) el PC
  se desloguea o se detienen sus workers **antes** de iniciar sesión en el servidor. Durante la fase de sombra el servidor **no
  lanza workers reales** (solo lectura/dry-run), así no hay doble sesión. ⚠ Comprobar si el plan de Claude de Oscar admite
  varios dispositivos y si el login desde IP de datacenter (VPS) pide verificación adicional.
- Rotación: tras el corte, rotar los tokens de Telegram que se hayan tocado durante pruebas.

## 5. Copias de seguridad y recuperación

| Qué | Frecuencia | Destino | Notas |
|---|---|---|---|
| `kanban.db` y kanban por tablero (SQLite) | cada hora: `sqlite3 .backup` (copia consistente, no copiar el fichero en caliente) + diaria retenida 14 días | disco secundario/USB del servidor **y** copia cifrada fuera de la máquina (p. ej. almacenamiento de objetos del proveedor o el PC de Oscar) | hoy ya hay «copia del kanban» programada: se migra |
| Estado del runner/integrador/monitor/telegram (`.state/`) | diaria | igual | pequeño |
| Worktrees con commits sin empujar | **regla:** todo commit se empuja a `origin` a diario (el runner ya empuja `lane/*`); ramas locales sin remoto = riesgo conocido | GitHub | antes del corte, script de inventario en solo lectura que liste ramas sin remoto en `dev/_lanes` y `_hub-wt` |
| Configuración (`/etc/systemd`, `cloudflared`, `HERMES_HOME`) | semanal y antes de cada cambio | cifrada, fuera de la máquina | sin secretos en claro |
| Snapshot del servidor (si VPS) | semanal | proveedor | barato, ayuda a volver rápido |

**Prueba de restauración:** una vez por mes y siempre antes del corte: restaurar `kanban.db` en un directorio temporal, abrir con
`sqlite3 'PRAGMA integrity_check'` y comprobar que el tablero lista las tarjetas esperadas. Sin esa prueba verde no hay corte.

## 6. Acceso desde el PC y seguridad

- **SSH:** solo con clave (ed25519), `PasswordAuthentication no`, `PermitRootLogin no`, usuario no-root para el servicio.
- **Red:** recomendado **Tailscale** (sin abrir puertos; el SSH solo escucha en la red privada) o, alternativa, Cloudflare Tunnel +
  Access. Firewall `ufw` en «deny incoming» salvo lo estrictamente necesario; si es VPS, sin SSH público si es posible, o
  con `fail2ban`.
- **Kanban y logs:** el panel actual sigue por túnel de Cloudflare (se reinstala en el servidor); logs por `journalctl -u ...` por SSH o
  con un comando `lanes.py status`/`tail` vía SSH.
- **Reiniciar el runner:** `ssh servidor "sudo systemctl restart agent-lanes"` (y `lanes.py restart --drain` como hoy para no cortar
  workers a medias). Se documenta como botón/comando en el README.
- **El integrador:** corre en el servidor como parte del runner (hoy `integrator.py`); sigue sin hacer push a `main` ni desplegar sin
  los botones de Oscar. Los tokens de deploy los pone Oscar (§4).
- **Actualizaciones:** `unattended-upgrades` para parches de seguridad; reinicio programado en ventana tranquila; actualizar a mano
  `claude`, Hermes y Python tras avisar en el tema de Integración.
- Principio de mínimo privilegio: el usuario de los workers no tiene `sudo`; los hooks `contract/*guard*` se mantienen.

## 7. Plan por fases (cada una reversible)

| Fase | Qué se hace | Qué se prueba | Vuelta atrás | Aprueba |
|---|---|---|---|---|
| **0. Inventario** (solo lectura, ~1 día) | Checklist del mini PC (§1.1); medir RAM real; inventario de secretos y de ramas sin remoto; lista de tareas programadas | Medidas anotadas; ninguna escritura | n/a | Oscar (proveedor/tamaño) |
| **1. Parametrizar rutas** (~1-2 días) | `LANES_ROOT`/`REPOS_ROOT` en `config.py`/`lanes.yaml`, `test_cmd`/`allowed_tools` por plataforma; tests nuevos con `tmp_path`; **sin cambiar nada en Windows** | Suite completa en verde; el PC sigue igual | Revertir el PR | Oscar al fusionar |
| **2. Servidor en paralelo, modo sombra** (~3-5 días) | Instalar SO, SSH, Tailscale, repos, Hermes y runner **sin reclamar tareas ni hacer polling de Telegram**; kanban restaurado desde copia; ejecución en seco | Arranca; ve el kanban; `claude --version` y login; un worktree se crea; restauración de copia verde | Apagar el servidor; el PC no ha cambiado | Oscar |
| **3. Ventana de corte** (~medio día, con copia previa) | Avisar en Integración; `drain` en el PC; parar Hermes y runner del PC; copia final de `kanban.db`/estado; rescatar ramas sin push; arrancar en el servidor; login de Claude a mano; **desactivar** servicios del PC (sin borrarlos) | Una tarea de prueba pasa de todo a done/review; un aviso llega a Telegram **una sola vez**; backup horario corre | Parar servicio del servidor, reactivar los del PC (kanban restaurado desde la copia previa) | Oscar (fecha de corte) |
| **4. Retorno / estabilización** (1-2 semanas) | Vigilar; si todo estable, retirar los servicios del PC; si hay problemas, volver | Cero cortes no explicados; alertas funcionan; prueba de restauración | Misma que fase 3 mientras se conserve el PC como respaldo | Oscar |

## 8. Riesgos y rabbit holes

| Riesgo | Efecto | Mitigación |
|---|---|---|
| **Límite de RAM** (workers `claude -p`, `pytest`, `node`) | OOM, tareas cortadas | Medir (fase 0); dimensionar con margen; `max_workers` conservador al empezar; límites `systemd MemoryMax`; swap |
| **Cuenta de Claude en dos máquinas** | Sesión invalidada, workers sin auth | Regla de UNA máquina; login a mano; sombra sin workers reales; PC deslogueado al cortar |
| **Telegram polling duplicado** entre dos máquinas | Mensajes duplicados, 409 `Conflict`, avisos perdidos | Hermes y runner **parados** en el origen antes de arrancar en el destino; el bot del servidor no se arranca en sombra; comprobar con `getMe`/`getUpdates` de solo lectura sin consumir actualizaciones |
| **Pérdida de worktrees / commits sin empujar** | Trabajo perdido | Inventario previo, regla de empujar a diario, no borrar nada en el PC hasta cerrar la fase 4 |
| **Coste que se dispara** | Factura mayor que los ~€50/mes ahorrados | Tope mensual (§9); alertas del proveedor; evitar Railway; ancho de banda y snapshots vigilados |
| **Dependencia de un proveedor** (subidas de precios de Hetzner en 2026, stock CX) | Sorpresas | Backups fuera del proveedor; instalación reproducible; alternativa documentada |
| **Mini PC no fiable** (luz/internet) | Sistema caído horas | SAI, autoarranque, aviso de «latido» en Telegram, VPS como plan B |
| **Carril `ops`** necesita rutas del PC (`E:\...LEGAL-LLC`) | No funciona en servidor | Dejarlo en el PC o rediseñar (rabbit hole: **fuera de este appetite**; decisión de Oscar) |
| **Rabbit hole:** reescribir lanzadores a Linux «bien» | Se come semanas | Solo lo necesario: parametrizar rutas, `systemd`, nada más |
| **Rabbit hole:** IA local en el NPU | Distracción | Fuera de alcance (el NPU no sustituye a Claude, decisión del 24-09) |
| Seguridad (SSH expuesto, secretos mal puestos) | Compromiso del sistema | §4 y §6 |

## 9. Decisiones para Oscar

1. **Proveedor/dónde**
   - A) Mini PC Minisforum con Linux (**recomendada**, tras verificación §1.1)
   - B) VPS OVH 16-24 GB
   - C) VPS Hetzner 16 GB
   - D) Railway (no recomendada)
2. **Tamaño** (mínimo para N workers)
   - A) 8 GB / 4 vCPU (2 workers)
   - B) 16 GB / 8 vCPU (3-4 workers, **recomendada** si VPS)
   - C) Lo que dé el mini PC (64 GB)
3. **Presupuesto mensual tope**
   - A) €0 (solo mini PC)
   - B) hasta €25
   - C) hasta €50 (**recomendada** si VPS: lo que ya se paga en cloud)
4. **Fecha de corte**
   - A) En ~2 semanas, tras fase 2 verde
   - B) En ~4 semanas (**recomendada**: sombra más larga)
   - C) La decide Oscar tras ver los resultados de la sombra
5. **Carril `ops` (rutas del PC)**
   - A) Se queda en el PC (**recomendada**)
   - B) Se rediseña en otro pitch

## Siguiente paso
Oscar responde §9 y ordena a oscar-bf/Oscar la fase 0 (inventario en solo lectura). Con eso se abren las tarjetas de la fase 1 (parametrizar rutas, ya bugfix/Spec-Lite).
