# Pitch · Workflows desde los carriles («implementar-con-revision»)

Tarjeta `t_3cd20f03` · carril `claude-hub` · modo OASP: **Pitch** · estado: **aprobado por Oscar (21-oct)** con el corte F0→F1→F2→F3a (F3b opcional); guion del piloto copiado a
`specs/piloto-funnel-workflow.js.txt`; backend de preview elegido: **A (entornos de PR de Railway)**. **F0 implementada**
en este PR (`tools/agent-lanes/workflows/`); F1-F3 en tarjetas siguientes.
Autorización: Oscar aprobó la tarjeta `t_3cd20f03` (encargo vía oscar-bf, 21-oct) tras el piloto de Funnel Studio
(oscar-hq PR #223: ~37 min de spec a PR sin preguntas intermedias).

## 1. Problema

El piloto demostró que un guion de workflow (Spec → Implementar → Revisar con 3 lentes → Corregir + PR) entrega una
feature grande en una sola pasada. Hoy ese guion vive en el scratchpad de Oscar, está atado a un proyecto y se lanzó a
mano. Para repetirlo hace falta: plantilla genérica, que el runner la use cuando una tarjeta lo pida, que Hermes pregunte
todo al principio, y una entrega que permita probar **antes** de desplegar.

## 2. Appetite

**1-2 semanas, 4 fases con PR propio cada una** (cada una útil sola; si aprieta se recorta F3b, no F0-F2):

| Fase | Contenido | Appetite |
|---|---|---|
| **F0 Plantilla** | `tools/agent-lanes/workflows/implementar-con-revision.js` + README + validador de `args` + modo `--dry-run` | 2 días |
| **F1 Runner** | Ruta `workflow` en el runner, tras flag apagado | 3-4 días |
| **F2 Hermes** | Preguntas al inicio, regla «duda de diseño → opción configurable», tarjeta marcada `workflow` | 2 días |
| **F3 Entrega** | Aviso final con URL de preview y pasos de prueba; backend de preview por rama | 2-3 días |

## 3. Diseño

### F0 · Plantilla
- Guion parametrizado **solo por `args`** (nada de rutas ni nombres de proyecto en el cuerpo):
  `repo`, `remoto`, `base`, `worktree`, `rama` (`lane/<id>`), `ficheros_permitidos`, `ficheros_vetados`,
  `decisiones_oscar` (texto literal), `autorizacion` (tarjeta `t_xxxx` + decisión con fecha y texto), `spec_ref`,
  `test_cmd`, `modelo`, `atribucion_commit`.
- **Lección del piloto:** `autorizacion` es obligatoria. El validador rechaza el arranque si falta y el guion la inserta
  al principio del encargo de **cada** agente («Autorizado por la tarjeta t_xxxx / decisión de Oscar del <fecha>: "<texto>"»).
  El runner la rellena siempre desde la tarjeta (id + cuerpo con las respuestas).
- Fases: Spec → Implementar → Revisar (3 agentes en paralelo: corrección / seguridad / configurable-agnóstico, salida
  con esquema JSON de hallazgos `{severidad, fichero, línea, resumen, escenario}`) → Corregir + push + PR. Nunca master,
  nunca merge; solo push de `rama`.
- `--dry-run`: no lanza agentes ni toca git; imprime el plan por fase, los `args` resueltos (con secretos enmascarados) y
  los prompts. Es lo que se adjunta al PR de F1 como prueba.
- **Hecho en F0:** plantilla `workflows/implementar-con-revision.js.txt` (extensión `.js.txt` porque el integrador bloquea
  `.js`), README, validador Python `agent_lanes/workflow_plantilla.py`, `dry_run` y tests. Parámetros extra respecto al
  pitch: `encargo` (obligatorio), `reglas`, `lentes`.
- **Decisión sobre F3b:** al aprobar A, F3b (entornos de PR de Railway por proyecto, flag `backend_preview: railway_pr`)
  entra como entrega opcional; crear los entornos en Railway/Vercel lo hace Oscar.

### F1 · Runner
- Marca: etiqueta de tarjeta `workflow` (nombre configurable en `workflows_carriles.yaml`: `marca`, `activo: false`).
- Con `activo: false` (por defecto) el runner ignora la marca y la tarjeta va como worker simple: **cero cambio de
  comportamiento hasta que Oscar lo active**.
- Con `activo: true`: misma rama `lane/<id>`, mismo worktree, mismos guards; en vez de un único `claude -p`, ejecuta la
  plantilla con los `args` derivados de la tarjeta y del carril. Cierre idéntico: `lanes.py close` → review → PR con
  botones en Telegram.
- Fallo a mitad: tarjeta a `blocked` con motivo + ruta del log, **sin reintento automático** (marca `workflow_fallido`
  para que no se vuelva a coger en bucle). Sin secretos en logs (se reutiliza el enmascarado del runner).
- Tope de gasto/tiempo por workflow desde la misma configuración (reutiliza `workflows.yaml`: `tope_eur`).

### F2 · Hermes / Telegram
- Prompt/skill de Hermes: ante una petición grande, **todas las preguntas en un solo mensaje al inicio** (máx. 5, con
  A/B/C y ⭐ recomendada; formato de `telegram-decisiones-v2`). Regla: **una duda de diseño no se pregunta; se resuelve
  creando una opción configurable en la UI** y se anota en la tarjeta.
- Con las respuestas crea **una** tarjeta con la marca `workflow`; el cuerpo lleva las respuestas literales
  (= fuente de autorización que F0 inyecta).
- **Criterio de uso** (documentado en el hub y en el prompt): workflow para entregas grandes o varias tareas del mismo
  proyecto que comparten código; lo pequeño sigue por carriles normales. Umbral configurable: estimación OASP mínima
  (`Spec-Lite` por defecto, o `Pitch`) y/o nº de ficheros estimado (por defecto 6).

### F3 · Entrega con vista previa
- El aviso final (formato único de aviso) añade: **URL de vista previa**, **pasos para probar antes de desplegar** (sacados
  del PR/spec), y qué NO se puede probar en preview.
- **Limitación actual:** la preview de Vercel del panel por rama usa el backend de producción → probar con escrituras
  reales es arriesgado.
- **Opciones de backend de preview:**
  - **A. Entornos de PR de Railway** (⭐ recomendada): un backend efímero por rama con su BD propia (copia sin datos
    reales / seed), y su URL inyectada en la preview de Vercel como variable de entorno de *Preview* por rama.
    Pros: aislamiento real. Contras: coste por entorno, hay que configurarlo en Railway/Vercel (acciones de Oscar,
    el worker no toca env vars) y las migraciones deben poder correr sobre BD vacía.
  - **B. Preview solo-lectura:** la preview apunta a producción con un modo `read_only` (el backend rechaza escrituras
    si la cabecera/origen es de preview). Barato, pero no prueba flujos de escritura y exige cambio en cada backend.
  - **C. Documentar la limitación:** el aviso lista qué se puede probar sin riesgo (pantallas de solo lectura, formularios
    sin enviar) y qué esperar al desplegar.
- Propuesta: F3a = C (entra ya, el aviso siempre dice la verdad); F3b = A como entrega opcional, por proyecto, con flag
  `backend_preview: railway_pr` en la config de cada proyecto (agnóstico: sin nombres de proyecto en código).

### Configurable desde la UI (regla de Oscar 21-oct)
Todo en `tools/agent-lanes/workflows_carriles.yaml`, editable por `set_ajuste` desde Telegram/panel: `activo`, `marca`,
`umbral_oasp`, `umbral_ficheros`, `modelo`, `tope_eur`, `max_preguntas`, `lentes` (lista de las 3 lentes de revisión),
`backend_preview` (`ninguno` | `readonly` | `railway_pr`). Sin constantes de negocio en el código.

## 4. Rabbit holes
- Ejecutar el guion `.js` desde el runner Python: requiere el motor de workflows de Claude Code; si no está disponible en
  el servicio, F1 cae a un orquestador Python equivalente (mismas fases) — decisión a tomar al empezar F1.
- Los hooks de guard del worker (`contract/*guard*`) deben seguir aplicando a cada agente del workflow: **no se aflojan**.
- Coste: un workflow gasta varias veces un worker simple; de ahí el tope y el flag apagado.
- Backend de preview con migraciones Alembic: nunca contra la BD de producción.

## 5. No-goals
Merge o despliegue automáticos; tocar hooks/contrato; crear los entornos Railway/Vercel (lo hace Oscar); reintentos en
bucle; sustituir a los carriles normales.

## 6. Pruebas
Tests con dobles en `tests/` (sin red, sin Telegram, sin `hermes`): validador de `args` (falta `autorizacion` → error),
flag apagado → ruta simple, flag activo → ruta workflow, fallo → `blocked` sin reintento, umbral de uso, `--dry-run` sin
efectos. Salida del dry-run contra una tarjeta de prueba adjunta a cada PR.

## 7. Cómo se corta al aprobar
F0 → F1 → F2 → F3a (→ F3b opcional), un PR por fase, sin mergear.
