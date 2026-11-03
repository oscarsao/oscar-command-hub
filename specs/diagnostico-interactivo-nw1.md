# Spec-Lite · N-W1 Diagnóstico interactivo como imán de clientes

Tarjeta: t_eaae7edc · Origen: workflow N-W1 (aprobado por Oscar el 23-10; estado 25-10: «SIN PLAN»).
Repos afectados: `pildora-website` (landing/flujo) y `oscar-hq` (cuestionario con token #354, CRM). **Ninguno es este repo**,
y el carril claude-hub no puede escribir en ellos: esta spec es el entregable; la implementación va a tarjetas de carril propias.
Tamaño: Spec-Lite/Pitch (más de 3 días) → no se implementa sin OK de Oscar.

## Objetivo
Un visitante de pildoradigital.com responde un diagnóstico corto, recibe un informe por correo con su puntuación y entra
al CRM como lead con ese score. Sirve de imán de clientes tras W1 (Recursos).

## Lo que ya existe
- Cuestionario por enlace con token (Oscar HQ, PR #354, commit 63ad250).
- `pildora-recursos` #6: landing `/kit-captacion` (PR abierto).

## Lo que falta (según el estado del 25-10)
1. **Cuestionario con puntuación**: definir preguntas, pesos y tramos (bajo/medio/alto) en datos editables, no en código
   (regla de Oscar 21-oct: configurable desde la UI).
2. **Informe por correo**: plantilla por tramo, envío con Resend, enlace al informe y CTA a reunión.
3. **Lead al CRM con score**: crear/actualizar lead en Oscar HQ con score, tramo y respuestas; deduplicar por correo.

## Diseño propuesto
- Página `/diagnostico` en pildora-website (sistema de diseño de la web), que pide un token público de sesión a Oscar HQ
  y reutiliza el motor de cuestionario de #354 en modo «público con captcha» (Turnstile) en lugar de enlace nominal.
- Endpoint en Oscar HQ `POST /api/diagnostico/{token}/enviar` → calcula score, guarda respuestas, crea lead, encola correo.
- Migración Supabase solo escrita (tabla de configuración de preguntas/tramos y columnas `score`/`tramo` en leads), con
  número reservado por el integrador; nada aplicado.
- Consentimiento RGPD explícito antes de enviar el correo (casilla + texto legal revisado).

## Dependencias y orden
W1 (Recursos) en producción → #6 fusionado → tarjeta Oscar HQ (API + migración) → tarjeta web (página) → tarjeta correo.

## Preguntas para Oscar
1. ¿Diagnóstico para el público general de Píldora o por marca (MigraTeam, etc.)?
2. ¿Quién redacta preguntas y tramos (contenido del handbook)?
3. ¿Se puede leer el código de #354 para confirmar que sirve en modo público, o hace falta otro endpoint?

## Fuera de alcance
Pagos/suscripción (N-W2), despliegues, variables de entorno, envío real de correos.

## Criterio de hecho (por tarjeta futura)
Tests del cálculo de score; lead visible en el CRM con score; correo recibido en entorno de prueba; «cambio → URL exacta»
al entregar.
