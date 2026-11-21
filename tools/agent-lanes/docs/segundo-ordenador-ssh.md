# Segundo ordenador por SSH: pasos manuales (Oscar)

Spec: `specs/worker-remoto-ssh.md` (decisiones de Oscar, 20-11-2026). Esto cubre las **entregas E1 y E2** (E2 apilada sobre E1, ver §12): el código ya sabe despachar un
carril con `host:` por SSH, pero ningún carril lo usa hasta que tú hagas estos pasos y pongas el bloque `host:` en `lanes.yaml`.

**Estado de la verificación.** Los tests de E1 usan un ejecutor simulado y el lanzador remoto se probó en local; **nunca se ha
conectado por SSH a ningún equipo**. Todo lo marcado «(sin verificar)» depende de cómo esté configurado tu Windows real: la
prueba de humo del §8 existe para comprobarlo antes de que una tarjeta dependa de esto.

Leyenda: **HUB** = este equipo (el del runner). **HOST** = el segundo ordenador (Windows 11).
`[ADMIN]` = hay que ejecutarlo como administrador. `[SIN ADMIN]` = no hace falta. Los pasos `[ADMIN]` son una sola vez.

Antes de empezar, apunta tres datos: la **IP fija del HUB** (reserva DHCP en el router, para `from=`), la **IP del HOST** y el
**SHA** de `oscar-command-hub` que corre el HUB (`git -C C:\Users\oscar\oscar-command-hub rev-parse HEAD`). El SHA tiene que
contener `tools/agent-lanes/agent_lanes/remote_exec.py` (E1 integrada en `main`).

## 0. Reglas que no se negocian

- En el HOST **no hay credenciales de producción**: ni `.env` del hub, ni tokens de Telegram, Hermes, Vercel, Dokploy, Supabase,
  Resend, Gmail o Apollo, ni sesión de tus cuentas personales en el navegador. Solo existen: la sesión de Claude de Hola@, el
  token de GitHub del paso 6 y el clon del repo.
- El token de GitHub **lo pegas tú, en el HOST, en el prompt de `gh`**. Nunca por el chat, ni en un fichero, ni en un comando
  con el token escrito (quedaría en el historial).
- Ninguna de estas credenciales entra en el repo ni en `.state/`.

## 1. HOST [ADMIN]: cuenta local estándar `agentlanes`

PowerShell como administrador (el grupo se llama `Usuarios` en Windows en español, `Users` en inglés):

```powershell
$p = Read-Host "Contraseña de agentlanes" -AsSecureString
New-LocalUser -Name agentlanes -Password $p -PasswordNeverExpires -AccountNeverExpires -Description "Worker remoto de agent-lanes"
Add-LocalGroupMember -Group "Usuarios" -Member agentlanes
net localgroup Administradores
```

La última línea **no debe listar** `agentlanes`. Si aparece, quítalo: `Remove-LocalGroupMember -Group Administradores -Member agentlanes`.
La contraseña solo sirve para iniciar sesión en el HOST (paso 5); el acceso por SSH será solo con clave.

## 2. HOST [ADMIN]: instalar Git, Node, Python 3.12 y GitHub CLI (para todos los usuarios)

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Python.Python.3.12 -e
winget install --id GitHub.cli -e
```

Cierra y abre PowerShell y comprueba: `git --version`, `node --version`, `py -3.12 --version`, `gh --version`.
El lanzador remoto se ejecuta con `py -3.12`, así que el lanzador `py` tiene que estar en el PATH de todos los usuarios.

## 3. HOST [SIN ADMIN, como `agentlanes`]: instalar Claude Code

Inicia sesión en el HOST como `agentlanes` (paso 5 explica cómo) e instala Claude Code con el **instalador nativo** de la
documentación oficial (hoy `irm https://claude.ai/install.ps1 | iex`; confírmalo en la documentación antes de pegarlo).
Comprueba en una consola nueva:

```powershell
where.exe claude
```

Tiene que salir un **`claude.exe`**. El lanzador ejecuta `claude` sin shell intermedia: una instalación que solo deje
`claude.cmd` (la de npm) no vale y daría rc 127 (sin verificar en tu Windows).

## 4. HOST [ADMIN]: carpeta de trabajo y servicio OpenSSH Server

