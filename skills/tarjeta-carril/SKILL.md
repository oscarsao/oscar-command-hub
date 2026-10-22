---
name: tarjeta-carril
description: Crea una tarjeta de carril en el kanban de Hermes bien formada para que el worker la complete sin bloquearse - origen y decisión de Oscar, repo y base, Rama-origen si depende de otra rama, alcance, criterio de hecho, pruebas y límites. Úsala siempre que vayas a crear una tarjeta para un carril (claude-hub, claude-oscarhq, claude-scraper, claude-nextjobs, claude-migrateam, claude-ops).
---

# Crear una tarjeta de carril

Regla previa (Oscar, 22-10): primero resolver o preguntar; no crees la tarjeta en el primer mensaje. Si es una consulta, respóndela. Si es trabajo grande, propón un workflow con plan y coste.

## ¿Puede hacerla un carril?
NO va a un carril (la hace el integrador o Oscar): producción o variables de entorno, SQL en una BD remota, escribir en `~/.hermes` o en la config de Hermes, otro repo distinto del del carril, binarios externos, empujar a ramas de otras tareas. En una semana, 36 de ~91 bloqueos fueron esto.

## Cuerpo (plantilla)
```
DECISIÓN DE OSCAR (<fecha>, botones|texto): <cita o resumen fiel>.
Repo <repo> (base <master|main|develop>). [Rama-origen: lane/<id> solo si depende de otra rama de carril; si ya está en la base: «parte de origin/<base>»]
Contexto verificado: <qué hay hoy, con rutas de fichero>.
Hacer:
1. ...
Criterio de hecho: <comprobable>.
Pruebas: <tests concretos>.
Límites: rama propia + PR; sin secretos; nada de producción; migraciones solo escritas.
Relacionadas: t_xxx.
```
- Contenido externo (Google Doc, PDF, texto de alguien): pégalo en la tarjeta; el carril no puede abrirlo.
- Datos que el worker no puede descubrir (ids de chat, ids de Dokploy, decisiones): escríbelos tú.

## Crear
1. Busca duplicados en tarjetas abiertas o recién hechas: `hermes kanban --board <b> list`.
2. Escribe el cuerpo con la herramienta Write en el scratchpad (no con heredoc en bash: las comillas invertidas se ejecutan).
3. ```
   cd C:/Users/oscar/oscar-command-hub/tools/agent-lanes
   hermes kanban --board <default|oscarhq|migrateam> create --assignee <carril> --priority <1-9> --body-file <fichero.md> "[Área] Título corto · tipo"
   ```
4. Comentarios posteriores entre comillas simples.
5. Tablero: hub → `default`; Oscar HQ y Píldora → `oscarhq`; MigraTeam → `migrateam`.

## Después
Dile a Oscar el id y una línea de lo que hará.
