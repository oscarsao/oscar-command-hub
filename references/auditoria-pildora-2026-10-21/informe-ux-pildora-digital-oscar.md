# Informe de Auditoría UX/UI — Píldora Digital
> Auditado por Ángel Granados con Claude Code · 21 octubre 2026
> Método: navegación de todas las secciones con lectura del árbol de accesibilidad y capturas de pantalla
> Destinatario: Oscar Alcántara

---

## NOTA DE MÉTODO

Los hallazgos marcados con ✅ se verificaron directamente en el navegador (texto visible o árbol de accesibilidad confirmado). Los marcados con ⚠️ son observaciones plausibles que requieren verificación manual porque no llegué a interactuar con ese elemento específico.

---

## RESUMEN

Se auditaron 14 secciones. Se identificaron **40 hallazgos** con la siguiente distribución:

- 🔴 Crítico: 7
- 🟠 Alto: 16
- 🟡 Medio: 15
- 🟢 Bajo: 2

---

## 1. SIDEBAR (navegación lateral)

### 🟠 #1 — "Resultados" está en el grupo Sistema, no en Contenido ✅
El ítem "Resultados" (outputs de crews: `/documents?tab=crews`) aparece agrupado bajo "Sistema" junto a Automatizaciones y Chat IA. Los crews generan contenido — un usuario que busca lo que produjo un crew lo busca en Marketing, no en Sistema.

**Propuesta:** Mover "Resultados" al grupo "Marketing y contenido", después de "Biblioteca".

---

### 🟠 #2 — Dos ítems de Signal en el menú con nombres que no se diferencian bien ✅
El menú muestra "Captación (Signal)" y debajo "Signal Hub" como ítems independientes bajo el grupo "Ventas". El paréntesis en el primero no deja claro la diferencia con el segundo. Un usuario nuevo entra al incorrecto.

**Propuesta:** Agrupar bajo "Signal ▾" con dos subítems: "Empresas" y "Reglas de routing". O al menos añadir una línea de descripción visible en el hover de cada uno.

---

### 🟡 #3 — El grupo "Configuración" tiene un emoji de texto `🛠️` en lugar de icono SVG ✅
El árbol de accesibilidad muestra `generic "🛠️"` como hijo del botón del grupo. Todos los demás grupos usan iconos SVG. El emoji se comporta distinto en modo claro y en algunas fuentes del sistema.

**Propuesta:** Sustituir por el mismo icono SVG de configuración que usa el resto.

---

### 🟡 #4 — "Nuevo Proyecto" aparece como link suelto después de "Proyectos" ✅
El árbol muestra `link "Nuevo Proyecto" [ref_82] href="/projects/new"` sin icono ni separación visual, debajo del link "Proyectos". Parece un ítem olvidado más que una acción.

**Propuesta:** Convertirlo en un botón `+` al lado de "Proyectos", igual que "Nueva brand" tiene su botón `+` al lado del selector de marca en el mismo sidebar.

---

### 🟡 #5 — El botón "Mostrar en desarrollo" tiene el número 31 sin contexto ✅
El texto visible es "Mostrar en desarrollo 31". El número no tiene explicación en ningún tooltip ni texto auxiliar. No queda claro si son 31 funciones, 31 elementos de menú ocultos o algo distinto.

**Propuesta:** Tooltip al hover que diga qué significa ese número. Por ejemplo: "31 herramientas ocultas: beta, WIP o en desuso".

---

### 🟢 #6 — "CEO AI" en el Dashboard vs "Chat IA" en el menú lateral ✅
El Dashboard muestra el widget con el texto "CEO AI · listo". El menú lateral llama al mismo destino "Chat IA". Son el mismo link (`/chat`) con dos nombres distintos.

**Propuesta:** Unificar a "Chat IA" en todos los contextos. Dentro de la pantalla ya se identifica el agente activo (CEO, CMO, etc.).

---

## 2. DASHBOARD (/)

### 🔴 #7 — "Calculando la semana…" con spinner que no termina ✅
El widget "Esta semana" mostró el spinner "Calculando la semana…" durante toda la sesión de auditoría. No hay estado de error ni fallback con datos previos si el cálculo falla.

