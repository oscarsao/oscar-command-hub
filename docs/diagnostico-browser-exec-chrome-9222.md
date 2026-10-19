# Diagnóstico: `browser_exec` no arranca (Chrome dedicado, puerto 9222)

Tarea t_45e1e66a · 2026-10-17 · solo diagnóstico, no se ha tocado ningún servicio ni `config.yaml`.

## Límite de esta investigación (leer primero)

El worker corre en un worktree aislado: **no puede leer `~/.hermes`, ni listar procesos, puertos o tareas
programadas del PC**. Por tanto lo que sigue separa lo **verificado** (el propio repo y el mensaje de error) de lo
**no verificado** (estado vivo del PC). La causa raíz no se puede confirmar sin los 3 comprobantes de la sección 3.

## 1. Qué sabemos (verificado)

- El error `WinError 10061` significa que **no hay ningún proceso escuchando en 127.0.0.1:9222**. No es un
  firewall ni un antivirus (un bloqueo daría timeout, no "conexión denegada expresamente"; en `localhost` Windows
  Defender no filtra). Es simplemente que **el Chrome de automatización no está abierto con
  `--remote-debugging-port=9222`**.
- `browser-harness` se limita a **conectarse** a `BU_CDP_URL`; espera 30 s y falla. No lanza Chrome él mismo
  (el propio mensaje dice "is the dedicated automation Chrome running?").
- En este repo **no hay nada** que arranque ese Chrome: ni `BU_CDP_URL`, ni `9222`, ni `remote-debugging-port` en
  `tools/` (agent-lanes, monitor, telegram, hermes-config). Los scripts de `hermes-config` solo conservan la
  herramienta `browser` en la lista de Telegram (`--keep-browser`), pero no lanzan ningún navegador.
- El monitor (`tools/monitor/monitor.py`) vigila procesos `chrome` de forma genérica, pero **no comprueba el 9222**:
  nadie avisa cuando este Chrome está caído.

**Conclusión 1 (pregunta 1 de la tarea):** dentro del repo, el Chrome dedicado **no está configurado para
autoarrancar con Hermes ni bajo demanda**. Si existe un arranque, vive fuera del repo (acceso directo, carpeta
Inicio de Windows o tarea programada) y no se ha podido ver desde aquí. Lo más probable es que **siempre haya que
lanzarlo a mano**, y que alguien lo hiciera una vez y se perdió al reiniciar/cerrar.

## 2. Causas posibles, de más a menos probable

| # | Causa | Cómo se distingue |
|---|-------|-------------------|
| A | El Chrome dedicado simplemente no está abierto (reinicio del PC, cierre manual, crash) | No hay `chrome` con `remote-debugging-port` |
| B | Chrome sí está abierto pero **sin** el flag (se abrió normal) | Hay chrome, pero nada escucha en 9222 |
| C | Se abrió otra instancia de Chrome con el **mismo perfil** que ya estaba en uso: la nueva se "fusiona" con la vieja y el flag se pierde | Pasa si se lanza sin `--user-data-dir` propio |
| D | Perfil dedicado inexistente o corrupto | Chrome arranca pero pide login/restaura vacío; o no abre |
| E | Puerto distinto al configurado | `netstat` muestra otro puerto de depuración |

Nota técnica relevante: desde Chrome 136, `--remote-debugging-port` **se ignora si se usa el perfil por defecto**;
hace falta un `--user-data-dir` propio. Si el acceso directo actual no lo lleva, Chrome abre pero el puerto no
escucha (causas B/C).

## 3. Cómo confirmar la causa (Oscar, copiar/pegar en PowerShell, solo lectura)

```powershell
# 1) ¿Escucha algo en 9222?  (vacío = no; causa A/B/C)
netstat -ano | findstr :9222
# 2) ¿Hay algún Chrome con depuración remota y con qué perfil?
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like '*remote-debugging*' } | Select-Object ProcessId, CommandLine
# 3) ¿Hay algún arranque automático existente?
Get-ScheduledTask | Where-Object { $_.TaskName -match 'chrome|browser|hermes' } | Select-Object TaskName, State
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
```

Interpretación: (1) vacío y (2) vacío → causa A. (2) vacío pero hay Chrome normal abierto → B/C. (3) sin
resultados → confirma que no hay autoarranque.

