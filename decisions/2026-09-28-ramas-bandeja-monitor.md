# 2026-09-28 — Ramas por repo, bandeja única, monitor y plan para que el sistema se sostenga solo

Sesión de Claude Code (coordinador) con Oscar. Objetivo declarado por Oscar: "aquí solo corregimos lo que
hace falta para que el sistema lo haga mejor; necesito que allá (Telegram) funcione y pueda apoyarme de
manera integral aunque no tenga acceso a esta sesión".

## 1. Ramas: MigraTeam entra por develop, nunca directo a master
- **Hallazgo:** el carril `claude-migrateam` partía de `master` y hacía PR contra `master` (PR #90), y la
  política del Integrador ofrecía "Fusionar y desplegar a producción". Contradecía el CLAUDE.md de MigraTeam
  (develop = staging para todo el desarrollo; master = producción solo por `release/<fecha>`). Además
  `develop` iba 20 commits por detrás de `master`: los hotfixes del 26-27/09 (p. ej. #89) nunca volvieron.
- **Decisión (Oscar):** todo por develop.
- **Hecho:** PR #91 sincroniza master → develop (merge commit, no squash; 3 migraciones aditivas aplicadas
  en staging tras db-migration-checker GO y comprobaciones de solo lectura). Carril a `base: develop`, PR #90
  re-apuntado a develop, Integrador de MigraTeam con botón "🔀 Fusionar en develop (despliega staging)" y
  verificación de `commit_sha` en el `/health` de staging. Test candado en `tools/agent-lanes` que falla si
  vuelve a master. El monitor alerta si develop se queda por detrás de master (hotfix sin devolver).
- **Regla:** un hotfix directo a master se devuelve a develop en el mismo día.

## 2. Dependencias entre tareas (#10 no antes que #9)
`agent_lanes/deps.py`: el Integrador lee los enlaces padre→hijo del kanban (`hermes kanban link`) y no ofrece
Fusionar a un hijo hasta que el padre tenga `INTEGRADO`; lo recomprueba al pulsar. `/aprobar` ordena por
dependencias. Hermes debe enlazar dependencias al crear tareas. Arreglado de paso: los avisos
"INTEGRADOR: …" contaban como integración.

## 3. Telegram
- Un bot por tema: Hermes en Operaciones (t5/230/231); C-level en Dirección, Ventas, Marketing·Decisiones y
  Finanzas (t355); CMO en modo ejecución en Marketing (t74/77/80/83). Nombres, descripciones y guía fijada
  por tema: `tools/telegram/organizar_temas.py`.
- Los temas los crea el bot de Trabajos (`tools/telegram/recrear_temas.py`): Hermes 0.21.4 trata cada mensaje
  de un tema que él creó como respuesta a él y se salta `require_mention`.
- Bot de roles (Oscar HQ Avisos): no inventa estado, no se pone nombre de persona, deja `[DATO: …]` en piezas.
  Contexto vivo del kanban cada 15 min (tarea "Kanban snapshot Oscar HQ").

## 4. Hermes: fallos vistos hoy y reglas añadidas a su skill
Buscaba tareas solo en el board `default` y creaba duplicados (t_ba5085cc, t_3a4f024e); asignó trabajo
operativo a un carril de código; dijo que no había `develop`; repetía con `clarify` decisiones que ya tenían
botones. Reglas nuevas: buscar en los 4 boards con `board=…` y nunca crear por no encontrar; reparto por tipo
(código → `claude-<repo>`, operativo → `claude-ops`, solo-Oscar → `oscar`); ramas por repo; cabecera
"Para Oscar:" en cada tarea; no re-preguntar lo que ya tiene botones. La sesión larga del DM debe reiniciarse
(`/new`) para cargarlas.

## 5. Bandeja única de decisiones (workstream B, en curso)
Todas las decisiones también en el DM (hoy solo si la tarea se pidió desde el DM: 5 de 12 no llegaban);
`/decisiones` + recordatorios incluyen las tarjetas de decisión asignadas a Oscar; botones por defecto
[✅ Sí] [❌ No] cuando una pregunta no trae opciones; botón [💬 Explícame más]; campo "Para Oscar" en vez
del "Qué:" técnico.

## 6. Carril claude-ops (workstream C, en curso)
Trabajo operativo sin repo (vault Obsidian, disco E:, calendario, ClickUp, accesos de Andrea): workspace
propio, hooks que impiden borrar/configurar sistema, acciones externas o irreversibles solo con botón de
Oscar, verificación por evidencias y siempre a revisión.

## 7. Plan D — que el sistema se sostenga sin esta sesión
1. Alertas del monitor al DM de Oscar (las graves al momento) + comando `/salud`.
2. Carril `claude-hub` para este repo: los arreglos del propio sistema se piden desde Telegram.
3. Auditor diario: revisa logs, kanban (duplicados, asignaciones por tipo, bloqueos técnicos) y ramas, y
   propone arreglos como tarjeta con botones.
4. Reinicio ordenado del runner (drain, t_bdee05fe) para integrar sin cortar tareas.
5. Candado de repos: cada carril declara su repo de GitHub y el runner se niega si `origin` no coincide.

**Criterio de salida de las sesiones de Claude Code:** 3 días seguidos sin intervención y al menos un flujo
completo de cada tipo resuelto solo desde Telegram (código→PR→develop/staging, tarea ops, decisión con
botones, alerta→diagnóstico→arreglo).
