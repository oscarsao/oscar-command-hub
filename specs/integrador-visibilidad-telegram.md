# Visibilidad del integrador en Telegram (t_5624611d) · Spec-Lite

**Problema.** Lo que el integrador hace desde Claude Code (merges de oscar-hq y MigraTeam, migraciones, semillas) no pasa
por `integrator.py` ni por el kanban, así que Oscar no lo ve en Telegram («nunca le pedí fusionar nada»).

**Alcance (reutiliza lo existente, sin canal nuevo).** Módulo `agent_lanes/integrador_visibilidad.py`; se muestra en
`/integrador` y en el parte de las 9:00 (`resumen_carriles`); avisos por el notificador del integrador.

## (a) Estado: `tools/agent-lanes/.state/integrador_estado.md`
El integrador lo reescribe tras cada paso, siempre igual. Cabecera opcional y un bloque `## ` por paso (el último es el más
reciente); las líneas o claves desconocidas se ignoran:

```
Actualizado: 2026-10-21T10:30:00+02:00
## Fusionar PR de oscar-hq
repo: oscar-hq
pr: 123
sha: abc1234def
estado: hecho | en curso | bloqueado | pendiente
siguiente: aplicar migración 0042 (necesita tu OK)
```
`/integrador` y el parte muestran los últimos `resumen_pasos` pasos y, si hay más, el botón **📖 Ver más**. Fichero ausente o
con más de `estado_max_horas` (24 h): una línea lo dice. Sin cabecera `Actualizado:` se usa la fecha del fichero.
(Formato único con la entrega A t_211c9902: si cambia allí, basta tocar `parse_estado`.)

## (b) PR de sesiones interactivas: `/encolar <repo> <PR>`
Valida el repo (lista configurable), lee el PR con `gh` y lo anota con su commit exacto en `.state/integrador_externos.json`.
Ficha con **✅ Dar OK a fusionar** / **🗄 Quitar**. El OK se ata al commit visto; si el PR cambia, no vale y hay que
repetir `/encolar`. Se ve en `/integrador` y en el parte de las 9:00. **Límite:** esta entrega registra y muestra el OK; la
fusión sigue haciéndola el integrador (consulta `ColaExternos.con_ok(clave, sha)`), no `integrator.py`, que va por tarjetas
del kanban. Sin botón de Oscar no se fusiona nada.

## (c) Incidencia de Railway
En `_deploy_railway_locked`, si Railway no responde: «🚧 Despliegue en cola por incidencia de Railway · repo · SHA» (una vez
por incidencia); al volver a responder: «✅ Railway vuelve a responder…». Mismo canal que las fichas del integrador.

## Configurable (regla de Oscar)
`tools/agent-lanes/integrador_visibilidad.yaml` con defaults: hora del parte, antigüedad máxima, pasos del resumen, tope de
«Ver más», repos encolables y textos de los avisos. Se relee en cada uso. **No hay panel ni comando de ajustes en
Telegram en el hub** (los demás ajustes, p. ej. `bandeja.yaml`, también son YAML); editarlos desde Telegram sería otra
tarea para todo el hub.

## Fuera de alcance
Fusionar PR externos desde `integrator.py`; sondeo propio de Railway (la normalización se detecta en el siguiente intento
de despliegue); que el integrador escriba el fichero (lo hace su sesión, siguiendo este formato).
