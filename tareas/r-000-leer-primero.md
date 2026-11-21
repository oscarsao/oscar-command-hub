# 000 LEER PRIMERO: orden de la preparación y qué hace cada persona

Id: r-000-leer-primero
Orden: 0
Riesgo: bajo
Límites: Esta tarea solo se lee. Las tareas de preparación las ejecutas SOLO cuando Oscar te lo ordena en el chat.

ORDEN DE LA PREPARACIÓN (cada una requiere la anterior):
r-00a instalar herramientas → r-00b herramientas de Python → r-00c carpetas, energía y git → r-00 verificar y registrar el equipo → r-01 clonar el hub → r-02 reglas locales → r-03 prueba de extremo a extremo → r-10 suite de oscar-hq.
r-00d (SSH, fase 2) y r-99 (pasos de Oscar) NO se ejecutan solas.

REGLAS DE LA PREPARACIÓN
1. Si Windows pide permisos de administrador (UAC) o una contraseña, PARA y díselo a Oscar: él lo acepta o la escribe EN ESTE EQUIPO. Tú nunca pidas, leas ni escribas una contraseña.
2. Nunca imprimas el valor de una variable de entorno, un token ni una clave. Nunca escribas secretos en ficheros, commits ni en esta cola.
3. Si un comando falla, no improvises otro método de instalación: escribe el error exacto en notas de la tarea, pon la tarea en bloqueada y pregunta a Oscar con tu recomendación.
4. Tras cada tarea: notas (máx. 10 líneas), un evento en la colección eventos, estado hecha o bloqueada, y actualiza ultimaSenal de tu equipo en hosts.
5. Lo que solo puede hacer Oscar (iniciar sesión en Claude, crear el token de GitHub y ejecutar gh auth login con él, compartir este artefacto, cuenta de Windows, OpenSSH y su clave) está en la tarea r-99. Si te falta algo de eso, dilo y espera.
