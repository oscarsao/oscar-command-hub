# Pitch · Seguimiento de peticiones, workflows desde Telegram y tipo de petición

Tarjeta `t_236bded4` · carril `claude-hub` · modo OASP: **Pitch** · estado: **pendiente del OK de Oscar** (nada implementado).
Lo presenta el integrador. Al aprobarse se parte en specs/tarjetas (ver «Cómo se corta»).

## 1. Problema

Las tarjetas se atascan porque **nadie sigue la cadena entera**: lo que Oscar pide (Telegram o Claude Code) acaba en una
tarjeta de Hermes, luego en tarjetas de carril, un PR, una integración y un despliegue, y cada eslabón se ve por separado.
Si una respuesta se pierde, un PR no tiene salida o el integrador no tiene sesión abierta, nadie lo ve hasta que Oscar
pregunta. Además, hoy al pedir no se distingue «arreglar algo que falla» de «crear algo nuevo», así que la tarjeta nace sin
las preguntas que cada caso necesita.

## 2. Appetite

**Grande: ~3 semanas en 3 entregas independientes** (cada una útil sola). **Decisión de Oscar: se lanzan las tres a la
vez** (A, B y C en paralelo); el orden de abajo es el de dependencias, no de calendario:

| Entrega | Contenido | Appetite |
|---|---|---|
| **A. Cadena + /estado** | Vincular petición → tarjetas → PR → integración → despliegue; `/estado`; formato único de aviso; fusión con el parte de las 9:00 | ~1 semana |
| **B. Tipo de petición** | Botón bug / feature al pedir (o detectado y confirmado), preguntas por tipo, decisión OASP | ~3-4 días |
| **C. Workflows dinámicos + cola autónoma del integrador** | Plan con preguntas previas, aprobación de plan y gasto, orquestación de carriles, entrega única; integrador que no depende de su sesión | ~1,5 semanas |

Si el tiempo aprieta, se recorta C (no A ni B): A y B ya resuelven «tarjetas atascadas» y «pedir bien». Al ir las tres a
la vez, C se apoya en los contratos de A (registro `req_<id>`) y B (tipo): se fijan primero en las specs A1/B1.

## 3. Qué ya existe (se cita, no se reconstruye)

- `docs/telegram-decisiones-v2.md` + `specs/telegram-decisiones-v2.md`: preguntas con A/B/⭐/✍️, «Acepto todas las
  recomendadas», `/decisiones` top 5, mensaje nuevo al aparecer un botón. **Base del formato de aviso.**
- `docs/lote-diario-integrador.md`: lote diario por proyecto (`batch: daily`), gates por rama y de lote, ficha 🚦 con
  «Fusionar lote». **Base de la cola del integrador.**
- `decisions/2026-09-27-arquitectura-carriles.md`: carriles, roles y límites de cada actor.
- `decisions/2026-09-27-informe-rediseno-orquestacion.md`: diagnóstico de atascos (§2) y visión de Oscar (§4); este pitch
  cierra buena parte de sus preguntas abiertas (§5).
- `decisions/2026-10-15-mapa-reglas-sistema.md`: mapa de reglas; recoge la auditoría P1–P4 (líneas 122 y 147).
- `docs/auditoria-aprobaciones-telegram-2026-10-15.md`: origen de P2 (`t_c09d8034`) y P4 (`t_56c47cf9`).
- **Ya en `main` de t_f8f58acb [AVISOS]:** `agent_lanes/resumen_carriles.py` (resumen de las 9:00 por carril con @mención y
  botón), `aviso.py` (avisos con @mención y botones) y la cola de bloqueos de guard del integrador que escala a Oscar a
  las 2 h (`/integrador`). Se **extienden**, no se duplican.
- **Hermana acotada:** `t_86c09458` [ATASCOS RÁPIDOS]. Este pitch es el marco; esa tarjeta es la parte rápida de «aviso
  inmediato de atasco». Debe ir primero o con el mismo formato; no se reimplementa aquí.

### Fuentes que NO he podido leer (pendiente, no bloquea el pitch pero sí su cierre)
- Sesión `hermes-orchestration-plan` y sesión en la nube «Problemas Telegram tareas atascadas»: **no están en disco ni en
  el repo**. Pido a **oscar-bf (integrador)** el texto o el enlace. Hasta tenerlo, el pitch no asume nada de ellas; si
  contradicen algo de aquí, prevalecen y se corrige el pitch.
