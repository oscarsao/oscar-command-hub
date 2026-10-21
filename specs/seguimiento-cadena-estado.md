# Spec-Lite · Seguimiento de peticiones, entrega A (t_211c9902)

Fuente: `pitches/seguimiento-peticiones.md` (§4.1–4.4, §5 A1–A4). Código nuevo: `agent_lanes/peticiones.py`.

## Contrato compartido (A1; B y C lo consumen sin cambiarlo)
- Registro: `Peticiones(path)` sobre un JSON propio (`.state/peticiones.json`, escritura atómica; **no toca el kanban**).
  `{"siguiente": n, "peticiones": {"req_<n>": {...}}}`. Id `req_<n>` secuencial y estable (nunca se reutiliza).
- Campos: `id, proyecto, titulo, tipo, tipo_confirmado_por, urgencia, modo_oasp, origen, tarjetas[], creado,
  ultimo_movimiento`. Listas cerradas: `PROYECTOS` (Hub, OHQ, MigraTeam, Pildora, Signal, NextJobs, Scraper, Personal),
  `TIPOS` (bug|feature|workflow|null), `URGENCIAS` (P0-P3, solo bug), `MODOS_OASP` (fast-track|spec-lite|pitch, solo
  feature), `ORIGENES` (telegram|claude-code).
- Regla B1: un `tipo` solo se fija con `tipo_confirmado_por` (el bot propone, la persona confirma): si no, `ValueError`.
- API: `crear(...)`, `get`, `listar`, `vincular(req, tid)`, `fijar_tipo(req, tipo, por, urgencia=, modo_oasp=)`, `tocar`.
- Vínculo en la tarjeta: línea `req: req_<n>` en cuerpo o comentario (`vinculo_de(texto)`, `linea_vinculo(req)`); tipo en
  el cuerpo `Tipo: bug · Urgencia: P1` (`tipo_de(texto)`, `linea_tipo(tipo, urgencia)`).
- **Derivados, nunca guardados**: `derivar(req, shows, ahora)` -> `Estado(estado, motivo, paso, total, proximo, quien,
  desde)` con estado `hecha | en curso | esperándote | atascada`. Sin tarjeta -> `atascada (sin tarjeta)`.
  `INTEGRADO <sha>` en todas las tarjetas -> `hecha`. `shows` = `{tid: hermes show}` (task, comments, events).

## A2 `/estado`
`estado_view(...)`: una línea por petición (atascadas y recientes primero), filtro por proyecto, botón «Ver más» (cadena
completa, paginada a 4096 sin recortar). `/estado` conserva la vista previa por marca debajo.

## A3 Formato único de aviso (`formato_aviso.py` dentro de peticiones.py)
Implementado (no más):
- `notices.render` (todo aviso de tarjeta: runner, `aviso` stuck, recordatorio, `/atascadas`): línea de proyecto
  (marca · carril · req), estado + `· paso N/6` (`PASO_ESTADO`), `➡️ Próximo paso … · Lo da: …` (`next_step`, ahora
  también en el recordatorio y en `/atascadas`) y, si le toca a Oscar, el pie `PIE_TEXTO_LIBRE` tras los botones.
  `for_oscar` largo se resume y remite a «💬 Explícame más» (el «ver más» de las tarjetas).
- Avisos de PETICIÓN (`/estado`, parte de las 9:00): `aviso_texto`, `resumir`/`paginas` y el botón «💬 Ver más»
  (`botones_aviso`). En la entrega A `botones_aviso` es SOLO «Ver más»: Integrar, Dar seguimiento y Aparcar los cablean
  B y C sobre este esqueleto.
- Tema por proyecto: `TaskNotices(tema_de=tema_por_texto(peticiones, lanes))` usa el tema del carril del proyecto de la
  petición cuando el aviso no trae tema de origen. NO está conectado en `runner.py` (no-go de la tarjeta): conectarlo es
  una línea al construir `TaskNotices` en `LaneRunner`; pendiente del OK de Oscar.
- `resumen_carriles` lleva el pie de texto libre.
- Texto libre en tema de foro (t_154c3748): `free_text` ya cubre temas (misma ruta que el DM: comentario en la tarjeta y
  luego desbloqueo); novedad: un reply a un aviso decide a cuál responde cuando hay varios. La causa del «Clarification
  cancelled» en foros está en el gateway de Hermes: ver `docs/propuesta-clarify-foro.md`.

## A4 Segundo parte de las 9:00
`DailyRequestSummary` junto a `DailyLaneSummary`: no repite detalle de carril (solo «ver carriles»), no sale sin novedad
desde ayer (huella del estado guardada), misma ventana 9–11 y un estado JSON propio.

## Tests (`tests/test_peticiones.py`)
Ids secuenciales y persistentes; validación de listas y de B1; vínculo/tipo parse; derivar (sin tarjeta, hecha,
esperándote, atascada, en curso); `/estado` texto y filtro; «ver más» sin recorte; formato de aviso; parte de las 9:00 con
reloj simulado (sale una vez, no sale sin novedad, no repite carriles).
