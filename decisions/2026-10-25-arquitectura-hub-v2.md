# Decisión 2026-10-25: arquitectura del hub v2 y plan de implementación

> Resumen versionado del plan definitivo del hub (Fable, 25-10-2026) que Oscar aprobó con botones, más sus decisiones
> nuevas de la tarde del 25-10. El plan completo, la arquitectura y la auditoría están en los documentos de trabajo
> del integrador; aquí queda lo que cualquier agente necesita para no contradecirlo. Personas y permisos:
> `2026-10-25-personas-roles-permisos.md`. Sustituye como plan vigente a `2026-10-23-plan-workflows-deuda.md` en lo
> que toque al hub; sus reglas de trabajo (workflows, sin conflictos, pantallas + URL) siguen vigentes.

## Objetivo

Cerebro `hub-core` en pildora-01 (ampliado a 16 GB), workers de código **solo** en el PC de Oscar (suscripción Max),
kanban y personas en Supabase, tres bots con un papel cada uno, Hermes acotado, despliegue por botón desde el servidor
y latido externo. **Configurable y reversible**: `HUB_MODE=pc` vuelve a todo en el PC en ≤10 min si el cerebro en el
servidor cuesta demasiado o falla.

## Respuestas de Oscar que fijan el diseño

| Pregunta | Respuesta |
|---|---|
| Arquitectura | Cerebro en servidor + PC como worker, configurable y reversible |
| Servidor | pildora-01 ampliado a 16 GB (CAX31), no un servidor aparte |
| Kanban | Propio en Supabase (esquema `hub`), sustituye al SQLite de Hermes; 2 semanas de espejo |
| Personas | `ohq_people` en Supabase como fuente única; `people.yaml` se genera de ahí en la transición |
| IA del cerebro | `BRAIN_LLM=workers` (el worker del PC con Max; 0 € de API) |
| GitHub | Hub **privado sin Pro**: sin rulesets; la barrera es de credenciales (el `claude -p` del worker no tiene token; solo empuja `lane-worker`; solo hub-core tiene el token de merge) más detección de commits directos a `master/main` |
| Despliegue | Botón en Telegram, una persona con doble toque, integrador en el servidor, Dokploy por red interna, CI verde obligatoria |
| Conflictos | «Todo es tarjeta» con alcance, reservas de zona y de número de migración, `update-branch` antes del lote y fichas con botones en 🎯 Decisiones |
| Secretos | Tres capas: Bóveda de Oscar HQ (proveedores), Dokploy env (solo arranque) y Credential Manager del PC (token de worker + PAT). Sin Infisical |
| Latido | Uptime Kuma en pildora-01 + healthchecks.io (tras el job APScheduler del latido) |
| Orden | Primero kanban a Supabase, después cerebro al servidor |

## Fases

| Fase | Semana | Qué |
|---|---|---|
| F0 | 1 | Copias del kanban y de la config, rotación de lo expuesto, SSH sin root, hub privado, MFA, datos de Telegram en BD, Kuma, 16 GB |
| F1 | 1-2 | Plan de la auditoría en el runner (F1-02..07: siembra, resumen diario, lote con `update-branch`, latido del PC, guardas del worker), docs (F1-08), PR #135 recortado (F1-09), SOUL (F1-10), botonera estándar (F1-14) y **un solo reinicio del runner** (F1-13) |
| F2 | 3-4 | Esquema `hub` en Supabase, importador, conflictos por tarjeta, MCP de lectura para Hermes, Bóveda para el hub, página «Tarjetas» y `/salud` en el panel, corte del kanban |
| F3 | 5-6 | Portabilidad, CI del hub y de Oscar HQ, imagen de hub-core, `lane-worker` en el PC, despliegue por botón, corte del cerebro |
| F4 | 7 | Personas en Supabase (`hub.permisos`), usuario de María en el panel, página «Personas» |
| F5 | 8 | Limpieza (copias de `.env`, dashboard de Hermes, túnel), rotaciones programadas, mini PC opcional |

Migraciones reservadas en oscar-hq: `20261107` (latido, F1-01), `20261108` (F2-01), `20261109` (F2-10), `20261110`
(F4-01 `people_hub`), `20261111` (F4-03). `#373` conserva `20261104`; `#374` pasa a `20261106`.

Puntos de control: 11 botones de Oscar repartidos por fase y 12 acciones solo suyas (rotar claves, SSH, hub privado,
16 GB, healthchecks, notificaciones de Dokploy, energía de Windows, GitHub App y PATs, claves personales, usuario de
María, token de Vercel de lectura, mini PC).

