# Registra la tarea programada "Resumen diario Hermes": lunes a viernes a las 08:00, usuario actual, sin ventana.
# No requiere administrador (tarea del propio usuario, LogonType Interactive, RunLevel Limited).
# Patrón de Hermes_Dashboard.vbs: wscript + .vbs que lanza python.exe con la ventana oculta (pythonw no sirve).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-lanes\brief\instalar-brief.ps1
$ErrorActionPreference = "Stop"

$TaskName = "Resumen diario Hermes"
$BriefDir = $PSScriptRoot
$LanesDir = Split-Path -Parent $BriefDir
$Vbs = Join-Path $BriefDir "resumen_diario.vbs"

# python.exe de py -3.12 (tiene PyYAML); ruta absoluta para no depender del PATH de la sesión programada.
$Python = (& py -3.12 -c "import sys; print(sys.executable)").Trim()
if (-not (Test-Path $Python)) { throw "No encuentro python 3.12 (py -3.12): '$Python'" }

$vbsText = @"
' Resumen diario de Hermes (t_631cf0bc). Generado por instalar-brief.ps1: no editar a mano.
' python.exe con la ventana oculta (0) y esperando a que termine (True). Errores en tools\agent-lanes\.state\brief.log
Option Explicit
Dim sh, env
Set sh = CreateObject("WScript.Shell")
Set env = sh.Environment("PROCESS")
env.Item("PYTHONIOENCODING") = "utf-8"
sh.CurrentDirectory = "$LanesDir"
sh.Run """$Python"" ""$BriefDir\brief_diario.py""", 0, True
"@
Set-Content -Path $Vbs -Value $vbsText -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\wscript.exe" -Argument "`"$Vbs`"" -WorkingDirectory $LanesDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 08:00
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Resumen diario del kanban de Hermes a Gestión · General (tools/agent-lanes/brief)" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
$info = $t | Get-ScheduledTaskInfo
Write-Output "Tarea '$TaskName' registrada: estado=$($t.State) usuario=$user próxima=$($info.NextRunTime)"
