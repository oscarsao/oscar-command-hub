# Handoff de la sesión del 30-10-2026 (Ola 1 del hub, reactivación de carriles y apertura de proyectos)

Para quien retome (Oscar o una sesión nueva). Todo lo que sigue está verificado salvo lo marcado «sin verificar».
Sin secretos. Contexto estratégico: `decisions/2026-10-30-INDICE-retomar.md` y `2026-10-30-fabrica-v3-borrador.md`.

## 1. Estado del hub (producción del sistema de carriles)
- Ola 1 fusionada en `main`: PR #147 (cuota, error real del worker, lock caducado, guard sin falsos positivos con imágenes,
  enrutado previo de tarjetas imposibles, `Zona:` en tarjetas, rama al día antes de review, Haiku para tamaño S con
  respaldo en Sonnet, menos ruido) y PR #148 (`import cuota` que faltaba en `lanes.py`). Plan completo:
  `C:\Users\oscar\.claude\plans\imperative-juggling-plum.md` (copia en esta rama no incluida: es del PC de Oscar).
- Runner: tarea programada «agent-lanes runner» (LogonTrigger, reinicio 3×/5 min) habilitada y viva (PID 42660 el 30-10 21:40).
  Plazas como antes (6 en total, 4 en Oscar HQ). Sin pausa por cuota (`.state/cuota.json` no existe).
- Kanban: sigue en SQLite de Hermes (`KANBAN_BACKEND=hermes`). El corte a Supabase (`hub.*`, vacío) es la Ola 2.
- `lanes.py status` funciona. Si el SQLite da «base de datos read-only (transitorio)», reintentar.

## 2. Tarjetas en vuelo (30-10 noche)
- En revisión: `t_e794cb2f` (cerrar avisos de Decisiones obsoletos; 48 seguros, los 22 «aprobar» sin constancia se dejan).
- En marcha: `t_f5422078` (integración automática de lo pequeño, apagada por defecto; el PR lo fusiona Oscar).
- Hechas y SIN integrar (esperan lote y botón): `t_076af94d` (pitch MCP), `t_f08c4e93` (ideas), `t_9184da25` (funnel_builder),
  `t_9ccf88e3` (8 plantillas), `t_9720dce7` (seguridad ronda 2), `t_6c4b5a53` (email Signal), `t_5a601b26`, `t_9d8d23ef`,
  `t_bf111a0a` (GDPR por marca), `t_d16c4155`, `t_d36294db`, `t_c782ff8c`, `t_89904910`, `t_71915729`.
- Esperando a otra: `t_2200be8f` (audit log CRM) en triage hasta que `t_bf111a0a` esté FUSIONADA; luego `requeue_triage`.
- Bloqueadas a propósito: `t_6fe6a510` y `t_ac097a32` (código sin imágenes en `lane/<id>-solo-codigo`; relanzar bien,
  sus carpetas de trabajo tienen puesta esa rama), `t_d05032a0` (espacios finales en `VideoLocalTab.tsx`), fase 2 del cableado
  (`t_da882551`, `t_35e48e4a`, `t_1a70ee55`, `t_401ad50d`, `t_4206bee5`: entran cuando acabe el goteo de Ángel).
- MigraTeam (carril `claude-migrateam`, base `develop`): en cola `t_6407068b` (test AST), `t_df2b177a` (Stripe/Signaturit),
  `t_24e8db6e` (correo y portal), `t_64486b12` (ledger). En frío (asignadas a oscar): `t_a9fb4409` (línea base de tests,
  necesita BD desechable), `t_5b4e0eed`, `t_44bb41e0`, `t_a86afa47`. El gate del carril NO ejecuta pytest: lo verifica el PR.
- Tarjetas nuevas en frío (asignadas a oscar): OHQ `t_5a5ef276`, `t_8fc2c011`, `t_01dc86f7`, `t_c70983ad`, `t_2a59c49f` (Sentry);
  `t_a75f634b` (panel de desarrollo, pitch + v1 en /carriles); `t_0f1d02ce` (autoaprendizaje, Ola 2); en el carril:
  `t_2d8da564` (mapa de cobertura de crews/skills + pitch del catálogo).
- Aprendizaje: reasignar una tarjeta entre carriles de repos distintos NO recrea su espacio de trabajo; hay que crear una
  tarjeta nueva y archivar la vieja (hecho con t_ddec6688, t_e7ae2a3f, t_189abf00).