- Paquete de traspaso `docs/traspaso/` (rama `docs/traspaso-2026-10-21`, aún sin PR): **no existe en `origin/main`**. Se
  cita por ruta y no se duplica (ver §9). Cuando exista el PR se enlaza aquí.
- Pitch de «Autonomía de carriles»: no está en este repo; pedir ruta a oscar-bf. Se aplicará lo que diga sobre qué decide
  un carril solo; no se redefine.

## 4. Solución

### 4.1 La cadena: un registro por petición (entrega A)

Nace un **registro de petición** (`req_<id>`) en un JSON propio del hub (patrón de `resumen_carriles`: fichero pequeño,
sin tocar el kanban). Cada eslabón guarda **estado, último movimiento y próximo paso + quién lo da**:

```
petición (Telegram / Claude Code)
  └─ tarjeta Hermes (t_…)           estado kanban
       └─ tarjetas de carril (t_…)  estado, rama lane/…
            └─ PR                    abierto / en lote / fusionado
                 └─ integración     INTEGRADO <sha>
                      └─ despliegue staging / prod
```

- El vínculo se escribe **al crear** la tarjeta (campo `req:` en el cuerpo o comentario, que ya lee el kanban) y se
  completa solo con lo que el runner/integrador ya anotan (`INTEGRADO <sha>`, rama, PR). **No se toca el runner**: el
  registro lee tarjetas y comentarios; quien escribe es el bot de Telegram y el integrador, que ya escriben ahí.
- Una petición sin tarjeta enlazada aparece como «sin tarjeta» (así se detecta la respuesta perdida de t_c09d8034/56c47cf9).
- **Estado de la petición** (derivado, no editable): `hecha` · `en curso` · `esperándote` · `atascada (motivo)`.
  Motivos cerrados: sin respuesta tuya >X h, PR sin salida (conflicto/gate), lote esperando migración, despliegue
  pendiente de tu OK, guard bloqueado, carril caído.

### 4.2 /estado (entrega A)

Una línea por petición, más recientes y atascadas primero:

```
📍 Estado · 6 peticiones

⏳ req_41 · MigraTeam · «Login con Google» — esperándote: elige A/B en t_a1b2 (hace 5 h). Lo das tú.   [Responder]
🧊 req_39 · Hub · «Avisos por tema» — atascada: PR #52 choca con main (src/a.py). Lo da el integrador.  [Ver más] [Dar seguimiento]
🛠 req_38 · Signal · «Export CSV» — en curso: carril claude-signal, rama lane/t_c3d4. Lo da el worker.   [Ver más]
✅ req_35 · Píldora · «Landing nueva» — hecha: INTEGRADO 3f9a1c0, desplegada ayer.
```

`/estado <proyecto>` filtra. «Ver más» despliega la cadena entera (§4.1) sin perder información.

### 4.3 Formato único de todo aviso (entrega A)

Mismo esqueleto para avisos, `/estado` y parte de las 9:00 (hoy cada uno tiene el suyo en `notices.py`, `aviso.py`,
`resumen_carriles.py`):

```
🧊 Hub · req_39 «Avisos por tema»
Estado: PR #52 en integración · paso 4/6
Próximo paso: resolver conflicto en src/a.py · lo da: el integrador (necesita tu OK si toca migración)

[🔀 Integrar] [👀 Dar seguimiento] [🗄 Aparcar] [💬 Explícame más]
(o escríbeme aquí la respuesta)
```

- **Texto resumido con «ver más»** que muestra el texto completo (nunca se recorta sin ofrecer el resto; encaja con P3
  `t_e71e7aca`).
- **Botones siempre + texto libre siempre**: cualquier respuesta escrita en el hilo del aviso cuenta como respuesta a ese
  aviso (P1 `t_83278452`, ya en marcha) y se registra en la tarjeta.
- **Tema por proyecto** (Píldora, MigraTeam, Hub, Signal, NextJobs…): cada aviso va al tema de su proyecto según el mapa de
  `decisions/2026-09-27-telegram-grupos-temas.md`; lo urgente además con @mención en Atención.
