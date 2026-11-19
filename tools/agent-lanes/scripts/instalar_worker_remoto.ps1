<#
.SYNOPSIS
  Prepara un SEGUNDO ordenador (Windows 11, con su propia cuenta de Claude) como worker de agent-lanes.
  Guia completa: tools/agent-lanes/docs/segundo-ordenador.md

.DESCRIPTION
  Idempotente. Dos modos:
    -Comprobar   SOLO LECTURA. Verifica herramientas, carpetas, repos y .env y lo cuenta por consola.
                 No crea carpetas, no clona, no escribe ficheros (salvo que pidas -Informe <ruta>).
    (normal)     Hace lo mismo y ademas: crea las carpetas que falten (raiz de worktrees y padres de repos),
                 clona con `gh repo clone` los repos que falten y escribe el informe en
                 tools/agent-lanes/.state/informe-segundo-worker.txt (esa carpeta no se versiona).

  Lo que NUNCA hace: pedir o escribir secretos, crear o editar el .env, borrar nada, instalar herramientas,
  registrar la tarea programada ni arrancar el runner (eso es un paso manual y explicito de la guia, seccion 8).
  El .env solo se lee para saber QUE NOMBRES de variable tiene y si cada una esta rellena: jamas imprime un valor.

.PARAMETER Comprobar
  Modo solo lectura.
.PARAMETER Informe
  Ruta donde escribir el informe tambien en modo -Comprobar (opcional). En modo normal se escribe siempre.
.PARAMETER ProbarConexion
  Ejecuta `runner.py --check` (solo lectura) para probar HUB_DB_DSN: contrato del esquema hub.*, sello y sonda del
  guard de los workers y si el kanban aceptaria reclamar a ESTE equipo (alta y activo en hub.workers, interruptor de
  workers externos). Es opcional porque abre una conexion real con la base del hub; imprime solo el codigo de salida y
  las lineas relevantes (nunca el DSN). Codigos de runner.py --check: 3 contrato roto, 4 guard no apto, 5 el kanban
  no aceptaria reclamar.
.PARAMETER EnvFile
  Ruta alternativa del .env a validar (por defecto tools/agent-lanes/.env). Solo se lee.
.PARAMETER ProbarClaude
  Ejecuta una llamada minima `claude -p` para ver que la cuenta de ESTE equipo responde. Gasta un poco de cuota.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\scripts\instalar_worker_remoto.ps1 -Comprobar
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\scripts\instalar_worker_remoto.ps1
#>
[CmdletBinding()]
param(
    [switch]$Comprobar,
    [string]$Informe = "",
    [switch]$ProbarConexion,
    [switch]$ProbarClaude,
    [string]$EnvFile = ""
)
# Solo ASCII en este fichero: Windows PowerShell 5.1 lee los .ps1 sin BOM como ANSI.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$LanesDir = Split-Path -Parent $PSScriptRoot            # ...\tools\agent-lanes
$HubRoot  = Split-Path -Parent (Split-Path -Parent $LanesDir)
$LanesYaml = Join-Path $LanesDir "lanes.yaml"
if (-not $EnvFile) { $EnvFile = Join-Path $LanesDir ".env" }
$ModoTxt   = if ($Comprobar) { "COMPROBAR (solo lectura)" } else { "NORMAL (crea carpetas y clona)" }

$script:Filas = New-Object System.Collections.ArrayList
function Add-Fila([string]$Nivel, [string]$Que, [string]$Detalle = "") {
    [void]$script:Filas.Add([pscustomobject]@{ Nivel = $Nivel; Que = $Que; Detalle = $Detalle })
    $color = switch ($Nivel) { "OK" { "Green" } "AVISO" { "Yellow" } "HECHO" { "Cyan" } default { "Red" } }
    $linea = ("[{0,-5}] {1}" -f $Nivel, $Que)
    if ($Detalle) { $linea += " - $Detalle" }
    Write-Host $linea -ForegroundColor $color
}
# Niveles: OK, HECHO (accion realizada en modo normal), AVISO (revisar), FALTA (hay que arreglarlo)

