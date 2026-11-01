# Decisiones de Oscar · 31-10-2026 (botones del chat)

## Fábrica v3 (D1–D8 del borrador 2026-10-30)
| # | Decisión | Respuesta de Oscar |
|---|---|---|
| D1 | Hermes con `terminal` | Sí en su DM; no en grupos ni para el equipo |
| D2 | María aprueba C1 de sus proyectos | Sí, con delegación explícita en `ohq_people` |
| D3 | C0 se fusiona solo a `develop` | Sí, con gates verdes y revisor sin objeciones (MigraTeam fuera) |
| D4 | Bots de equipo: suscripción o API key | API key para lo que atiende a otros; **debe poder cambiarse después** |
| D5 | MCP a clientes externos | No hasta F3 y con aislamiento probado |
| D6 | Decisión vencida sin respuesta | Ver criterio abajo |
| D7 | Conector CRM | Se usa el CRM interno de Píldora y webhooks ahora; integraciones (HubSpot, GoHighLevel) después |
| D8 | Extraer el núcleo como paquete | Sí, al cerrar F4, no antes |

### Criterio permanente D6 (palabras de Oscar, resumidas)
- Una tarea que necesita una decisión para avanzar lanza un aviso **etiquetando a la persona responsable**.
- Sin respuesta: recordatorio a 1 h, luego 2 h, 4 h y 24 h.
- Tras 24 h y varios avisos sin respuesta, avanza con la opción recomendada **solo si es reversible y siempre en una rama**.
- Todo **configurable desde la interfaz**, granular y según el estándar de la industria. Es lo que bloquea los trabajos.
- Implementación: t_0de1458e (sobre t_b0fc4283).

## Tarjetas bloqueadas
- t_f3a311c8 fuentes europeas: sí. t_7f334a78: solo BuiltWith ahora, tope de gasto por barrido. t_33418e40 rentabilidad de agencia: los 3 bloques.
- t_e15bd5bb: código ya; corrida piloto en t_4b9b6501. t_eef4074e: a claude-oscarhq. t_5e674245 HubSpot: aparcado (ver D7).
- t_4a0368d8 roles: Fase 1, conviven con editor/viewer. t_fff4d24a Sheets: sí con `drive.file`. t_2200be8f audit log: sí y migración ahora.
- t_5a45fb49 webhook saliente: t_c9d6890c. t_ff1045cc NPS: t_924a45cd, alerta 0-6 a Oscar por defecto, configurable por dueño de contacto/empresa/proyecto.
- MigraTeam: sigue excluido de lotes; las 4 tarjetas en `todo` esperan a sus padres (t_1d2d6b76, t_2cef2690).
