# Auditoría de crews de Oscar HQ (2026-09-27)

Fuente: `ohq_execution` (Supabase `kkkxptlwknyafbavajhj`, últimos 30 días) + código `origin/master` (`AVAILABLE_CREWS`, `crews.yaml`, `src/crews/*.py`) + conversación de Hermes (`state.db`, desde el 24-09). Solo lectura.

## Por qué Oscar no tenía respuesta
Hermes dio dos estados opuestos: el 24-09 dijo "18 crews: 0 listos, 7 rotos, 11 zombies" y el 26-09 "12 crews operativos en producción". Lo segundo es falso según `ohq_execution`. Además, nunca devolvió los pendientes: H-090, H-122, las 5 decisiones del PR #31 y la prueba de crews.

## Estado real (22 crews)

| Estado | Crews |
|---|---|
| Funciona (uso manual) | content_factory, demo_follow_up, sales_pipeline_review |
| **Roto** | content_creation (marca success con hard timeout, llm_calls=0; era el único programado y está inactivo), brand_onboarding (sin runner, H-123; el wizard lo llama), ads_campaign_builder (nunca un éxito), video_production (sin saldo en fal.ai) |
| Fantasma | output_compactor (archivar) |
| Sin uso desde may-jun | analytics, strategic_review, full_business_audit, funnel_builder, design_system_migration |
| Nunca usados | content_curation, email_campaign, project_launcher, project_optimizer, project_creator, financial_review, incident_response, team_load_review, seo_geo, migrateam_outbound |

**No hay ningún crew programado.** La única recurrente (content_creation semanal) tiene `is_active=false`.

El documento del PR #30 (DIAGNOSTICO_COBERTURA_AGENTES_CREWS) no cuadra con la base de datos: marca email_campaign y content_curation como activos (tienen 0 filas) y output_compactor como fantasma (tiene 1 fila).

## Brechas frente a la visión de Oscar

| Área | Qué hay | Qué falta |
|---|---|---|
| CTO y equipo de desarrollo | Agentes, pero sin uso | No va por CrewAI: va por los carriles `claude-<repo>` (agent-lanes) |
| Marketing | Solo content_factory | Programación, verificación del output, contexto de marca |
| Ventas | 2 crews de uso manual | Revisión semanal programada; outreach de Signal pausado (t_b889c49a) |
| Finanzas | Nada en uso | El ledger (t_e14f89ad) y después un CFO real |
| Recurrentes que aprenden | Nada activo | Todo; se aborda en W5 (t_9ccfdf80) |
| Bots por rol | Persona por tema | Datos vivos y lanzamiento de crews: W4 (PR #37) |

## Tarjetas creadas (kanban de Hermes)
- **Carril `claude-oscarhq`:**
  - t_5ef35468: content_creation
  - t_caab2d8b: brand_onboarding (H-123)
  - t_0077852a: archivar output_compactor
  - t_b0f386a8: corregir el doc del PR #30
  - t_49d9ad5c: spec de sales_pipeline y demo_follow_up programados
  - t_f371a1a8: spec de incident_response
  - t_1af88ebf: spec de H-122
- **Decisiones de Oscar:** t_dbd74152. Incluye las 5 del PR #31, H-090, fal.ai, qué programar y con qué tope, y la prueba de ads_campaign_builder.
- **Peticiones sin rastro:**
  - t_53c184e6 (MigraTeam: tipos de documento, alerta de fuera de plazo, custom fields)
  - t_ffaf1078 (10 peticiones del 24 al 26-09)
