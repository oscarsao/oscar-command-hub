# Handoff del integrador — 23-10-2026 (tarde)

Escrito porque el límite semanal está casi agotado. Retomar desde aquí.

## Publicado hoy en producción
- Píldora Digital: lote 2 (#301: Ajustes, crews, objetivos, cómo vamos, vigilante, peticiones; migraciones 20261046-48 y 50-53; traspaso de 35 almas), navegación (#308), P0 de aislamiento del Kanban (#313). Hub #115 con el runner reiniciado (5f2eb58).
- Variables: BLOG_PUBLISH_JOB_ENABLED=true en Dokploy OHQ; PD_SSO_LEGACY_GRACE_UNTIL en Vercel pildora-admin (redesplegado).
- MigraTeam: #261 (guardias de arranque) en Railway; #270 (blog sin jsdom; el artículo «escalar-red-franquicia…» publicado y en 200). En develop: #265 (RLS fase 1, sin aplicar), #268, #269, #272, #275 y el back-merge; CI en verde.
- MigraTeam en producción: limpieza Mosquera (3 plantillas y 9 notas) y bucket privado firm-library creado.

## En marcha (agentes y workflows)
- Workflow wuevg9a6h: chatbot de citas + recepción de correo común + intercambio de funciones. PRs sin fusionar.
- Workflow wnffp2o5x: MigraTeam LexCloud F1, F4a, F2 y F3 (develop, solo despacho QA). PRs sin fusionar.
- Agente de la base del DS (feat/ds-base): Radix, tokens y components/ds; estilo elegido Attio/Twenty.
- Agente del hotfix de cabecera (fix/cabecera-unica-marca): una sola cabecera de página, marca solo en el chip global, «+ Nuevo» y popovers en portal y opacos. La barra superior global se queda. Después, un 2.º PR con la barra superior configurable (5 accesos y 4 botones, en ohq_user_prefs).

## Decisiones de Oscar pendientes de ejecutar
- Enriquecer empresa: integrar feat/crm-enriquecer-empresa (27 commits + migración de 15 columnas: OK por migración) y después «Todo lo propuesto».
- Carriles por proyecto t_05c99181: integrar la parte A (lane/t_05c99181 aacee28) y crear las tarjetas B, C y D.
- Permisos CRM y propuestas: tarjeta creada, t_cabbcee1.
- Post-pago de MigraTeam: variables solo en Dokploy en el corte; remitente hola@migrateam.com.
- #310, tope de IA por persona: aprobado, con conflictos con master; integrar.
- Tareas (#303): aceptadas D18-D21 y D3; siguiente paso, construir F0 cuando el DS esté listo.
- Corte de MigraTeam a Dokploy: fin de semana por la mañana temprano; el viernes, bajar el TTL del DNS.

## Pendiente de Oscar
- Lanzar `! bash …/scratchpad/aplicar_equipo_y_plan.sh` (vincula a las 4 personas con su usuario y aplica el plan T4 de MigraTeam).
- Copiar los 2 contratos a firm-library (comando PowerShell con --apply).
- Rotar los tokens de GitHub, las claves de Meta (luego vaciar el campo config de ohq_brand_integrations) y el secreto del panel de Hermes.
- Comprobar si el AUTH_SECRET local coincide con el de producción (tokens de 8 h firmados en local).
- Clave de servicio ohq_sk_ para el hub (vigilancia y cambios-externos) en tools/agent-lanes/.env.
- Elegir la opción de cabecera de sección (https://claude.ai/artifact/NNLF7QkBymFrXoCKcDCzas).
- Decisiones de Finanzas (#304) y de RR. HH. (#307).
- Sentry DSN a Preview en Vercel migrateam.

## Avisos
- Errores de hermes kanban (timeouts y «read-only» transitorio) en el tablero oscarhq desde las ~17:09; si persisten, reiniciar Hermes.
- 5 worktrees con cambios locales sin limpiar: t_c8b8ff22, t_e27b1b5e, t_23f0ea55, t_b930f3d0 y t_609ef657. Revisarlos y empujarlos; NO borrarlos.
- next dev de navegación sigue en :3377 (PID 2212).

## Añadido 23-10 tarde
- PUBLICADO: base del DS #315 (e852cf58; cinta, PageShell y overlays en portal y opacos).
- Workflow de chatbot y correo terminado. PRs sin fusionar:
  - oscar-hq: #314 chatbot (migración 20261055), #312 correo entrante (sus migraciones 20261050, 53 y 54 CHOCAN con el lote 2 en producción: renumerar a 1056+), #306 spec, #305 intercambio de funciones;
  - MigraTeam (develop): #278 API del puente, #280 correo terminado (inbmail1023a cuelga de rlsfase1; falta db-migration-checker; arregla el P1 del Reply-To en producción), #273, #271.
- core.py de correo divergente entre repos (verify_svix_signature con los argumentos en otro orden): no copiarlo tal cual.
- Hotfix de cabecera: basado en feat/ds-base; conecta `PageShell tabs` y quita cabeceras y marcas duplicadas; después, el 2.º PR de la barra configurable.
- PUBLICADO: #316 (cabecera única, marca solo en el chip global, popovers opacos) y #317 (barra superior configurable, preferencia barra_superior). Vercel READY 94e5a862 y Dokploy done.
- Pendiente menor de #315: barra de scroll vertical en NavTabs (añadir overflow-y-hidden en ds/tabs.tsx).
- Incidente: un backend local con el .env de producción tuvo los schedulers encendidos ~1 h (check_new_leads, sweep_expired_pending_bookings). Si hubo avisos de lead duplicados, viene de ahí.
- Hay copias de .env en C:\Users\oscar\dev\_lanes\hotfix-cabecera\ (.env, dashboard\.env.development, dashboard\.env.production.local): las tiene que borrar Oscar.
