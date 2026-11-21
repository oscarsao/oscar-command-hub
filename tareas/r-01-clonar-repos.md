# 01 Clonar el hub y comprobar que las pruebas corren

Id: r-01-clonar-repos
Orden: 5
Riesgo: bajo
Límites: Sin secretos. Sin tocar ningún repo salvo clonarlo. No ejecutes nada de despliegue. Si pide credenciales, PARA.

Requiere que la tarea 00 esté hecha.
1. Crea C:\agent\repos. Clona oscarsao/oscar-command-hub allí con gh repo clone (si no tienes acceso, para y avisa).
2. git -C C:\agent\repos\oscar-command-hub log -1 y git status: debe estar limpio y en main.
3. Instala solo las dependencias de pruebas del hub con py -3.12 -m pip install pytest ruff pyyaml (si el repo declara otras, usa su fichero de requisitos, sin ejecutarlo si pide algo raro).
4. Desde tools/agent-lanes ejecuta una sola vez: py -3.12 -m pytest tests/test_tests_delta.py tests/test_relanzar.py -q -p no:cacheprovider. Debe pasar entero.
5. Configura la identidad de git de este clon con la misma que usa Oscar en su equipo principal: pregunta a Oscar el nombre y el correo (no los guardes en esta cola ni en ningún fichero versionado) y ejecútalo con git config SIN --global.
6. Informe en notas (qué funciona y qué no) y evento. Estado hecha o bloqueada con la pregunta.