- **Botones de opción**: integrar, dar seguimiento, aparcar, responder, explícame más. Sin códigos: el usuario nunca teclea
  ids; el botón lleva el id. Solo personas autorizadas (la lista existente del bot); un botón pulsado por otro se ignora.

### 4.4 Parte de las 9:00 y atasco inmediato — **decisión de Oscar: mantener los dos**

A las 9:00 siguen **dos partes** con funciones distintas:
- **Resumen por carril** (t_f8f58acb, `resumen_carriles.py`): no se toca; dice qué hace cada carril.
- **Parte por petición/proyecto** (nuevo, `/estado` completo agrupado por proyecto): dice cómo va cada cosa que Oscar pidió.

Para que no sea ruido doble, el parte por petición **no repite** el detalle de carril (solo enlaza «ver carriles») y no
sale si no hay novedad desde ayer. Ambos usan el formato de §4.3 y son recuperables hasta las 11:00 como hoy.
El **aviso inmediato de atasco** (t_86c09458) usa el mismo formato y se dispara cuando una petición pasa a `atascada`; no
espera a las 9:00. Un atasco no se repite: recordatorio a las 24 h (ya existe en [ATENCIÓN]).

### 4.5 Tipo de petición (entrega B)

Al pedir (mensaje a Hermes o `/pedir`), **un botón**: `[🐞 Arreglar algo que falla] [✨ Crear algo nuevo]`. Si Oscar escribe
sin pulsar, el bot **propone** el tipo («parece un fallo, ¿lo confirmas?» con los mismos botones). Nunca decide solo.

| | 🐞 Bug | ✨ Feature |
|---|---|---|
| Pregunta | cómo reproducirlo, dónde, qué esperaba, cuánto urge | objetivo, quién lo usa, criterio de «hecho» |
| Prioriza | sí: urgencia (P0 prod roto … P3) sube la cola | no: va a la cola por appetite |
| Modo OASP | Fast-Track (<1 h). Si el diagnóstico crece → sube a Spec-Lite y avisa | el bot **propone** Fast-Track / Spec-Lite / Pitch según lo respondido y Oscar confirma con botón |

Preguntas con el formato de `telegram-decisiones-v2` (A/B/⭐/✍️ y «Acepto todas las recomendadas»); máximo 3-4 por tipo,
las que ya estén respondidas en el mensaje original no se repiten. El tipo y las respuestas se guardan en el cuerpo de la
tarjeta (`Tipo: bug · Urgencia: P1 …`) y en el registro de la petición.

### 4.6 Workflows dinámicos desde Telegram (entrega C)

Para tareas complejas o funciones completas, `/workflow <qué quieres>` (o botón «🧩 Esto es grande» al pedir):

1. **Preguntas previas** (botones + texto libre, tope 5): solo lo que el agente necesita para dar el mejor resultado.
2. **Plan**: un mensaje con los carriles implicados, tarjetas previstas, orden/dependencias, riesgos, cómo se probará y
   **coste estimado con tope**. Botones `[✅ Aprobar plan y gasto] [✍️ Cambiar] [🗄 Aparcar]`.
3. **Aprobación explícita antes de lanzar**: nada se crea en el kanban hasta ese botón.
4. **Ejecución**: crea las tarjetas de carril enlazadas a la misma `req_<id>` (§4.1); `/estado` muestra una sola línea
   con «3/5 tarjetas».
5. **Entrega única**: un mensaje final con *qué se hizo, enlaces (PR, staging) y cómo probarlo*; mientras tanto solo avisan
   los atascos y las decisiones, no cada paso.

**Límites de coste y aprobación:**
- Tope por defecto por workflow: **8 €, ampliable con botón** (decisión de Oscar). El plan lo
  muestra estimado; si lo gastado alcanza el **80 %** → aviso; al **100 %** → se **pausa** y pide `[➕ Ampliar] [⛔ Parar]`.
- Máx. 5 tarjetas de carril activas por workflow, y respeta el límite de 3 workstreams de código en paralelo.
- Nunca sin OK de Oscar: producción, migraciones, borrados, dinero (reglas de `~/.claude/CLAUDE.md`). El workflow las
  **para** en el plan como «paso que te pedirá OK».

### 4.7 Cola del integrador que no depende de su sesión (entrega C)

Un proceso automático (extensión de `integrator.py` y de la cola de bloqueos de t_f8f58acb) procesa la cola
**con o sin** sesión del integrador abierta. Reparto cerrado:

| Siempre automático (sin humano) | Nunca automático (pide a Oscar con botones) |
|---|---|
| Montar el lote diario y pasar los gates (§ lote-diario) | Aplicar **migraciones** (Alembic) — siempre el OK de Oscar |
| Abrir/actualizar el PR del lote; reabrir lote si la base se movió | **Resolver conflictos de código** — él solo no; saca la rama del lote con el motivo y avisa |
| Dejar fuera una rama que rompe un gate, con motivo, sin bloquear al resto | Fusionar a `main`/rama de producción (botón «Fusionar lote», solo Oscar) |
| Reintentar gates tras cambio de base; cerrar PR sueltos ya integrados | Desplegar a producción; borrar ramas/archivos ajenos |
| Escribir `INTEGRADO <sha>` y actualizar el registro de la petición | Tocar hooks/contrato de seguridad |
| Escalar a Oscar a las 2 h de un bloqueo y recordar a las 24 h | Cualquier cosa fuera de las listas de la izquierda |

**Absorbe `t_62ec72b1` (PRs sin salida / lotes):** un PR que no puede entrar (conflicto, gate roto, espera de migración) ya
no se queda «abierto sin dueño»: el estado `atascada (motivo)` lo hace visible con su botón y su responsable, y el lote
siguiente lo reintenta. Complementa `t_86c09458`, que cubre solo el aviso rápido.

### 4.8 Qué se absorbe (el integrador las cierra al aprobar el pitch)

| Tarjeta | Qué parte del pitch la cubre |
|---|---|
| `t_62ec72b1` PRs sin salida / lotes | §4.7 y estado `atascada` de §4.1 |
| `t_c09d8034` relé determinista (P2) | §4.1 «petición sin tarjeta» + §4.3 «texto libre = respuesta del aviso» |
| `t_56c47cf9` no perder respuestas + bug `clarify` (P4) | §4.3 (la respuesta se registra en la tarjeta antes de confirmar) + entrega A |

El integrador las cierra con comentario `ABSORBIDA por t_236bded4 (pitch aprobado)`. Si el pitch se rechaza, siguen vivas.

## 5. Partes entregables y criterio de aceptación (para la persona técnica)

Cada parte se entrega y se prueba por separado. «Se prueba» = `py -3.12 -m pytest tools/agent-lanes/tests -q` (dobles, sin
red, sin Telegram real) **más** la comprobación manual indicada en un chat de prueba.

| # | Parte | Criterio de aceptación | Cómo se prueba |
|---|---|---|---|
| A1 | Registro de petición + vínculo | Crear una tarjeta con `req:` crea el registro; `INTEGRADO <sha>` lo pasa a `hecha`; sin tarjeta → «sin tarjeta» | tests con kanban simulado |
| A2 | `/estado` | Una línea por petición con estado y quién da el próximo paso; filtro por proyecto; «ver más» muestra la cadena completa | test de texto + captura en chat de prueba |
| A3 | Formato único de aviso | Todo aviso lleva proyecto, estado/paso, próximo paso y quién; botones + texto libre; va al tema de su proyecto | tests de formato; un aviso real por proyecto en chat de prueba |
| A4 | Parte de las 9:00 por petición, junto al de carriles | Dos partes a las 9:00 (carril intacto + petición); el de petición no repite el detalle de carril y no sale si no hay novedad | test con reloj simulado |
| B1 | Botón de tipo + detección confirmada | Sin pulsar, el bot propone y espera confirmación; nunca fija tipo solo | tests de callbacks |
| B2 | Preguntas por tipo + modo OASP | Bug pregunta repro/dónde/esperado y prioriza; feature pregunta objetivo/usuarios/hecho y propone modo | tests; ejemplos de tarjeta resultante |
| C1 | Plan + aprobación de gasto | No se crea ninguna tarjeta antes de «Aprobar plan y gasto»; el plan muestra tope | test: sin aprobar → 0 tarjetas |
| C2 | Orquestación + entrega única | N tarjetas enlazadas a una `req`; un solo mensaje final con hecho/enlaces/cómo probar | test con carriles simulados |
| C3 | Tope de coste | A 80 % avisa; a 100 % pausa y pide ampliar/parar | test con gasto simulado |
| C4 | Cola autónoma del integrador | Con la sesión cerrada monta lote y pasa gates; **nunca** aplica migración ni resuelve conflicto de código | tests con git real en repos temporales (patrón `test_batch.py`) |

