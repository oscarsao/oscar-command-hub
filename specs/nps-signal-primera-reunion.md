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

## Decisiones de Oscar (31-10)
- **Destinatario de la alerta 0-6**: el dueño de la cuenta, y debe poder cambiarse en ajustes (no hardcodeado).
- **Implementación**: tarjeta nueva en el carril `claude-oscarhq` (esta spec es su entrada). El hub no toca código.

## Decisiones por defecto (reversibles, tomadas sin preguntar)
- Testimonios: tabla propia `nps_responses` (`testimonial_text`, `testimonial_consent`); nada en Drive.
- El toast se muestra solo a quien agenda la primera reunión.

## Tarjeta para `claude-oscarhq` (texto listo para pegar)
Título: «[Signal] NPS in-app tras la primera reunión agendada». Repo oscar-hq. Alcance y criterio de hecho: ver
`specs/nps-signal-primera-reunion.md` del hub (rama `lane/t_ff1045cc`), con la alerta 0-6 al dueño de la cuenta,
configurable en ajustes. Pruebas: los tres rangos, límites 6/7 y 8/9, idempotencia, primera vs segunda reunión.
La migración Supabase la aplica Oscar (`migracion-supabase`).
