# Propuesta: avisos claros (resuelto / pendiente), bucles de re-pregunta y ¿un solo kanban?

Tarea t_ac3f2c1c · 2026-10-01 · Solo diagnóstico y propuesta: **no se ha cambiado nada del runner ni del bot.**
Para decidir tú: al final hay una tabla de qué se hace primero y qué necesita tu OK.

## Lo que he podido y no he podido comprobar

- He leído el código de los carriles y del bot (avisos, botones, bloqueos repetidos, qué tableros se miran).
- **No he leído el kanban real** (las reglas del carril me lo prohíben), así que no he visto la historia de
  t_d5877cd1. Lo que digo de ella viene de tu descripción y del código. Un paso 0 de la propuesta es leerla con tu OK.
- El benchmark de mercado (Linear, GitHub, Jira) es de conocimiento general, sin consultar sus webs hoy.
- La tarjeta t_bd182c83 (claims del hub) no está en el repo; abajo explico cómo la trato.

---

## 1. Diagnóstico: qué pasa de verdad

### Problema A — "ya está hecho pero me sigue pidiendo botones" (confirmado en parte)

Tu hipótesis es cierta, pero con matices. Lo que **ya funciona** (lo he comprobado en `decisions.py`): al pulsar un
botón, el aviso se edita a "💬 respondida: …", se quitan los botones y se edita también la copia del DM y del tema.
Es decir, el bot **sí edita mensajes ya enviados**; no hace falta construir nada nuevo para eso.

Lo que **causa la confusión** (confirmado en el código):

1. **Cada bloqueo nuevo borra el aviso anterior y manda uno nuevo con botones** (`notices.py`, `publish`). El mensaje
   nuevo no recuerda lo que ya contestaste: solo trae la pregunta nueva. Para ti parece "la misma tarjeta otra vez".
2. **El aviso no tiene un hueco para "ya resuelto".** Tiene "Para ti:" (texto libre del worker) y "Qué:". Lo hecho y
   lo pendiente van mezclados en una frase, como dices.
3. **Varias preguntas = una tarjeta que cambia de pregunta.** Contestas la 1/3 y el mensaje se transforma en la 2/3
   con botones nuevos. Es correcto, pero visualmente parece que no ha pasado nada.
4. **La tarjeta no se cierra hasta que acaba TODO el trabajo.** Mientras tanto sigue en blocked/triage aunque el 80 %
   esté hecho; `/decisiones` la lista igual que una tarjeta virgen.
5. Los avisos antiguos que salieron con el bot de Hermes no se pueden editar desde el bot de carriles (está
   documentado en `renotify.py`): si te quedan mensajes de esa época, seguirán tal cual.

El dato que falta para cerrar el diagnóstico: qué mensajes concretos viste con botones tras contestar (¿copias viejas
del DM, o mensajes nuevos de un re-bloqueo?). Eso lo sabremos con el paso 0.

### Problema B — el bucle de re-pregunta (t_d5877cd1) (cubierto a medias)

Hoy existe esto:
- Hermes marca `block_loop_detected` cuando una tarea se bloquea dos veces seguidas por lo mismo y la pasa a
  `triage`.
- El bot ya lo trata: la llama 🧊 "atascada", la lista en `/decisiones` y en los recordatorios, y ofrece
  [🔄 Reintentar] [🗄 Aparcar]; el auditor la cuenta como bloqueo repetido.

Lo que **no cubre** (y creo que es lo de t_d5877cd1):
1. **Depende de que Hermes vea el "mismo motivo".** Si el worker pide el mismo acceso con otras palabras tres veces,
   puede no contar como repetición (no he podido ver la regla interna de Hermes: es software ajeno al repo).
2. **El aviso 🧊 no trae el historial.** Solo "atascada" + 2 botones. No te dice "esto ya se preguntó 3 veces, esto
   contestaste, esto sigue sin existir". Reintentar vuelve a lanzar al worker con la misma carencia → otra pregunta.
3. **Nadie obliga al worker a leer lo ya contestado** antes de volver a preguntar.
4. La tarjeta en `triage` solo se ve si es de un carril (`stuck_tasks`); una en `triage` en otro sitio pasa inadvertida,
   que es lo que te pasó con la tarjeta que se saltó Hermes.

