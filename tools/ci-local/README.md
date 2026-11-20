# Suite nocturna local de oscar-hq

Sustituye a GitHub Actions (que no se paga, decisión de Oscar 20-11-2026). Ejecuta pytest y vitest de `origin/master`
de oscar-hq contra un **clon temporal propio** (el repo original no se toca) y deja el informe en
`C:\Users\oscar\integrador-docs\ci_local\AAAA-MM-DD.markdown` con fecha, SHA, conclusión y fallos nuevos frente a la
línea base.

## Ejecución manual

```
py -3.12 C:\Users\oscar\oscar-command-hub\tools\ci-local\suite_nocturna.py
```

Opciones: `--rama master`, `--sin-vitest`, `--sin-pytest`, `--forzar` (ignora el aviso de RAM),
`--umbral-ram 85`, `--actualizar-base`, `--repo`, `--salida`, `--tmp`.

Conclusiones del informe: **PASA**, **SIN FALLOS NUEVOS** (hay fallos, todos ya en la línea base), **FALLA** (fallos
nuevos), **ERROR** (no se pudo ejecutar) y **OMITIDO** (RAM por encima del 85 % u otra pasada en marcha). Código de
salida: 0 = pasa/sin fallos nuevos, 1 = falla/error, 2 = omitido.

## Línea base

`ci_local\linea_base.json` guarda los fallos conocidos. La primera pasada la crea con los fallos que encuentre.
Para aceptar el estado actual como nueva base: `--actualizar-base`.

## Programarlo de noche (Task Scheduler) — lo registra Oscar, el worker no

Ejecuta en una consola normal (sin administrador) y se programa a las 03:00:

```
schtasks /Create /TN "OscarHQ-SuiteNocturna" /SC DAILY /ST 03:00 /TR "py -3.12 C:\Users\oscar\oscar-command-hub\tools\ci-local\suite_nocturna.py" /F
```

Para que se ejecute al encender el portátil si estaba apagado a las 03:00: en el Programador de tareas, propiedades de
la tarea → Configuración → «Ejecutar la tarea lo antes posible si se pasó una ejecución programada». Quitarla:
`schtasks /Delete /TN "OscarHQ-SuiteNocturna" /F`.

## Límites y garantías

- Un solo proceso de pruebas a la vez: candado `ci_local\.candado` (caduca a las 6 h) y pytest y vitest van en serie.
- Aviso de RAM: si al empezar supera el 85 % no se ejecuta y el informe queda como OMITIDO con el aviso.
- Sin secretos, sin Telegram, sin tocar producción ni `.github/workflows/`. Solo escribe en `ci_local\` y en su
  carpeta temporal (que borra al terminar).
- Requiere en la máquina: Python 3.12 con las dependencias de oscar-hq, Node y corepack (para `pnpm@10.18.3`).
  La instalación de dependencias del panel (`pnpm install --frozen-lockfile`) se repite en cada pasada.