```powershell
New-Item -ItemType Directory -Force C:\agent-lanes, C:\agent-lanes\repos, C:\agent-lanes\_lanes | Out-Null
# Solo Administradores, SYSTEM y agentlanes (sin herencia de permisos de C:\):
icacls C:\agent-lanes /inheritance:r /grant "Administradores:(OI)(CI)F" /grant "SYSTEM:(OI)(CI)F" /grant "agentlanes:(OI)(CI)M"

Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
```

Cierra el servidor a lo mínimo. Edita `C:\ProgramData\ssh\sshd_config` (como administrador) y deja estas líneas activas
(descomenta o añade; **todavía no** pongas `PasswordAuthentication no`, va en el paso 9):

```
PubkeyAuthentication yes
KbdInteractiveAuthentication no
AllowUsers agentlanes
AllowTcpForwarding no
PermitTunnel no
```

(En una instalación en inglés el grupo es `Administrators`. `AllowTcpForwarding` y `PermitTunnel` van globales, no solo en un
bloque `Match`.)

Reinicia el servicio y limita el firewall a la IP del HUB (el nombre de la regla es el que crea Windows al instalar OpenSSH):

```powershell
Restart-Service sshd
Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -RemoteAddress <IP del hub>
Get-NetConnectionProfile   # la red del HOST debe ser Privada, no Pública
```

El shell por defecto del servidor (cmd.exe) se deja como está: el comando remoto funciona en cmd y en PowerShell.

## 5. HOST [SIN ADMIN, como `agentlanes`]: sesión de Claude (cuenta de Hola@)

Inicia sesión en Windows como `agentlanes` en el propio HOST (Cambiar de usuario, o `runas /user:agentlanes powershell`).
Ejecuta `claude`, usa `/login` y entra con la cuenta de **Hola@** (la de pago). Nada más: no abras ahí tu correo ni tus webs.
Luego `/status` debe mostrar esa cuenta. La sesión queda en el perfil de `agentlanes`; el paso 10 comprueba que una conexión
por SSH la puede leer (sin verificar: depende de dónde guarde las credenciales tu versión de Claude Code).

## 6. Token de GitHub de una cuenta técnica

En github.com, **con la cuenta técnica** (no con `oscarsao`):

**Este carril NO puede apuntar a `oscar-command-hub` ni a `oscar-hq`** (el runner rechaza `host:` en esos repos; lista en
`host_sensitive_repos` de `lanes.yaml`): el código lo escribe otro equipo y no debe acabar ejecutándose junto al hub ni en
producción. Usa un repo no sensible. El clon de `oscar-command-hub` del HOST (paso 7) es solo de lectura: contiene el
lanzador y el guard; no es el repo del carril.

1. Que `oscarsao` la añada como colaboradora con permiso de escritura **solo** al repo del carril.
2. Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token:
   - Repository access: **Only select repositories**, solo ese repo (y los demás del carril, si los hay).
   - Permissions: **Contents: Read and write** y nada más (Metadata es de solo lectura y viene sola). **Sin** Pull requests,
     Workflows ni Administration: **los PR los abre el hub**, nunca el host.
   - Caducidad: 90 días (apunta la fecha en el §11).
3. Ruleset recomendado en el repo del carril (Settings > Rules > Rulesets):
   - `main`: exigir PR con aprobación de CODEOWNERS y bloquear force push y borrado.
   - Todas las ramas: bloquear force push y borrado.
   - Creación de ramas limitada a `lane/*` para la cuenta técnica.
   En repos privados de una cuenta personal gratuita puede que los rulesets no estén disponibles (sin verificar).
4. **Decisión de Oscar: por ahora NO se activa la protección de ramas.** Riesgo residual que se acepta de forma consciente:
   con un token con `Contents: write` y sin protección, un host comprometido (o un worker que obedezca texto malicioso de una
   tarjeta) puede empujar a `main` del repo del carril, reescribir o borrar ramas. Por eso el carril solo se usa con repos no
   sensibles y sus PR **no se fusionan sin Oscar**. Cuando se active la protección, hazlo antes de ampliar el uso.

Pega el token **tú** en el HOST, como `agentlanes` (SIN ADMIN):

```powershell
gh auth login --hostname github.com --git-protocol https
```