### Problema C — cuatro tableros (confirmado y acotado)

Tableros reales en `lanes.yaml`: `oscarhq` (carriles oscarhq, scraper, ops), `migrateam`, `default` (nextjobs y
claude-hub). `personal` **no tiene carril**: el bot no lo mira nunca (`/decisiones` lee los tableros de los carriles
más `default`). Por eso hay que mirar "en 4 sitios" y `personal` es un punto ciego del bot.
Ojo: **el bot ya da una vista unificada** (`/decisiones`, `/estado`); lo que falla es que no cubre todo (personal,
triage fuera de carriles) y que Hermes, al revisar a mano, mira tablero a tablero.

---

## 2. Opciones por problema

Riesgo = riesgo de romper el sistema de carriles. Todo lo que toca el bot/runner se puede probar con dobles (sin
Telegram ni Hermes reales) y solo rige cuando fusionas y pulsas [🔁 Aplicar].

### A. Avisos "✅ Ya resuelto / ⏳ Pendiente de ti"

Diseño del mensaje (ejemplo):

```
⏳ t_d5877cd1 · Acceso a staging · MigraTeam
✅ Ya resuelto: despliegue de la rama (tú: "A, usar develop") · tests OK
⏳ Pendiente de ti (solo esto): credenciales de staging
Pregunta 1/1 · responde con un botón
```
Y tras pulsar: `✅ Resuelto por ti · "usar el token de lectura" · 14:02 · el carril sigue trabajando`.

| Opción | Qué cambia | Riesgo | Veredicto |
|---|---|---|---|
| A1. Pintar "Ya resuelto" con lo que ya sabe el bot | `render()` añade la sección con tus respuestas anteriores (ya las lee `answer_progress` de los comentarios del kanban) y el último avance. Sin cambiar el worker. | Bajo: solo texto de aviso | **Recomendada (primera)** |
| A2. El worker declara "hecho / pendiente" por separado | Campo nuevo en el JSON de salida del worker | Medio-alto: toca `contract/*.schema.json` (contrato de seguridad, requiere tu OK expreso) | Después, si A1 se queda corto |
| A3. No borrar el aviso al re-bloquear: editarlo y añadir historial | Cambia `publish()`: una tarjeta viva por tarea, con línea de tiempo | Medio: toca el circuito de avisos y de copias DM/tema | Segunda fase |
| A4. Cerrar la tarjeta y abrir otra para lo pendiente | Partir tareas al bloquear | Alto: cambia el modelo del kanban | No |

### B. Bucles de re-pregunta

| Opción | Qué cambia | Riesgo | Veredicto |
|---|---|---|---|
| B1. Aviso 🧊 con todo el contexto | Al pasar a atascada, un solo mensaje: qué se preguntó (con fechas), qué contestaste, qué falta, y botones "darle X / aparcar / es cosa mía" | Bajo | **Recomendada** |
| B2. Escalada automática a las 2 re-preguntas | El runner cuenta bloqueos `needs_input` por tarea (da igual el texto) y a la 2.ª crea tarjeta de decisión para ti con el historial en vez de relanzar al worker | Medio: toca la ruta de bloqueo del runner | **Recomendada, tras B1** |
| B3. Regla en el rol del worker | `roles/*.md`: "antes de preguntar lee los comentarios; no repitas; si falta un acceso que no se puede conseguir, di exactamente cuál, una vez" | Bajo (texto), pero un LLM puede ignorarla | Sí, como complemento |
| B4. Detector de "pregunta parecida" por similitud de texto | Comparar preguntas nuevas con las ya contestadas | Medio, con falsos positivos | No de entrada |

Conclusión sobre `block_loop_detected`: **no basta.** Detecta el síntoma tarde y sin contexto; hay que reforzar con
B1+B2 por nuestra cuenta (contando bloqueos nosotros, sin depender de cómo Hermes decida "mismo motivo").

### C. Kanban único o varios

