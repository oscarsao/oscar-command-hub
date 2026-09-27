> Archivado 2026-09-27: Sesión Maestra sustituida por carriles; ver decisions/2026-09-27-arquitectura-carriles.md

# Auditoría solicitada: sesión de orquestación multi-agente (24-25 sept 2026)

## Contexto para quien audite esto
Soy Oscar, solopreneur (Píldora Digital + MigraTeam + otros proyectos). Uso a Hermes (asistente vía Telegram, corre en mi Windows) como coordinador central que gestiona/lanza sesiones de Codex CLI y Claude Code CLI en paralelo para trabajar en varios repos a la vez. Quiero que audites cómo fue esta sesión de ~24 horas y me ayudes a mejorar tanto el proceso como la forma en que interactúo con Hermes.

## Arquitectura que se usó (24-25 sept)
- **Hermes**: corre en mi Windows, coordina todo, habla conmigo por Telegram.
- **~10-11 ventanas de trabajo simultáneas**: cada una una consola de Codex CLI o Claude Code CLI, lanzada manualmente por Hermes con `.bat`/`start`, cada una en su propio `git worktree` sobre el mismo repo o en carpetas de trabajo sueltas.
- **1 "Sesión Maestra"** (Codex, en un repo dedicado `oscar-command-hub`) pensada para coordinar las otras 9-10 ventanas via un archivo compartido `ORDENES_MAESTRO.md` — nunca llegó a cumplir ese rol del todo.
- **Kanban de Hermes** (board `oscarhq` combinado Oscar HQ + Píldora, board `migrateam` separado) como mapa de tareas — actualizado a mano por Hermes tras investigar, no automáticamente.
- **Mecanismo de comunicación Hermes→ventanas**: capturas de pantalla + IA de visión para leer el contenido, más teclado simulado (`SendKeys` de Windows) para escribir instrucciones — sin confirmación de entrega.

## Trabajo real completado en la sesión
**PRs fusionados** (repo `oscarsao/oscar-hq`):
- #10 — fix crew runner (timeout falso)
- #11 — diagnóstico crews + H-090/H-122 documentados
- #12 — sincronización Scraper→CRM + gate de outreach (kill-switch)
- #13 — B3.3/B3.4 formulario unificado lanzar/programar crews + fix H-126 (bug real: estado de ejecución mentía "triggered" en vez de reportar skip/fallo real)

**Otro trabajo:**
- MigraTeam: hallazgo MOSQ-02 promovido a producción (`master@3160ae6d`)
- Auditoría de 19 ramas sueltas en `oscar-hq`: 8 confirmadas como descartables (ya integradas en `master` vía merge `71c8676c`), borradas en local+remoto tras verificar
- Triaje de hallazgos técnicos H-094 a H-119 cerrado
- Propuesta comercial para cliente "tattoo" entregada
- 3 documentos de negocio para Píldora (propuesta comercial, pipeline de prospección, caso de éxito Multiverse) generados por una ventana de Codex
- Fix real de producción: dependencia `tavily-python` faltante en el worker de Railway (`ModuleNotFoundError` en producción desde hacía semanas, solo funcionaba en mi máquina local) — corregido, publicado, redesplegado
- Reactivación de 2 "Workers" de captación de leads (Signal Engine) que llevaban apagados por configuración (`is_active: false`) sin que nadie se hubiera dado cuenta

## Problemas reales encontrados (con causa raíz, no solo síntoma)

1. **Bug de foco de ventana silencioso**: la función Win32 `SetForegroundWindow` reportaba éxito sin cambiar realmente el foco de la ventana activa. Esto hizo que Hermes revisara repetidamente la ventana equivocada durante más de una hora, pensando que las demás estaban en silencio cuando en realidad sí trabajaban. Se corrigió con un truco de tecla Alt antes de forzar el foco, pero es un parche, no una solución robusta.

2. **Mensaje truncado sin aviso de error**: Hermes le envió a una ventana de trabajo (Píldora-Ventas) una instrucción larga vía teclado simulado. El mensaje se cortó a mitad de camino por un límite de compresión de contexto del lado de Hermes — la ventana se quedó 12+ horas sin saber qué hacer, con la carpeta de trabajo completamente vacía, y nadie se enteró hasta que se revisó manualmente la pantalla.