**Propuesta:** Añadir timeout con mensaje de error explícito y opción de reintentar. Mientras tanto, mostrar los últimos datos calculados cacheados aunque no sean del momento exacto.

---

### 🔴 #8 — Actividad Reciente muestra solo `brand_id · success` sin nombre del crew ✅
Las entradas de actividad en el Dashboard muestran: `pildora_digital_inbound · success`, `migrateam · success`, etc. No hay nombre del crew ejecutado, hora ni enlace al resultado.

**Propuesta:** Mostrar al menos el nombre del crew. El formato mínimo útil sería: `[nombre crew] · [marca] · [hora]`.

---

### 🟠 #9 — KPI "Ejecuciones hoy: 182" tiene el sub-número "132 Corriendo" pero sin etiqueta clara ✅
El tile muestra 182 en grande y 132 con "Corriendo" debajo. No queda claro si 182 es el total del día y 132 las que están activas en este momento, o si son la misma cifra con distinto estado. El 50 restante (182 − 132) no tiene etiqueta.

**Propuesta:** Etiquetar los tres componentes: `182 total · 133 OK · X en curso`. Hacer el tile clickeable a un listado filtrado.

---

### 🟠 #10 — "HUECOS VS OBJETIVO 4" sin acción directa ✅
El widget muestra `Blog 0/1 · faltan 1`, `Email 0/1 · faltan 1`, `Instagram 0/2 · faltan 2`, `LinkedIn 0/3 · faltan 3`. El badge naranja "4" indica urgencia pero no hay ningún botón de acción junto a cada hueco.

**Propuesta:** Añadir un link "Crear" al lado de cada línea que lleve a `/content/new` con el canal preseleccionado.

---

### 🟠 #11 — El panel lateral derecho dice "Sin ejecuciones recientes" mientras el main muestra 5 entradas de actividad ✅
La sección "Actividad Reciente" del área principal lista 5 entradas (con marcas y estados "success"). El panel complementario de la derecha (`complementary [ref_162]`) dice explícitamente: "Sin ejecuciones recientes. Lanza un crew desde Operaciones." Las dos fuentes de datos no coinciden.

**Propuesta:** Sincronizar ambas secciones o eliminar el panel lateral si es redundante.

---

### 🟡 #12 — "Mis Proyectos" muestra 4 de las 7 marcas sin indicarlo ✅
El Dashboard muestra MigraTeam, NextJobs AutoApply, Marketing Dashboard y Píldora Digital. En el selector del sidebar hay 7 marcas. No hay texto que indique que la lista está truncada ni un "Ver todas →".

**Propuesta:** Añadir un link "Ver todas (7) →" junto al título "Mis Proyectos", o mostrar todas las marcas.

---

### 🟢 #13 — Botón "¿Qué hago hoy?" — comportamiento no verificado ⚠️
El botón existe en el Dashboard. No lo pulsé durante la auditoría, por lo que no puedo confirmar si tiene feedback visual al hacer click o si rellena el campo de chat automáticamente. **Requiere prueba manual.**

---

## 3. BRAND BRAIN (/brand-brain)

### 🔴 #14 — Secciones ⚠ Incompleto no tienen acción para completarlas ✅
Las secciones "Canales" y "Competidores" están marcadas como `⚠ Incompleto` pero no hay ningún botón ni link junto al aviso que lleve directamente a editar esa sección. El usuario tiene que saber dónde encontrar el formulario de edición.

**Propuesta:** Añadir un botón o link "Completar →" al lado de cada sección incompleta, que lleve directamente al campo correspondiente del editor.

---

### 🟡 #15 — El tab "Editor .md (avanzado)" es visible para todos los roles ✅
El tab está en primera línea visible junto a "Conocimiento", "Alimentar IA", "Brandkit" y "Avanzado". Un usuario con rol Editor o Viewer no necesita acceder al editor de Markdown técnico.

**Propuesta:** Moverlo dentro del tab "Avanzado" o restringirlo a rol Admin.

---