| Opción | Qué cambia | Riesgo | Veredicto |
|---|---|---|---|
| C1. Mantener los tableros y completar la vista unificada | `personal` entra en lo que lee el bot; `/estado` lista TODO lo no cerrado de todos los tableros, incluido triage, agrupado por proyecto | Muy bajo (solo lectura) | **Recomendada** |
| C2. Vista unificada de solo lectura generada | El bot/brief diario publica una lista única "pendiente por proyecto" | Muy bajo | Sí, junto con C1 (el brief diario ya recorre tableros) |
| C3. Un solo tablero con campo "proyecto" | Mover tarjetas entre tableros, cambiar `board:` de cada carril y todo lo que lo lee. Hermes guarda un tablero por base de datos (`kanban --board`); no he verificado que soporte etiquetas/campos de proyecto con filtro fiable. Hay que migrar historial, ids y eventos | **Alto**: si falla, paran todos los carriles | No ahora; solo si C1 no basta, y tras probar en copia |
| C4. Fusionar solo los dos tableros que comparten carriles (`oscarhq`+`default`) | Menos sitios, migración pequeña | Medio | Opcional más adelante |

---

## 3. Benchmark (conocimiento general, sin consultar las webs hoy)

1. **Linear — estados por categoría + bandeja "Triage" + vistas que cruzan equipos.** Cada estado pertenece a una
   categoría (sin empezar / en curso / hecho / cancelado); una vista única junta varios equipos. Aplicable: C1/C2 —
   no hace falta unificar tableros, sí una vista que los cruce y una cola de triage visible.
2. **GitHub Projects/Issues — un proyecto abarca varios repos y las acciones se reflejan en el sitio.** Un tablero con
   campo "repositorio/proyecto" y vistas guardadas; al aprobar/pedir cambios, el hilo muestra el resultado en el
   propio sitio. Aplicable: A1/A3 (el aviso es una ficha con historial, no mensajes sueltos) y C3 como modelo a largo plazo.
3. **Jira — marca "impedimento" y reglas de automatización.** Un flag de bloqueado y reglas tipo "si pasa a bloqueado
   N veces, avisa al responsable con el historial". Aplicable: B2, casi literal.
4. (Patrón de chat, no de tablero) Los mensajes interactivos de Slack/GitHub-en-Slack se **actualizan en su sitio**
   tras pulsar: ya lo hacemos.
Height lo dejo fuera: no puedo afirmar con fiabilidad cómo trata esto.

## 4. Relación con t_bd182c83 (claims del hub)

La tarjeta no está en el repo, así que no puedo leer su alcance. Hay dos "claims" en el sistema: el de Hermes (reservar
una tarea para un runner, `claim --ttl`) y los de MigraTeam (propiedad de ficheros, `.coordination/claims/`).
Ninguna de las opciones de arriba toca ninguno: no cambian quién reclama una tarea ni quién posee un fichero. Solo si
t_bd182c83 va a cambiar los estados de la tarjeta (p. ej. añadir "reclamada") habría que reflejarlo en el aviso A1.
**Antes de implementar B2, mira que t_bd182c83 no haya cambiado cómo se cuentan los bloqueos.**

## 5. Plan por fases

| Fase | Qué | ¿Seguro y aislado? | ¿Necesita tu OK? |
|---|---|---|---|
| 0 | Leer (solo lectura) la historia de t_d5877cd1 y de un par de tarjetas con "botones tras resolver" para confirmar A y B | Sí | Sí, para leer el kanban real |
| 1 | A1 (aviso "Ya resuelto / Pendiente"), B1 (🧊 con historial), C1 (`personal` + `/estado` que incluya triage). Con tests con dobles | Sí: solo texto de avisos y lectura | Revisión del PR; rige al [🔁 Aplicar] |
| 2 | B2 (escalada automática a la 2.ª re-pregunta), B3 (texto de rol), A3 (una tarjeta viva con historial) | Probable con dobles, pero toca runner/avisos | **Sí, apruebas el diseño antes** |
| 3 | A2 (campo hecho/pendiente en el contrato del worker) | No: es contrato de seguridad | **Sí, expreso** |
| 4 | C3/C4 (unificar tableros), solo si C1 no resuelve | No: infraestructura crítica | **Sí, y prueba previa en copia de la base de Hermes** |

## 6. Lo que te pido decidir

1. ¿Autorizas la Fase 0 (leer la historia real de t_d5877cd1)?
2. ¿Vamos con la Fase 1 como carril aparte? (recomendado: sí; es lo que más reduce la confusión con menos riesgo)
3. ¿Dejamos la unificación de tableros aparcada (recomendado) y damos primero la vista unificada?
