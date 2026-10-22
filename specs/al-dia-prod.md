# Tarjeta hija (b) de /al_dia: prod + «🛠 Resolver» + oferta proactiva

**Estado:** pendiente de crear en el kanban (carril `claude-hub`), **depende de t_a1e4e8cf** (tarjeta a, ya entregada:
diagnóstico + poner al día + integrar en dev). El worker de la (a) no puede crear tarjetas; el integrador la crea con
`Rama-origen: lane/t_a1e4e8cf` hasta que la (a) se fusione. Formato de la skill `tarjeta-carril`.

## Texto de la tarjeta
**Título:** [Hub] /al_dia prod + «Resolver» + oferta proactiva (hija de t_a1e4e8cf)

- **Origen y decisión de Oscar (22-10):** trocear /al_dia en dos tarjetas; esta es la (b). Umbral de rama atrasada = 10
  commits, en yaml (`al_dia.yaml`, ya existe).
- **Repo / base:** oscar-command-hub, `main` (tras fusionar la (a); mientras tanto Rama-origen `lane/t_a1e4e8cf`).
- **Alcance:**
  1. `prod` en `/al_dia` (`al_dia.py` ya parsea el destino; hoy `commands.cmd_al_dia` lo rechaza): doble confirmación
     como `/desplegar` (`ad_prod` → «¿Seguro?» → `ad_prod_ok`), solo Oscar o quien tenga `"*"` en `people.yaml`.
     Dokploy `application.deploy` (ya hecho para `/desplegar` en el commit 7bb4117) para oscar-hq/nextjobs/signal/
     OpenDesign/Postiz; Vercel para webs; MigraTeam Railway hasta su corte y develop→master por `/promover`.
     Migraciones SQL y variables de entorno: nunca automáticas; se listan con «pedir al integrador».
  2. «🛠 Resolver» por fallo (CI rojo, gate rojo, deploy error, health KO): crea la tarjeta de arreglo en el carril
     correcto con el log adjunto y prioridad alta, formato `tarjeta-carril`; idempotente por (fallo, SHA).
  3. Proactivo: `/hoy` (8:00) y el monitor ofrecen «🔄 Poner al día» ante deriva: PR listos >24 h, ramas ≥N commits detrás
     (`rama_atras_commits`), develop detrás de master, fusionado sin desplegar >24 h. Umbrales en `al_dia.yaml`.
     Una oferta por estado (clave = hash del diagnóstico en `.state/al_dia/ofertas.json`).
- **Hecho cuando:** tests de dev vs prod (doble confirmación, permiso denegado a quien no sea Oscar), «Resolver» crea
  tarjeta con log, oferta proactiva idempotente; suite en verde.
- **Límites:** sin tocar hooks ni contrato; sin reiniciar el runner (el `restart --drain` con smoke test lo hace Oscar o
  el integrador tras fusionar).