### 🟡 #16 — Los botones "Re-analizar" y "Exportar contexto" no tienen estado de progreso ✅
Ambos botones están presentes. No hay spinner ni indicador de que algo está ocurriendo al pulsarlos. No pulsé ninguno de los dos durante la auditoría, pero la ausencia de estado de carga es visible desde el árbol de accesibilidad. **El comportamiento al hacer click requiere verificación manual.**

---

### 🟡 #17 — La sección "CONFIG TÉCNICA (BRANDS.YAML)" aparece al final de la página para todos los usuarios ✅
El texto de la página incluye la etiqueta "CONFIG TÉCNICA (BRANDS.YAML)" como parte del contenido visible. No hay restricción de rol aparente.

**Propuesta:** Restringir a rol Admin o mover al tab "Avanzado".

---

## 4. CONTENIDO — PIEZAS (/content)

### 🟠 #18 — El tab "Más…" en la barra de filtros no se distingue visualmente de los otros tabs ✅
La barra de tabs muestra: Todos · Borrador · Aprobado · Programado · Publicado · **Más…** · Canal. El tab "Más…" parece un filtro más cuando en realidad es un menú oculto. No tiene ningún indicador visual (chevron, etc.) que lo diferencie.

**Propuesta:** Añadir un chevron ▼ al tab "Más…" o separarlo visualmente del resto con un `|`.

---

### 🟠 #19 — Tres botones de creación con propósitos solapados ✅
La barra de acciones tiene: "Generar artículo" / "Nuevo artículo (pegar contenido)" / "Crear contenido" (CTA naranja). Los dos primeros hacen cosas similares desde el nombre. El tercero, al ser el CTA principal en naranja, debería ser el punto de entrada unificado.

**Propuesta:** Consolidar en un único botón `+ Nueva pieza ▼` con un dropdown que ofrezca las tres opciones con descripción de cada una.

---

