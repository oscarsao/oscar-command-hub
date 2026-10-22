---
name: verificar-bot-telegram
description: Diagnostica los bots de Telegram de Oscar (Avisos @PildoraD_BOT de Oscar HQ, Hermes @oscarsaoBot, carriles @pildora_carriles_bot) cuando no responden, responden doble, tratan mal el rol o la marca, o tras una migración o despliegue. Úsala cuando Oscar diga que un bot «no contesta», «contesta raro» o después de tocar webhooks, tokens o el servidor.
---

# Verificar bot de Telegram

Solo lectura salvo el arreglo final, que se pregunta a Oscar con botones. Nunca imprimas tokens ni secretos (compara huellas).

## Qué bot es cuál
| Bot | Corre en | Entrada | Dónde mirar |
|---|---|---|---|
| @PildoraD_BOT «Avisos Oscar HQ» | Oscar HQ en pildora-01 (Dokploy) | webhook `https://api.pildoradigital.com/api/telegram/webhook` | `ohq_telegram_configs` (Supabase OHQ), logs del contenedor oscar-hq |
| @oscarsaoBot (Hermes) | PC de Oscar | polling | `hermes gateway status`, `%LOCALAPPDATA%\hermes\logs\gateway-stdio.log`, `errors.log` |
| @pildora_carriles_bot (Trabajos) | runner de agent-lanes en el PC | polling | `oscar-command-hub/tools/agent-lanes/.state/runner.log` (líneas `tg-updates`) |

Ojo: `tools/agent-lanes/.env` `TELEGRAM_BOT_TOKEN` es el de **Hermes**, no el de carriles.

## Pasos
1. **Nunca** llames `getUpdates` en un bot con webhook ni en uno que ya hace polling: le robas los mensajes.
2. Avisos: `getWebhookInfo` → url, `pending_update_count`, `last_error_date`/`last_error_message`. Síntoma «todos salen como `ceo`/`migrateam` y se ignoran DMs del equipo» = la config por marca no se encuentra: compara la huella del `secret_token` registrado con el de `ohq_telegram_configs`. Arreglo (con OK): re-guardar la config de Telegram de la marca en el panel o `POST /api/telegram/config/{brand}`; desde el PR #252 `/set-webhook` ya usa el secreto de la BD.
3. Logs de Avisos: `ssh -i ~/.ssh/id_ed25519_hetzner root@2.28.20.203` → `docker logs --since 2h <contenedor oscar-hq> | grep -i telegram`. Busca `non-whitelisted chat`, `chat not found`, `403`, `429`, `can't parse entities`, y el rol/marca resueltos.
4. Hermes: `hermes gateway status`; últimas líneas de `gateway-stdio.log` y `errors.log`. `Blocked unauthorized user <id>` = falta en `TELEGRAM_ALLOWED_USERS` / `TELEGRAM_GROUP_ALLOWED_USERS`. Cambiar `.env` o `config.yaml` de Hermes requiere OK de Oscar, copia `.bak-<fecha>` y `hermes gateway restart`.
5. Carriles: runner vivo (proceso `runner.py --all`) y últimas líneas `tg-updates` del runner.log.
6. Respuestas dobles: en qué temas es admin cada bot y `role_routes`/`primary` de la config de Avisos; un tema = un bot que responde.
7. Prueba real: un mensaje de Oscar o de la persona afectada, confirmado en los logs. Un `people.yaml` válido no garantiza acceso.

## Informe
Bot · síntoma · evidencia (línea de log o campo) · causa · arreglo propuesto (botón) · cómo lo prueba Oscar.
