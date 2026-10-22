---
name: barrido-carriles
description: Revisa el estado de los carriles y del kanban de Hermes (runner vivo, tarjetas bloqueadas, en triage, en review, PR listos) y convierte cada pendiente en una acción o en una pregunta con botones para Oscar. Úsala al empezar la sesión del integrador, cuando Oscar pregunte «qué tengo pendiente» y después de cada bloque de trabajo.
---

# Barrido de carriles

Deber permanente del integrador (Oscar, 22-10): carriles andando, ninguna decisión pendiente sin botón, nada por integrar olvidado.

## 1. ¿Funcionan runner y monitor?
- `tail -20 C:/Users/oscar/oscar-command-hub/tools/agent-lanes/.state/runner.log`: actividad reciente y errores repetidos.
- Procesos vivos: `runner.py --all` y `tools/monitor/monitor.py --headless` (una sola instancia de cada).
- Si el runner está caído: smoke en primer plano antes de reiniciar (memoria: murió en silencio por un NameError tras `restart --drain`).

## 2. Tableros (default, oscarhq, migrateam, personal)
`hermes kanban --board <b> list --status <estado>` para `blocked`, `triage`, `review` y `running`. Por cada tarjeta:
- **Bloqueada con pregunta** → lee el último comentario y conviértela en pregunta con AskUserQuestion (recomendada primero).
- **Bloqueada por LIMITACION-GUARD** (producción, SQL remoto, otro repo, binario, ~/.hermes) → no la repreguntes: hazla tú como integrador si está aprobada, o reasígnala con el motivo.
- **Triage** → no sale con `unblock`. Resuelve la causa y usa `HermesCLI(board).requeue_triage(id, author=...)` de `agent_lanes/hermes.py`.
- **Review con PR** → `gh pr view`; si está en verde, propón integrarla (skill `integrar-lote`).
- **Review sin PR o de Oscar** (`· tuyo`, `· decidir`) → pregunta con botones o recuérdala en el resumen.
- **Esperando a otra** → no vuelvas a preguntar lo ya decidido; anota la dependencia.

## 3. Otros pendientes
- PR abiertos: `gh search prs --owner oscarsao --state open` y `--owner PildoraDigital`.
- Cola: `tools/agent-lanes/.state/integrador_cola.json`.
- Merges sin desplegar y migraciones sin aplicar.

## 4. Salida
- Una llamada a AskUserQuestion con las 4 decisiones más importantes; el resto en una lista corta.
- Actualiza `integrador_estado.md`: en marcha, bloqueado y por qué, pendiente de Oscar.