### 🟡 #20 — Las piezas programadas no muestran si tienen destino de publicación configurado ✅
Las piezas muestran canal (LinkedIn, Instagram, etc.) y fecha. No hay ningún indicador de si hay un destino de publicación web configurado para esa pieza. Dado que ninguna marca tiene destinos configurados (hallazgo #40), todas las piezas se van a quedar sin publicar sin que el usuario lo sepa.

**Propuesta:** Añadir un badge de estado junto al canal: `📤 Destino OK` / `⚠ Sin destino`.

---

## 5. SIGNAL (/signal)

### 🔴 #21 — "0 de 0 fuentes activas" sin explicación de qué son las fuentes ✅
La sección "Descubrimiento automático" muestra: `0 de 0 fuentes activas · Última: — nunca se ha lanzado`. El link "Ver cuáles y por qué" existe pero lleva a información adicional, no a la configuración. Un usuario nuevo no sabe qué es una "fuente" ni cómo activar una.

**Propuesta:** Convertir el link en un botón de acción que lleve directamente a donde se configuran las fuentes.

---

### 🟠 #22 — Signal tiene su propio selector de marca independiente del selector global del sidebar ✅
El sidebar tiene el selector global (MigraTeam seleccionado). Signal además tiene sus propios tabs: `Marca activa · MigraTeam · Píldora · Multiverse`. Hay dos controles de marca activos en la misma pantalla que parecen independientes. No está documentado si el selector global afecta a Signal o no.

**Propuesta:** Si son independientes, explicarlo con un tooltip. Si el de Signal ya sincroniza con el global, eliminar la redundancia.

---

### 🟠 #23 — La tab "Cuentas" en Signal no muestra las cuentas ✅
Al entrar a Signal > Cuentas, el mensaje es: "Para esta marca las cuentas viven directamente en el CRM. Abrir en el CRM". El usuario navegó a esta tab esperando ver sus 493 despachos y la pantalla lo saca de Signal hacia otro módulo.

**Propuesta:** Mostrar los despachos directamente aquí (aunque sea una vista resumida) con "Abrir en CRM →" como acción secundaria. O renombrar la tab a "Abrir en CRM" para que la expectativa sea correcta desde el menú.

---

### 🟡 #24 — Los lotes recientes no tienen nombre ni resultado legible ✅
La sección muestra: `Lote de 1 filas · Vista previa · 18 oct` y `Lote de 1 filas · Hecho · 18 oct`. No se muestra qué empresa o dominio se procesó ni cuál fue el resultado (enriquecida / error / duplicada).

**Propuesta:** Mostrar el nombre o dominio de la empresa del lote y el resultado por fila.

---

## 6. CRM (/crm)

### 🔴 #25 — Los KPIs en cero no distinguen "módulo vacío" de "sin actividad reciente" ✅
El CRM muestra CUALIFICADOS: 0, EN NEGOCIACIÓN: 0€, GANADOS DEL MES: 0€. Signal tiene 493 despachos y el CRM tiene 108 contactos nuevos esta semana. Los ceros podrían indicar que el pipeline de cualificación no se ha usado nunca, o que hay un problema de sincronización. Visualmente es lo mismo.

**Propuesta:** Distinguir con texto auxiliar: "Aún no hay contactos cualificados — revisa el flujo de Signal → CRM" vs simplemente `0`.

---

### 🟠 #26 — El tile "Respuestas Pendientes" no muestra ningún número ✅
El árbol muestra: `RESPUESTAS PENDIENTES · Ver · Abre la actividad filtrada por respuestas`. Todos los demás tiles tienen un número en grande. Este solo tiene "Ver".

**Propuesta:** Mostrar el conteo antes de "Ver". Si el conteo es costoso de calcular, al menos poner `—` en lugar de dejarlo vacío.

---

### 🟡 #27 — El tab "Listas e importación" es el más largo de la barra del CRM ✅
La barra tiene 9 tabs: Inicio · Contactos · Empresas · Pipeline · Actividad · Secuencias · **Listas e importación** · Citas · Ajustes. En pantallas de ancho medio el tab largo comprime los demás.

**Propuesta:** Renombrar a "Listas" y mover la acción de importar dentro de esa pantalla.

---

## 7. PROPUESTAS (/proposals)

### 🟠 #28 — El tab "Paquetes" — estado no verificado ⚠️
Al navegar a Propuestas, la página mostraba "Cargando propuestas…" en el área principal. No esperé a que terminara de cargar para ver si el tab Paquetes tiene un empty state o no. **Requiere verificación manual: entrar a Paquetes y confirmar qué muestra cuando no hay paquetes configurados.**

---

### 🟡 #29 — La barra de estado de propuestas tiene 7 tabs para una sección actualmente vacía ✅
Los tabs Todas · Borrador · Enviada · Aceptada · Pagada · Rechazada · Caducada ocupan espacio visual con cero propuestas reales. No es un error, pero con escala inicial genera ruido.

---

## 8. EMAIL MARKETING (/tools/email)

### 🔴 #30 — El módulo tiene 4 contactos (todos de prueba) sin ningún aviso de que está vacío ✅
La pantalla muestra `4 Total · 3 Activos · 0 Baja · 0 Rebotes`. Los contactos visibles incluyen `raquenaanden@gmail.com` marcado como Andrea, y otro con la etiqueta `[PRUEBA] Despacho`. No hay ningún banner ni mensaje que lleve al usuario a importar los 329 emails disponibles en Signal.

**Propuesta:** Banner fijo hasta que haya más de, por ejemplo, 20 contactos reales: "Tienes X empresas con email en Signal. Importar →".

---

### 🟠 #31 — El botón "Sincronizar CRM" no muestra cuándo fue la última sincronización ✅
El botón está presente pero no hay texto auxiliar con estado de la última sync (fecha, hora, cuántos contactos se añadieron).

**Propuesta:** Añadir debajo del botón: "Última sincronización: nunca" o la fecha y resultado de la última ejecución.

---

## 9. AUTOMATIZACIONES (/automatizaciones)

### 🔴 #32 — La automatización inactiva no tiene toggle en la vista de lista ✅
La tarjeta muestra: `Post-pago: Bienvenida despacho MigraTeam · payment.completed · **inactiva** · 1 acción · 0 disparos`. El estado "inactiva" está visible pero no hay toggle ni botón de activar en la propia tarjeta — habría que entrar al detalle para hacerlo. **No entré al detalle durante la auditoría; es posible que haya un toggle dentro. Verificar si el toggle existe en la vista de detalle.**

**Propuesta independientemente:** Añadir toggle on/off directamente en la tarjeta de la lista, igual que los crons tienen switch en su listado.

---

### 🟠 #33 — El campo de generación IA no tiene ejemplos ni guía ✅
El área de texto tiene como label "Describe la automatización que quieres" y un botón "Generar con IA". No hay placeholder con ejemplo ni sugerencias de qué tipos de automatización son posibles.

**Propuesta:** Añadir un placeholder con un ejemplo concreto y 2-3 botones de sugerencia rápida.

---

## 10. CHAT IA (/chat)

### 🟠 #34 — El selector de agente (CEO ▼) no tiene descripción de para qué sirve cada uno ⚠️
El selector está en la barra superior con el valor "CEO". No hice hover para verificar si hay tooltip con descripción. **Requiere verificación manual.**

**Si no hay descripción:** Añadir tooltip con el caso de uso de cada agente (CEO para estrategia y lanzar crews, CMO para contenido, CTO para temas técnicos, etc.).

---

### 🟡 #35 — El placeholder del input incluye "/ comandos" sin explicar qué son ✅
El placeholder visible es: `Escribe a CEO… ( / comandos, 🔗 adjuntar)`. Un usuario sin contexto técnico no sabe qué son los "/ comandos" ni cuáles existen.

**Propuesta:** Cambiar a algo como `Escribe aquí — escribe / para ver comandos disponibles` o añadir un botón de ayuda junto al campo.

---

## 11. CONFIGURACIÓN (/settings)

### 🟡 #36 — Dos selectores de modelo LLM en la misma página sin diferenciación clara de a qué afecta cada uno ✅
La página tiene "Configuración LLM" arriba (con su selector de modelo) y "Modelo LLM para agentes (CrewAI)" abajo (con otro selector). El bloque de abajo tiene una nota que dice "Este selector SÍ afecta a todos los agentes (CEO, CMO, copywriter, etc.)" — lo que implica que el de arriba no afecta a los crews. Pero la separación visual no lo deja claro a primera vista.

**Propuesta:** Añadir subtítulo explicativo a cada bloque: "Para el chat" / "Para ejecuciones de crews (controla el coste)".

---

### 🟡 #37 — Dos opciones distintas en Avatar Studio tienen el mismo label de display ✅
En el selector de imagen del Avatar Studio, el árbol de accesibilidad muestra:
- `option "Gemini 3 Pro Image (OpenRouter) — $0.04/img, ~12s" value="openrouter:flux-1.1-pro"`
- `option "Gemini 3 Pro Image (OpenRouter) — $0.04/img, ~12s" value="openrouter:gemini-3-pro"`

Dos valores diferentes (`flux-1.1-pro` y `gemini-3-pro`) tienen el mismo texto visible. Uno de los dos labels es incorrecto.

**Propuesta:** Corregir el label de Flux 1.1 Pro para que muestre "Flux 1.1 Pro (OpenRouter)" en lugar de "Gemini 3 Pro".

---

## 12. ADMIN CONFIG SISTEMA (/admin/settings)

### 🟡 #38 — Las claves de configuración usan los nombres internos de la BD como títulos ✅
La página muestra directamente las claves técnicas como `fallos_informe_chat_ids`, `pm_resumen_hora`, `fallos_tarjeta_brand`, `llm_router_override`, etc. como encabezados de sección. Un admin no técnico no puede descifrar qué controla cada clave sin leer la descripción completa.

**Propuesta:** Mostrar el label amigable como H3 principal y la clave técnica como subtexto en monospace. La estructura actual invierte el orden de importancia.

---

## 13. CALENDARIO (/calendar)

### 🟠 #39 — El calendario de octubre aparece vacío a pesar de haber piezas programadas en Content ✅
El módulo Calendar mostró octubre 2026 completamente sin eventos. El módulo Content tiene al menos 7 piezas con fecha programada en octubre (LinkedIn 5, 7, 9 oct; Instagram 5, 30 sept; Email 6 oct; etc.). No se refleja ninguna en el calendario.

No puedo confirmar si es un bug o si el calendario solo muestra ciertos tipos de eventos (crons, citas). **Requiere verificación: ¿el calendario debería mostrar piezas programadas de Content?**

---

## 14. DESTINOS DE PUBLICACIÓN (/publicacion)

### 🟠 #40 — Las 7 marcas tienen la sección de destinos vacía ✅
Todas las marcas (MigraTeam, NextJobs, Marketing Dashboard, Píldora Digital, Aterrizaje Médico, MNEE, Multiverse) muestran solo el botón "Añadir destino" sin ningún destino configurado. Las piezas programadas en Content no tienen a dónde publicarse automáticamente.

**Propuesta:** Añadir un aviso visible en las piezas programadas que no tienen destino: "Sin destino — no se publicará sola".

---

## PLAN DE ACCIÓN — RESUMEN

### 🔴 Críticos — esta semana

| # | Descripción | Esfuerzo estimado |
|---|-------------|-------------------|
| #7 | Spinner "Calculando la semana" sin fallback | ~30 min |
| #8 | Actividad reciente sin nombre de crew | ~1h |
| #14 | Brand Brain: secciones incompletas sin CTA de edición | ~45 min |
| #21 | Signal: "0 fuentes activas" sin ruta de configuración | ~1h |
| #25 | CRM: KPIs en cero sin contexto | ~2h |
| #30 | Email: 4 contactos de prueba sin aviso de onboarding | ~30 min |
| #32 | Automatización inactiva — verificar toggle y añadirlo a la lista | ~45 min |

### 🟠 Altos — próximas 2 semanas

| # | Descripción | Esfuerzo estimado |
|---|-------------|-------------------|
| #1 | "Resultados" mal agrupado en el menú | ~15 min |
| #2 | Signal doble en el menú sin diferenciación | ~15 min |
| #9 | KPI ejecuciones: sub-cifras sin etiqueta clara | ~1h |
| #10 | Huecos vs Objetivo sin link de acción por hueco | ~1h |
| #11 | Panel actividad lateral contradice el main | ~1h |
| #18 | Tab "Más…" sin indicador de que es un menú | ~30 min |
| #19 | Tres botones de creación de contenido solapados | ~30 min |
| #22 | Dos selectores de marca en Signal | ~2h |
| #23 | Tab "Cuentas" de Signal no muestra cuentas | ~2h |
| #26 | Tile Respuestas Pendientes sin número | ~30 min |
| #31 | Email: Sincronizar CRM sin estado de última sync | ~1h |
| #33 | Automatizaciones: campo IA sin guía | ~30 min |
| #34 | Chat: agentes sin descripción (verificar primero) | ~1h |
| #39 | Calendario vacío con piezas programadas | diagnóstico |
| #40 | Destinos de publicación vacíos sin aviso en piezas | ~30 min |
| #28 | Paquetes en Propuestas — verificar empty state | verificación manual |

### 🟡 Medios — próximo mes

#3, #4, #5, #6, #12, #15, #16, #17, #20, #24, #27, #29, #35, #36, #37, #38

---

## LO QUE FUNCIONA BIEN

- **Dark mode** es consistente en todas las secciones visitadas
- **Crews** — la página `/crews` tiene la información más completa: nombre, agentes, duración media, fecha de última ejecución con estado (OK/error) y tiempo. Es el módulo con mejor UI del sistema
- **Signal — Entrada** — el flujo de "primero verás qué se ha entendido y cuánto costaría; nada se busca hasta que lo confirmes" es correcto. Evita que el usuario gaste créditos por error
- **Automatizaciones** — la nota "nunca se guarda directamente, lo revisas en el modal antes de confirmar" es el comportamiento correcto para acciones irreversibles
- **Selector de marca en sidebar** — funciona y persiste entre navegaciones

---

*Ángel Granados + Claude Code · 21/10/2026*
