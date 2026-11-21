# 00a Instalar las herramientas con winget

Id: r-00a-instalar-herramientas
Orden: 1
Riesgo: medio
Límites: Solo estos cuatro paquetes y solo con winget. Ningún otro programa. Sin scripts descargados de internet. Si winget no existe o pide una contraseña, PARA y avisa a Oscar.

Ejecútala solo cuando Oscar lo ordene. Cada instalación puede abrir un aviso de Windows (UAC): Oscar lo acepta en el equipo.
1. Comprueba winget --version. Si no existe, para.
2. Para cada paquete que NO esté ya instalado (comprueba antes con la herramienta o con winget list --id <id>), ejecuta con winget install --id <id> -e --source winget --accept-package-agreements --accept-source-agreements --silent:
   - Git.Git
   - OpenJS.NodeJS.LTS
   - Python.Python.3.12
   - GitHub.cli
3. Claude Code ya está instalado (si no, no estarías leyendo esto); no lo toques.
4. Cierra y abre una terminal nueva para refrescar el PATH y comprueba: git --version, node --version, py -3.12 --version, gh --version. Si alguna falla tras reabrir la terminal, anótalo y bloquea.
5. Notas: versiones instaladas y cuáles ya estaban. Estado hecha.
