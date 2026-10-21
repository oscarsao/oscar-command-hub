# Spec-Lite · Workflows desde Telegram y cola autónoma del integrador (entrega C) · t_305a7734

Fuente: `pitches/seguimiento-peticiones.md` §4.6, §4.7, §5 (C1-C4). Tipo de contrato de la petición: `workflow`.

## Contrato
- `agent_lanes/workflows.py` (nuevo, sin red ni kanban propio): `Plan` → `Workflow` (estado en `.state/workflows.json`,
  patrón `resumen_carriles`). Estados: `preguntas` → `plan` → `aprobado` → `en curso` ⇄ `pausado` → `entregado` | `parado`.
- **C1** `propose(...)` valida ≤ `max_preguntas` preguntas y construye el plan (carriles, tarjetas, orden, riesgos, prueba,
  coste estimado + tope). `render_plan` → texto + botones `[✅ Aprobar plan y gasto] [✍️ Cambiar] [🗄 Aparcar]`.
  `launch()` NO crea ninguna tarjeta si el estado no es `aprobado`.
- **C2** `launch(wf, create_card)`: crea tarjetas con `req: req_<n>` (el registro de A1 lo aporta quien llama; aquí `req`
  es un dato recibido, no se inventa); máx. `max_tarjetas_activas` (5) activas y nunca más de `max_workstreams_codigo` (3)
  de código a la vez. Solo avisan atascos y decisiones; `render_entrega` = un mensaje final (qué se hizo, enlaces, cómo probar).
- **C3** `record_spend`: ≥ `aviso_pct` (80 %) → aviso una sola vez; ≥ 100 % → `pausado` + `[➕ Ampliar] [⛔ Parar]`.
  `ampliar` sube el tope (`paso_ampliar_eur`), `parar` cierra. Tope por defecto 8 €.
- **Configurable (regla de Oscar 21-oct)**: `workflows.yaml` con todos los números anteriores y `set_ajuste(clave, valor)`
  (validado) para editarlos desde Telegram/panel. Sin constantes de negocio en el código salvo los valores por defecto.
- **C4** `agent_lanes/cola_integrador.py`: lista cerrada de acciones. Automáticas: `montar_lote`, `pasar_gates`,
  `abrir_pr_lote`, `reabrir_lote`, `dejar_fuera_rama`, `reintentar_gates`, `cerrar_pr_integrado`, `escribir_integrado`,
  `actualizar_registro`, `escalar_2h`, `recordar_24h`. Nunca automáticas (piden OK con botones): `aplicar_migracion`,
  `resolver_conflicto`, `fusionar_main`, `desplegar_prod`, `borrar`, `tocar_hooks`. Cualquier otra → pide OK.
  `procesar(cola, ejecutores)` ejecuta solo las automáticas, sin sesión del integrador; una que falla no bloquea al resto;
  devuelve las peticiones de OK (con botones). Umbrales de escalado (2 h/24 h) también en `workflows.yaml`.

## Casos / tests (`tests/test_workflows.py`, `tests/test_cola_integrador.py`)
1. Sin aprobar → `launch` crea 0 tarjetas; aprobado → crea y enlaza `req`.
2. Más de 5 preguntas o tarjetas activas → rechazo / el resto queda en espera; tope de 3 de código.
3. Gasto 80 % avisa una vez; 100 % pausa; ampliar reanuda; parar cierra.
4. `set_ajuste` valida y persiste; yaml roto → defaults.
5. Cola: migración y conflicto nunca se ejecutan aunque haya ejecutor; fallo aislado; acción desconocida pide OK; escalado 2 h/24 h.

## Fuera de esta tarjeta / pendiente
- Cableado de `/workflow` y callbacks en Telegram y el montaje del registro `req_<n>`: esperan el contrato de A1 (tarjeta de A).
- Línea en `docs/traspaso/04-estado-y-deuda.md`: el paquete no está en `main`; se pregunta a oscar-bf.