**Traspaso a la persona técnica:** la documentación de traspaso (mapa del sistema, bots de Telegram, runbooks, estado y deuda,
glosario y decisiones) la mantiene la sesión `hermes-orchestration-plan` en `docs/traspaso/` (rama
`docs/traspaso-2026-10-21`). **Este pitch no la duplica**: al implementar cada parte, solo se **añade una línea** en
`docs/traspaso/04-estado-y-deuda.md` y, si hay comando nuevo, en `03-runbooks`. Si a la hora de implementar el paquete
aún no existe en `main`, se pregunta a oscar-bf la ruta.

## 6. Rabbit holes (y cómo se evitan)

- **Inferir la cadena adivinando** (por texto del mensaje): frágil. Se evita con vínculo explícito `req:` al crear; lo que
  no tenga vínculo se muestra como «sin tarjeta», no se inventa.
- **Estimar el coste con precisión**: imposible de verdad. Se muestra como **rango con tope duro**; el tope es lo que
  manda, no la estimación.
- **Detección de tipo con IA**: solo propone; la persona confirma con un botón. Nada de reglas complejas.
- **Dos fuentes de verdad** (registro vs kanban): el registro es **derivado y regenerable** desde el kanban + comentarios;
  si se pierde, se reconstruye. No guarda estado que el kanban no tenga, salvo `req → [tarjetas]`.
- **Mensajes de Telegram >4096 caracteres**: «ver más» pagina; no recorta.
- **Cola autónoma que «arregla de más»**: lista cerrada de §4.7; todo lo que no esté en la columna izquierda pide a Oscar.
- **Coste de ruido**: un workflow no avisa paso a paso; solo atascos, decisiones y entrega final.

## 7. No-goes (fuera de este pitch)

- Tocar el runner, hooks, contrato de seguridad, `.env`, `.state/` o workflows de GitHub.
- Que el integrador resuelva conflictos de código o aplique migraciones sin Oscar.
- Un panel web/dashboard: todo es Telegram.
- Seguimiento de peticiones anteriores a este pitch (no hay backfill; solo desde que se despliega A).
- Reescribir el reparto de temas por proyecto (`2026-09-27-telegram-grupos-temas.md`) o el lote diario.

## 8. Riesgos

- Es código del hub que ejecuta a todos los carriles: cada entrega va por PR, con suite verde, y entra en vigor cuando
  Oscar fusiona y pulsa [🔁 Aplicar].
- Dependencia de `t_86c09458` y de las fuentes pendientes (§3): si contradicen el diseño, se ajusta antes de implementar.
- Gasto: C puede lanzar varios carriles; sin el tope de §4.6 el coste se dispara. Por eso C3 es obligatorio, no opcional.

## 9. Cómo se corta al aprobarse

1. Oscar aprueba (o recorta) el pitch → el integrador cierra las absorbidas (§4.8).
2. Se crean 3 specs-lite en `specs/`: `seguimiento-cadena-estado.md` (A), `tipo-de-peticion.md` (B),
   `workflows-telegram-cola-integrador.md` (C), cada una una tarjeta `claude-hub`.
3. Las tres a la vez (decisión de Oscar), con `t_86c09458` primero o en paralelo con el mismo formato. Una persona técnica
   puede tomar B o C con los criterios de §5.

## 10. Decisiones de Oscar (ya tomadas, 21-oct)

1. **Partes de las 9:00:** mantener los dos (carril y petición). Ver §4.4.
2. **Tope de gasto por workflow:** 8 €, ampliable con botón. Ver §4.6.
3. **Alcance de la primera entrega:** las tres (A, B y C) a la vez. Ver §2 y §9.
4. **Detección de tipo:** el bot propone y la persona confirma. Ver §4.5.

**Único punto abierto (no bloquea aprobar el pitch):** oscar-bf, ¿texto/enlace de `hermes-orchestration-plan`, de
«Problemas Telegram tareas atascadas» y del pitch de Autonomía de carriles? Sin ellos el pitch no asume nada de esas
fuentes; si contradicen algo, prevalecen y se corrige antes de implementar.
