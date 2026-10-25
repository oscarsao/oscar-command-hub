# Hermes de equipo (t_f77fc36c) · Spec-Lite · pendiente del OK de Oscar

**Problema.** Hermes (@oscarsaoBot) tiene terminal y ficheros del PC de Oscar y no se puede limitar por persona en Telegram.
Ingerber y Andrea quedaron fuera (`people.yaml` `hermes: false`, hub #131).

**Objetivo.** Que Ingerber y Andrea hablen con un Hermes sin terminal, ficheros ni navegador, con kanban/todo/web/memoria
propia, y que cree tareas por la vía oficial. Hecho cuando no pueden ejecutar comandos ni leer ficheros y `people.yaml`
vuelve a `hermes: true` para el de equipo.

**Decisión que necesito de Oscar (la única que bloquea).**

| Opción | Cómo | Pros | Contras |
|---|---|---|---|
| A (recomendada) | Bot aparte con su propio token y **perfil de Hermes aparte** (toolsets: kanban, todo, web, memoria; sin terminal/file/browser), SOUL sin contexto privado | Aislamiento real: lo que no existe en el perfil no se puede pedir; sin depender de que la versión soporte enrutado | Un bot más en BotFather; hay que dar de alta el token |
| B | Mismo bot con enrutado por usuario | Un solo bot | Hermes no limita herramientas por usuario hoy; un fallo de enrutado da terminal a un no operador |

**Pasos tras el OK (A).** 1) Oscar crea el bot en BotFather y guarda el token en el `.env` del perfil (no en el repo).
2) Perfil `equipo` de Hermes con toolsets acotados y SOUL propio. 3) Allowlist = solo miembros no operadores, respondiendo
en el tema 🤖 Asistente. 4) Verificar con un usuario de prueba que `terminal`/`read_file` fallan. 5) `people.yaml`:
`hermes: true` para Ingerber y Andrea apuntando al bot de equipo. 6) Tests en `tools/agent-lanes/tests` del filtrado por
`hermes:` en `sincronizar_permisos`.

**Fuera de alcance.** Cambiar el Hermes de Oscar; dar herramientas nuevas al de equipo.
