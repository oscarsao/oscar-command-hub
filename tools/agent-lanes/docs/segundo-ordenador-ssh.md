# Segundo ordenador por SSH: pasos manuales (Oscar)

Spec: `specs/worker-remoto-ssh.md` (decisiones de Oscar, 20-11-2026). Esto es la **entrega E1**: el código ya sabe despachar un
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
icacls C:\agent-lanes /grant "agentlanes:(OI)(CI)M"

Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
```

Cierra el servidor a lo mínimo. Edita `C:\ProgramData\ssh\sshd_config` (como administrador) y deja estas líneas activas
(descomenta o añade; **todavía no** pongas `PasswordAuthentication no`, va en el paso 9):

```
PubkeyAuthentication yes
AllowUsers agentlanes
```

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

1. Que `oscarsao` la añada como colaboradora con permiso de escritura **solo** al repo del carril (`oscarsao/oscar-command-hub`).
2. Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token:
   - Repository access: **Only select repositories**, solo ese repo (y los demás del carril, si los hay).
   - Permissions: **Contents: Read and write** y **Pull requests: Read and write**. Nada más (Metadata es de solo lectura y
     viene sola; **sin** Workflows ni Administration).
   - Caducidad: 90 días (apunta la fecha en el §11).
3. Barrera real: `main` debe estar protegida (sin push directo). Compruébalo en Settings > Branches o Rules del repo; si no lo
   está, protégela **antes** de dar este token. El push de `lane/*` es lo único que hace falta. (Sin verificar: no tengo acceso
   a la configuración del repo.)

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
    BatchMode yes
    ServerAliveInterval 15
```

**Aceptar la clave del HOST una vez, a mano** (el runner usa `BatchMode=yes` y no puede responder a esa pregunta; sin esto
falla con rc 255). Desde el HUB: `ssh worker-b` y compara la huella que muestra con la del HOST
(`ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub` en el HOST) antes de escribir `yes`. Nunca se usa
`StrictHostKeyChecking=no`.

## 9. HOST [ADMIN]: cerrar la contraseña (solo cuando la clave ya entre)

Con `ssh worker-b whoami` funcionando desde el HUB, edita `C:\ProgramData\ssh\sshd_config`, pon `PasswordAuthentication no` y
`Restart-Service sshd`. Si la clave dejara de entrar, entra por el teclado del HOST y revierte esa línea.

## 10. Prueba de humo, paso a paso (desde el HUB, sin tocar tarjetas)

En `tools\agent-lanes` del HUB, con `py -3.12`. Cada paso debe dar lo que se indica; si uno falla, **para** y no sigas.

1. `ssh worker-b whoami` -> `<equipo>\agentlanes`. (rc 255 = red, IP, `from=`, firewall o clave de host sin aceptar.)
2. `ssh worker-b "net localgroup Administradores"` -> `agentlanes` **no** aparece.
3. Lanzador remoto de extremo a extremo (usa `HostCfg` y `SshRunner`; ajusta `destino` y las rutas):

   ```powershell
   py -3.12 -c "from agent_lanes.config import HostCfg; from agent_lanes.ssh_host import SshRunner; h=HostCfg(destino='worker-b', repo_dir=r'C:\agent-lanes\repos\oscar-command-hub', worktree_root=r'C:\agent-lanes\_lanes'); r=SshRunner(h); print(r(['git','--version'], cwd=r'C:\agent-lanes', timeout=60, on_start=lambda p: print('PID remoto', int(p), p.created)))"
   ```

   Espera `PID remoto <n> <hora>` y un `CompletedProcess` con `returncode=0`. Un `HostError` aquí = `py`, la ruta del clon o el SHA
   del paso 7 mal (el mensaje trae la cola del error).
4. Con ese mismo `SshRunner`, `['git','-C',r'C:\agent-lanes\repos\oscar-command-hub','rev-parse','HEAD']` -> el SHA del HUB.
5. **Claude por SSH** (consume céntimos de la cuota de Hola@):
   `r(['claude','-p','Responde solo OK','--max-budget-usd','0.05','--model','sonnet'], cwd=r'C:\agent-lanes', timeout=120)` ->
   stdout con `OK`. Si pide login, la sesión del paso 5 no se lee desde una conexión por SSH: hay que resolverlo antes de seguir.
6. `r(['gh','auth','status'], ...)` -> autenticado con la cuenta técnica; `r(['git','-C',<clon>,'ls-remote','origin','HEAD'], ...)` -> un SHA.
7. `r(['git','-C',<clon>,'push','--dry-run','origin','HEAD:refs/heads/lane/t_humo'], ...)` -> sin error de permisos. (Un dry-run
   no crea la rama; que el token no pueda tocar `main` lo garantiza la protección de la rama, paso 6.3.)
8. Entorno limpio: `r(['cmd','/c','set'], ...)` -> **ninguna** variable con tokens (`*TOKEN*`, `*KEY*`, `*SECRET*`) y ninguna
   `AGENT_LANES_*` heredada del equipo.
9. Caída simulada: en el HOST `Stop-Service sshd` [ADMIN] y repite el paso 3 -> `HostUnreachable ... (ssh rc=255)`, claramente
   distinto de un fallo del worker. Vuelve a `Start-Service sshd`.

Solo con todo en verde, añade el carril (en el HUB, `lanes.yaml`; hay un ejemplo comentado `claude-hub-remoto`):

```yaml
    host:
      destino: worker-b
      max_parallel: 1
      repo_dir: 'C:\agent-lanes\repos\oscar-command-hub'
      worktree_root: 'C:\agent-lanes\_lanes'
```

y comprueba que carga: `py -3.12 -c "from agent_lanes.config import load_lanes; print(load_lanes()['claude-hub-remoto'].host)"`.
El runner lee `lanes.yaml` al arrancar: reiniciarlo lo decides tú (`lanes.py restart --drain`, sin workers en vuelo).
Esta entrega **no** reencola sola una tarjeta si el HOST cae (E2): con `HostUnreachable` la tarjeta se bloquea como
«error del runner» y la sueltas a mano.

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

## 12. Lo que E1 no hace (para que no te sorprenda)

- **E2**: reencolar sin contar bloqueo si el HOST cae, cortar `claude` huérfanos en el HOST (el estado ya guarda `host`,
  `host_worker_pid` y `host_worker_created` para eso; el runner **no** los trata como PID local), comprobar disco, pausa de cuota
  por host, tope por host compartido entre carriles, limpieza del worktree remoto (`cleanup`), poner la rama al día en el HOST
  (`poner_al_dia`), comprobar que el SHA y el hash de `contract/` del HOST son los del HUB antes de despachar, instalar
  dependencias (pnpm) en el worktree remoto.
- **E3**: latido del HOST, panel, aviso a los 5 minutos, resumen diario, ajustes en `/ajustes`, `host_gasto_dia_usd`, piloto de 3 tarjetas.
- La revisión (carril review) y el integrador siguen trabajando sobre el clon **del HUB** (`repo:` del carril) contra GitHub, y
  **ejecutan allí** el `test_cmd` de la rama y el revisor: la separación «el código de la rama solo corre en el HOST» vale
  para la primera verificación, no para la revisión ni la integración. No uses un carril remoto con repos sensibles hasta que se
  decida (E2) si la revisión también pasa por el HOST.

## 13. Riesgos abiertos de E1 (conócelos antes de usarlo)

- **Corte de red con el worker vivo**: si ssh cae a mitad de una tarjeta, `claude` puede seguir en el HOST hasta el plazo del
  carril (`max_runtime_seconds`, donde el lanzador lo corta). El error lo dice («pudo seguir vivo»). La tarjeta se bloquea como
  «error del runner»: **no la sueltes antes de que pase ese plazo**, o dos workers escribirían en el mismo worktree. E2 lo
  comprobará solo con `host_worker_pid`/`host_worker_created`.
- **`MODO WORKER` en el HOST**: las reglas del worker viajan por `--settings` (guard de `contract/`) y el prompt, pero el
  `~/.claude/CLAUDE.md` global y el hook de permisos de Oscar **no existen** en el perfil `agentlanes`. Es lo que se quiere
  (cero credenciales y cero acceso a producción); no lo «arregles» copiándolos.
- La verificación en el HUB rechaza un worktree del HOST con cambios sin commitear antes de correr `test_cmd`.
