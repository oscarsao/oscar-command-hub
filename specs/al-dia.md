# Spec · /al_dia [proyecto|todos] [dev|prod]

**Estado: SPEC, sin implementar (22-10).** Tamaño: Pitch (≈3-5 días con tests). Por la regla OASP el worker no lo
implementa; espera el OK de Oscar a las preguntas de abajo. Petición de Oscar: un comando para poner todo al día,
fusionar y resolver, por proyecto o todos, dev o prod, con «Resolver» y ofrecido solo por el bot.

## Qué se reutiliza (nada se duplica)
| Paso | Pieza existente |
|---|---|
| Diagnóstico de lo que falta | `falta.py` (`falta_message`, `env_pending`) |
| Estado de PR / ramas | `prlisto.py`, `solapes.py`, `integration.py`, `health.migrateam_drift` |
| Poner al día ramas | `git_ops.sync_base` (merge limpio → push; conflicto → lo resuelve `atascos`) |
| Conflicto no trivial | `atascos.AutoResolver` (+ `is_append_only` para CHANGELOG/LEDGER) y `pr_branch_card_body` (tarjeta Rama-push) |
| Lote / gate combinado | `batch.BatchPlanner` |
| Desplegar con doble toque | `int_deploy` del integrador (`/desplegar`) |
| MigraTeam develop→master | `promote.Promoter` (`/promover`) |
| Permisos | `people.py` (`people.yaml`; prod solo Oscar o `"*"`) |

## Diseño
Módulo nuevo `al_dia.py` (lógica pura, dobles inyectables) + handler en `commands.py` + callbacks `ad_*` en `telegram.py`.
Un solo mensaje que se edita con el progreso; al final, resumen con botones.

1. **Diagnóstico** (`diagnose(project) -> ProjectState`): ramas `lane/*` y PR (aprobado / CI rojo / conflicto / detrás),
   tarjetas review/blocked/triage, develop↔master, fusionado sin desplegar, migraciones SQL pendientes, `falta`.
   Solo lectura, sin efectos.
2. **Poner al día** (automático, solo ramas de carril): `sync_base` de las `lane/*` atrasadas; limpio → push +
   re-verificación; conflicto → `AutoResolver` si trivial (lockfiles, inserción en CHANGELOG/LEDGER); si no, tarjeta de
   arreglo Rama-push y se dice en el resumen. Nunca toca `main`/`develop`/`master`.
3. **Fusionar**: lista de PR listos (verde + review aprobada + sin conflicto) con [✅ Integrar todo lo listo] y un botón
   por PR. «Integrar todo» monta el lote (`BatchPlanner`) y corre el gate combinado; MigraTeam sigue develop→master vía
   `/promover`.
4. **Destino**:
   - `dev`: oscar-hq (sin staging) = fusionar en la rama de integración y verificar sin desplegar; migrateam = develop +
     staging; nextjobs/scraper = fusionar sin desplegar.
   - `prod`: doble confirmación (`ad_prod` → «¿Seguro?» → `ad_prod_ok`), solo Oscar/`"*"`. Dokploy `application.deploy`
     (NO redeploy) para oscar-hq/nextjobs/signal/OpenDesign/Postiz; Vercel para webs; MigraTeam Railway hasta su corte.
   - **Migraciones SQL y variables de entorno: nunca automáticas**; se listan con [pedir al integrador].
5. **Resolver**: por cada fallo (CI rojo, gate rojo, deploy error, health KO) botón [🛠 Resolver] → tarjeta bien formada
   (formato `tarjeta-carril`) en el carril correcto con el log adjunto, priorizada. Idempotente por (fallo, SHA).
6. **Proactivo**: `/hoy` (8:00) y el monitor ofrecen [🔄 Poner al día] ante deriva. Umbrales en yaml (`al_dia:` en
   `lanes.yaml`): `pr_listo_horas: 24`, `rama_atras_commits: N`, `develop_atras: true`,
   `fusionado_sin_desplegar_horas: 24`. Una oferta por estado: clave = hash del diagnóstico en
   `.state/al_dia/ofertas.json`.

## Hallazgo a corregir dentro de esta tarea
`lanes.yaml` (líneas ~291-293) dice que oscar-hq ya corre en Dokploy (api.pildoradigital.com) pero su `deploy:` sigue
`railway_up`. `/desplegar` hay que adaptarlo a Dokploy `application.deploy` para los proyectos migrados antes de que
`/al_dia prod` pueda desplegarlos. Sería el primer commit.

## Tests previstos (dobles, sin red)
Diagnóstico con repos simulados en `tmp_path`; sync limpio vs conflicto; «Integrar todo» monta el lote; dev vs prod
(doble confirmación, permiso denegado a no-Oscar); «Resolver» crea tarjeta con log; oferta proactiva idempotente.

## Preguntas para Oscar
1. ¿Hago primero el cambio de `/desplegar` a Dokploy (commit aparte) o va dentro de `/al_dia`? (recomendada: aparte, primero)
2. ¿Troceo en dos tarjetas: (a) diagnóstico + poner al día + fusionar en dev, (b) prod + Resolver + proactivo?
   (recomendada: sí, menos riesgo y el runner se reinicia con smoke test dos veces, en pasos pequeños)
3. ¿Umbral de «rama atrasada» N = 10 commits? (recomendada)

## Riesgos
- Toca el camino de producción y el integrador: cada paso mutante reutiliza `int_*` sin lógica de despliegue nueva.
- El «restart --drain + smoke test» final lo hace Oscar o el integrador tras fusionar: este worker no reinicia el runner.
