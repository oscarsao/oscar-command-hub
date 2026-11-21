# 04 Clonar el hub para el lanzador remoto (solo lectura)

Id: r-04-clonar-hub-lanzador
Orden: 7
Riesgo: bajo
Límites: Solo clonar y comprobar. No ejecutes el runner ni ningún script del hub. No crees ramas ni empujes nada. Sin secretos. Esta tarea la ejecutas como el usuario agentlanes (sesión del propio usuario o por SSH desde el hub).

Contexto: el lanzador remoto (agent_lanes/remote_exec.py) vive en un clon del hub en este equipo (config host.hub_dir). Debe estar en main y limpio.
1. Crea C:\agent-lanes\hub si no existe.
2. Clona: gh repo clone oscarsao/oscar-command-hub C:/agent-lanes/hub (si la carpeta ya existe, haz git -C C:/agent-lanes/hub fetch origin y git -C C:/agent-lanes/hub merge --ff-only origin/main).
3. Comprueba con git -C C:/agent-lanes/hub status y log -1: en main, limpio.
4. Comprueba que existen tools/agent-lanes/agent_lanes/remote_exec.py y tools/agent-lanes/contract/.
5. Resultado en resultados/r-04.md: SHA del clon (git rev-parse HEAD) y estado. No ejecutes nada del clon.
