# Spec · NPS in-app tras la primera reunión agendada (Signal)

**Estado: SPEC, sin implementar (31-10).** Tamaño: Spec-Lite (1-3 días). Pidió: Ángel.
**Repo afectado: oscar-hq (Signal), NO este hub.** La tarjeta cayó en el carril `claude-hub`; el código debe hacerlo
el carril `claude-oscarhq`. Aquí solo queda la spec.

## Objetivo
Medir satisfacción en el mejor momento: justo tras la primera reunión que una cuenta agenda vía Signal.

## Criterio de hecho
Toast tras la primera reunión agendada de una cuenta; cada rango de score dispara su acción.

## Diseño
1. **Disparo**: al crearse una reunión, si es la primera de la cuenta (comprobado de forma atómica) y no hay fila
   previa en `nps_prompts` (account_id UNIQUE) → registrar el prompt y mostrar el toast en el siguiente render del
   usuario que agendó.
2. **Toast**: «¿Qué probabilidad hay de que recomiendes Signal a un colega?» 0-10, descartable (`dismissed_at`; no se
   vuelve a mostrar).
3. **Respuesta** `POST /api/nps` {score, user_id, account_id}: guarda en `nps_responses` (idempotente por cuenta) y
   ramifica:
   - 9-10 → pedir testimonio (texto + permiso de uso).
   - 7-8 → pedir feedback para el roadmap (texto libre).
   - 0-6 → alerta a Oscar por el canal de avisos existente de oscar-hq con cuenta, usuario, score y texto:
     «llamada de rescate esta semana».
4. **Migración Supabase** (`nps_prompts`, `nps_responses`, RLS por cuenta/marca): la aplica Oscar con la skill
   `migracion-supabase`.
5. **Tests**: primera vs segunda reunión; idempotencia; los tres rangos (límites 6/7 y 8/9); alerta solo en 0-6.

## Configurable desde la UI (regla de Oscar 21-oct)
Umbrales (6/7/9), textos de las preguntas y destinatario de la alerta, editables en ajustes; nada hardcodeado.

## Preguntas para Oscar
- ¿Quién recibe la alerta de 0-6: solo Oscar o también Ángel?
- ¿Dónde se guardan los testimonios (tabla propia o Drive)?
- ¿El toast se muestra a quien agenda o a todos los usuarios de la cuenta?
