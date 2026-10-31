# Recordatorios escalonados de decisiones (31-10-2026)

**Decisión de Oscar:** una decisión que bloquea trabajo avisa a su responsable; si nadie responde, recordatorios a 1, 2, 4 y 24 h.
Todo configurable desde los ajustes del hub. Tras 24 h sin respuesta: si la recomendada es reversible se aplica (siempre en una
rama `lane/*`, nunca master ni producción); si no lo es, se escala a María y luego a Oscar. Código: `agent_lanes/recordatorios.py`.

## Valores por defecto (Ajustes → Avisos; clave `decisiones.*`)

| Ajuste | Por defecto | Qué hace |
|---|---|---|
| `recordatorios_activa` | 0 | Interruptor general. Apagado = no cambia nada. |
| `escalera_horas` | 1, 2, 4, 24 | Horas desde que la decisión espera. |
| `escalera_destinos` | dueno | Quién recibe cada paso (`dueno`, `oscar` o clave de people.yaml); si faltan, se repite el último. |
| `duenos` | (vacío) | Pares `marca/persona`. Sin par: el operador de people.yaml con esa marca; si no, Oscar. |
| `silencio_inicio` / `silencio_fin` | 22:00 / 08:00 (Madrid) | Lo que cae dentro sale al terminar el silencio, no se pierde. |
| `max_avisos` | 4 | Tope de avisos por decisión. |
| `aplicar_reversible` | 0 | Con 1, tras el último paso se aplica la recomendada si la tarjeta es `Reversible: sí`. |
| `escalado` | maria, oscar | Quién recibe el aviso de una decisión irreversible al agotar la escalera. |

## Reglas
- Cada decisión tiene un solo hilo: a partir del segundo, cada aviso es una respuesta al anterior (Telegram notifica y el hilo se mantiene).
- Solo el último paso de la escalera aplica o escala; si `max_avisos` corta antes, la escalera se cierra sin aplicar ni escalar.
- Margen (`margen_aplicar_horas`, 1 h): no se aplica en el mismo tic en que se avisa si no hubo un aviso previo (tarjeta vieja al
  activar el ajuste, runner parado) o si el previo es más reciente que el margen; se avisa y se aplica pasado el margen.
- Si la aplicación falla, se avisa al responsable y se escala igual que una irreversible.
- Si el runner estuvo parado y vencieron varios pasos, sale un solo aviso (el último vencido).
- Una decisión vetada (⛔) o ya aplicada por el plazo de veto no se vuelve a aplicar.
- Lo irreversible nunca se aplica solo.
- El aviso de aplicación automática lleva «↩️ Deshacer» (reutiliza el Deshacer del texto libre: vuelve a bloquear la tarea
  mientras siga en `ready`; si el worker ya la recogió, lo dice y no toca nada). Solo aparece con el texto libre activo
  (`telegram.free_text` en lanes.yaml).
