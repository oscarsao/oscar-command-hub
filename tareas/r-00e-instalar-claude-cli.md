# 00e Instalar el CLI de Claude Code (necesario para el worker remoto)

Id: r-00e-instalar-claude-cli
Orden: 5
Riesgo: medio
Límites: Solo el CLI de Claude Code. No toques el PATH a mano ni instales otros programas. No ejecutes scripts descargados sin permiso expreso de Oscar. No inicies sesión ni muestres tokens.

Contexto: r-00 encontró solo la app de escritorio; el worker remoto necesita el comando `claude` (modo no interactivo).
1. Comprueba si existe el paquete con winget: `winget search --id Anthropic.ClaudeCode -e --source winget`.
2. Si existe: `winget install --id Anthropic.ClaudeCode -e --source winget --accept-package-agreements --accept-source-agreements --silent`. Abre una terminal nueva y comprueba `claude --version`.
3. Si NO existe, PARA y dile a Oscar: el instalador oficial es un script de claude.ai y necesita su permiso expreso antes de ejecutarlo. No lo ejecutes sin él.
4. Resultado en resultados/r-00e.md: ruta de `claude`, versión y estado. NO inicies sesión: la cuenta la configura Oscar (fase SSH).