function Get-Cmd([string]$Nombre) {
    $c = Get-Command $Nombre -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source } else { return $null }
}
function Invoke-Quiet([string]$Exe, [string[]]$Argumentos) {
    # Devuelve @{Codigo; Salida}. No lanza si el programa falla; no muestra nada por pantalla.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Exe @Argumentos 2>&1 | Out-String
        $code = $LASTEXITCODE
    } catch {
        $out = $_.Exception.Message
        $code = 255
    } finally {
        $ErrorActionPreference = $prev
    }
    return @{ Codigo = $code; Salida = $out.Trim() }
}
function Test-MismaRuta([string]$A, [string]$B) {
    $na = ($A -replace "/", "\").TrimEnd("\").ToLowerInvariant()
    $nb = ($B -replace "/", "\").TrimEnd("\").ToLowerInvariant()
    return ($na -eq $nb)
}

Write-Host "== instalar_worker_remoto - modo $ModoTxt ==" -ForegroundColor White
Write-Host "Equipo: $env:COMPUTERNAME  Usuario: $env:USERNAME  Hub: $HubRoot"
Write-Host ""

# ---------------------------------------------------------------- 1. Herramientas
Write-Host "-- 1. Herramientas" -ForegroundColor White
$git = Get-Cmd "git"
if ($git) { Add-Fila "OK" "git" ((Invoke-Quiet $git @("--version")).Salida) } else { Add-Fila "FALTA" "git" "instala Git for Windows" }

$gh = Get-Cmd "gh"
if (-not $gh) {
    Add-Fila "FALTA" "gh (GitHub CLI)" "instala GitHub CLI y haz 'gh auth login'"
} else {
    $r = Invoke-Quiet $gh @("auth", "status")
    if ($r.Codigo -eq 0) { Add-Fila "OK" "gh autenticado" "gh auth status = 0" }
    else { Add-Fila "FALTA" "gh sin sesion" "ejecuta 'gh auth login' con la cuenta que tiene escritura en los repos" }
}

$node = Get-Cmd "node"
if (-not $node) { Add-Fila "FALTA" "node" "instala Node 24" }
else {
    $v = (Invoke-Quiet $node @("--version")).Salida
    if ($v -match "^v24\.") { Add-Fila "OK" "node" $v } else { Add-Fila "AVISO" "node" "$v (se espera v24.x)" }
}

$py = Get-Cmd "py"
$pyOk = $false
if (-not $py) { Add-Fila "FALTA" "py (lanzador de Python)" "instala Python 3.12 con el lanzador py" }
else {
    $r = Invoke-Quiet $py @("-3.12", "--version")
    if ($r.Codigo -eq 0) {
        $pyOk = $true
        Add-Fila "OK" "python 3.12" $r.Salida
        foreach ($m in @("yaml", "psycopg2")) {
            $rm = Invoke-Quiet $py @("-3.12", "-c", "import $m")
            if ($rm.Codigo -eq 0) { Add-Fila "OK" "modulo $m" "importa" }
            else {
                $pkg = if ($m -eq "yaml") { "pyyaml" } else { "psycopg2-binary" }
                Add-Fila "FALTA" "modulo $m" "py -3.12 -m pip install $pkg"
            }
        }
    } else { Add-Fila "FALTA" "python 3.12" "py -3.12 no responde" }
}

$corepack = Get-Cmd "corepack"
if ($corepack) { Add-Fila "OK" "corepack" "disponible (pnpm@10.18.3 se activa con la guia, seccion 2)" }
else { Add-Fila "FALTA" "corepack" "viene con Node 24; reinstala Node" }

$claude = Get-Cmd "claude"
if (-not $claude) { Add-Fila "FALTA" "claude (Claude Code)" "instalalo e inicia sesion con la cuenta de ESTE equipo" }
else {
    Add-Fila "OK" "claude" ((Invoke-Quiet $claude @("--version")).Salida)
    if ($ProbarClaude) {
        $r = Invoke-Quiet $claude @("-p", "Responde solo la palabra: ok")
        if ($r.Codigo -eq 0) { Add-Fila "OK" "claude -p responde" "la cuenta de este equipo tiene sesion y cuota" }
        else { Add-Fila "FALTA" "claude -p no responde" "codigo $($r.Codigo): inicia sesion (claude, luego /login)" }
    } else {
        Add-Fila "AVISO" "claude -p sin probar" "usa -ProbarClaude (gasta una llamada minima) o la prueba manual de la guia"
    }
}
# Presencia (nunca el valor) de una clave de API que haria facturar por API en vez de por el plan.
if ($env:ANTHROPIC_API_KEY) {
    Add-Fila "AVISO" "ANTHROPIC_API_KEY definida en el entorno" "los workers facturarian por API, no por tu plan: quitala si no es lo que quieres"
}

# RAM -> max_workers orientativo
try {
    $ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    $sug = [math]::Max(1, [math]::Min(3, [math]::Floor($ramGb / 8)))
    Add-Fila "OK" "RAM" "$ramGb GB -> max_workers orientativo para este equipo: $sug (se pasa con install-service.ps1 -MaxWorkers $sug)"
} catch {
    Add-Fila "AVISO" "RAM" "no se pudo leer"
}

# ---------------------------------------------------------------- 2. lanes.yaml -> repos y carpetas
Write-Host ""
Write-Host "-- 2. Repos y carpetas (leidos de lanes.yaml)" -ForegroundColor White
$cfg = $null
if (-not (Test-Path $LanesYaml)) {
    Add-Fila "FALTA" "lanes.yaml" "no existe $LanesYaml (ejecuta el script desde un clon del hub)"
} elseif (-not $pyOk) {
    Add-Fila "FALTA" "lanes.yaml sin leer" "hace falta py -3.12 con PyYAML"
} else {
    $pyCode = @'
import json, sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
out = {"worktree_root": (d.get("defaults") or {}).get("worktree_root"),
       "max_workers": (d.get("runner") or {}).get("max_workers"), "repos": []}
seen = set()
def add(lane, v):
    gh = (v or {}).get("github") or ""
    repo = (v or {}).get("repo") or ""
    if repo and (gh, repo) not in seen:
        seen.add((gh, repo))
        out["repos"].append({"lane": lane, "repo": repo, "github": gh, "base": (v or {}).get("base") or ""})
for name, v in (d.get("lanes") or {}).items():
    add(name, v)
    for sub, sv in ((v or {}).get("repos") or {}).items():
        add(name + "/" + sub, sv)
print(json.dumps(out))
'@
    # El codigo va por stdin: pasar un programa multilinea como argumento lo rompe el quoting de Windows PowerShell 5.1.
    $salidaPy = ""
    try {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $salidaPy = ($pyCode | & $py -3.12 - $LanesYaml 2>&1 | Out-String).Trim()
    } catch { $salidaPy = "" } finally { $ErrorActionPreference = $prev }
    try { $cfg = $salidaPy | ConvertFrom-Json } catch { $cfg = $null }
    if (-not $cfg) { Add-Fila "FALTA" "lanes.yaml ilegible" "no se pudo interpretar" }
}

if ($cfg) {
    $perfil = $env:USERPROFILE
    $wtRoot = [string]$cfg.worktree_root
    if (-not $wtRoot) { Add-Fila "FALTA" "worktree_root" "defaults.worktree_root vacio en lanes.yaml" }
    else {
        $wtNative = $wtRoot -replace "/", "\"
        if (Test-Path -LiteralPath $wtNative) { Add-Fila "OK" "carpeta de worktrees" $wtNative }
        elseif ($Comprobar) { Add-Fila "FALTA" "carpeta de worktrees" "$wtNative no existe (el modo normal la crea)" }
        else {
            try { New-Item -ItemType Directory -Force -Path $wtNative | Out-Null; Add-Fila "HECHO" "carpeta de worktrees creada" $wtNative }
            catch { Add-Fila "FALTA" "carpeta de worktrees" "no se pudo crear $wtNative : $($_.Exception.Message)" }
        }
        if (-not $wtNative.ToLowerInvariant().StartsWith($perfil.ToLowerInvariant())) {
            Add-Fila "AVISO" "ruta fuera de tu perfil" "lanes.yaml usa $wtRoot y tu perfil es $perfil (ver guia, seccion 3: mismo nombre de usuario o edicion local)"
        }
    }

    foreach ($rp in $cfg.repos) {
        $dir = ([string]$rp.repo) -replace "/", "\"
        $gh_ = [string]$rp.github
        $tag = "repo $($rp.lane)"
        if (Test-Path -LiteralPath (Join-Path $dir ".git")) {
            # El runner solo reclama si origin coincide con el owner/repo de lanes.yaml (candado de seguridad).
            $o = Invoke-Quiet $git @("-C", $dir, "remote", "get-url", "origin")
            if ($o.Codigo -ne 0) { Add-Fila "FALTA" $tag "$dir sin remote origin" }
            elseif ($gh_ -and ($o.Salida -notmatch [regex]::Escape($gh_))) {
                Add-Fila "FALTA" $tag "origin de $dir no apunta a $gh_ (el candado del runner no reclamaria)"
            } else {
                $st = Invoke-Quiet $git @("-C", $dir, "status", "--short")
                $limpio = if ($st.Salida) { "con cambios locales (no los toca nadie, pero el runner trabaja en worktrees aparte)" } else { "limpio" }
                Add-Fila "OK" $tag "$dir ($gh_, base $($rp.base)), $limpio"
            }
        } elseif (Test-Path -LiteralPath $dir) {
            Add-Fila "FALTA" $tag "$dir existe pero no es un clon git: no se toca, revisalo a mano"
        } elseif ($Comprobar) {
            Add-Fila "FALTA" $tag "$dir no existe: el modo normal clonaria $gh_"
        } elseif (-not $gh) {
            Add-Fila "FALTA" $tag "sin gh no se puede clonar $gh_"
        } else {
            try {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dir) | Out-Null
                $c = Invoke-Quiet $gh @("repo", "clone", $gh_, $dir)
                if ($c.Codigo -eq 0) { Add-Fila "HECHO" $tag "clonado $gh_ en $dir" }
                else { Add-Fila "FALTA" $tag "gh repo clone $gh_ fallo (codigo $($c.Codigo)): tienes acceso al repo?" }
            } catch { Add-Fila "FALTA" $tag "no se pudo preparar $dir : $($_.Exception.Message)" }
        }
    }
}

# ---------------------------------------------------------------- 3. .env (solo nombres)
Write-Host ""
Write-Host "-- 3. .env de este equipo (solo se mira que variables estan rellenas; nunca se imprime un valor)" -ForegroundColor White
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Add-Fila "FALTA" ".env" "no existe $EnvFile. Creala a mano con los nombres de la guia, seccion 4 (este script no la crea)"
} else {
    if ($git) {
        $ig = Invoke-Quiet $git @("-C", $LanesDir, "check-ignore", "-q", ".env")
        if ($ig.Codigo -eq 0) { Add-Fila "OK" ".env ignorado por git" "no se puede commitear por error" }
        else { Add-Fila "FALTA" ".env NO esta ignorado por git" "revisa tools/agent-lanes/.gitignore" }
    }
    $vars = @{}
    foreach ($linea in (Get-Content -LiteralPath $EnvFile -Encoding UTF8)) {
        $t = $linea.Trim()
        if (-not $t -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $i = $t.IndexOf("=")
        $vars[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    }
    foreach ($n in @("HUB_DB_DSN", "KANBAN_BACKEND", "HUB_WORKER_ID")) {
        if ($vars.ContainsKey($n) -and $vars[$n]) { Add-Fila "OK" "$n" "definida (valor no mostrado)" }
        else { Add-Fila "FALTA" "$n" "ausente o vacia en .env" }
    }
    if ($vars.ContainsKey("KANBAN_BACKEND") -and $vars["KANBAN_BACKEND"] -and $vars["KANBAN_BACKEND"].ToLowerInvariant() -ne "supabase") {
        Add-Fila "FALTA" "KANBAN_BACKEND" "debe ser supabase en un worker remoto (con hermes seria un kanban distinto al del equipo principal)"
    }
    if ($vars.ContainsKey("HUB_DB_DSN") -and $vars["HUB_DB_DSN"] -and ($vars["HUB_DB_DSN"] -notmatch "^postgres(ql)?://")) {
        Add-Fila "AVISO" "HUB_DB_DSN" "no empieza por postgresql:// (formato no reconocido)"
    }
    if ($vars.ContainsKey("HUB_WORKER_ID") -and $vars["HUB_WORKER_ID"]) {
        # Mismo formato que el CHECK de hub.workers.id: minusculas, numeros, - o _ (2 a 63 caracteres). Distinto = el kanban lo rechaza.
        if ($vars["HUB_WORKER_ID"] -cnotmatch "^[a-z0-9][a-z0-9_-]{1,62}$") {
            Add-Fila "FALTA" "HUB_WORKER_ID con formato no valido" "usa solo minusculas, numeros, - o _ (por ejemplo pc2-casa); debe coincidir con el alta que hace Oscar en hub.workers"
        } elseif ($vars["HUB_WORKER_ID"] -eq $env:COMPUTERNAME.ToLowerInvariant()) {
            Add-Fila "AVISO" "HUB_WORKER_ID" "igual al nombre del equipo: vale si es unico entre todos los workers"
        }
    }
    # Variables que NO deben estar en un worker secundario (duplicarian al equipo principal).
    foreach ($n in @("CARRILES_BOT_TOKEN")) {
        if ($vars.ContainsKey($n) -and $vars[$n]) { Add-Fila "FALTA" "$n presente" "quitala: dos procesos con getUpdates sobre el mismo bot se pisan" }
    }
    if ($vars.ContainsKey("INTEGRATOR_ENABLED") -and $vars["INTEGRATOR_ENABLED"].ToLowerInvariant() -eq "true") {
        Add-Fila "FALTA" "INTEGRATOR_ENABLED=true" "el integrador solo corre en el equipo principal"
    }
    if ($vars.ContainsKey("BOARD_GUARD_APPLY") -and $vars["BOARD_GUARD_APPLY"].ToLowerInvariant() -eq "true") {
        Add-Fila "AVISO" "BOARD_GUARD_APPLY=true" "mueve tarjetas: dejalo solo en el equipo principal"
    }
    if ($vars.ContainsKey("TELEGRAM_BOT_TOKEN") -and $vars["TELEGRAM_BOT_TOKEN"]) {
        Add-Fila "AVISO" "TELEGRAM_BOT_TOKEN presente" "opcional (solo avisos de envio). Sin CARRILES_BOT_TOKEN no escucha botones. Ver guia, seccion 4"
    }
}

if ($ProbarConexion) {
    if (-not $pyOk) { Add-Fila "FALTA" "probar conexion" "necesita py -3.12" }
    else {
        $r = Invoke-Quiet $py @("-3.12", (Join-Path $LanesDir "runner.py"), "--check")
        $lineas = @($r.Salida -split "`r?`n" | Where-Object { $_ -match "kanban:|contrato|guard|admisi|aceptar|^\s+- " } | ForEach-Object { $_ -replace "(postgres(ql)?://)\S+", '$1<oculto>' })
        $resumen = ($lineas -join " | ")
        if ($r.Codigo -eq 0) { Add-Fila "OK" "runner.py --check" "contrato del kanban, guard de workers y admision OK. $resumen" }
        else {
            $que = switch ($r.Codigo) { 3 { "contrato del esquema roto o sin conexion" } 4 { "guard de los workers no apto (sello o sonda)" } 5 { "el kanban no aceptaria reclamar a este equipo (pidele a Oscar el alta en hub.workers)" } default { "fallo" } }
            Add-Fila "FALTA" "runner.py --check" "codigo $($r.Codigo): $que. $resumen"
        }
    }
}

# ---------------------------------------------------------------- 4. Guard de los workers y tarea programada
Write-Host ""
Write-Host "-- 4. Guard de los workers y estado del runner" -ForegroundColor White
$manifest = Join-Path $LanesDir "contract\guard-manifest.json"
if (-not (Test-Path -LiteralPath (Join-Path $LanesDir "agent_lanes\multiworker.py")) -or -not (Test-Path -LiteralPath $manifest)) {
    Add-Fila "FALTA" "clon del hub desactualizado" "falta el soporte de varios equipos (agent_lanes\multiworker.py o contract\guard-manifest.json): git -C $HubRoot pull --ff-only"
} elseif (-not $pyOk) {
    Add-Fila "FALTA" "guard de los workers sin comprobar" "hace falta py -3.12"
} else {
    # Solo lectura: recalcula el sello de los ficheros del guard y ejecuta el hook real con una orden prohibida (push a master).
    Push-Location $LanesDir
    try { $g = Invoke-Quiet $py @("-3.12", "-m", "agent_lanes.guard_hash") } finally { Pop-Location }
    $detalle = (($g.Salida -split "`r?`n" | Where-Object { $_ -match "^- |^aviso:" } | ForEach-Object { $_.Trim() }) -join " | ")
    if ($g.Codigo -eq 0) { Add-Fila "OK" "guard de los workers" "sello y sonda OK (este equipo lleva el mismo guard aprobado). $detalle" }
    else { Add-Fila "FALTA" "guard de los workers NO apto" "$detalle (git pull y, si sigue, avisa a Oscar: este equipo no debe ejecutar workers)" }
}
$task = Get-ScheduledTask -TaskName "agent-lanes runner" -ErrorAction SilentlyContinue
if ($task) { Add-Fila "AVISO" "tarea 'agent-lanes runner'" "ya existe (estado $($task.State)). El script no la toca" }
else { Add-Fila "OK" "tarea 'agent-lanes runner'" "no registrada (el registro es manual, guia seccion 8)" }

# ---------------------------------------------------------------- Resumen e informe
$faltas = @($script:Filas | Where-Object { $_.Nivel -eq "FALTA" }).Count
$avisos = @($script:Filas | Where-Object { $_.Nivel -eq "AVISO" }).Count
Write-Host ""
$veredicto = if ($faltas -gt 0) { "NO LISTO: $faltas por arreglar" } else { "LISTO para el paso 8 de la guia" }
Write-Host "Resumen: $veredicto ($avisos aviso(s))" -ForegroundColor White

$destino = $Informe
if (-not $destino -and -not $Comprobar) { $destino = Join-Path $LanesDir ".state\informe-segundo-worker.txt" }
if ($destino) {
    $cab = @("Informe instalar_worker_remoto - $(Get-Date -Format 'yyyy-MM-dd HH:mm')", "Modo: $ModoTxt", "Equipo: $env:COMPUTERNAME", "Hub: $HubRoot", "Resumen: $veredicto", "")
    $cuerpo = $script:Filas | ForEach-Object { "[{0}] {1} - {2}" -f $_.Nivel, $_.Que, $_.Detalle }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destino) | Out-Null
    [System.IO.File]::WriteAllText($destino, ((@($cab) + @($cuerpo)) -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Informe: $destino"
}
if ($faltas -gt 0) { exit 1 } else { exit 0 }
