# Lecciones del hub

Autoaprendizaje (decisión de Oscar 30-oct; `decisions/2026-10-30-fabrica-v3-borrador.md` §4.3-4.4). Una lección =
**un hecho comprobado + la regla que se deriva**. Fichero `L-NNNN.md`:

```
---
id: L-0002
fecha: 2026-11-02
origen: tarjeta t_xxxxxxxx (o incidente)
regla: "bloqueos.yaml: <qué patrón>"      # dónde vive la regla
estado: activa                            # activa | retirada | propuesta
carriles: [claude-hub]                    # opcional; sin ella vale para todos los carriles
---
# Título corto (una línea)

Hecho comprobado y regla, en pocas líneas. Sin secretos ni datos de personas.
```

Cómo funciona:
- **La retro propone** (cada lunes, `agent_lanes/retro.py`): un bloqueo sin regla que se repita ≥ `retro.umbral_propuesta`
  veces en `retro.ventana_dias` días sale en UNA tarjeta [DECISIÓN] para oscar, con tarjetas de evidencia, ejemplo sin
  secretos y la regla sugerida. **Nunca aplica nada**: aceptar = pedir a claude-hub que cree la lección y la regla.
- **La regla cita su lección**: en `bloqueos.yaml`, `leccion: L-NNNN`. Cada vez que casa suma un disparo en
  `.state/lecciones/disparos.json` (no va a git). Sin disparos en `lecciones.retirar_dias` días (60), la retro la
  propone retirar (estado → `retirada`, a mano).
- **Los workers las leen**: `worker.build_prompt` añade un resumen de las lecciones `activa` (tope
  `lecciones.resumen_max` caracteres). Sin lecciones no cambia nada. Un fichero con frontmatter inválido se ignora con
  un aviso en el log.
- Importar desde `~/.claude/memory/lessons.jsonl`: solo las que traten del hub o los carriles, revisadas a mano y sin
  datos personales, aprobadas por Oscar en el PR. No se copia el fichero entero.