Elige «Paste an authentication token» y pégalo en el prompt (no se muestra). Después:

```powershell
gh auth setup-git
git config --global user.name  "agentlanes"
git config --global user.email "<correo noreply de la cuenta técnica>"
gh auth status
```

## 7. HOST [SIN ADMIN, como `agentlanes`]: clon de oscar-command-hub, al mismo SHA que el HUB

```powershell
git clone https://github.com/oscarsao/oscar-command-hub C:\agent-lanes\repos\oscar-command-hub
git -C C:\agent-lanes\repos\oscar-command-hub checkout --detach <SHA del hub>
git -C C:\agent-lanes\repos\oscar-command-hub rev-parse HEAD
```

El último comando debe imprimir el mismo SHA que apuntaste del HUB. El HOST ejecuta el guard y los settings de **su** clon
(`contract/`), por eso tienen que ser los mismos que los del HUB. **Cada vez que el HUB avance** (se integre algo en
`tools/agent-lanes/`), repite `fetch` + `checkout --detach <SHA nuevo>` en el HOST. E1 no lo comprueba solo (es de E2).
Mientras E1 no esté en `main`, para probar solo puedes fijar el SHA de la rama `mejoras/worker-remoto-e1`
(`git fetch origin mejoras/worker-remoto-e1` y `checkout --detach <SHA>`); no despaches tarjetas reales con eso.

## 8. HUB [SIN ADMIN]: par de claves dedicado `hub-a-host`

PowerShell en el HUB:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\hub-a-host" -C "hub-a-host"
Get-Content "$env:USERPROFILE\.ssh\hub-a-host.pub"
```

Esta clave **no se reutiliza para nada más**. Sin passphrase, porque el runner arranca solo y no puede teclearla: la protección
es el resto de capas (`restrict`, `from=`, `AllowUsers`, ACL del fichero y firewall). La clave **privada** se queda en
`~\.ssh` del HUB: nunca en el repo, ni en `.state/`, ni en un chat. Solo la `.pub` viaja al HOST.

En el **HOST**, como `agentlanes` (SIN ADMIN), crea `C:\Users\agentlanes\.ssh\authorized_keys` con **una sola línea**:

```
restrict,from="<IP del hub>" ssh-ed25519 AAAA...(contenido del .pub) hub-a-host
```

Sin `command=` (decisión de Oscar del 20-11 para el piloto). `restrict` apaga el pty, los reenvíos de puertos, de agente y de
X11. Guárdalo sin BOM y ajusta permisos (para una cuenta estándar el fichero es el de su perfil, **no**
`administrators_authorized_keys`):

```powershell
$f = "C:\Users\agentlanes\.ssh\authorized_keys"
Set-Content -Path $f -Value '<la línea de arriba>' -Encoding ascii
icacls $f /inheritance:r /grant "agentlanes:F" /grant "SYSTEM:F"
```

En el **HUB**, añade el alias a `~\.ssh\config` (`destino:` de `lanes.yaml` es el nombre del `Host`):

```
Host worker-b
    HostName <IP del host>
    User agentlanes
    IdentityFile ~/.ssh/hub-a-host
    IdentitiesOnly yes
    ServerAliveInterval 15
