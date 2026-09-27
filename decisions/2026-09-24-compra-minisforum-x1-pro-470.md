# Decisión — Compra Minisforum AI X1 Pro-470 (mini PC infra interna)

**Fecha:** 2026-09-24
**Decisor:** Oscar Alcántara (COO, dentro de autoridad — compra puntual, no recurrente)

## Qué se compró
Minisforum AI X1 Pro-470 — AMD Ryzen AI 9 HX470, 64GB RAM, 1TB SSD.
Precio: €1.729,00. Financiado en 4 cuotas sin intereses (~€441/cuota, coste extra total €35).

## Para qué
Sustituir túneles, GitHub Actions y parte del hosting de gestión interna que hoy cuestan
~€50/mes en servicios cloud, y evitar límites de build/ejecución de los tiers actuales.

## Alcance explícito (decisión de riesgo)
- SÍ: túneles (Cloudflare Tunnel), scrapers, cron jobs, CI/CD self-hosted, servicios internos.
- NO: hosting de producción de clientes de Píldora Digital — eso se queda en Vercel/Railway
  (SLA y redundancia que un equipo casero no puede igualar; riesgo de reputación con clientes
  que pagan si cae la luz/internet de casa).

## Análisis de retorno
€1.729 vs ~€50/mes de ahorro directo = ~35 meses para pagarse solo por ahorro puro. La
justificación real no es el ahorro directo sino no quedar limitado por tiers gratuitos/starter
en momentos de carga alta (múltiples agentes Codex/Claude corriendo en paralelo).

## Notas
- El NPU (86 TOPS) es limitado para IA local seria — no reemplaza Claude/GPT para razonamiento.
  Tiene puerto OCuLink por si en el futuro se añade una eGPU dedicada.
- Checkout gestionado directamente por Oscar en minisforumpc.eu, Hermes no participó en el pago.
