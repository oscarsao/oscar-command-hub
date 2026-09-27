# Recupera el túnel pildora-design como servicio de Windows y publica el panel del kanban (27-09).
# Ejecutar en PowerShell COMO ADMINISTRADOR:
#   powershell -ExecutionPolicy Bypass -File C:\Users\oscar\oscar-command-hub\tools\infra\instalar-panel-y-tunel.ps1
# Requisito previo: apply_dashboard_config.py ya aplicado (public_url + password_hash).
$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "FAIL: abre PowerShell como administrador." -ForegroundColor Red; exit 1 }

$cf      = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$cfgDir  = 'C:\Users\oscar\.cloudflared'
$cfg     = Join-Path $cfgDir 'config-design.yml'
$tunnel  = '555b2880-0d3e-448b-9045-1a577a16fb98'
$hermes  = 'C:\Users\oscar\AppData\Local\hermes'
$pyw     = Join-Path $hermes 'hermes-agent\venv\Scripts\pythonw.exe'

Write-Host '== 1/5 Validar configuración del túnel =='
& $cf --config $cfg tunnel ingress validate
if ($LASTEXITCODE -ne 0) { throw 'config-design.yml no valida' }

Write-Host '== 2/5 DNS kanban.pildoradigital.com -> túnel pildora-design =='
& $cf --origincert (Join-Path $cfgDir 'cert.pem') tunnel route dns $tunnel kanban.pildoradigital.com
if ($LASTEXITCODE -ne 0) { Write-Host 'AVISO: route dns falló (¿ya existía el registro?). Sigo.' -ForegroundColor Yellow }

Write-Host '== 3/5 Servicio cloudflared (auto-arranque, con --config explícito) =='
if (Get-Service cloudflared -ErrorAction SilentlyContinue) {
    Stop-Service cloudflared -ErrorAction SilentlyContinue
    sc.exe delete cloudflared | Out-Null
    Start-Sleep -Seconds 2
}
$bin = "`"$cf`" tunnel --config `"$cfg`" run $tunnel"
New-Service -Name cloudflared -BinaryPathName $bin -DisplayName 'Cloudflare Tunnel (pildora-design)' -StartupType Automatic | Out-Null
sc.exe failure cloudflared reset= 86400 actions= restart/5000/restart/5000/restart/30000 | Out-Null
Start-Service cloudflared

Write-Host '== 4/5 Tarea programada del panel (al iniciar sesión, sin ventana) =='
$action  = New-ScheduledTaskAction -Execute $pyw -Argument '-m hermes_cli.main dashboard --no-open --skip-build' -WorkingDirectory (Join-Path $hermes 'hermes-agent')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'Hermes Dashboard' -Action $action -Trigger $trigger -Settings $set -User $env:USERNAME -Force | Out-Null
[Environment]::SetEnvironmentVariable('HERMES_HOME', $hermes, 'User')
Start-ScheduledTask -TaskName 'Hermes Dashboard'

Write-Host '== 5/5 Comprobación =='
Start-Sleep -Seconds 12
(Get-Service cloudflared).Status
try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9119/' -TimeoutSec 10).StatusCode } catch { "panel local: $($_.Exception.Message)" }
foreach ($u in 'https://design.pildoradigital.com', 'https://kanban.pildoradigital.com/kanban') {
    try { "$u -> $((Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 15 -MaximumRedirection 0 -ErrorAction Stop).StatusCode)" }
    catch { "$u -> $($_.Exception.Response.StatusCode.value__) (302/401/403 = protegido OK; 530 = túnel caído; 502 = app local caída)" }
}
Write-Host 'LISTO.' -ForegroundColor Green
