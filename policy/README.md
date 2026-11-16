# Política por repo (borrador, 16-11-2026)

Una ficha `policy/<repo>.yaml` por repo que dice **qué se decide solo, qué se pregunta siempre y qué no se hace nunca** en
ese repo, y cómo se publica. Hoy es **documentación inerte**: ningún sistema la lee. Sirve para que las sesiones no
vuelvan a preguntar lo ya decidido y para que la puerta de decisión (`tools/puerta-decision`) pueda leerla más adelante.

Manda siempre `~/.claude/CLAUDE.md` (reglas duras §1, que no se pueden relajar aquí): una ficha solo puede **añadir** cautelas.

## Campos

| Campo | Significado |
|---|---|
| `repo`, `ruta_local` | Nombre en GitHub y ruta en el PC de Oscar |
| `rama_produccion`, `rama_staging` | Ramas; `null` si no existe |
| `despliegue.produccion` / `.staging` | Qué ocurre al fusionar o empujar (texto breve) |
| `proteccion` | Reglas de la rama de producción (PR, revisión, comprobaciones) |
| `decide_solo` | Cambios que una sesión puede hacer y avisar en una línea (nivel 1 de la puerta) |
| `pregunta_siempre` | Siempre con evidencia a Oscar (botón o el aviso del hook) |
| `nunca` | No se hace sin una decisión nueva y escrita de Oscar |
| `verificacion` | Cómo se prueba un cambio en este repo |
| `aviso_a` | A quién afecta lo que se cambia (vía Oscar) |
| `verificado` | Qué se comprobó, cuándo y cómo. Lo no comprobado va en `sin_verificar` |

## Estado

Borrador sin revisar por Oscar. Cada dato lleva su comprobación. Si algo cambia en un repo, se actualiza la ficha en el
mismo PR que lo cambia.
