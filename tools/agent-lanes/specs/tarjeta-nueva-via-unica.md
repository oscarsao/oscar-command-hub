# Vía única para crear tarjetas (`lanes.py tarjeta nueva`) — t_4379284f

Implementado en `agent_lanes/nueva_tarjeta.py`. Este documento deja el **texto propuesto para la skill/SOUL de Hermes**
(el cambio lo aplica el integrador con el OK de Oscar; aquí no se toca).

## Texto propuesto para SKILL.md / SOUL de Hermes

> **Crear tarjetas de carril: SOLO con `agent-lanes tarjeta nueva`.** Nunca con `hermes kanban create` ni escribiendo
> a mano la línea `Origen-Telegram`.
>
> ```
> py -3.12 tools/agent-lanes/lanes.py tarjeta nueva "<[Proyecto] Qué · paso>" "<cuerpo>" \
>     --solicitante <clave de people.yaml | user_id | oscar> --chat <chat> --thread <tema> --mensaje <id> \
>     [--carril X | --proyecto P] [--repo alias] [--prioridad N]
> ```
>
> - `--solicitante` es quien ESCRIBIÓ el mensaje (no quien lo reenvía). `--chat/--thread/--mensaje` son los del mensaje
>   original; sin ellos se usa el DM de esa persona.
> - Código 2 (vetada o datos mal): díselo a la persona con la frase que imprime la herramienta, sin crear nada. Una
>   persona solo pide tarjetas de las marcas de su `brands`; no la saltes cambiando de carril.
> - Código 3 (posible duplicada): enséñale la tarjeta existente; solo si la persona confirma que es distinta, repite
>   con `--aunque-duplicada`.
> - `--dry-run` enseña dónde caería sin crear nada.
> - La herramienta suscribe el aviso al solicitante (`notify-subscribe … --delivery-mode notify`): no avises tú a mano.

## Qué hace la herramienta
Solicitante (people.yaml) · `Origen-Telegram: chat= thread=` generado como primera línea + `Pidió: X` (la ficha del
aviso lo muestra) · veto de marca por persona · duplicadas (parecido de título, `triaje.yaml`) · carril por
`--carril`/palabras clave · `--repo` validado contra la lista blanca del carril · aviso al chat/tema de origen con
`hermes kanban notify-subscribe` (el runner conserva las suscripciones en modo `notify` al limpiar las automáticas).

## Reparar las 22 tarjetas con el origen mal (en seco)
`py -3.12 tools/agent-lanes/lanes.py tarjeta reparar-origen [--board X]` lista cada tarjeta con la línea de origen mal
escrita y la línea que propone. **No aplica nada**; aplicarlo requiere el OK de Oscar.
