# Tema «Píldora · Liquid Glass V4» para el panel y kanban de Hermes

Viste `kanban.pildoradigital.com` (`hermes dashboard`, 127.0.0.1:9119, servido por el túnel `pildora-design`) con el
estilo del Design System V4 de oscar-hq: fondo violeta oscuro `#0a0613` con brillos ambientales, tarjetas de cristal
(gradiente diagonal + `backdrop-filter` + borde sutil), acento coral→naranja `#ff6a52 → #ff9a45`, Manrope/JetBrains Mono,
insignias de estado con la paleta V4. **No se modifica el código de Hermes.**

## Cómo se inyecta (vía elegida: tema de usuario de Hermes)

Hermes ya trae el mecanismo: cada `~/.hermes/dashboard-themes/*.yaml` es un tema de usuario con paleta, tipografía,
`colorOverrides` y `customCSS` (hasta 32 KB, sin sanear; ver `hermes_cli/web_server_dashboard.py`). Por eso no hace falta
proxy ni Worker: sin cambios de túnel, sin dependencias nuevas, y sobrevive a actualizaciones de Hermes (la carpeta es
del usuario). Descartadas: (b) proxy delante de 9119 (otra pieza viva y cambio de túnel) y (c) Cloudflare Worker
(HTMLRewriter) — solo valdrían si Hermes dejara de admitir temas.

Ficheros:
- `pildora-liquid-glass.css` — el CSS (variables `--pd-*` arriba; selectores `.hermes-kanban-*` estables del plugin kanban
  y elementos genéricos para el resto del panel).
- `pildora-liquid-glass.theme.yaml` — paleta, tipografía y `colorOverrides`.
- `instalar_tema.py` — une ambos y escribe `~/.hermes/dashboard-themes/pildora-liquid-glass.yaml`.

## Instalar (en el PC de Hermes)

```
py -3.12 tools/hermes-theme/instalar_tema.py --dry-run   # comprueba
py -3.12 tools/hermes-theme/instalar_tema.py             # instala/actualiza
```

Después, en el panel: selector de temas → «Píldora · Liquid Glass V4». Elegirlo desde la UI (o poner
`dashboard.theme: pildora-liquid-glass` en `config.yaml`) persiste en el `config.yaml` de Hermes: **requiere el OK de
Oscar**; el script nunca toca `config.yaml`. Para volver atrás, elegir otro tema o borrar el YAML instalado.

## Mantenimiento

- Tras actualizar Hermes, `py -3.12 -m pytest tools/agent-lanes/tests/test_hermes_theme.py` comprueba que cada clase
  `.hermes-kanban-*` del tema sigue existiendo en el `style.css` del plugin kanban.
- Los selectores genéricos (`aside`, `header`, `[role=dialog]`, `button[class*="bg-primary"]`) dependen del marcado del
  panel, que no se pudo inspeccionar en vivo desde el carril: ajustar tras verlo en el navegador.
- Modo claro: Hermes tiene un único tema por vez; este es oscuro (el V4 del dashboard de oscar-hq también arranca oscuro).
  Una variante clara sería otro YAML con la paleta `--background: #f6f4fc` de globals.css.
