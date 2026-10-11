# Propuesta de diff · skill `oscar-multi-agent-orchestration` (Hermes)

Estado: PROPUESTA, no aplicada. Archivo destino (fuera del repo):
`C:/Users/oscar/AppData/Local/hermes/skills/autonomous-ai-agents/oscar-multi-agent-orchestration/SKILL.md`
Para aplicarla, Oscar (o una tarea de `claude-ops`) pega el bloque `diff` (inserta una sección tras la línea 225).

Qué consigue: cuando Oscar pide por el DM algo pequeño y ejecutable, Hermes crea la tarjeta para el carril adecuado
(con las líneas de confianza), contesta con su id y "te aviso al terminar", y nunca dice "no puedo" sin ofrecer la
tarjeta. Hermes sigue sin ejecutar nada (opción A del 01-10). El comando equivalente en el bot de Trabajos es `/hazlo`.

```diff
--- a/SKILL.md
+++ b/SKILL.md
@@ -224,6 +224,29 @@
   - Solo cuando Oscar la apruebe se convierte en tarea de carril.
 - **Lo que NO puedes hacer, dilo claro:** no editas `.env`, `config.yaml` ni código, y no despliegas. Si algo lo requiere (p. ej. añadir a alguien a `TELEGRAM_ALLOWED_USERS`), di que es un cambio manual de Oscar o una tarea para un carril. Nunca ofrezcas "¿lo hago yo?".
+- **Peticiones pequeñas ejecutables (01-10, opción A).** Si Oscar pide por el DM algo pequeño que un carril puede hacer (un arreglo, un typo, un ajuste de texto, un dato que consultar en el repo): no lo hagas tú y no digas "no puedo"; crea la tarjeta.
+  1. **Carril** (solo estos, nunca un perfil de Hermes): `claude-migrateam` (board `migrateam`), `claude-oscarhq` y `claude-scraper` (board `oscarhq`), `claude-nextjobs` (board `default`), `claude-hub` (board `default`; es el propio sistema: agent-lanes, monitor, bot de Telegram), `claude-ops` (board `default`; PC, cuentas, archivos). Si el texto no deja claro cuál, pregunta con `clarify` y tu recomendación.
+  2. **Prioridad alta** (`--priority 10`) y, en el cuerpo, una línea "Tarea pequeña: tope de 15 minutos de trabajo; si no cabe, devuelve `needs_input` proponiendo cómo trocearla".
+  3. **Líneas de confianza**, justo tras `Origen-Telegram` y solo si hacen falta: `Origen-Telegram: chat=6744452215 thread=0` (el DM, para que el resultado vuelva a Oscar); `Origen-Ops: <ruta>` / `Destino-Ops: <ruta>` en trabajo de `claude-ops` con archivos; `Rama-origen: <rama>` si el trabajo continúa sobre una rama que ya existe (el worker podrá traerla a su `lane/<id>`). Nunca inventes una ruta o una rama: si no la sabes, pregunta.
+  4. **`idempotency_key`** estable (`tg:6744452215:0:<slug>`), para que un reintento no duplique la tarjeta.
+  5. **Respuesta en una línea** con el id: "Hecho: t_xxxx en <carril> (prioridad alta, máx. 15 min). Te aviso al terminar."
+  6. **Nunca digas "no puedo" a secas.** Si lo pedido toca producción, dinero o borrar, no lo ejecutas ni se crea sin su OK: dilo y ofrece la tarjeta (o `[DECISIÓN]` para Oscar) con la propuesta. Desplegar y promover siguen siendo botones del Integrador, nunca tuyos.
+  7. Para consultar o actuar desde el móvil, remite al bot de Trabajos: `/hazlo <texto>` crea la tarea pequeña, `/estado [marca]` da el resumen en un mensaje.
 - **Resultados de lo pedido por DM:** cuando una tarea que Oscar pidió por DM termine (done/review/needs_input), avísale también en el DM con 1-2 líneas y el `t_id`, aunque el aviso detallado vaya al tema de la marca.
```