Coste final estimado ≈ 23-25 €/mes (+12 €/mes frente a hoy, casi todo el servidor de 16 GB).

## Criterios globales de «hecho» (resumen)

1. Desde Telegram, Hermes no lista directorios, no lee variables locales y no ejecuta comandos **para quien no deba**
   (ver «terminal de Hermes» abajo: el caso de Oscar está pendiente).
2. El PC de Oscar se apaga sin que Trabajos, kanban, decisiones e integrador se paren; solo esperan las tarjetas.
3. María y Ángel aprueban con botones validados contra `hub.permisos`, con su identidad en la auditoría.
4. Despliegues de Oscar HQ desde el servidor por la API de Dokploy tras botón, con health verificado.
5. Ningún proceso de IA tiene credenciales de escritura a GitHub ni a producción; un commit directo en `master` se
   detecta y avisa.
6. Una sola fuente de verdad para personas, kanban, mapa de temas y claves de proveedor; lo demás es generado.

## Decisiones nuevas de Oscar (25-10, tarde) que el plan aún no recoge

### 1. Interruptores del hub en el panel (página «Hub»)

- Página **«Hub»** en Ajustes de app.pildoradigital.com, visible y editable **solo por el propietario** (rol de #373).
- Cada cambio pide **doble confirmación** y queda en un **registro** (quién, cuándo, valor anterior y nuevo), con
  vuelta al valor por defecto.
- Interruptores candidatos: `HUB_MODE` (pc/servidor), `KANBAN_BACKEND` (hermes/supabase), `BRAIN_LLM`
  (workers/api), `TELEGRAM_TRANSPORT`, carriles e integrador encendidos/apagados, `max_workers`.
- Base existente: `tools/agent-lanes/agent_lanes/ajustes_hub.py` ya guarda ajustes editables con auditoría
  (`ajustes_hub_auditoria.jsonl`), recarga en caliente y valor por defecto; hoy solo se editan desde el bot. La página
  del panel los leería y escribiría a través de hub-core (F3) con el token de servicio, nunca tocando `.env`.
- Propuesta de encaje (a confirmar al reprogramar el plan): API de interruptores en hub-core en F3 y página del panel
  en F4, tras `#373` (propietario). Si necesita tabla, se reserva el siguiente número libre de migración antes de crearla.

### 2. Hermes y Claude en los PCs de María y Ángel (F4-F5)

- Conectados a los datos de Oscar **por MCP con su clave personal** (A9: clave personal del panel, canjeada por JWT
  corto en hub-core), con sus marcas y niveles; nada de copiar `.env`, ficheros del PC de Oscar ni secretos.
- Opcional: su PC como **worker extra** (como el mini PC de F5-05): su propio login de Claude, `worker_id` propio, su PAT
  de push restringido a `lane/*` y `max_workers` repartidos por hub-core.
- Requisitos previos: `hub.permisos` (F4-01), MCP `hub-tools`/`ohq-read`/`repo-read` (F2) y `lane-worker` (F3).

### 3. Terminal del Hermes de Oscar: en revisión, pendiente de Oscar

- El plan (P1-A, F2-11) quitaba `terminal`, `file` y `browser` a Hermes para todos.
- Oscar ha dicho que **su** Hermes, que corre en su propio portátil, debería conservar la consola. Hay una propuesta de
  **dos perfiles**: el de Oscar con terminal (approvals manuales; `/yolo` y admin solo Oscar; secretos fuera de `.env`)
  y otro perfil o bot sin terminal para Ángel (lectura de sus adjuntos, repos por GitHub en solo lectura con veto de
  marca, API con su clave). Dónde va María (con Oscar o en el perfil sin terminal) es parte de la misma pregunta.
- **Estado: pendiente de confirmación de Oscar.** Hasta entonces nadie toca los toolsets de Hermes: ni la parte de
  herramientas de F1-10 ni F2-11. El resto de F1-10 (config de Telegram y SOUL, p. ej. el formato
  `RESPUESTA-<PERSONA> (id=…)` que exige F1-07) sigue siendo del integrador, con `.bak` y OK. El aviso
  `apply_hermes_config.py --check` («telegram: terminal, file, browser de más») sigue siendo solo informativo.

## Reglas permanentes

Confirmar merges a `main`/`master`, despliegues, migraciones, borrados y variables de entorno; nunca secretos en
commits; config y SOUL de Hermes con copia `.bak-<fecha>` y OK; un solo reinicio del runner por cambio, con prueba de
humo en primer plano.