## 4. Opciones

**Comando de arranque propuesto** (perfil propio, no toca tu Chrome diario):

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="C:\Users\oscar\.hermes\chrome-automation-profile" --no-first-run
```

(Ajustar la ruta de `chrome.exe` si Chrome está en `Program Files (x86)` o en `AppData\Local`.)

| Opción | Pros | Contras |
|--------|------|---------|
| **1. Acceso directo en el Escritorio** con ese comando (doble clic) | Cero riesgo, nada que instalar, Oscar lo controla | Manual: si se olvida, `browser_exec` falla; se pierde al reiniciar |
| **2. Acceso directo en la carpeta Inicio de Windows** (`shell:startup`) | Arranca solo al iniciar sesión; sin servicios ni cambios en Hermes; reversible borrando el acceso directo | Abre una ventana de Chrome visible siempre; si se cierra, hay que reabrirlo; los logins (LexCloud) persisten porque el perfil es propio |
| **3. Tarea programada "al iniciar sesión"** con reinicio si falla | Más robusto que la opción 2 (relanza si cae) | Más cosas que mantener; requiere crear una tarea (acción que pide OK) |
| **4. Que Hermes/`browser-harness` lo lance bajo demanda** | Sin ventana permanente; sin olvidos | Requiere tocar `config.yaml`/código de Hermes (fuera del hub y prohibido sin confirmar); más complejo |
| **5. Añadir al monitor un chequeo del 9222** (aviso si cae) | Complementa cualquiera de las anteriores; detecta el problema antes de que falle una tarea | Es cambio en `tools/monitor`; solo avisa, no arregla |

## 5. Recomendación

**Opción 2 + 5**, por este orden:

1. **Ahora (sin riesgo):** crear el acceso directo con el comando de la sección 4 y hacer doble clic; relanzar la
   tarea de LexCloud. Si funciona, causa confirmada (A/B/C).
2. **Autoarranque:** copiar ese mismo acceso directo a `shell:startup`. Es la opción más simple, reversible y no
   toca Hermes ni servicios.
3. **Después, como tarea aparte:** añadir al monitor un aviso "Chrome de automatización caído (9222)" para no
   descubrirlo a mitad de una auditoría.

La primera vez, en esa ventana hay que hacer login en LexCloud una vez; el perfil dedicado lo recuerda.

## 6. Decisiones de Oscar (17-oct) y siguiente paso

- Autoarranque elegido: **acceso directo en Inicio** (opción 2).
- Aviso del monitor si cae el 9222 (opción 5): **sí**, como tarea aparte (pendiente de abrir en el kanban).
- Comprobantes de la sección 3: Oscar dijo que los pega, pero su salida no ha llegado al worker. La causa es A/B/C
  (nada escucha en 9222); el paso siguiente la confirma solo.

### Crear el acceso directo en Inicio (copiar y pegar en PowerShell, una vez)

Crea el acceso directo en la carpeta Inicio (arranca al iniciar sesión) y lo lanza ahora. No toca Hermes,
`config.yaml` ni servicios. Para deshacerlo, borra `Chrome automatizacion.lnk` de `shell:startup`.

```powershell
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe","${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe","$env:LocalAppData\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "No encuentro chrome.exe" }
$perfil = "$env:USERPROFILE\.hermes\chrome-automation-profile"
$lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Chrome automatizacion.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $chrome
$s.Arguments = "--remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=`"$perfil`" --no-first-run"
$s.Save()
Start-Process $lnk
Start-Sleep 5
netstat -ano | findstr :9222
```

Si la última línea muestra `LISTENING` en `127.0.0.1:9222`, funciona: relanza la tarea de LexCloud (t_63023435) y
haz login en LexCloud una vez en esa ventana (el perfil propio lo recuerda). Si sale vacía, pega lo que devuelva y
se revisa (causa C/D). También se comprueba abriendo `http://127.0.0.1:9222/json/version` en cualquier navegador.

Límite: la ventana de Chrome queda abierta; si se cierra, `browser_exec` vuelve a fallar hasta relanzar el acceso
directo. Por eso el aviso del monitor (opción 5) es el complemento.
