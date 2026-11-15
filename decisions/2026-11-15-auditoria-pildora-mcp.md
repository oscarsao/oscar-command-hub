# Auditoría de pildora-mcp (15-11-2026, corregida el mismo día)

Alcance: lectura de `oscarsao/pildora-mcp` en `origin/main` (`6f7fcd2`, PR #3). Repo **privado**. No se abrió ningún `.env` (solo
nombres de variables) ni se ejecutó ninguna herramienta.

**Corrección.** La primera versión de este documento se hizo sobre un checkout local 8 commits por detrás de `origin/main` y
daba como pendientes cosas que ya estaban arregladas (identidad única como admin y URL de Railway por defecto). Esta versión
está contrastada con lo publicado. Lo que **no** se ha verificado: que las herramientas funcionen contra el servidor real, el
estado del daemon de OpenDesign y del túnel, y la autenticación que admiten Claude chat y Cowork para conectores remotos.

## 1. Qué ofrece hoy (14 herramientas)

| Grupo | Herramientas |
|---|---|
| Open Design (9) | `list_design_systems`, `list_skills`, `list_projects`, `generate_carousel`, `get_run_status`, `list_project_files`, `get_project_file`, `export_carousel` (ZIP/PDF), `export_carousel_png` |
| Oscar HQ crews (5) | `ohq_list_crews`, `ohq_run_crew` (cuesta ~0,5-5 $ por ejecución; límite 5 / 5 min), `ohq_get_execution`, `ohq_list_executions`, `ohq_cancel_execution` |

Dos transportes: **stdio** (local, cada persona con su `.env`) y **HTTP** (`design.pildoradigital.com/mcp`, Basic auth con la
credencial de OpenDesign). Necesita el portátil encendido (daemon OD + túnel).

## 2. Cómo se identifica quien lo usa (ya resuelto en parte)

- Las herramientas `ohq_*` usan una **clave personal `ohq_pt_`** (`OHQ_PERSONAL_KEY`) que el MCP canjea por un JWT de 5 minutos de
  esa persona (`POST /api/auth/mcp-token`). El servidor de Oscar HQ aplica **exactamente los permisos de ese usuario** (marcas,
  roles; hay pruebas, tarjeta `t_bbb824ea`) y la clave se revoca desde Oscar HQ.
- Por HTTP las `ohq_*` van **apagadas** salvo `OHQ_TOOLS_OVER_HTTP=1`, porque quien entra por el túnel usa una sola credencial
  compartida de OpenDesign y actuaría con la clave de quien arranca el proceso.
- Consecuencia para «otros perfiles»: **hoy funciona si cada persona lo registra en su propio equipo** (stdio, Node, el repo y su
  clave personal). Por el túnel remoto solo llegan las herramientas de diseño, con una contraseña compartida.

## 3. Hallazgos que siguen abiertos

**Seguridad**
- **S1.** `HANDOFF.md` tenía en claro la contraseña de OpenDesign (la de 9 caracteres) y sigue en el historial. PR #4 de
  `pildora-mcp` la redacta; **rotarla es decisión y acción de Oscar** y es lo que lo cierra de verdad.
- **S2.** El `.gitignore` solo ignoraba `.env`; hay 4 copias `.env.bak-*` sin versionar con credenciales y un `git add .` las
  habría subido. PR #4 añade `.env.*` (salvo `.env.example`).
- **S3.** `ohq_run_crew` gasta dinero sin confirmación propia y no está bajo el hook de permisos (la política solo cubre MCP de
  terceros listados). Sugerencia: parámetro `confirmar_gasto` y coste por usuario.

**Fiabilidad**
- **F1.** `pildora-design` **no aparece en `claude mcp list` en esta máquina**, aunque el README dice que se añade con
  `claude mcp add`. Hoy Claude Code aquí no lo tiene registrado (verificado 15-11).
- **F2.** Sin comprobar que las 5 herramientas `ohq_*` y `export_carousel_png` funcionen de punta a punta (hay `test-ohq-auth.mjs`
  para la autenticación; no pruebas de las herramientas).
- **F3.** Dependencia de infraestructura personal: portátil encendido + daemon + túnel; el MCP no ofrece un diagnóstico de salud.

**Capacidades que faltan para el programa de bandeja única** (`pitches/bandeja-unica-y-revision-de-pantallas.md`)
- Herramientas de **solo lectura** de la bandeja de decisiones, el estado de carriles y los comentarios de pantalla
  (`ohq_ui_feedback`). Resolver decisiones desde el MCP **no** entra en la primera versión.
- Para Claude chat y Cowork hace falta acceso remoto con identidad por persona (probablemente OAuth); es un punto abierto, no
  verificado, y debe decidirse antes de diseñar la entrega D del pitch.

## 4. Orden de mejora propuesto

1. Rotar la credencial de OpenDesign (Oscar) y fusionar PR #4 de `pildora-mcp`.
2. Registrar `pildora-design` en Claude Code (stdio, con `--env-file`) y probar las herramientas con 1 llamada de cada una antes de
   usarlas en serio (`ohq_run_crew` cuesta dinero: medir con una).
3. Añadir pruebas para `ohq_*` y `export_carousel_png`.
4. Herramientas de lectura de bandeja, carriles y comentarios (entrega D del pitch).
5. Decidir cómo se autentica Claude chat/Cowork antes de abrir nada remoto.

## 5. Respuesta corta a «¿puedo usarlo de forma fluida ya?»

Probablemente casi: la identidad por persona y la URL ya están bien. Falta **registrarlo aquí** (no lo está) y **probarlo** (no se
ha probado ninguna herramienta). Para otras personas: cada una en su equipo con su clave personal; por el túnel solo diseño.
