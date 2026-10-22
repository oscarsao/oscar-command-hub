# Borrador de respuesta a Angel — auditoría Píldora Digital (21-oct)
> Preparado por Hermes para que Oscar revise y envíe (o edite) — no enviado todavía.

---

Angel, gracias — es un trabajo serio y se nota el criterio técnico. Lo he leído entero (UX, fallos técnicos, documentación y MOC) y lo contrasté con el estado real. Resumen:

**1. Lo más grave coincide con lo que yo ya tenía visto, y lo doy por bueno:**
- Google OAuth sin restricción de dominio → cualquiera con la URL puede entrar.
- Webhooks de n8n sin autenticación → cualquier POST externo dispara automatizaciones.
- Riesgo de contaminación entre marcas en Brand Brain (RAG) si las queries no filtran por `brand_id`.
- API keys (Claude MAX, OpenRouter, Stripe) sin rotación ni alertas de gasto.
- Cron `run_dev_sync` corriendo en producción cada 10 min.

Añado dos tuyos que no tenía: 5 de 9 usuarios con rol Admin (sin mínimo privilegio) y 6 de 7 marcas con presupuesto de IA en $0 (los crews fallan silenciosamente ahí, no es un bug, es config). Voy a convertir estos 7 puntos en tareas reales para los carriles — esta semana.

**2. Un aviso importante: parte de tu auditoría quedó desactualizada en 24h.**
La hiciste el 21-oct por UI, sin acceso al código (lo dices tú mismo en el MOC: "pendiente de acceso al repositorio"). El 22-oct por la mañana migramos la infraestructura de Railway a un servidor propio (Hetzner + Dokploy, pildora-01). Así que:
- FC-08 (Cloudflare Tunnel de Open Design depende de un equipo local) — sigue siendo cierto en espíritu, pero ya no es "Railway o Vercel" la alternativa: ahora vive en el servidor propio, aunque Open Design está PARADO por una clave OpenRouter revocada en el mismo incidente.
- FA-08 (n8n sin aislar de Railway) y el "mapa de riesgos" (caída de Railway afecta todo) — ya no aplican tal cual: el backend está en Hetzner, no en Railway. MigraTeam es el único proyecto que sigue en Railway por ahora.
- El resto de tu análisis (OAuth, n8n auth, Brand Brain, keys, Admin, presupuestos $0, SQLite, LLM Router desconectado, etc.) sigue siendo válido tal cual lo escribiste — no depende de dónde corre el servidor.

**3. Lo que te pido:**
- Si tienes "pildora-digital-biblia.md" (la wiki que cita tu MOC con el Brief Ejecutivo CTO/CMO/CSO/CFO/COO), mándamela — no llegó con el resto del paquete y la quiero antes de cerrar el plan de acción completo.
- Tu roadmap de 3 fases (UX) y tus prioridades "esta semana / este mes / antes de 10+ usuarios" (técnico) me parecen razonables como estructura. Antes de que te pongas a implementar nada: lo que toca producción, fusión o despliegue en OscarHQ/Píldora o Signal lo puedes hacer tú directamente (tienes ese nivel), pero avísame primero en los 7 puntos de seguridad — quiero verlos uno a uno, no en lote.

Gracias por el nivel del trabajo — esto es justo lo que necesitaba de un CTO.

— Oscar