## 3. Goteo de las peticiones de Ángel (fuera de los repos)
- Carpeta `C:\Users\oscar\dev\_ops\goteo-frio\` : `goteo.py`, `frio_mapa.tsv` (53 tarjetas de Ángel con su carril original),
  `hechas.json`. Quedan unas 37 en frío.
- Cada pasada devuelve a su carril hasta 6 tarjetas si hay plazas libres (orden: BUG/GDPR/CRÍTICO primero, luego antigüedad).
  `py -3.12 goteo.py --seco` imprime lo que haría. Parar: crear un fichero `PARAR` en esa carpeta.
- Para que corra sin sesión: tarea de Windows cada hora (el asistente no pudo crearla: el sistema bloqueó la persistencia). La crea
  Oscar con `!` :
  `schtasks /Create /TN goteo-frio /SC HOURLY /MO 1 /TR "\"C:\Users\oscar\AppData\Local\Programs\Python\Launcher\py.exe\" -3.12 \"C:\Users\oscar\dev\_ops\goteo-frio\goteo.py\"" /F`
  Se borra sola al agotar el mapa. Revertir la tanda completa: cada línea de `frio_mapa.tsv` es `board<TAB>id<TAB>carril`.
- Las otras 53→37 devoluciones y la lista de «frío» original están en `scratchpad\frio_mapa.tsv` de la sesión (sin versionar).

## 4. Qué dependía de la sesión de Claude Code (se pierde al cerrarla)
- Programaciones de sesión: integración a las 8:47 y 18:47, vigilancia ligera de día (cada 3 h, 9-21 h) y dos recordatorios
  del 3-nov (fin de la semana de prueba de la plataforma). Los prompts completos están en la transcripción
  `C:\Users\oscar\.claude\projects\C--Users-oscar\6f56351f-0148-4350-bad6-6cbc2cc1dad0.jsonl`.
- Sin la sesión nadie: mueve tarjetas atascadas, prepara lotes ni fusiona. Los botones de Telegram sí funcionan.

## 5. Pendiente de Oscar
- Borrador para Fabbina en Gmail (no enviado): rellenar 3 huecos de reunión, confirmar «datos a salvo», nombre de la persona de soporte.
- Worktree `t_2fd5f3d5` con `scan_btn.py` sin guardar; 8 worktrees fusionados con ficheros sin guardar; PR #449 (Vex) con conflicto;
  MigraTeam PR #180, #271, #278 viejos.
- Verificar las 22 aprobaciones «aprobar» de hace ~4 días contra `master` (t_e794cb2f las deja abiertas).
- Sin verificar en esta sesión: versión desplegada en Dokploy, pildora-01, Supabase (advisors/respaldos), Sentry, Vercel, Stripe.

## 5b. Para no depender de la sesión (decisión de Oscar 30-10)
Verificado con `python lanes.py doctor-config --sin-gh`:
- ACTIVO: bot de carriles con botones, integrador (INTEGRATOR_LANES), latido del PC a Oscar HQ (el servidor avisa si el PC
  lleva >30 min sin latir), temas Decisiones e Integración.
- APAGADO (lo que hoy ata a una sesión): despliegue por botón de claude-oscarhq en Dokploy (faltan
  `integrator.lanes.claude-oscarhq.dokploy_application_id` y `DOKPLOY_API_TOKEN`), prodwatch de Vercel (falta `VERCEL_TOKEN`),
  latido a Uptime Kuma (falta `KUMA_PUSH_URL`), vigilancia de Supabase (falta `SUPABASE_SERVICE_ROLE_KEY`). Son variables de
  entorno del hub: las pone Oscar; el asistente no las toca.
- PC: enchufado no se suspende nunca (standby en CA = 0); en batería se suspende a los 10 min. Para operar solo, debe estar enchufado.
- Tarjetas que cierran la dependencia (todas con su PR y el botón de Oscar): avisos esenciales `t_e1ca40ec`, carril libre y
  reposición `t_ebd2b253` (sustituye al goteo), integración automática de lo pequeño `t_f5422078`, cierre de avisos obsoletos
  `t_e794cb2f` (en revisión), y en frío `t_73351cea` (triaje automático de bloqueos con lista blanca; activar tras las dos de arriba)
  y `t_0f1d02ce` (autoaprendizaje).
- Orden recomendado de activación: t_e794cb2f → t_e1ca40ec → t_ebd2b253 → t_f5422078 → t_73351cea → t_0f1d02ce.

## 6. Lecciones para `/distill`
- Probar `lanes.py status` y `ruff F821` tras tocar `runner.py`/`lanes.py`: la suite (3028 tests) no cubría un `NameError`.
- Un cierre masivo de avisos o tarjetas «resueltas» hay que cruzarlo con la constancia de integración: 22 de 70 no lo estaban.
- Edit de estado vivo (`.state`) con el runner arriba: no; arreglar el mecanismo en el hub con tarjeta y test.
- Comprobar la rama de trabajo real de cada repo (Scraper no usa `main`) antes de decir «no fusionado».
