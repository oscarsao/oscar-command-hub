# Puerta de decisión

Herramienta de solo lectura que automatiza el **grupo A (contexto)** de la «puerta de decisión» de `~/.claude/CLAUDE.md` §7
antes de decidir solo. Escribe la «ficha» de una línea por criterio, lista para pegarla en la tarjeta o el PR.

```
py -3.12 tools/puerta-decision/puerta.py "texto de la idea o decisión" --nivel 1
py -3.12 tools/puerta-decision/puerta.py "texto" --nivel 2 --prs oscarsao/oscar-hq --ramas C:/Users/oscar/dev/oscar-hq --json
```

| Criterio | Qué comprueba | Fuente |
|---|---|---|
| A1 | No existe ya ni está en marcha | tarjetas abiertas de los 3 tableros (`hermes kanban list --json`), PR abiertos (`--prs`) |
| A2 | No se decidió lo contrario | `decisions/`, `pitches/` del hub y la memoria de `~/.claude` (`--docs` para cambiar carpetas); las palabras deben coincidir **cerca** (ventana de 8 líneas) |
| A3 | Sin tarjeta parecida reciente | tarjetas hechas en los últimos 30 días |
| A4 | No choca con ramas en curso | ramas locales y remotas de los repos de `--ramas` (≥ 2 palabras en comunes en ideas largas) |
| A5, B-G | Permisos, producto, seguridad, reversibilidad, verificación, personas, escalada | **a mano**: salen como lista (nivel 1: C y D; nivel 2: todos) |

Símbolos: `✓` pasa, `✗` bloquea (no se decide solo: se pregunta a Oscar con esta evidencia), `⚠` revisar, `n/a` no comprobable aquí
(fuente caída o criterio manual).

Salida: `0` sin ✗, `2` con algún ✗, `1` error de uso.

## Límites (a propósito)

- No decide nada: un `✓` solo dice «no encontré un parecido». El juicio sobre seguridad, datos personales o legal sigue siendo humano.
- La similitud es léxica (palabras y parecido de cadena), no semántica: una idea reformulada con otras palabras puede no aparecer.
- Si el kanban no responde (el SQLite de Hermes se bloquea un instante a veces), A1 y A3 salen `n/a`, nunca `✓`.
- Solo lectura: no crea tarjetas, no escribe ficheros, no llama a modelos ni gasta créditos. Los fragmentos pasan por un redactor de secretos.

## Pruebas

`py -3.12 -m pytest tools/puerta-decision -q`
