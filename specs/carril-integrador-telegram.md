# Spec-Lite · Carril integrador desde Telegram

**Estado: APROBADA por Oscar (22-10): fases 1-2 ya hechas; variables: el valor lo introduce el aprobador en formulario local.** Decisión de Oscar (22-10): lo que hoy hace el integrador desde la
consola de Claude Code debe poder hacerse desde Telegram con un carril integrador. Esta spec fija el diseño; el código
de las fases 3-4 NO se ha escrito. Hecho ya: fase 0 (`people.yaml`) y fases 1-2 (`agent_lanes/acciones_prod.py`,
`integrador_telegram.yaml`: registro, validadores, redacción, auditoría y nivel A con dobles; nivel B rechazado).

## Qué puede hacer (decisión de Oscar)
| Nivel | Acciones | Cómo |
|---|---|---|
| **A · sin preguntar** | leer logs de contenedores en pildora-01 (SSH), `SELECT` en Supabase (Management API), health, `getWebhookInfo`; abrir PR en repos de la lista blanca (pildora-mcp, handbook, Scraper, nextjobs, webs); back-merge a develop | el carril ejecuta directamente |
| **B · con botón** | fusionar a main/master, desplegar (Dokploy `application.deploy`, Vercel), aplicar migración SQL (tras revisión), cambiar variables (Dokploy y Vercel) | ficha del integrador + doble toque |
| **C · nunca** | DROP, TRUNCATE, DELETE masivo, borrar recursos/proyectos/volúmenes/ramas ajenas, `--force`, tocar `.env` | solo Oscar por consola |

Aprueban (nivel B) quienes tengan `permisos: [aprobar_produccion]` en people.yaml: Oscar (owner, implícito), María y
Angel, en TODAS las marcas para este permiso (decisión explícita de Oscar; el veto por marca de `brands` sigue
rigiendo el resto de comandos de Angel).

## Modelo de amenazas
1. **Prompt injection** desde tarjetas, comentarios, texto de PR, logs y Telegram: todo eso es DATO, nunca instrucción.
   El worker no decide acciones B por texto libre: propone una **acción tipada** (`{tipo, objetivo, parámetros}`) y el
   código la valida contra la lista blanca; lo que no encaje se rechaza.
2. **Escalada**: un carril no se auto-aprueba. La aprobación solo la da un `callback_query` de Telegram de un user_id
   con `aprobar_produccion`; el id lo pone Telegram, no el texto. Una acción B queda ligada a (tarjeta, SHA, hash de
   los parámetros): si cambia cualquiera, el botón caduca. Caducidad del botón: 30 min.
3. **Secretos**: el worker NUNCA ve valores. Los tokens (Dokploy, Supabase, Vercel, SSH) viven en los ficheros que ya
   usa cada herramienta; los lee un **comando envuelto** de agent_lanes (no el modelo). Las salidas se pasan por
   redacción (patrones de token/JWT/`KEY=`) antes de volver al worker o a Telegram. Variables de entorno: el worker
   propone solo el NOMBRE; el valor lo da Oscar/María/Angel fuera del chat del worker (fichero local o
   formulario del panel), y el botón enseña el nombre y un hash, nunca el valor.
4. **Cadena de suministro**: un carril solo ve la lista blanca de hosts/repos de `integrador_telegram.yaml`.

## Lista blanca (sin shell libre en producción)
Acciones registradas en un módulo `acciones_prod.py`, cada una con: argumentos validados por regex, plantilla de
comando FIJA (lista argv, sin `shell=True`), nivel A/B y marca. Ejemplos nivel A: `logs(servicio, líneas≤500)`,
`sql_select(proyecto, consulta)` (solo un `SELECT`/`WITH`, una sentencia, sin `;`, sin funciones de escritura, y
siempre con rol de solo lectura), `health(url)`, `webhook_info(bot)`, `abrir_pr(repo, rama)`. Nivel B:
`fusionar(pr)`, `desplegar(app)`, `migracion(archivo)`, `variable(app, nombre)`. Cualquier otro comando, `ssh`
libre, `docker exec`, `rm`, o un SQL con `DROP/TRUNCATE/DELETE/UPDATE sin WHERE/ALTER ... DROP` → rechazado y avisado.
Los hooks de guard del carril integrador siguen denegando todo lo demás.

## Aprobación (reutiliza los botones del integrador)
Reutiliza `int_merge`, `int_deploy`, migración y variables de `/falta` y `/desplegar`: ficha → [🚀/✅ acción] →
"¿Seguro? Esto actúa en PRODUCCIÓN de <marca>" → [✅ Sí] [✖ No]. Un solo toque nunca ejecuta producción. Staging
puede ir con un toque. Migraciones: la ficha muestra el SQL y el veredicto de revisión previa (skill
`migracion-supabase`) y se aplica con rol propio, nunca con `service_role` desde el worker.
Cada decisión se firma: «aprobado por <nombre> · <fecha/hora UTC>» en la tarjeta de Hermes y en el registro.

## Registro de auditoría
`.state/integrador_telegram/auditoria.jsonl` (append-only, 0600): `ts, tarjeta, acción, objetivo, hash_params, nivel,
quién_pidió (carril), quién_aprobó (user_id+nombre), toque1_ts, toque2_ts, resultado, SHA`. Sin valores de variables ni
SQL con datos: solo hash y longitud. Las acciones A también se registran. El aviso a Oscar resume cada acción B.

## Fases (un PR por fase)
0. **Hecho aquí**: `people.yaml` + `People.puede_aprobar_produccion(user_id)` + tests.
1. Registro de acciones, validadores, redacción de salida y auditoría (sin ejecutar nada real) + tests con dobles.
2. Nivel A: logs/SELECT/health/webhook/PR/back-merge, con lista blanca y límites de tamaño y tiempo.
3. Nivel B: botones con doble toque, firma, caducidad y ligadura a SHA/hash. **Requiere `security-auditor` antes de
   fusionar**, igual que cualquier cambio de guard.
4. Carril `claude-integrador` en `lanes.yaml` + settings del worker (cambio de contrato: se avisa a Oscar con frase llana).

## Límites y riesgos
- Sin borrar datos ni recursos, ni acceso a Hermes/producción en las pruebas: todo con dobles.
- Lo de otro repo (pildora-mcp: p. ej. exponer estas acciones al MCP) va en tarjeta de seguimiento, no aquí.
- Riesgo: un aprobador (María/Angel) con "todas las marcas" para producción es más amplio que su `brands` de operador.
- Tamaño: Pitch (~1-2 semanas por la fase 3 y la revisión de seguridad).

## Decisiones de Oscar (22-10)
1. OK a la spec; empezar por las fases 1-2.
2. Variables de entorno: el valor lo introduce el aprobador en un formulario local, nunca el worker.
