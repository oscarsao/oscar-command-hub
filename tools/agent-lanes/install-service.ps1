<#
.SYNOPSIS
  Registra (o quita) la tarea programada "agent-lanes runner": un único runner para todos los carriles,
  que arranca al iniciar sesión de Windows. Lo ejecuta Oscar (registrar una tarea programada requiere su OK).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-service.ps1              # instala y arranca
  powershell -ExecutionPolicy Bypass -File install-service.ps1 -MaxWorkers 2
  powershell -ExecutionPolicy Bypass -File install-service.ps1 -Uninstall

.NOTES
  - Sin ventana: pyw.exe -3.12. Log rotativo en .state\runner.log. Candado .state\runner.lock:
    si ya hay un runner vivo, el segundo sale solo.
  - MaxWorkers = tope global de procesos claude simultáneos (todos los carriles + review). 1 por defecto (OOM 27-09).
  - Con el PC apagado, las tareas esperan en 'ready' en el kanban.
#>
param(
    [int]$MaxWorkers = 0,          # 0 = lo que diga lanes.yaml (runner.max_workers)
    [switch]$Uninstall,
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$TaskName = "agent-lanes runner"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Quitada la tarea '$TaskName'."
    } else {
        Write-Host "No existe la tarea '$TaskName'."
    }
    return
}

$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { throw "No encuentro pyw.exe (Python launcher). Instala Python 3.12 con el launcher." }
& py -3.12 -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 sin PyYAML: py -3.12 -m pip install pyyaml" }

New-Item -ItemType Directory -Force -Path (Join-Path $Root ".state") | Out-Null
$runnerArgs = "-3.12 `"$Root\runner.py`" --all --log-file `"$Root\.state\runner.log`""
if ($MaxWorkers -gt 0) { $runnerArgs += " --max-workers $MaxWorkers" }

$action = New-ScheduledTaskAction -Execute $pyw -Argument $runnerArgs -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "agent-lanes: runner de carriles del kanban de Hermes (oscar-command-hub/tools/agent-lanes)" `
    -Force | Out-Null
Write-Host "Registrada la tarea '$TaskName' (al iniciar sesión, sin privilegios elevados)."
Write-Host "  $pyw $runnerArgs"

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 8
    & py -3.12 (Join-Path $Root "lanes.py") status
    Write-Host "Log: $Root\.state\runner.log"
}
