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
- Cada decisión tiene un solo hilo: los avisos siguientes editan el mensaje anterior (o lo citan si ya no se puede editar).
- Si el runner estuvo parado y vencieron varios pasos, sale un solo aviso (el último vencido).
- Una decisión vetada (⛔) o ya aplicada por el plazo de veto no se vuelve a aplicar.
- Lo irreversible nunca se aplica solo.
- Pendiente (siguiente tarjeta): botón «Deshacer» en el aviso de aplicación automática.
