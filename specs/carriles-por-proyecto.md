# Spec-Lite · Carriles por proyecto (varios repos por carril)

Tarjeta t_05c99181 · decisión de Oscar 23-10: «los carriles deberían ser por proyectos, no por repo».
Sustituye al alcance original (`claude-web`: NO se crea). Tamaño: **Pitch/Spec-Lite de varios días** (toca config,
runner, enrutado, integrador, detector de choques, avisos). No se implementa en esta tarjeta: espera el OK de Oscar.

> Nota: el texto de la tarjeta que recibió el worker llegó **truncado** (la lista de proyectos y el punto 3 de
> «trabajo compartido» se cortan). Los proyectos de abajo son los del título; confirmar los repos exactos.

## Decisiones ya tomadas (no reabrir)
1. Un carril por **proyecto**, con varios repos y repo elegido por tarjeta con `Repo: <clave>` (mecanismo `repos:` de
   `claude-telegram`, con valor por defecto).
2. **Píldora** = renombrar/ampliar `claude-oscarhq`: `oscar-hq` (por defecto), `pildora-website`, `pildora-recursos`, …
   Las webs usan Vercel: push a `main` = producción → el integrador trata la fusión como PRODUCCIÓN (doble
   confirmación + vista previa de Vercel enlazada en la tarjeta antes de aprobar). `forbidden_paths` para webs:
   `vercel.json`, `.github/workflows/`, `.env*`. `test_cmd` de cada web según su `package.json` (build + tests).
3. Proyectos del título: Píldora, MigraTeam, Signal, NextJobs, **Hub y Telegram**.
4. **Hub y Telegram** = un solo carril (`oscar-command-hub` por defecto + `oscar-hq` solo para el código de los
   bots) con la **plaza reservada** (`reserved_slots`) que hoy tiene `claude-telegram`. `claude-telegram` y
   `claude-hub` pasan a ser **alias** del mismo carril: las tarjetas abiertas no se rompen.
5. Trabajo compartido entre proyectos (CRM `ohq_crm_*` de Oscar HQ alimenta Signal y consolas; portal de cliente y
   Multiverse sobre Signal): una tarjeta tiene un proyecto **principal** (el carril que la hace) y puede declarar
   `Afecta: <proyecto/repo>…`; el detector de choques compara **entre proyectos**, no solo dentro de uno.

## Diseño propuesto
- `lanes.yaml`: claves nuevas (`pildora`, `migrateam`, `signal`, `nextjobs`, `hub-telegram`) y `aliases:` por carril
  (`claude-oscarhq`, `claude-scraper`, `claude-telegram`, `claude-hub`…). Un único resolvedor
  `config.resolve_lane(name)` que usan runner, integrador, clasificador, telegram y bandeja; el nombre canónico es el
  nuevo y los viejos se aceptan en entrada, nunca se emiten.
- `reviews:` y la sección del integrador (`claude-hub`, `claude-oscarhq`, `skip_health`…) se re-keyan por proyecto;
  la vista previa de Vercel y la doble confirmación son una propiedad del **repo** (`repos.<k>.deploy: vercel-auto`),
  no del carril.
- Mapa repo→carril del enrutado (clasificador) generado desde `repos:` de todos los carriles, para que citar
  `pildora-website` enrute a Píldora.
- `choques.py`: la huella de una tarjeta = (repo, rutas) del principal + los `Afecta:`; el solape se evalúa sobre
  todas las tarjetas abiertas de cualquier carril; aviso a Oscar cuando una tarjeta toca un esquema/contrato que otra
  de otro proyecto lee.
- Tests: configuración (alias, repos, forbidden_paths, test_cmd), enrutado (repo→carril, alias), plaza reservada,
  `Afecta:` y choque entre proyectos, fusión de web = producción.

## Orden de entrega sugerido (cada una un PR)
A. Alias + `resolve_lane` + renombrado de carriles (sin cambio de comportamiento) y Hub+Telegram unificado.
B. Píldora multi-repo (+ webs, marca de producción en el integrador).
C. Signal / MigraTeam / NextJobs como carriles de proyecto con sus repos.
D. `Afecta:` y choques entre proyectos.

## Riesgos
- Renombrar carriles toca el kanban vivo (assignees de tarjetas abiertas), `.env` (`INTEGRATOR_LANES`), hilos de
  Telegram y el estado del runner: la migración debe ser compatible hacia atrás y el reinicio `--drain` lo hace el
  integrador con smoke test.
- Mezclar `oscar-hq` (master) con webs (main) y repos con `base` distinta exige que `base` sea siempre por repo.

## Preguntas para Oscar
- ¿Signal incluye `Scraper` (icp-prospect-engine) y el portal de cliente/Multiverse, o van aparte?
- ¿Entrego A–D como cuatro tarjetas (recomendado) o todo en una?
- Confirmar el resto de repos por proyecto (el texto de la tarjeta llegó cortado).
