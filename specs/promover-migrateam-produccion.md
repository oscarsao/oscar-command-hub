# Spec-Lite · Producción desde Telegram (Promover MigraTeam, Scraper, 🚀 Oscar HQ)

Tarea t_bc181313 · carril claude-hub · aprobado por Oscar el 01-10 · **estado: pendiente de OK de esta spec**.

Por qué es spec y no código: son 3 funcionalidades que mueven producción real (merge a master, Railway del Scraper,
`railway up` de Oscar HQ), con un flujo de doble confirmación y un modelo de seguridad nuevo. Es un Pitch de varios
días (OASP) y no cabe como cambio <1 h. El código vive sobre `integrator.py` (1534 líneas) y `decisions.py`.

## Piezas (en orden de entrega, cada una un PR independiente)

### PR-1 · Scraper en el integrador (el más pequeño, ~½ día)
- `lanes.yaml` → `integrator.lanes.claude-scraper`: `deploy: on_merge`, `risk` sin definir (= 🔴 PRODUCCIÓN) o
  `risk: production` explícito, `risk_label: SCRAPER`.
- Nueva verificación de deploy `deploy_check: github_deployments`: tras el merge, sondear
  `gh api repos/<repo>/deployments?sha=<merge_sha>` y el último `statuses` hasta `success` (o `failure`/`error` → aviso
  de fallo; timeout → aviso de "sin confirmar"). Solo lectura.
- Añadir `claude-scraper` a `INTEGRATOR_LANES` lo hace el coordinador (`.env`, no se toca aquí).
- Tests: fakes de `gh` con estados queued/in_progress/success/failure/timeout.

### PR-2 · 🚀 Oscar HQ fiable (~1 día)
- Desplegar la **punta de master** (hoy se despliega el commit fusionado concreto): `git fetch` + worktree limpio de
  `origin/master`.
- Migraciones pendientes (`supabase/migrations/` entre el último desplegado y la punta): el aviso las nombra una a una
  y ofrece [✅ Migración aplicada] (ya existe `migration_applied_cli`); el despliegue queda bloqueado hasta pulsarlo.
- Rechazo ("No desplegar") queda en el log de decisiones.
- El botón se manda como **mensaje nuevo** (notificación) además de editar la ficha.

### PR-3 · Promover MigraTeam develop → master (~2 días, el delicado)
Entrada: botón [🚀 Promover a producción] en 🚦 y `/promover migrateam` (solo Oscar). Máquina de estados persistida en
`.state` del integrador (nunca en el repo):

1. **Preparar PR**: `gh pr create/edit` develop → master (o `release/<fecha>` si el CLAUDE.md del repo lo exige; se lee
   de la config `promote_branch`). Solo `gh`; no se hace checkout ni se ejecuta nada del PR.
2. **Esperar checks**: los 4 obligatorios de la protección de rama (`gh api .../branches/master/protection` para leer
   los nombres, no hardcodeados). Cualquiera rojo → aviso y se para.
3. **Solo lectura de producción**: `alembic_version` de prod vs head del PR. Mecanismo a decidir (pregunta 1).
   Si no cuadra → **sin botón**, aviso al coordinador.
4. **Resumen**: commits, PR incluidos (`gh pr list --search`), migraciones (nombre + docstring), seeds, riesgos
   (archivos sensibles tocados).
5. **Confirmación 1** "Revisado" → **confirmación 2** "Sí, promover" (nombra las migraciones una a una si hay).
6. **Merge**: `gh pr merge --merge` sin `--admin`, sin bypass. Si la protección lo rechaza → aviso, nunca se fuerza.
7. **Verificar**: sondear `/health` de producción hasta `commit_sha == merge_sha` (timeout → aviso, no reintento ciego).
8. **Aviso final** con resultado y cómo revertir (`git revert -m 1 <sha>` vía PR; migraciones: nunca downgrade
   automático, se escala).

### Modelo de seguridad (común, auditado por security-auditor antes de entregar cada PR)
- Solo el `chat_id/user_id` de Oscar puede pulsar (se valida en el callback, no solo en el envío).
- `callback_data` firmado con HMAC (secreto del bot en `.env`) e incluye: acción, id de promoción, SHA del PR head y
  paso. Si el head del PR cambió desde el resumen → la confirmación no vale y se regenera.
- Confirmación con **caducidad** (propuesta: 30 min por paso, 2 h máximo de flujo) y de un solo uso.
- Nada del PR se ejecuta: ni scripts, ni tests, ni hooks; solo metadatos por `gh api`.
- Nunca `--admin`, `--force`, ni modificar la protección de rama. Todo queda en el log de decisiones.

## Tests
Fakes de `gh`, HTTP y Telegram; sin red, sin tokens. Casos: callback sin firma / de otro usuario / caducado /
reutilizado / con head cambiado; checks rojos; `alembic_version` distinto; con y sin migraciones; merge rechazado;
`/health` que nunca llega al SHA.

## Preguntas abiertas
1. **¿Cómo leemos `alembic_version` de producción en solo lectura?** Opciones: (a) endpoint `/health` o
   `/health/db` de MigraTeam que exponga la revisión (requiere cambio en el repo MigraTeam, otro carril);
   (b) `DATABASE_URL_READONLY` de un rol solo-SELECT en `.env` del hub; (c) `railway run` (no recomendado: ejecuta en
   prod). Recomendada: (a), y (b) como alternativa si no se quiere tocar MigraTeam.
2. **¿develop → master directo o `release/<fecha>`?** El CLAUDE.md de MigraTeam no está en este repo; hace falta
   confirmarlo. Recomendada: develop → master directo con PR único.
3. **¿Caducidad 30 min por paso?** Recomendada: sí.
4. **¿Entrega por PR separado (1→2→3)?** Recomendada: sí, así el Scraper y Oscar HQ no esperan al delicado.

## Fuera de alcance
Activar `INTEGRATOR_LANES`, tocar `.env`, reiniciar el runner, cambios en el repo MigraTeam, hooks del contrato.
