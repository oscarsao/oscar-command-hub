# 00d Servidor SSH solo para el hub (FASE 2: ejecutar SOLO cuando Oscar lo ordene en el chat)

Id: r-00d-ssh-solo-para-el-hub
Orden: 6
Riesgo: medio
Nota: el usuario de trabajo real de este equipo se llama agentplanes (con p), no agentlanes; comprueba con whoami dentro de su sesión y usa SIEMPRE el nombre real en las rutas y en authorized_keys.
Límites: Requiere administrador: Oscar acepta el UAC delante de este equipo. Nunca habilites acceso con contraseña ni desde cualquier IP. No abras nada en el router. No muestres contraseñas ni claves privadas. Si un comando falla, PARA, anota el error exacto y bloquea con tu recomendación.

Datos (verificados por Oscar el 21-11-2026):
- IP del hub (solo esta puede conectar): 192.168.1.181
- IP de este equipo: 192.168.1.205
- Clave PÚBLICA de hub-a-host (no es secreta): ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICndHmLEOHodOCihky68bHO0ovJA3j3uTEoSlvvzY32Y hub-a-host
- Usuario de trabajo sin administrador: agentplanes
- Requiere r-00e hecha (CLI de claude). Si el usuario agentplanes no existe, pregunta a Oscar cómo crearlo (contraseña la escribe él EN ESTE EQUIPO; tú nunca la pidas ni la escribas).

Pasos (PowerShell como administrador):
1. Instala el servidor: Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0. Si falla en Windows 10 Home, para y dilo.
2. Set-Service sshd -StartupType Automatic; Start-Service sshd.
3. Cortafuegos: Disable-NetFirewallRule -Name OpenSSH-Server-In-TCP (si existe) y crea New-NetFirewallRule -Name sshd-hub -DisplayName "SSH solo desde el hub" -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress 192.168.1.181 -Profile Private,Public,Domain.
4. Carpeta C:\Users\agentplanes\.ssh y fichero authorized_keys con UNA sola línea: restrict,from="192.168.1.181" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICndHmLEOHodOCihky68bHO0ovJA3j3uTEoSlvvzY32Y hub-a-host   (sin command=). Permisos solo para agentplanes y SYSTEM: icacls en la carpeta .ssh con /inheritance:r.
5. En C:\ProgramData\ssh\sshd_config: PasswordAuthentication no, KbdInteractiveAuthentication no, AllowTcpForwarding no, PermitTunnel no. Reinicia sshd.
6. Carpeta de trabajo: C:\agent-lanes con /inheritance:r y acceso solo a Administradores, SYSTEM y agentplanes.
7. NO pruebes la conexión desde aquí: la prueba la hace el hub (Oscar o la sesión principal).
8. Resultado en resultados/r-00d.md: qué se cambió y cómo revertirlo (Disable-NetFirewallRule sshd-hub; Stop-Service sshd; Set-Service sshd -StartupType Disabled; Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0).
