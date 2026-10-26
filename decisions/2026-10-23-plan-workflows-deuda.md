# Plan de workflows integrales y deuda técnica (23-10-2026)

> **Actualizado: 25-10-2026** (F1-08, CF-13): cifras de D-HB-01 y estado de la CI y de H5. Para el hub manda
> además `2026-10-25-arquitectura-hub-v2.md`. La frase «manda la página» se mantiene hasta que Oscar decida
> si las decisiones vivas pasan al repo (botón CF-13).

> Decisión de Oscar, tomada con botones en la página de planificación
> https://claude.ai/artifact/FcnvknBmUPEMEuscHqYevf (colecciones `workflows`, `deuda` y `decisiones`).
> Esa página es la fuente viva: si este documento y la página discrepan, manda la página.
> Lo que aquí se fija son las **reglas** y el **orden**, que todas las sesiones, carriles y bots deben seguir.

## Reglas de trabajo (vigentes desde el 23-10)

1. **Workflows integrales en vez de muchas tarjetas sueltas.** Un workflow es una cadena de tarjetas
   hijas con una zona de código propia, criterios de hecho y un final integrado. Se encadenan:
   un trabajo activo por cadena, como máximo 3 de código a la vez.
2. **Sin conflictos ni rebases a mano.** Al lanzar, una tarjeta que toca los mismos ficheros que otra en
   curso o en revisión espera detrás (prevención al lanzar, hub #119). Integración de una en una.
   Las ramas fusionadas se borran.
3. **Toda función lleva sus pantallas**, y al entregar se da la lista «cambio → URL exacta».
4. **Una pasada completa con el sistema de diseño:** no se elimina nada; lo que se toca se arregla del
   todo, reutilizando lo que ya existe (componentes del sistema de diseño #315, Unlayer, etc.).
5. **Ahorro de tokens:** encargos cerrados, entregas cortas, Sonnet por defecto, sin instalaciones ni
   auditorías que ya hace el CI. Cada workflow borra sus worktrees y ramas al terminar.
   *(25-10: hoy no hay CI activa en el hub ni en Oscar HQ (CF-01, CF-02); llega con F3-02. Mientras tanto, las
   pruebas las corren el carril y el integrador.)*
6. **Toda acción que envíe correos o mensajes lo dice antes**, con recuento y confirmación.
7. Producción, migraciones remotas, secretos, DNS y env vars: siempre con OK explícito de Oscar.

## Cadenas de esta noche (23-10)

| Cadena | Orden | Zona |
|---|---|---|
| Oscar HQ | **O4** Dirección y equipo (departamentos #325, tareas F1 troceada, objetivos, finanzas, RR. HH.) → **N-O2** agentes por departamento | ohq:tareas · departamentos · finanzas · rrhh |
| Oscar HQ (en paralelo) | **O1** CRM 360 (ficha con enlace propio t_f2e85006, #311, enriquecer empresa) | ohq:crm |
| MigraTeam | **M2** lote #283 a staging → **M5** calidad y tests → **N-M5** casos USCIS | mgrt:release · alembic · ci |
| Hub | **H1** runner fiable (t_cce5da81, #119) → **H2** carriles por proyecto → **N-H1** workflows como producto | hub:runner · scheduler |
| Hub (en paralelo) | **H3** Telegram como consola del integrador | hub:telegram |

## Próximos días (aprobados, en cola)

- **Arreglos y mejoras:** H0 cero conflictos; H4 observabilidad; M1 salida de Railway (ventana con Oscar); M4 correo y chatbot del despacho; O2 correo completo (tras las 4 tarjetas del recorte); O3 todo el panel al sistema de diseño (al final y solo); O7 staging de Píldora (Oscar aún elige la base de datos; no montar todavía); S1 a S5 de Signal; W1 recursos; W3 y W4 (asignados a **María**); W5 analítica.
- **Nuevas funciones:** N-O1 portal de cliente (con permisos granulares editables desde la UI); N-O3 integraciones de anuncios y redes; N-O4 asistente de citas instalable; N-O5 informes automáticos; N-M1 app móvil del cliente; N-M2 revisión de documentos con IA (configurable por despacho y con la declaración de IA de cada jurisdicción); N-M3 notificaciones de la Administración; N-M4 cobros (completar lo existente y cobrar también vía QuickBooks); N-H2 QA tras publicar (opcional, como botón en una tarjeta o workflow terminado, no en cada PR); N-H3 dictar tareas por voz; N-H4 costes de IA e infraestructura (integrado con Finanzas); N-S1 a N-S3 de Signal; N-W1 diagnóstico interactivo; N-W2 suscripción a recursos.
- **Más tarde:** M3 IA del despacho; O5 Funnel Studio completo; O6 plataforma (flags, 2FA, formularios); W2 web al sistema de diseño (pitch y revisión aparte: «ganar diseño, no perderlo»); N-W3 academia.
- **Sin decidir:** H5 servidor pildora-01 (disco, copias). *(25-10: decidido ampliar pildora-01 a 16 GB; copias de
  volúmenes en F3-09 del plan del hub.)*

## Deuda técnica medida (23-10)

23 áreas en la vista «Deuda técnica» de la página, con su cifra, impacto y fases. Las cinco más urgentes:

1. **D-MG-01 RLS de MigraTeam:** unas 105 de 115 políticas siguen en `USING(true)`; el aislamiento entre despachos depende de que cada endpoint filtre por `firm_id`, con un cliente real en producción.
2. **D-OH-01 hallazgos rojos de Oscar HQ:** H-129 (SSRF en /api/media/register-from-url), H-069 (secretos sin rotar), H-102 (Stripe cobra el 100 % donde la propuesta dice 50 %), H-001/H-002 sin verificar.
3. **D-MG-02 altas del ledger de MigraTeam:** SEC-09-0906, SEC-15-0917, TEN-07-0918, LEGAL-01/05.
4. **D-HB-01 `.env` sueltos en worktrees** (31 ficheros el 23-10; **6 el 25-10**: 4 `dashboard/.env.development` sin seguimiento en worktrees cerrados de
   `_lanes` (ds-base, hotfix-cabecera, invitar-manual, nav-lote) y 2 en `lane-t_6b596f70/frontend` con seguimiento en git
   y solo la URL de la API): borrarlos lo decide Oscar.
5. **D-OH-02 RLS de Supabase en Oscar HQ:** 108 políticas `USING(true)` en 31 migraciones.

Cada área se convierte en workflow cuando Oscar la marca «Convertir en workflow» en la página.

## Cómo lo usan carriles y bots

- Antes de crear o tomar una tarjeta, comprobar en este documento a qué cadena y zona pertenece; si
  no pertenece a ninguna, preguntar antes de lanzarla.
- Las decisiones nuevas de Oscar se leen de la página (`decisiones/<id>`); el integrador actualiza
  este documento cuando cambie el orden.
