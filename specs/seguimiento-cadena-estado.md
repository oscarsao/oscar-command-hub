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
`aviso_texto(proyecto, titulo, estado, proximo, quien, req=None)` + `botones_aviso(req)` (Integrar, Dar seguimiento,
Aparcar, Explícame más) + pie «(o escríbeme aquí la respuesta)»; `resumir(texto)` devuelve (corto, tiene_mas) y «ver
más» muestra el resto sin recortar. Tema por proyecto: `tema_de(proyecto)`. Aplicado a `notices.render`,
`aviso` (stuck) y `resumen_carriles` añadiendo las líneas Estado/Próximo paso/Lo da.

## A4 Segundo parte de las 9:00
`DailyRequestSummary` junto a `DailyLaneSummary`: no repite detalle de carril (solo «ver carriles»), no sale sin novedad
desde ayer (huella del estado guardada), misma ventana 9–11 y un estado JSON propio.

## Tests (`tests/test_peticiones.py`)
Ids secuenciales y persistentes; validación de listas y de B1; vínculo/tipo parse; derivar (sin tarjeta, hecha,
esperándote, atascada, en curso); `/estado` texto y filtro; «ver más» sin recorte; formato de aviso; parte de las 9:00 con
reloj simulado (sale una vez, no sale sin novedad, no repite carriles).
