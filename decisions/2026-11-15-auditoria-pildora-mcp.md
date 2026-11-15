# Auditoría de pildora-mcp (15-11-2026)

Alcance: lectura de `dev\pildora-mcp` (HEAD `5d1e287`, 8 commits por detrás de `origin/main`), sin abrir ningún `.env`
(solo los nombres de variables) y sin ejecutar herramientas. Repo remoto: `oscarsao/pildora-mcp`, **privado**.
Verificado en esta sesión: lo que consta en código/ficheros. **No verificado:** que las herramientas funcionen contra el
servidor real (no se llamó a ninguna) ni el estado de `OD_DAEMON_URL` ni del túnel.

## 1. Qué ofrece hoy (14 herramientas)

| Grupo | Herramientas |
|---|---|
| Open Design (9) | `list_design_systems`, `list_skills`, `list_projects`, `generate_carousel`, `get_run_status`, `list_project_files`, `get_project_file`, `export_carousel` (ZIP/PDF), `export_carousel_png` |
| Oscar HQ crews (5) | `ohq_list_crews`, `ohq_run_crew` (cuesta ~0,5-5 $ por ejecución; límite 5 / 5 min), `ohq_get_execution`, `ohq_list_executions`, `ohq_cancel_execution` |

Transportes: stdio (local) y HTTP (`--http N`, remoto vía `design.pildoradigital.com/mcp` con Basic auth). Para usarlo desde
fuera, el portátil debe estar encendido (daemon OD + túnel).

## 2. Hallazgos

**Seguridad (prioridad alta)**
- **S1.** `HANDOFF.md:23` contiene en claro la contraseña de OpenDesign (la misma de 9 caracteres ya retirada de oscar-hq) y
  sigue en el historial de 4 commits. Repo privado, pero rotar la credencial lo cierra del todo (decisión y acción de Oscar).
  Falta una tarjeta de redacción para este repo.
- **S2.** `.gitignore` solo ignora `.env`. Hay 4 copias sin versionar (`.env.bak-*`) con credenciales que **no están ignoradas**:
  un `git add .` las subiría. Añadir `.env*` con excepción de `.env.example`.
- **S3.** Identidad única: el cliente de Oscar HQ firma siempre un token como el usuario `oscar` (admin) con el secreto
  compartido `PD_SSO_SECRET`. Cualquiera que llegue al MCP actúa como administrador. El acceso remoto usa **una contraseña
  compartida** (Basic), no cuentas por persona. Esto impide hoy «usarlo desde otros perfiles» con permisos distintos.
- **S4.** `ohq_run_crew` gasta dinero sin confirmación propia y no está bajo el hook de permisos (la política solo cubre
  MCP de terceros listados). Sugerencia: parámetro `confirmar_gasto` y registro de coste por usuario.

**Fiabilidad**
- **F1.** `OHQ_BASE_URL` por defecto sigue apuntando a Railway (`oscar-hq-production.up.railway.app`), que ya no existe. Hoy
  funciona porque el `.env` local lo sobrescribe con `api.pildoradigital.com`; en cualquier otra máquina o perfil fallaría.
  Los comentarios del cliente también citan variables de Railway.
- **F2.** **El MCP no aparece registrado en esta máquina:** `claude mcp list` no muestra `pildora-design` (el README dice
  «ya añadido»). Hoy Claude Code no lo tiene disponible aquí.
- **F3.** Sin pruebas para las 5 herramientas de Oscar HQ ni para `export_carousel_png` (lo dice el propio HANDOFF); un script
  de prueba sin versionar importa un módulo que no existe.
- **F4.** README y cabecera de `index.mjs` desactualizados (faltan las 6 herramientas nuevas, cita `get_artifact_html` que no existe).
- **F5.** Dependencias de infraestructura personal: portátil encendido + daemon + túnel; sin salud/diagnóstico en el propio MCP.
- **F6.** Repo 8 commits por detrás de `origin/main`; no se ha revisado qué traen.

**Capacidades que faltan para el programa de bandeja única**
- Herramientas de **lectura** del kanban/bandeja (decisiones pendientes, estado de carriles), de `ohq_ui_feedback`
  (comentarios nuevos) y de «publicado sin revisar». Sin ellas, Claude chat y Cowork no ven lo que Telegram sí.
- Resolver una decisión desde el MCP **no** debería ser posible en la primera versión (solo lectura), para no sortear los
  botones de Oscar.

## 3. Cómo mejorarlo (orden propuesto)

1. **Higiene (ya, nivel 1):** redactar `HANDOFF.md`, `.gitignore` con `.env*`, `OHQ_BASE_URL` por defecto = `api.pildoradigital.com`,
   actualizar README. Reversible, solo documentación y config.
2. **Registrar y probar:** volver a registrar `pildora-design` (stdio) y ejecutar los 4 tests existentes + tests nuevos para `ohq_*`.
3. **Identidad por persona:** token por usuario (no contraseña compartida), mapeado a usuario y marcas de Oscar HQ; el MCP usa
   el JWT de ese usuario en vez de firmar como `oscar`. Prerrequisito para otros perfiles (Mary, Linda, Mauredy, Andrea).
4. **Herramientas de lectura** de bandeja, carriles y comentarios (entrega D del pitch).
5. **Para Claude chat/Cowork:** comprobar qué métodos de autenticación admiten los conectores remotos (probablemente OAuth, no
   Basic) antes de diseñar el acceso; punto abierto, no verificado.

## 4. Respuesta corta a «¿puedo usarlo de forma fluida ya?»

Desde esta máquina, **no se puede afirmar**: el MCP no está registrado en Claude Code aquí, no se ha probado ninguna herramienta
en esta auditoría y depende de que el portátil, el daemon y el túnel estén encendidos. Desde otros perfiles, hoy no: solo hay
una contraseña compartida y todas las acciones se ejecutan como administrador.
