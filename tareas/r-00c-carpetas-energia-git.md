# 00c Carpetas de trabajo, energía y ajustes seguros de git

Id: r-00c-carpetas-energia-git
Orden: 3
Riesgo: bajo
Límites: No cambies nada fuera de lo indicado. No toques la identidad de git (la pone Oscar en r-01). No desactives antivirus, cortafuegos ni actualizaciones.

Requiere r-00a hecha.
1. Crea las carpetas C:\agent, C:\agent\repos, C:\agent\_lanes y C:\agent\logs.
2. Git (ajustes globales, no personales): git config --global init.defaultBranch main; git config --global core.longpaths true; git config --global core.autocrlf false; git config --global pull.ff only.
3. Energía con el equipo enchufado, solo en la configuración de usuario: powercfg /change standby-timeout-ac 0 y powercfg /change hibernate-timeout-ac 0 (el equipo no se suspende cuando trabaja). Deja la pantalla como está. Si powercfg pide administrador, pide a Oscar que lo ejecute él.
4. Anota en notas qué se cambió y cómo deshacerlo (los comandos inversos). Estado hecha.