3. **Rama de trabajo real (~horas de auditoría) nunca publicada a GitHub**: existía solo en el disco local de mi máquina. Si la máquina hubiera fallado antes de que alguien lo notara, se habría perdido el trabajo. Se corrigió publicándola manualmente al notarlo.

4. **Kanban desincronizado de la realidad**: Hermes actualizaba las tarjetas del Kanban a mano después de investigar (no automáticamente), así que en varios momentos el tablero mostraba "nada en proceso" o "2 tareas en MigraTeam" mientras había 8-10 sesiones activas trabajando de verdad. Un board combinado (`oscarhq` = Oscar HQ + Píldora) generaba confusión adicional sobre por qué "no había nada de Píldora".

5. **Sin aviso proactivo**: Hermes investigaba durante 1+ hora antes de reportar estado, generando la sensación de "silencio" cuando en realidad sí había actividad real. Los cierres/aperturas de tareas, fusiones de PR, y bloqueos no se comunicaban en el momento en que ocurrían, solo cuando yo preguntaba.

6. **Fusión de trabajo (a nivel prosa/análisis, no código) nunca escrita en el archivo compartido pese a pedirlo explícitamente**, hasta que se insistió una segunda vez y se encontró que se había escrito en un archivo local equivocado (una copia sin trackear del mismo nombre, dentro del worktree de la propia ventana, en vez del archivo real del repo `oscar-command-hub`).

7. **La "Sesión Maestra" nunca entregó el plan integral pedido** pese a estar "viva" (proceso corriendo) durante 11+ horas — no hubo forma de saber si estaba bloqueada, trabajando lento, o simplemente no priorizó esa tarea, porque la única señal disponible era capturas de pantalla ambiguas.

## Decisiones de negocio tomadas durante la sesión
- Compra de un mini PC (Minisforum AI X1 Pro-470, ~€1.729, 4 cuotas sin intereses) para correr túneles/scrapers/CI/self-hosted runner — explícitamente NO para hosting de clientes.
- Reactivación de los Workers de Signal tras corregir el bug de dependencia faltante.
- Kill-switch de outreach de Scraper-CRM se mantiene apagado — pendiente de decisión de negocio sobre cuándo reanudar envíos reales a leads.

## Lo que ya identificamos juntos como mejora (investigación previa a esta auditoría)
Encontramos que existen mecanismos nativos diseñados para esto que no estábamos usando:
- **Modo headless/no-interactivo** de ambos CLIs (`claude -p --output-format json`, `codex exec`): permite invocar una tarea como llamada de función con salida JSON estructurada garantizada (o error explícito), en vez de teclear en una ventana y esperar que "se vea bien" en una captura.
- **Git worktrees** por tarea (ya se usaba parcialmente, faltaba el hábito de publicar la rama a `origin` inmediatamente al crearla).
- **"Agent Teams" de Claude Code** (experimental): una sesión líder con lista de tareas compartida y buzones de mensajes entre "compañeros" — mismo patrón (estado en disco/socket, no en píxeles), pero limitado a Claude Code puro, no mezcla con Codex, y muere si se cierra la sesión líder.
- Idea propuesta (sin validar aún en producción): que Hermes lance las tareas cortas/acotadas en modo headless (sin ventana visible), y reserve las ventanas interactivas visibles solo para trabajo largo que realmente necesita "juicio sostenido" — actualizando el Kanban automáticamente con cada resultado en vez de a mano.

## Lo que quiero que audites y me ayudes a mejorar
1. ¿Es razonable este nivel de paralelismo (10+ sesiones simultáneas) para un solopreneur, o es mejor operar con menos sesiones pero mejor supervisadas?
2. ¿El diseño propuesto (headless para tareas cortas, interactivo solo para trabajo largo, Kanban como fuente de verdad automática) es sólido, o hay un enfoque mejor/más simple?
3. ¿Cómo deberíamos dividir responsabilidades entre Codex y Claude Code de forma más clara (hoy se usaron un poco intercambiablemente)?
4. ¿Qué reglas de comunicación (entre Hermes y yo, y entre Hermes y las sesiones de trabajo) evitarían los problemas de silencio/desincronización que tuvimos?
5. ¿Cómo preparo esto para correr en un mini PC dedicado (llega pronto) de forma que sea robusto 24/7, sin depender de que mi laptop esté encendida ni de capturas de pantalla?
6. Cualquier riesgo de seguridad/pérdida de trabajo que veas en este diseño que no hayamos identificado nosotros mismos.
