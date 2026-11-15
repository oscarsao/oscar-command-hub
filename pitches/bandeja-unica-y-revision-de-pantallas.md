# Pitch · Bandeja única de decisiones y revisión de pantallas con evidencia

Modo OASP: **Pitch** · estado: **pendiente del OK de Oscar** (nada implementado) · autor: sesión de orquestación (14-15 nov 2026).
Idea de Oscar (15-11-2026, texto libre): un solo sitio donde ver todo lo que espera a un humano, accesible desde Claude Code,
Claude chat y Cowork; que los comentarios de pantalla generen elementos a revisar; y que una pantalla publicada sin
comentario/aprobación se vea como «no revisada».

## 1. Problema

- Oscar recibe ~27-31 pantallas de botones al día (medido, PR #158) y le llegan por caminos distintos: botones de sesiones de
  Claude Code, Telegram (varios canales), botones de la Integradora, tarjetas del kanban.
- `/decisiones`, `/pendiente` y `/hoy` existen (Telegram) pero **solo ven tarjetas del kanban**, no lo que preguntan las
  sesiones abiertas ni lo publicado sin revisar.
- «Comentar esta pantalla» (oscar-hq, `ohq_ui_feedback`) guarda notas pero no crea tarjetas ni cambia estado: el 9-nov había
  21 notas, todas «nuevo».
- No hay inventario de qué pantallas publicadas se han revisado después de publicarse.

## 2. Appetite

**Grande: ~2-3 semanas en 4 entregas independientes** (cada una útil sola). Si aprieta, se recorta la D.

| Entrega | Contenido | Appetite |
|---|---|---|
| **0. Oscar HQ pasa siempre por staging** | Ver sección 4b: el comentario de pantalla solo en staging, y todo cambio de Oscar HQ se prueba en staging antes de `master`, como en MigraTeam | ~1 semana, por fases |
| **A. `/decisiones` en Claude Code** | Skill que lee la bandeja existente y muestra UNA tanda de botones (formato fijo de CLAUDE.md §7) | 2-3 días |
| **B. Comentarios → tarjetas** | Cada comentario nuevo de `ohq_ui_feedback` crea tarjeta: fallo → carril con fix + PR (aprobación de Oscar antes de publicar); idea/cambio → bandeja | ~1 semana |
| **C. Inventario de revisión de pantallas** | Detecta pantallas cuyo código cambió desde su última aprobación → «pendiente de revisar»; un botón «aprobada» guarda fecha, SHA y captura | ~1 semana |
| **D. Otros clientes** | Herramienta de `pildora-mcp` «decisiones pendientes» para Claude chat/Cowork (ver auditoría del MCP) | 2-3 días |

## 3. Qué ya existe (se extiende, no se reconstruye)

- `docs/telegram-decisiones-v2.md`, `agent_lanes/lo_tuyo.py` (`/pendiente`), `bandeja.yaml` (top 5, dedupe, aplazar solo,
  veto 48 h en modo aviso), `botonera.yaml` (botones fijos por estado), resumen de las 8:00/9:00 y `/hoy`.
- `pitches/seguimiento-peticiones.md` (cadena petición→tarjeta→PR→despliegue, `/estado`): **A y B de este pitch dependen de su
  registro `req_<id>`**; se coordinan, no se duplican.
- `oscar-hq`: `src/services/ui_feedback.py` + `src/api/routes/ui_feedback.py` + tabla `ohq_ui_feedback`.
- Sistema de diseño de oscar-hq y puerta `lint:ds` (PR #628): la UI nueva reutiliza `dashboard/src/components/ds/`.
- `pildora-mcp`: puente MCP a Oscar HQ (ver `decisions/2026-11-15-auditoria-pildora-mcp.md`).

## 4. Diseño (resumen)

1. **Una sola fuente de verdad: el kanban/bandeja.** Toda pregunta que necesite a Oscar se registra como tarjeta con los 5
   campos de §7 (qué se decide, recomendación, reversible, plazo, evidencia). Telegram, Claude Code y MCP son vistas del mismo
   dato, no copias.
2. **Tres secciones** en cada vista: Decisiones · Estado de carriles · Publicado sin revisar.
3. **Comentarios**: un trabajo periódico (no un hilo manual) convierte `ohq_ui_feedback` en tarjetas y actualiza su estado con
   evidencia. Los fallos siguen la ruta habitual: carril → PR → revisión → botón de Oscar antes de publicar.
4. **Inventario de pantallas**: lista de rutas del dashboard + tabla de aprobaciones (ruta, SHA aprobado, quién, captura).
   «Pendiente» = el código de esa ruta cambió después de la última aprobación. Sin obligación de comentar cada pantalla.
5. **MigraTeam**: mismo componente detrás de un **feature flag por despacho** (empezar con despachos piloto, solo staging).

## 4b. Staging de Oscar HQ (petición de Oscar, 15-11-2026)

Objetivo de Oscar: no «arruinar el panel de un cliente» al probar; separar desarrollo y producción en Píldora igual que en
MigraTeam (`develop` → staging, `master` → producción), con su propio dominio y datos con los que probar de verdad; poco a poco.

**Qué ya existe (28-10, tarjetas `t_5989f653` y `t_b8b430aa`; no revalidado entero hoy):** un staging de Oscar HQ en
`https://ohq-stage.2-28-20-203.sslip.io` (health responde 200 el 15-11) con `APP_ENV=staging`, base de datos propia
`oscar-hq-pruebas`, sin planificador, y una clave X-API-Key de **solo lectura** (403 en escrituras y rutas de secretos).
La app `oscar-hq-staging` figura en Dokploy.

**Qué falta:**
1. **Fase 1 (reglas, sin infraestructura nueva):** regla escrita en el CLAUDE.md de oscar-hq: todo cambio de interfaz o de
   comportamiento se verifica en staging antes de proponer el paso a `master`; la ficha de la tarjeta lleva la URL de staging.
2. **Fase 2 (comentar solo en staging):** el botón «Comentar esta pantalla» se muestra solo con `APP_ENV=staging` (flag; en
   producción apagado, con la excepción que decida Oscar). Los comentarios de staging alimentan la entrega B.
3. **Fase 3 (flujo como MigraTeam):** rama `develop` de oscar-hq que despliega staging solo, y paso a `master` por PR con
   revisión; la CI (cuando esté estable) corre en ese PR. Mientras tanto, pruebas locales, como hasta ahora.
4. **Fase 4 (dominio propio):** `stage.<dominio de Píldora>` en lugar del `sslip.io` actual.
5. **Fase 5 (datos):** la BD de staging hoy es de pruebas. Para probar con datos «reales» hay dos caminos: copia **anonimizada**
   de producción (recomendada) o datos sintéticos. **No** se copiará producción tal cual a un entorno con menos protección: son
   datos personales de clientes (RGPD).

**Riesgos:** un staging con datos reales y sin la protección de producción es el hueco RGPD más serio de este plan; la separación
solo protege si staging **no** comparte claves, webhooks ni envíos con producción (correo, Telegram, Stripe, Apollo deben ir
a modo prueba o apagados en staging).

## 5. Riesgos y huecos (a cerrar antes de empezar)

- **Datos personales (RGPD):** una captura en MigraTeam puede mostrar datos de clientes reales. Decidir: staging con datos de
  prueba primero; qué se guarda de la captura; retención; quién puede verla.
- **Personas afectadas:** Mauredy, Mary, Linda y Andrea usan MigraTeam/oscar-hq; avisarles vía Oscar antes de activar nada en
  producción.
- **Fricción:** si cada pantalla exige comentario, se añade un proceso. Por eso la aprobación es por cambio detectado.
- **Auto-arreglo de fallos:** solo hasta PR; nunca publicar sin el botón de Oscar (regla existente).
- **Coste:** ninguna entrega llama a modelos de pago salvo los carriles que arreglen fallos; medir con 1 llamada antes de un lote.
- **Claude chat/Cowork:** un conector remoto exige identidad por usuario y HTTPS con el método de autenticación que admitan esos
  clientes (a verificar; ver auditoría del MCP).

## 6. No-gos

Pantalla nueva en oscar-hq para decisiones (duplica `/decisiones`); aprobar pantallas sin evidencia; publicar sin botón de
Oscar; guardar capturas con datos reales sin decisión de RGPD; tocar el runner vivo en estas entregas.

## 7. Decisiones que necesita de Oscar (opción por defecto marcada)

1. Orden de entregas: **A → B → C → D** (por defecto) o empezar por B (cierra las 21 notas).
2. MigraTeam: **solo staging con datos de prueba y flag por despacho piloto** (por defecto) o también producción.
3. Aprobación de pantallas: **por cambio detectado, un clic** (por defecto) o comentario obligatorio.
4. Quién aprueba pantallas de MigraTeam: **Oscar** (por defecto) o también Mauredy.
5. Staging de Oscar HQ: empezar por **fase 1 y 2 (regla + comentar solo en staging)** (por defecto) y dejar el flujo `develop` y el
   dominio para después de la CI; o ir directo a la fase 3.
6. Datos de staging: **copia anonimizada** (por defecto) o datos sintéticos. Producción tal cual, no.
7. Comentarios en producción: **apagados** (por defecto) o dejar un canal solo para ti.

Si no contesta: se parte A en tarjeta de nivel 1 (reversible, solo hub/skill) y el resto espera.

## 8. Cómo se corta

A → 1 tarjeta (claude-hub). B → spec-lite + 2 tarjetas (claude-oscarhq: servicio y job; hub: aviso). C → spec-lite + 2-3 tarjetas
(oscar-hq; MigraTeam tras decidir RGPD). D → 1 tarjeta (pildora-mcp) tras la auditoría.
