# Signal Engine / Scraper + Consolas — estado

> Última actualización: 2026-09-24. Signal es el pipeline de ICP/prospección.

## Repos
- **Scraper** (`C:\Users\oscar\Scraper`) → remoto activo `origin` = icp-prospect-engine.
  Rama de trabajo: `cambio/multiverse-benchmark-harness`. Signal Engine, icp_pildora,
  icp_multiverse, consular_intel.
- **Consolas**: `oscar-console`, `pildora-signal-console`, `multiverse-console` (Vercel).
  **Miedo**: logins distintos, redirigen mal. Intención: unificar todo en una sola.

## Estado del signal
- 2 ICP de pildora (marketing_hiring, ia_automatizacion_hiring) ahora sí ejecutados desde el job
  discover (antes nunca se llamaban).
- Apollo enrich: por lotes (tramo a tramo) para controlar gasto por créditos. 2.101 empresas Píldora.
- Contador de créditos (H-092) usa `incrementar_contador()` del repo Scraper → api_tracker.
- El ref viejos: `evergreen` → evergreen-scraper (congelado). No tocar salvo decisión explícita.

## Problemas abiertos
- Consola principal redirige a logins sin credenciales disponibles.
- Visual de pildora-signal-console más rica que /signal — hay que aplicar 80/20 a la visual
  (wizard para crear ICP, runs, etc).
- Salida/trazabilidad de runs de Apollo y créditos gastados — ver dónde se ven los resultados.
- Runs automáticos en marcha (posiblemente pruebas) → parar y poner standby.

## Próximo paso
Unificar consolas; validar funcionamiento del signal tras el cambio de máquina (2026-09-23/24);
dejar todo en standby para empezar fresh.