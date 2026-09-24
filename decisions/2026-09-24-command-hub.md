# Registro de decisiones — Oscar Command Hub

Formato: un archivo por decisión `decisions/<fecha>-<slug>.md`. El más reciente arriba.

## 2026-09-24 — Crear Oscar Command Hub como cerebro compartido
Carpeta: **root** (este mismo repo).
- El contexto integral de Oscar vive en DOS lugares: memoria local de Hermes (compacta) **y** un
  repo nuevo compartido (`oscar-command-hub`) accesible desde cualquier agente que conecte
  (Hermes, Claude Code, Codex, OpenCode).
- Motivo: no depender de una sola sesión de Claude; poder ponerse al día desde cualquier punto de entrada.

## 2026-09-24 — Prioridad de tokens: Claude Max a los agentes de código
- **Claude Code** gasta de la suscripción **Max** (ya autenticado OAuth en la máquina).
- **Codex** NO consume tokens de Claude (OpenAI propio) → **dejarlo en pausa por ahora**, priorizar Claude Max.
- Hermes usa deepseek (OpenRouter) como base y actúa de asistente integral / orquestador.

## 2026-09-24 — Role de Hermes en el ecosistema
- Hermes = **agente de asistencia integral + coordinador**: enruta/consolida, vigila estado,
  presupuesto y RAM, responde a las sesiones de Claude.
- Las **decisiones finales y los deploys/merges a producción** los toma Oscar (dejarlo "allá por ahora").

## 2026-09-23/24 — Dominios Píldora
- pildoradigital.com = agencia. pildora.ai = plataforma (Oscar HQ). Rebranding a futuro.