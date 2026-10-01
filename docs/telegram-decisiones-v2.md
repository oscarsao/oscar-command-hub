# Telegram · decisiones v2 (t_40fe70c3) — parte 1

Implementado en esta tarea: puntos **1, 2, 4 y 5** de `specs/telegram-decisiones-v2.md`.
Pendientes para la segunda tarea (decisión de Oscar, 01-10): punto **3** (tarjetas [DECISIÓN]/[SEMANA]/[IDEA] con sus
botones) y punto **6** (límite de 5 tarjetas activas y "elige tus 3 prioridades" del lunes).

Capturas de texto de cada mensaje (los botones se muestran entre corchetes).

## 1. Pregunta con opciones escritas y botones genéricos

```
❓ t_aaaaaaa1 · Publicar la landing
MigraTeam · claude-migrateam
Qué: Algo concreto.
necesita tu decisión · hace 29 h
• ¿Publicar ya?
   A) Sí ⭐
   B) No

[A] [B]
[⭐ Recomendada] [✍️ Otra]
[🗄 Aparcar] [💬 Explícame más]
```

- Con 3-4 opciones salen `[A] [B] [C] [D]` en la primera fila. Sin recomendada no hay `⭐ Recomendada`.
- Lo que se registra en la tarjeta del kanban es el **texto** de la opción (`Respuesta de Oscar: ¿Publicar ya? → Sí`),
  nunca la letra. `✍️ Otra` abre la respuesta libre de siempre.
- Una pregunta sin opciones sigue con `[✅ Sí, adelante] [❌ No]` y `✍️ Otra respuesta`.

## 2. ✅ Acepto todas las recomendadas (tarjeta con varias preguntas)

```
❓ t_aaaaaaa1 · Publicar la landing
…
necesita tu decisión · Pregunta 1/2 · hace 29 h
• ¿Publicar ya?
   A) Sí ⭐
   B) No

[A] [B]
[⭐ Recomendada] [✍️ Otra]
[🗄 Aparcar] [💬 Explícame más]
[✅ Acepto todas las recomendadas]
```

Al pulsarlo se anota la respuesta recomendada de **cada pregunta pendiente** de la tarjeta y se desbloquea la tarea una
sola vez. La tarjeta (y sus copias en el tema y el DM) pasa a:

```
💬 t_aaaaaaa1 · Publicar la landing
…
💬 respondida (lo recomendado): Sí · 19 €
```

El botón solo aparece si **todas** las preguntas pendientes tienen recomendada. En tarjetas de grupo (la misma pregunta
en varias tareas) no aparece: ahí sigue valiendo la cabecera de `/decisiones`.

## 4. Botón nuevo ⇒ mensaje nuevo (integrador)

Al fusionar una ficha, además de editarla (`✅ fusionado · ddddddd · sin desplegar`), llega un mensaje **nuevo** al final
del tema y a tu DM, con notificación:

```
🔔 t_1 · Arreglar login
✅ fusionado · ddddddd · sin desplegar
Ahora puedes pulsar: 🚀 Desplegar

[🚀 Desplegar]
```

Lo mismo para `✅ Migración aplicada` y `🔁 Aplicar`. Un solo mensaje por teclado nuevo; los tres mensajes comparten el
mismo botón (pulsar uno retira los demás y todos se actualizan con lo que pasó).

## 5. `/decisiones`: las 5 más importantes

Con más de 5 elementos, se muestran 5 (cada uno su mensaje con sus botones) y una línea final:

```
❓ 7 decisiones · la más antigua hace 29 h
7 con opción recomendada en todas sus preguntas        [✅ Aceptar todo lo recomendado]

❓ t_aaaaaa00 · …   (tarjeta con A/B/⭐/✍️)
… (5 tarjetas)

➕ 2 más · ver todas                                    [📋 Ver todas]
```

- **Criterio de importancia** (decidido por Oscar): primero lo que **bloquea agentes** (preguntas en `needs_input` y 🧊
  atascadas), cada grupo de la más antigua a la más reciente; después las tarjetas de decisión de Oscar por antigüedad.
- `📋 Ver todas` envía la bandeja completa de siempre (tope 20). Con 5 o menos elementos, `/decisiones` es idéntico a antes.
- Las tarjetas [DECISIÓN]/[SEMANA]/[IDEA] entran en el ranking como una línea con enlace; sus botones propios llegan con la
  segunda tarea.

## Qué se tocó

`agent_lanes/notices.py` (opciones en el texto), `decisions.py` (teclado, `accept_card`, `see_all`), `commands.py`
(top 5), `integrator.py` (aviso de botón nuevo). Tests: `tests/test_telegram_decisiones_v2.py` + ajuste de los tests de
botones antiguos (`⭐ 1) texto` → `A`/`B`/`⭐ Recomendada`).
