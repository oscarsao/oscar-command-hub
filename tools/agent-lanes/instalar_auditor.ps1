# Registra la tarea programada "Auditor diario carriles": lunes a viernes a las 07:40, usuario actual, sin ventana.
# Lanza tools\agent-lanes\auditor.py con pyw (sin consola). Con hallazgos crea UNA tarjeta [DECISION] en el board
# default para oscar (idempotente por fecha); sin hallazgos no hace nada. Log: tools\agent-lanes\.state\auditor.log
# No requiere administrador (tarea del propio usuario, LogonType Interactive, RunLevel Limited).
#
# Ejecutar DESDE EL CHECKOUT VIVO (C:\Users\oscar\oscar-command-hub), tras fusionar: las rutas salen de $PSScriptRoot.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\instalar_auditor.ps1
# Probar antes a mano:  py -3.12 tools\agent-lanes\auditor.py --dry-run
$ErrorActionPreference = "Stop"

$TaskName = "Auditor diario carriles"
$LanesDir = $PSScriptRoot
$Script = Join-Path $LanesDir "auditor.py"
if (-not (Test-Path $Script)) { throw "No encuentro $Script" }

# pyw.exe (lanzador de Python sin consola); ruta absoluta para no depender del PATH de la sesion programada.
$Pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $Pyw) { $Pyw = Join-Path $env:WINDIR "pyw.exe" }
if (-not (Test-Path $Pyw)) { throw "No encuentro pyw.exe (lanzador de Python)" }
& py -3.12 -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) { throw "py -3.12 no tiene PyYAML" }

$action = New-ScheduledTaskAction -Execute $Pyw -Argument "-3.12 `"$Script`"" -WorkingDirectory $LanesDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 07:40
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Auditor diario del sistema de carriles (tools/agent-lanes/auditor.py): tarjeta [DECISION] con hallazgos" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
$info = $t | Get-ScheduledTaskInfo
Write-Output "Tarea '$TaskName' registrada: estado=$($t.State) usuario=$user proxima=$($info.NextRunTime)"
