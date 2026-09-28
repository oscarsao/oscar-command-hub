# Decisión 2026-09-27: organización de Telegram (2 marcas, colaboradores, 2 bots)

Decidido por Oscar en la sesión de diseño con Claude. Complementa `2026-09-27-arquitectura-carriles.md`.

## Principios
- **El grupo define quién puede ver:** Gestión es privado; Marketing es compartido con Andrea y futuros colaboradores.
- **El tema define el frente de trabajo:** marca × función. Cada tema tiene rol, marca, audiencia, kanban de destino y **un único bot principal**.
- **Un mensaje, un bot que responde.** El bot secundario solo actúa si se le menciona. No se deja a dos LLMs "adivinar" quién contesta.
  - Por qué: Telegram entrega el mensaje a ambos bots. Si los dos deciden con un LLM, el coste se duplica, compiten en carrera y a veces responden los dos o ninguno.
- **La inteligencia vive en el bot principal**, que clasifica cada mensaje:
  - consulta → responde
  - trabajo → crea la tarea y confirma con el `t_id` (o con una reacción 👀 → ✅)
  - decisión → pregunta con opciones y una recomendación
- **Hermes nunca muestra razonamiento ("Reasoning:") ni fija mensajes en los grupos.**

## Gestión Píldora Digital (privado: Oscar + Hermes + bot de roles), nivel holding

| Tema | Rol | Marca | Bot principal | Kanban | Uso |
|---|---|---|---|---|---|
| General | CEO | Holding | Bot de roles | — | Resumen diario (8:00 L-V): decisiones pendientes, carriles, bloqueos |
| Operaciones · Código (t5) | CTO | Según repo | **Hermes** | Hermes | Encargar trabajo de código; eventos de los carriles (inicio/review/bloqueo/fin) |
| Negocio · Ventas (t6) | CSO | [MGT]/[PD] | Bot de roles | Píldora | Pipeline, Signal, propuestas |
| Marketing · Decisiones (t7) | CMO | Ambas | Bot de roles | Píldora | Estrategia, presupuesto, aprobación de gasto en ads |
| **Finanzas (nuevo)** | CFO | Holding | Bot de roles | Píldora | Cobros, Stripe, control financiero, LLC, gastos |

## Marketing Píldora Digital (compartido): ejecución

| Tema | Rol | Marca | Bot principal | Uso |
|---|---|---|---|---|
| General | COO | Ambas | Bot de roles | Avisos del equipo y dudas |
| Marketing · MigraTeam (t5) | CMO | MigraTeam | Bot de roles | Contenido: pedir piezas, lanzar crews, revisar |
| Marketing · Píldora (t6) | CMO | Píldora | Bot de roles | Ídem |
| Planificación y calendario (t7) | COO | Ambas | Bot de roles | Calendario editorial (se publica cada lunes); **solo aquí** se aprueba el gasto en ads |
| **Recursos (nuevo)** | COO | Ambas | Bot de roles | Enlaces fijados (Drive, Canva, guías de marca, banco de imágenes); el bot contesta "¿dónde está X?" |

**Autonomía de Andrea y colaboradores (decidido): autonomía en contenido.**
- Pueden lanzar crews de contenido y dejarlo listo para publicar sin pasar por Oscar.
- Solo el gasto en ads requiere el OK de Oscar en Planificación.
- Sus tareas van al kanban de Píldora.
- El bot de este grupo **no ve** finanzas, datos de clientes, código ni secretos.

## Cambios técnicos
1. `role_routes` pasa de `thread → rol` a `thread → {rol, marca, audiencia, bot_principal, kanban, alcance_datos}`. Hoy la marca es una sola por bot, y por eso el CEO de Gestión se presenta como "de MigraTeam".
2. **Context pack por rol y marca:**
   - estado de los kanbans
   - `CONTEXT.md` y `projects/`
   - ejecuciones recientes (`ohq_execution`)
   - para el CTO: deploys recientes en solo lectura
3. **Hermes en los grupos:**
   - principal solo en Operaciones · Código
   - en los demás temas, silencioso salvo mención o eventos de sus carriles
   - Mecanismo: `gateway.profile_routes` hacia un perfil de grupos con su propia config (verificar `require_mention` o equivalente en 0.21.4); si no existe, filtro en la skill.
4. **Bot de roles:** en los temas donde Hermes es el principal, responde solo si se le menciona.
5. **Permisos de Hermes:** hoy `TELEGRAM_ALLOWED_USERS` es solo Oscar. Si Hermes debe aceptar a Andrea, restringirlo al grupo de Marketing vía perfil. Pendiente: user_id de Andrea.

## Actualización 2026-09-28: roles (decisión de Oscar)

- **Gestión** (Oscar + María): asesores C-level por tema.
  - General → CEO; Ventas → CSO; Marketing·Decisiones → CMO; Finanzas (t148) → CFO.
  - Operaciones (t5 General, t230 MigraTeam, t231 Píldora) → Hermes.
  - María trabaja desde aquí: escribe, no ejecuta, y sus ideas pasan por Oscar.
- **Marketing** (Andrea): el **CMO en modo ejecución** en todos los temas, **a prueba**.
  - Produce contenido, calendario y crews de contenido.
  - Sin finanzas, clientes, código ni estrategia.
  - Escala presupuesto y posicionamiento a Gestión·Marketing-Decisiones.
  - Sustituye al COO en general, t7 y t54.
  - Andrea tiene un bot dedicado, pendiente de decidir si sustituye al bot de roles aquí.
- **DM** = solo Hermes. Los roles no van por DM.

## Pendiente de Oscar
- Crear en Telegram los temas **Finanzas** (Gestión) y **Recursos** (Marketing). El `thread_id` se detecta con `TELEGRAM_TOPIC_DEBUG` o desde `state.db`.
- Renombrar Marketing t7 a "Planificación y calendario" (opcional).
- Pasar el user_id de Telegram de Andrea, o que ella escriba un mensaje en el grupo y se detecta.
