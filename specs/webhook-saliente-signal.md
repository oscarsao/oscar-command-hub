# Spec-Lite · Webhook saliente por marca para Signal (Zapier / Make) (t_5a45fb49)

Pidió: Ángel. Modo OASP: Spec-Lite (1-3 días). **No es código del hub**: el código vive en `oscar-hq` (aceptar empresa, Signal/CRM) y, si el evento nace en el motor, en `icp-prospect-engine`. Esta spec es para pasarla a un carril `claude-oscarhq`. El hub no implementa nada.

## Objetivo
Cuando se **acepta una empresa** en Signal, enviar un POST JSON a la URL que cada marca tenga configurada, con un secret header, para que HubSpot, Pipedrive, Notion o Sheets reciban el lead sin intervención.

## Criterio de hecho (el de la tarjeta)
Al aceptar una empresa, el webhook configurado recibe un POST real con el JSON esperado y el secret header correcto.

## Diseño
1. **Config por marca** (todo desde la UI, no hardcodeado): tabla `brand_webhooks(brand, url, secret, activo, eventos, creado, actualizado)`. El secret se guarda cifrado o en el gestor de secretos del backend; nunca se devuelve entero por la API (solo los 4 últimos caracteres). Pantalla en el panel: URL, secret (generar/rotar), activar/desactivar, botón «Enviar prueba».
2. **Evento**: `company.accepted`. Payload JSON versionado:
   `{ "event": "company.accepted", "version": 1, "event_id": "<uuid>", "occurred_at": ISO8601, "brand": "...", "company": { id, name, domain, country, industry, size, score, source, contacts:[{name,title,email,linkedin}] } }`
3. **Cabeceras**: `Content-Type: application/json`, `X-Signal-Secret: <secret>` (lo que pide la tarjeta), más `X-Signal-Event` y `X-Signal-Delivery` (event_id) para idempotencia en el receptor. Opcional v2: firma HMAC-SHA256 del cuerpo en `X-Signal-Signature`.
4. **Entrega**: asíncrona (cola `ohq_signal_jobs` o tarea en segundo plano; aceptar la empresa nunca espera ni falla por el webhook). Timeout 10 s, 3-5 reintentos con espera creciente en 5xx/timeout, sin reintento en 4xx. Tabla `webhook_deliveries(event_id, brand, estado, http_status, intentos, error_corto)` para depurar desde la UI.
5. **Seguridad**: solo `https://`; bloquear destinos a IP privadas/loopback/metadata (SSRF) también tras resolver DNS; no seguir redirecciones; no registrar el secret en logs.

## Tests (sin red real)
- Servidor HTTP local de prueba que comprueba método, JSON y secret header (el criterio de hecho).
- Marca sin webhook o inactivo → no envía. Reintentos en 5xx, no en 4xx. Fallo del webhook no revierte la aceptación.
- SSRF: `http://`, `127.0.0.1`, `169.254.169.254` y redirección rechazados. Secret nunca en la respuesta de la API ni en logs.

## Fuera de alcance de esta tarjeta (decisión/acción de Oscar)
- **Plantillas Zap en el marketplace de Zapier** (Signal→HubSpot, Signal→Google Sheets): exige cuenta de desarrollador de Zapier, app publicada y revisión de Zapier; no es trabajo de un carril. Propuesta: primero Catch Hook genérico + guía de 1 página; la app de Zapier como fase 2.
- Make: sirve el mismo webhook (módulo «Custom webhook»), sin trabajo extra.

## Preguntas abiertas
- ¿Un webhook por marca o varios? (recomendado: uno en v1).
- ¿Solo `company.accepted` o también rechazada/actualizada? (recomendado: solo aceptada).
- ¿La app de Zapier entra ya o tras validar el webhook con un buyer real?