```

(Sin `BatchMode` en el alias: el runner lo añade él mismo en cada conexión, junto con `StrictHostKeyChecking=yes` e
`IdentitiesOnly=yes`; así el paso manual de abajo sí puede preguntar.)

**Aceptar la clave del HOST una vez, a mano** (el runner no puede responder a esa pregunta; sin esto falla con rc 255).
Desde el HUB: `ssh -o BatchMode=no worker-b` y compara la huella que muestra con la del HOST
(`ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub` en el HOST) antes de escribir `yes`.
**Prohibido `StrictHostKeyChecking=no`** (ni en el alias, ni en la línea de comandos, ni en un `Host *`).

## 9. HOST [ADMIN]: cerrar la contraseña (solo cuando la clave ya entre)

Con `ssh worker-b whoami` funcionando desde el HUB, edita `C:\ProgramData\ssh\sshd_config`, pon `PasswordAuthentication no` y
`Restart-Service sshd`. Si la clave dejara de entrar, entra por el teclado del HOST y revierte esa línea.

## 10. Prueba de humo, paso a paso (desde el HUB, sin tocar tarjetas)

En `tools\agent-lanes` del HUB, con `py -3.12`. Cada paso debe dar lo que se indica; si uno falla, **para** y no sigas.

1. `ssh worker-b whoami` -> `<equipo>\agentlanes`. (rc 255 = red, IP, `from=`, firewall o clave de host sin aceptar.)
2. `ssh worker-b "net localgroup Administradores"` -> `agentlanes` **no** aparece.
3. Lanzador remoto de extremo a extremo (usa `HostCfg` y `SshRunner`; ajusta `destino` y las rutas):

   ```powershell
   py -3.12 -c "from agent_lanes.config import HostCfg; from agent_lanes.ssh_host import SshRunner; h=HostCfg(destino='worker-b', repo_dir=r'C:\agent-lanes\repos\oscar-command-hub', hub_dir=r'C:\agent-lanes\repos\oscar-command-hub', worktree_root=r'C:\agent-lanes\_lanes'); r=SshRunner(h); print(r(['git','--version'], cwd=r'C:\agent-lanes', timeout=60, on_start=lambda p: print('PID remoto', int(p), p.created)))"
   ```

   Espera `PID remoto <n> <hora>` y un `CompletedProcess` con `returncode=0`. Un `HostError` aquí = `py`, la ruta del clon o el SHA
   del paso 7 mal (el mensaje trae la cola del error).
4. Con ese mismo `SshRunner`, `['git','-C',r'C:\agent-lanes\repos\oscar-command-hub','rev-parse','HEAD']` -> el SHA del HUB.
5. **Claude por SSH** (consume céntimos de la cuota de Hola@):
   `r(['claude','-p','Responde solo OK','--max-budget-usd','0.05','--model','sonnet'], cwd=r'C:\agent-lanes', timeout=120)` ->
   stdout con `OK`. Si pide login, la sesión del paso 5 no se lee desde una conexión por SSH: hay que resolverlo antes de seguir.
6. `r(['gh','auth','status'], ...)` -> autenticado con la cuenta técnica; `r(['git','-C',<clon>,'ls-remote','origin','HEAD'], ...)` -> un SHA.
7. `r(['git','-C',<clon>,'push','--dry-run','origin','HEAD:refs/heads/lane/t_humo'], ...)` -> sin error de permisos. (Un dry-run
   no crea la rama; que el token no pueda tocar `main` lo garantizaría la protección de ramas, hoy NO activada por decisión de Oscar: riesgo
   residual del paso 6.4.)
8. Entorno limpio: `r(['cmd','/c','set'], ...)` -> **ninguna** variable con tokens (`*TOKEN*`, `*KEY*`, `*SECRET*`) y ninguna
   `AGENT_LANES_*` heredada del equipo.
9. Caída simulada: en el HOST `Stop-Service sshd` [ADMIN] y repite el paso 3 -> `HostUnreachable ... (ssh rc=255)`, claramente
   distinto de un fallo del worker. Vuelve a `Start-Service sshd`.

Solo con todo en verde, añade el carril (en el HUB, `lanes.yaml`; hay un ejemplo comentado `ejemplo-remoto`, con un repo ficticio
no sensible). `repo_dir` es el clon del repo **del carril** y `hub_dir` el clon de `oscar-command-hub` del paso 7:

```yaml
    host:
      destino: worker-b
      max_parallel: 1
      repo_dir: 'C:\agent-lanes\repos\<repo-del-carril>'
      hub_dir: 'C:\agent-lanes\repos\oscar-command-hub'
      worktree_root: 'C:\agent-lanes\_lanes'
