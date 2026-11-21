# 03 Prueba de extremo a extremo: rama, push y PR en borrador (no fusionar)

Id: r-03-prueba-de-extremo-a-extremo
Orden: 7
Riesgo: bajo
Límites: Rama propia lane/r-03-prueba, PR en borrador, SIN fusionar. Un solo fichero nuevo. Sin secretos.

Requiere las tareas 00, 01 y 02 hechas. Objetivo: comprobar de verdad el ciclo completo antes de dar trabajo real.
1. Worktree propio: git -C C:\agent\repos\oscar-command-hub fetch origin y git -C C:\agent\repos\oscar-command-hub worktree add C:\agent\_lanes\r-03 -b lane/r-03-prueba origin/main.
2. En ese worktree crea tools/agent-lanes/docs/prueba-segundo-equipo.md con 5 líneas: nombre del equipo, fecha y hora, versión de git, node, python y claude, y la frase «prueba del ciclo; no fusionar». Sin ningún dato personal ni secreto.
3. git diff --check limpio; commit con pathspec; push de la rama; abre un PR EN BORRADOR con gh titulado «[Prueba] Segundo equipo: ciclo completo (no fusionar)».
4. Comprueba que el push usa la identidad de Oscar y no otra (git log -1 --format=%an).
5. Pon en notas la URL del PR y un resumen, añade un evento y pasa la tarea a revision. Oscar o la sesión principal cierran el PR.
