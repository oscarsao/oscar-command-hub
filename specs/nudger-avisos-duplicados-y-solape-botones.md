# Spec-Lite · Nudger «Agente Píldora Avisos»: repetición, tarjetas de Oscar y solape sin botones

Tarjeta t_e31e4d9c · 22-10-2026 · Estado: **B APLICADA** en esta rama (OK de Oscar); **A pendiente** en una tarjeta para
`claude-oscarhq` (OK de Oscar: crearla, backoff 3 h y máx. 2 por tarjeta, configurable en el panel).
Nota B: «Son duplicadas» solo comenta en ambas tarjetas (no hay comando de triage en `HermesCLI`; no se mueve nada).

## Diagnóstico

**Dónde vive cada aviso**
- «Hay un carril libre. Propongo liberar «…». Riesgo medio: revísala antes.» [Liberar] [Ahora no]
  **No está en este repo.** Búsqueda en `tools/` (agent-lanes, monitor, telegram, hermes-config) sin ningún resultado
  para ese texto ni para «Liberar». Por `decisions/2026-10-15-mapa-reglas-sistema.md` (l.179) es el *Asistente PM
  proactivo* del bot del equipo (nube, Railway, `oscar-hq`): «Prepara ideas y Oscar libera (el riesgo bajo se libera
  solo, máx. 3 flujos)». El fix de los bugs 1 y 2 hay que hacerlo ahí, en una tarjeta del carril `claude-oscarhq`.
- «🔀 Posible solape» **sí es del hub**: `tools/agent-lanes/agent_lanes/solapes.py` (`SolapeTicker`, enviado desde
  `runner.py:216` con `html=False`, es decir texto plano y sin teclado).

**Bug 1 (repite cada ~15 min).** Patrón compatible con un bucle PM sin memoria de «ya avisé»: el intervalo de ~15 min
coincide con un tick, y no hay estado por tarjeta. Contraste: `SolapeTicker` sí deduplica (clave = ids de las
tarjetas, `.state/solapes.json`), por eso el solape avisa una sola vez. Falta confirmarlo leyendo el código de oscar-hq.

**Bug 2 (tarjeta de Oscar tratada como carril).** `t_8ebb4dec` tiene `assignee: oscar`, es épica-contenedor y su único
hijo pendiente (`t_ab4b8676`) también es de Oscar. El candidato «carril libre» no filtra por assignee.

**Solape sin botones.** `solapes.aviso()` termina en «Decides tú» pero solo en texto. El falso positivo real
(t_37e8052e vs t_a7a11953) se resolvió a mano.

## Propuesta

### A. Nudger PM (carril `claude-oscarhq`; mismo diseño, a confirmar en su código)
1. **Filtro de assignee:** solo proponer liberar si `assignee` ∈ perfiles de carril válidos
   (`claude-oscarhq, claude-migrateam, claude-scraper, claude-nextjobs, claude-hub`). Leer la lista de `lanes.yaml`
   o de ajustes, no hardcodearla (regla «todo configurable desde la UI»).
2. **Excluir épicas-contenedor** (tarjetas con hijos no `done` que se cierran solas).
3. **Tarjeta de Oscar bloqueada esperando decisión:** recordatorio distinto («Tienes una decisión pendiente: …») sin
   botón [Liberar]; con [Abrir tarjeta] / [Ahora no].
4. **Dedupe + backoff por tarjeta:** estado `{task_id: {avisos, ultimo_ts, resuelto}}`. Reglas por defecto
   (ajustables en el panel): intervalo mínimo 3 h, máx. 2 avisos por tarjeta; tras el tope, silencio hasta que cambie
   el estado de la tarjeta. «Ahora no» silencia 24 h. Cualquier botón pulsado marca `resuelto`.

### B. Hub: botones en «Posible solape» (`solapes.py` + `runner.py`)
- Teclado bajo el aviso: **[🔀 Son duplicadas, fusionar]** · **[✅ No son duplicadas]** · **[⏸ Luego]**.
- «No son duplicadas» guarda la pareja en una lista de excepciones (`.state/solapes_descartados.json`) que
  `detectar()` ignora → no vuelve a avisar de esa pareja aunque cambien rutas o estados.
- «Fusionar» NO fusiona solo: deja un comentario en ambas tarjetas y pasa la segunda a triage para que Oscar/Hermes
  decida (el aviso ya promete «no se fusiona nada solo»).
- «Luego»: no hace nada; la clave ya está en `seen`, no repite.
- Reutiliza el registro de teclados de `.state/callbacks` y el despachador de acciones del runner (como `renotify`).
- Tests en `tests/test_solapes.py` con dobles: teclado presente, descarte persistente, `detectar` ignora excepciones.

## Preguntas para Oscar
1. ¿Apruebo aplicar **B** (botones en solape) ahora en este carril? Es independiente de A.
2. A debe hacerse en oscar-hq: ¿creo la tarjeta para `claude-oscarhq` con este diseño?
3. Valores por defecto del backoff: 3 h entre avisos y máx. 2 por tarjeta, ¿o prefieres otros?