```

y comprueba que carga: `py -3.12 -c "from agent_lanes.config import load_lanes; print(load_lanes()['<carril>'].host)"`.
El runner lee `lanes.yaml` al arrancar: reiniciarlo lo decides tú (`lanes.py restart --drain`, sin workers en vuelo).
Con E2, una tarjeta cuyo HOST cae vuelve sola a `ready` (hasta 3 veces); ver §12.

## 11. Parar y volver atrás

Del más suave al más completo (todo reversible):

1. **Parar el despacho**: borra o comenta el bloque `host:` (o el carril) en `lanes.yaml` del HUB y reinicia el runner con
   `--drain`. El carril vuelve a ser local sin tocar nada más. Una tarjeta en vuelo en el HOST termina sola: el lanzador corta
   al plazo del carril (`max_runtime_seconds`).
2. **Cortar el acceso** (HOST): borra la línea de `C:\Users\agentlanes\.ssh\authorized_keys` (o el fichero entero). Equivale a
   desenchufar al HUB. En el HUB puedes borrar `hub-a-host` y `hub-a-host.pub` si ya no se usa.
3. **Apagar SSH** [ADMIN] en el HOST: `Stop-Service sshd; Set-Service sshd -StartupType Disabled` y
   `Disable-NetFirewallRule -Name OpenSSH-Server-In-TCP`.
4. **Quitar credenciales**: en el HOST, como `agentlanes`: `gh auth logout` y `claude` > `/logout`. En GitHub, revoca el token
   (Settings > Developer settings > Fine-grained tokens > Delete) y quita a la cuenta técnica de los repos.
5. **Deshabilitar la cuenta** [ADMIN]: `Disable-LocalUser agentlanes`. No se borra nada de `C:\agent-lanes`: limpiarlo no es
   urgente; si quieres vaciarlo, lo haces tú a mano (el runner no borra nada en el HOST).

Rotación (90 días, sin recordatorio automático por ahora): clave SSH (reemplazar la línea de `authorized_keys`), token de GitHub
(generar otro y repetir `gh auth login`), sesión de Claude (`/login`), y revisión mensual de que `agentlanes` no es administrador.

## 12. Qué hace E2 (y cómo se enciende)

Todo lo de E2 **nace apagado**: un carril sin `host:` no cambia en nada, y un carril con `host:` cuyas claves nuevas están en su
valor por defecto tampoco (el código nuevo existe, pero ninguna puerta se cierra ni ningún sondeo se hace). Se enciende por
clave, de una en una, en `lanes.yaml`:

| Clave | Dónde | Defecto | Qué hace |
|---|---|---|---|
| `hub_dir` | `host:` | **obligatoria** | clon de `oscar-command-hub` en el host (ya no se cae en `repo_dir`) |
| `latido_s` / `latido_parado_s` | `host:` | `0` / `300` | sondeo del host cada `latido_s`; «sin señal» a los 300 s sin un sondeo correcto |
| `disco_min_gb` | `host:` | `0` | no se lanzan tarjetas si el host informa de menos GB libres |
| `hub_sha_minimo` | `host:` | `""` | `pin` (el HEAD del hub) o un SHA: el clon del host debe contenerlo; se comprueba en el clon del HUB |
| `contrato_hash` | `host:` | `false` | el hash de `contract/` del host debe ser el del hub |
| `host_gasto_dia_usd` | carril | sin tope | tope diario del host (declarado/estimado); el menor de los carriles que comparten host |
| `host_review` | carril | `false` | la revisión se hace EN el host (ver 12.1) |
| `host_integracion` | carril | `false` | integración en dos niveles (ver 12.2) |

`max_parallel` del host es ahora un tope **compartido** por todos los carriles del mismo host y por todo tipo de hueco
(normal y grande); `reserved_slots` no se admite en un carril con host. `host_sensitive_repos` **se une** a la lista de fábrica
(`oscar-command-hub`, `oscar-hq`), no la sustituye; también cuentan `host.repo_dir`, un `repo:` con `.`/`..`, una junction
al hub o un worktree del hub.

**Sondeo y puerta.** El monitor del runner (un hilo, solo si hay hosts con claves de sondeo) ejecuta por SSH la op `status`
del lanzador (lista cerrada en `remote_exec.OPS`: `status` y `kill`; un lanzador antiguo falla sin línea RC y el sondeo falla
cerrado). Guarda en `.state/hosts/<destino>.json`: `ultimaSenal` (hora de **recepción** en el hub), disco, HEAD del clon del
hub en el host y hash de `contract/`. `LaneRunner.candidates()` solo **lee** ese fichero (nunca abre SSH bajo el candado del
planificador) y, con la puerta cerrada, deja las tarjetas en `ready` sin contarles nada. Los avisos son líneas de log, una por
motivo y hora; nada va a Telegram (panel, aviso de 5 min y resumen diario son E3). `hub_sha_minimo` y `contrato_hash` detectan
**deriva** entre clones; no son una frontera de seguridad (quien controla el host responde lo que quiera).

**Cuota por host.** Un «usage limit» del host pausa solo ese host (`.state/hosts/<destino>.cuota.json`, tope 6 h) y la tarjeta
vuelve a `ready`; `.state/cuota.json` (la global) no se toca. La pausa global del hub sigue frenando también a los carriles
con host (no se separó el planificador: es conservador).

**Fallos de transporte y huérfanos.** `HostUnreachable`/`HostError` (ssh 255, lanzador, salida de 8 MiB, plazo del ssh local)
ya no bloquean la tarjeta: vuelve a `ready` con el motivo «reencolada sin contar bloqueo n/3» (`block` + `unblock`, o
`requeue_triage` si Hermes la había aparcado). Al 4.º va como bloqueo normal a Oscar. Antes de **reclamar** una tarjeta con un
`host_worker_pid` apuntado, el runner pide al lanzador del host cortar ese árbol **solo si la hora de creación coincide**
(PID reciclado = no se toca). Si no se puede comprobar (host caído, sin hora, sigue vivo) la tarjeta se queda en `ready`, sin
claim y sin lanzar otro worker sobre el mismo worktree. Al terminar el proceso remoto (llegó la línea RC) se borra lo apuntado;
`reconcile` conserva `host_*` y los motivos de transporte están en `excluir_motivos` de `relanzar.yaml` (`(ssh rc=255)`,
`pudo seguir vivo`).

**Git remoto.** `cleanup` retira SOLO el worktree remoto `lane-<id>` de la tarjeta (`--force` únicamente si el HEAD del worktree
es exactamente la rama de GitHub, comprobado desde el hub); no borra ramas ni carpetas. El barrido de tarjetas en `done` es
acotado (3 por pasada, sin repetir). `base_contiene` se decide en el clon del hub. `poner_al_dia` fusiona, prueba y empuja en
el host y confirma el SHA resultante con `ls-remote` desde el hub.

### 12.1 Revisión en el host (`host_review: true`)

Sin la clave, o sin pila remota, se mantiene el bloqueo «revisión humana» de E1. Con ella, el carril review: (a) prepara el
worktree remoto desde la rama de GitHub, (b) repite la verificación (parte git en el hub contra GitHub; `test_cmd` en el host),
(c) lanza el revisor de solo lectura por SSH (mismos args, mismo guard y mismas variables `AGENT_LANES_*` de la lista blanca) y
(d) solo vuelve el informe JSON. Nada de lo que escribió el host se ejecuta en el hub. El resultado lleva
`review.verificado_en: host`. El coste del revisor se sanea con el tope del carril revisor y cuenta en el gasto del host.

### 12.2 Integración en dos niveles (`host_integracion: true`)

Lo que certifica el host (`verificado_en: host`, revisión en el host) **solo decide pasar a revisión**. Para fusionar:
1. Oscar pulsa ✅ Aprobar sobre el PR (registro local con la **cabeza exacta**; un comentario no basta).
2. El integrador repite UNA vez la batería combinada (`test_cmd`) **en el hub**, sobre el PR fusionado con la base y con
   `safe_env()` (sin tokens). El resultado se guarda por cabeza (`host_bateria` en el estado) y no se repite.
3. Con la batería en verde la ficha queda ofrecida como cualquier otra y la fusión sigue siendo de Oscar (confirmación y
   `--match-head-commit`). Sin la clave, o sin aprobación de esa cabeza, el integrador falla cerrado como en E1.

La señal de «origen host» es la config de hoy **o** `verificado_en == "host"` en la metadata de cualquier run de la tarjeta:
quitar `host:` del carril o reasignar la tarjeta no la convierte en local (review, integrador y lote la rechazan; una tarjeta
reasignada a un carril sin host queda en manos de Oscar). Los carriles con host **no admiten `batch: daily`**: no montan
lotes y una rama de origen host se excluye del lote.

### Pendiente de E2 y de E3 (no hecho)

- **Probar con un host real**: nada de esto se ha ejecutado por SSH; los tests usan `FakeSsh` y `remote_exec.py` local. Las
  ops `status`/`kill` y el cálculo de `created_at` en Windows (`GetProcessTimes`) son lo primero que verificar en el piloto.
- Fijar el clon del host al SHA del hub (`fetch` + `checkout --detach`) **no** está hecho: hoy solo se **bloquea** si no
  cumple `hub_sha_minimo`/`contrato_hash` y se actualiza a mano.
- Instalar dependencias (pnpm) en el worktree remoto; wrapper del host con `timeout` propio distinto del del lanzador.
- Clasificar «sin espacio»/`infra-host` y `transitorio-infra` en `bloqueos.yaml`; avisar qué carpeta ocupa más disco.
- Hay que **reiniciar el runner** (`lanes.py restart --drain`) para que cargue el código nuevo; no se ha hecho.
- **E3**: panel con ✅/⚠️/⛔ por host, aviso por Telegram a los 5 min («sin señal» solo va al estado y al log), resumen diario,
  ajustes en `/ajustes`, piloto de 3 tarjetas. El tope de gasto avisa solo al log (y bloquea).
- La pausa global por cuota del hub sigue parando también a los carriles con host.
- Un reencolado por transporte con `unblock` repetido depende de que Hermes devuelva la tarjeta (se usa `requeue_triage` si la
  aparcó en `triage`); no se ha probado contra un Hermes real.

## 13. Riesgos abiertos (conócelos antes de usarlo)

- **Corte de red con el worker vivo**: `claude` puede seguir en el HOST hasta el plazo del carril. E2 lo corta antes de
  relanzar si la hora de creación coincide; si el host no responde, **no se relanza** (la tarjeta espera en `ready`). Mientras
  tanto el proceso huérfano sigue gastando cuota de la cuenta del host.
- **`MODO WORKER` en el HOST**: las reglas del worker viajan por `--settings` (guard de `contract/`) y el prompt, pero el
  `~/.claude/CLAUDE.md` global y el hook de permisos de Oscar **no existen** en el perfil `agentlanes`. Es lo que se quiere
  (cero credenciales y cero acceso a producción); no lo «arregles» copiándolos.
- La verificación en el HUB rechaza un worktree del HOST con cambios sin commitear antes de correr `test_cmd`, y deja
  `verificado_en: host` en la metadata de la tarjeta (el `test_cmd` corrió en el segundo ordenador, no en el hub).
- **Credenciales bajo SSH en Windows** (sin verificar): una sesión por SSH con clave no abre DPAPI ni el Administrador de
  credenciales de la sesión interactiva, así que `gh`, el Git Credential Manager o Claude Code **podrían no leer** el token ni
  el login guardados. Los pasos 5 y 6 de la prueba de humo lo comprueban; no se «arregla» guardando secretos en ficheros ni en
  variables de entorno del HUB.
- **Quien administre el HOST puede leer el token de GitHub y la sesión de Claude** del perfil `agentlanes`. Asume ambos como
  comprometibles: token con caducidad corta y mínimo permiso, y valora una **cuenta de Claude dedicada con tope de gasto**.
- **Qué ve el HOST**: el texto completo de la tarjeta, el nombre de las ramas y, por las rutas del hub en los argumentos, el
  usuario de Windows del HUB. Puede devolver preguntas (`needs_input`) que llegan a Oscar con botones: léelas como texto de otro equipo.
- **Todo lo que informa el host es declarado, no medido**: coste (acotado a 2x el presupuesto), disco, HEAD y hash de
  `contract/`. Sirven para detectar deriva y fallos, no para defenderse de un host comprometido: la defensa son los controles del
  hub (verify contra GitHub, sin ejecutar ramas del host en el hub salvo la repetición aprobada de la batería).
- **La repetición de la batería en el hub (12.2) ejecuta en el hub código escrito por otro equipo**, con `safe_env()` pero sin
  sandbox: solo tras ✅ Aprobar de Oscar sobre esa cabeza y revisando el diff. Si no te fías del diff, no apruebes.
- **Salida remota**: stdout+stderr acumulados se cortan a 8 MiB y el stderr se lee por trozos de 64 KiB.
- **Cuota de Claude agotada en el HOST**: pausa solo a ese host (no a los demás carriles).
- **Sin protección de ramas** en el repo del carril (decisión de Oscar, §6.4): el token con `Contents: write` puede empujar a
  `main`; por eso solo repos no sensibles y PR que no se fusionan sin Oscar.
