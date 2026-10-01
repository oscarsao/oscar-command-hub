# Spec-Lite · /desplegar, /promover y /lote (pendiente del OK de Oscar)

Estado: NO implementado. `/hazlo` y `/estado` (y la ayuda de `/start` y `/ayuda`) ya están en la rama; esta parte
mueve producción y su diseño no existe aún en el integrador, así que se para aquí en lugar de inventarlo.

## Por qué no se ha implementado
- **No hay "doble confirmación" que reutilizar.** Hoy el integrador despliega con un botón de un solo toque
  (`int_deploy`, `int_merge_deploy`) y siempre sobre la ficha de una tarjeta fusionada (estado
  `.state/integrator/t_*.json`). No existe un deploy "de un proyecto" sin tarjeta.
- **No existe la promoción.** `develop → master` de MigraTeam (`release/<fecha>`, producción) no está automatizada:
  hoy es manual. Un `/promover` que despliega producción es lo que la regla de AUTONOMÍA obliga a confirmar.
- **"Lote del día" no está definido** en ningún sitio del repo (qué entra, quién lo cierra, qué orden).

## Propuesta
1. `/desplegar <proyecto>`: busca en el integrador las fichas fusionadas y sin desplegar de ese proyecto y muestra la
   ficha (`render_ficha`) con [🚀 Desplegar] → pide confirmar una segunda vez ("¿Seguro? Esto despliega <proyecto> a
   <staging|producción>" con [✅ Sí, desplegar] [✖ No]); el segundo toque llama al `int_deploy` existente. Sin fichas
   pendientes: "Nada que desplegar".
2. `/promover <proyecto>`: solo MigraTeam. Ficha con develop↔master (`health.migrateam_drift`), lista de PRs que suben
   y la misma doble confirmación. Acción: crear la tarjeta de promoción (`release/<fecha>`) para el integrador; el
   merge a master sigue siendo del integrador, nunca del bot.
3. `/lote <proyecto>`: monta el lote del día = tarjetas `done` aprobables del proyecto en orden de dependencias
   (`/aprobar` ya las ordena) + las fusionadas sin desplegar; un único mensaje con [✅ Aprobar lote] que NO fusiona
   por sí mismo: abre las fichas una a una.

## Preguntas para Oscar
- ¿Qué es exactamente el "lote del día" (propuesta de arriba) y quién lo cierra?
- ¿`/promover` puede lanzar la promoción a producción con doble toque, o solo preparar la tarjeta/PR para que la
  fusione el integrador? (recomendada: solo preparar; producción siempre con la ficha del integrador)
- ¿Quiero `/desplegar` para claude-hub (el equivalente es [🔁 Aplicar])?

## Riesgos
- Cualquier atajo de deploy desde el DM rompe la regla "producción siempre confirmada"; por eso la doble confirmación
  y el reuso de `int_*` sin lógica nueva de despliegue.
- Tamaño estimado: 1-2 días (Spec-Lite) con tests de dobles del integrador.
