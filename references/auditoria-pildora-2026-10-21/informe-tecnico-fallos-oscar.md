# Informe Técnico — Fallos e Incidencias en Píldora Digital
**Autor:** Ángel Granados  
**Fecha:** 21/10/2026  
**Complemento a:** `informe-ux-pildora-digital-oscar.md`  
**Clasificación:** Interno — uso operativo

---

## Resumen ejecutivo

Este informe documenta los fallos técnicos, deuda de arquitectura y riesgos operativos identificados en Píldora Digital durante la auditoría de plataforma de octubre 2026. Se distinguen los hechos observados directamente de los inferidos por síntoma. El objetivo es que Oscar disponga de una visión técnica ordenada antes de incorporar nuevos usuarios o iniciar captación comercial.

**Distribución de hallazgos:**
- 🔴 Crítico: 8 fallos que pueden interrumpir el servicio o comprometer datos
- 🟠 Alto: 12 problemas que degradan la fiabilidad o aumentan el coste operativo
- 🟡 Medio: 9 elementos de deuda técnica que escalan con el crecimiento
- 🟢 Bajo: 3 mejoras de mantenimiento

**Cobertura de la auditoría:** Análisis multi-dimensional realizado por 5 agentes especializados simultáneos: Arquitectura/CTO, Backend/CrewAI, DevOps/Infraestructura, Pipelines de Datos, y Seguridad.

**Veredicto:** El sistema no está listo para clientes de pago externos. La infraestructura aguanta el uso interno actual pero tiene al menos 3 puntos de fallo únicos, configuraciones de producción con datos de desarrollo mezclados, y vulnerabilidades de seguridad que un primer cliente externo podría descubrir.

---

## Fallos críticos 🔴

### FC-01 — Cron `run_dev_sync` activo en producción
**Observado:** Listado de crones muestra `run_dev_sync` ejecutándose cada 10 minutos en el entorno de producción.  
**Diagnóstico:** Tarea de sincronización de desarrollo nunca fue desactivada al promover a producción. Consume recursos de Railway en cada ciclo.  
**Riesgo:** Si la tarea escribe a la base de datos de producción datos de fixtures de desarrollo, puede corromper registros activos. También es un vector de ruido en los logs que enmascara errores reales.  
**Acción requerida:** Desactivar inmediatamente en producción. Si es necesaria en dev, añadir variable de entorno `ENV` que la inhabilite cuando `ENV=production`.  
**Urgencia:** Inmediata.

---

### FC-02 — Heartbeat en estado YELLOW con 4 checks fallando
**Observado (21/10/2026):** El servicio Heartbeat muestra estado YELLOW. 4 checks están en fallo activo.  
**Diagnóstico inferido:** Los checks de Heartbeat suelen monitorizar endpoints de la propia API, bases de datos y servicios externos (Telegram, APIs de LLM). Un estado YELLOW persistente indica que al menos 4 de estos están respondiendo fuera de los umbrales o no respondiendo.  
**Riesgo:** Si Heartbeat está en YELLOW permanente nadie lo monitoriza activamente — se convierte en ruido y deja de ser útil como sistema de alerta. Cuando algo se caiga de verdad, no habrá señal diferenciada.  
**Acción requerida:** Revisar logs del Heartbeat, identificar los 4 checks concretos que fallan, corregir o ajustar umbrales. Si son falsos positivos, eliminarlos del check.

---

### FC-03 — LLM Router desconectado de los Crews de CrewAI
**Observado:** La página `/settings` muestra el `task_model_map` (LLM Router) como DISCONNECTED respecto a los crews. Los crews tienen su propio selector de modelo configurado a DeepSeek V4 Flash via OpenRouter.  
**Diagnóstico:** Hay dos sistemas de configuración de modelo que no se comunican: el LLM Router centralizado y la configuración directa por crew. La UI sugiere que el Router debería gobernar los crews pero no lo hace.  
**Riesgo:** Cualquier cambio de modelo en el LLM Router no se propaga a los crews. Si se configura un modelo de fallback por coste o disponibilidad en el Router, los crews lo ignoran. El control centralizado de costes no funciona.  
**Acción requerida:** Conectar el LLM Router a la capa de ejecución de CrewAI, o documentar explícitamente que son sistemas independientes y actualizar la UI para reflejarlo.

---

### FC-04 — Hard Stop de presupuesto en 6 de 7 marcas ($0/mes)
**Observado:** Solo MigraTeam tiene presupuesto de IA configurado. Las otras 6 marcas están en $0/mes, lo que significa que cualquier crew lanzado para esas marcas alcanzará el hard stop inmediatamente.  
**Diagnóstico:** La funcionalidad de Hard Stop existe para evitar costes desbordados, pero al estar en $0 bloquea completamente la operación de IA para esas marcas.  
**Riesgo:** Si un cliente o usuario lanza un crew sobre cualquier marca distinta de MigraTeam, fallará silenciosamente o con un error técnico que el usuario no entenderá. Los crews parecerán rotos cuando en realidad están bloqueados por presupuesto.  
**Acción requerida:** Definir presupuestos operativos mínimos para cada marca activa, o documentar explícitamente que solo MigraTeam está operativa en modo IA.

---

### FC-05 — Google OAuth sin restricción de dominio (acceso abierto)
**Inferido:** El SSO pd_sso con Google OAuth probablemente no tiene el parámetro `hd` (hosted domain) configurado, lo que significa que cualquier cuenta de Google puede autenticarse en la plataforma si conoce la URL.  
**Riesgo:** Un externo que descubra la URL de Píldora Digital puede crear una cuenta y acceder al sistema. Sin lista de emails permitidos post-autenticación, el control de acceso depende únicamente de que nadie encuentre la URL.  
**Acción requerida:** Añadir restricción de dominio en la config de Google OAuth (`hd: "dominio.es"`) o implementar lista blanca de emails autorizados que se verifique tras la autenticación de Google.  
**Urgencia:** Inmediata antes de cualquier uso con clientes.

---

### FC-06 — n8n webhooks sin autenticación — cualquier POST activa automaciones
**Inferido:** n8n genera URLs de webhook del tipo `https://n8n.railway.app/webhook/[uuid]` que son públicas por defecto. Sin Basic Auth o Header Auth en el nodo webhook, cualquiera que conozca la URL puede triggerar automaciones arbitrarias.  
**Riesgo:** Un atacante puede activar workflows que envíen mensajes en nombre del sistema, exfiltren datos de Signal, o consuman presupuesto de IA.  
**Acción requerida:** Activar autenticación (Basic Auth o Header Token) en todos los nodos webhook de n8n que tengan datos reales de producción.  
**Urgencia:** Alta — verificar cuáles webhooks están expuestos esta semana.

---

### FC-07 — Contaminación cruzada potencial en Brand Brain entre marcas
**Inferido de arquitectura:** Si la query de retrieval del RAG no incluye un filtro explícito por `brand_id` en cada consulta, el sistema puede devolver documentos de una marca cuando un agente de otra marca hace una pregunta semántica similar.  
**Riesgo:** El CEO Agent de la Marca A puede responder con datos estratégicos, de clientes o financieros de la Marca B. En un sistema multi-cliente esto es una brecha de confidencialidad con riesgo legal.  
**Acción requerida:** Auditar el código de retrieval del Brand Brain y confirmar que todas las queries incluyen `filter={"brand_id": current_brand_id}`. Ejecutar query de verificación: `SELECT brand_id, COUNT(*) FROM embeddings GROUP BY brand_id` para confirmar aislamiento.  
**Urgencia:** Verificar antes de activar el sistema para más de una marca simultáneamente.

---

### FC-08 — Cloudflare Tunnel para Open Design depende de equipo local
**Observado:** Open Design usa Cloudflare Tunnel en lugar de Railway o Vercel.  
**Diagnóstico inferido:** Un Cloudflare Tunnel requiere que el daemon `cloudflared` esté corriendo en un equipo local. Si ese equipo se apaga, se reinicia o pierde conexión, Open Design deja de ser accesible externamente.  
**Riesgo:** Si Open Design sirve alguna funcionalidad a usuarios o a otras partes del sistema, cualquier interrupción del equipo local causa una caída de servicio que Railway o Vercel no causarían. No hay SLA posible con esta arquitectura.  
**Acción requerida:** Migrar Open Design a Railway o Vercel, o documentar claramente que es un servicio en beta con disponibilidad dependiente de infraestructura local.

---

## Fallos de alta gravedad 🟠

### FA-01 — Jobs crónicos `timeout_stale_executions` y `reclaim_stale_jobs`
**Observado:** Existen dos crones regulares con estos nombres en el sistema.  
**Diagnóstico:** La existencia de jobs de limpieza de ejecuciones colgadas como rutina periódica indica que hay ejecuciones que se quedan en estado "running" o "pending" de forma habitual sin completarse ni fallar limpiamente. Esto no debería ser normal — es síntoma de que el manejo de errores en los crews o en la capa de jobs no hace cleanup al fallar.  
**Riesgo:** Acumulación silenciosa de jobs zombie, consumo de recursos innecesario, datos inconsistentes en el estado de la plataforma.  
**Acción requerida:** Investigar por qué los jobs quedan colgados. Los crones de limpieza son un workaround, no una solución. La causa raíz está en el manejo de excepciones de los crews.

---

### FA-02 — Spinner "Calculando la semana…" infinito en dashboard
**Observado directamente:** El widget "¿Qué hago hoy?" muestra spinner de carga permanente sin resolverse ni mostrar error.  
**Diagnóstico:** El endpoint que alimenta este widget no responde o responde con error, pero el frontend no maneja el estado de error — muestra el spinner indefinidamente.  
**Impacto:** Es la primera pantalla que ve cualquier usuario al entrar. Un spinner infinito transmite que el sistema está roto.  
**Acción requerida:** (a) Corregir el endpoint backend o (b) añadir timeout y estado de error explícito en el frontend.

---

### FA-03 — Activity log muestra brand_id y success/fail sin nombre de crew
**Observado directamente:** El feed de actividad reciente en el dashboard solo muestra identificadores internos (`brand_id`, estado booleano) sin el nombre de la crew ejecutada, la marca en texto legible, ni timestamp visible.  
**Diagnóstico:** El componente de actividad no está haciendo join con las tablas de marcas y crews para resolver los IDs a nombres.  
**Impacto:** El historial de actividad es ilegible para cualquier usuario que no conozca los IDs internos de memoria.  
**Acción requerida:** JOIN en el query de actividad con tablas de marcas y crews para obtener nombres en lugar de IDs.

---

### FA-04 — Avatar Studio: dos opciones con el mismo nombre de display
**Observado directamente:** Dos entradas del selector de modelo en Avatar Studio muestran "Gemini 3 Pro Image (OpenRouter)" pero corresponden a modelos distintos: `openrouter:flux-1.1-pro` y `openrouter:gemini-3-pro`.  
**Diagnóstico:** El campo `display_name` está mal configurado para `flux-1.1-pro` — probablemente se copió la entrada de Gemini sin actualizar el label.  
**Impacto:** El usuario no sabe qué modelo está seleccionando cuando elige "Gemini 3 Pro Image" porque hay dos opciones idénticas con comportamiento distinto.  
**Acción requerida:** Corregir el `display_name` de `flux-1.1-pro` a "Flux 1.1 Pro (OpenRouter)" en la configuración de modelos de Avatar Studio.

---

### FA-05 — Calendario vacío con 7 piezas de contenido programadas
**Observado directamente:** El módulo de Calendario de octubre 2026 aparece completamente vacío. El módulo de Contenido muestra 7 piezas programadas.  
**Diagnóstico:** Los dos módulos no están sincronizados. El Calendario no lee el estado de publicación del módulo de Contenido, o el campo de fecha del contenido no se mapea correctamente al calendario.  
**Impacto:** El calendario como herramienta de planning no sirve para nada si no refleja el contenido real programado.  
**Acción requerida:** Revisar la query del módulo Calendario para confirmar que incluye contenido de todos los estados o que la integración entre módulos está activa.

---

### FA-06 — 7 marcas con 0 destinos de publicación configurados
**Observado directamente:** Ninguna de las 7 marcas tiene redes sociales, blogs u otros destinos de publicación conectados.  
**Diagnóstico:** La funcionalidad de publicación directa no está operativa para ninguna marca. Todo el contenido generado por los crews se queda en la plataforma sin ruta de salida automatizada.  
**Impacto:** La propuesta de valor central de automatización de marketing no puede completarse sin destinos de publicación.  
**Acción requerida:** Definir qué marcas se van a activar primero y conectar sus destinos de publicación antes de cualquier demo a cliente.

---

### FA-07 — 5 de 9 usuarios tienen rol Admin
**Observado:** De los 9 usuarios registrados, 5 tienen rol Admin (Ángel, @Admin, Oscar, Maria @oscar_admin, Ingerber Azuaje).  
**Diagnóstico:** No hay principio de mínimo privilegio. En un sistema multi-tenant, todos los Admins tienen acceso a datos de todas las marcas y a configuraciones de sistema.  
**Riesgo:** Si alguna cuenta Admin se ve comprometida, el atacante tiene acceso total. También imposibilita crear perfiles de acceso diferenciado para clientes.  
**Acción requerida:** Revisar roles antes de añadir clientes externos. Los clientes nunca deben tener rol Admin.

---

### FA-08 — n8n sin migrar a infraestructura propia
**Inferido:** n8n se menciona como parte del stack en Railway pero sin detalles de aislamiento.  
**Riesgo:** Si n8n comparte instancia de Railway con el backend principal, un problema en n8n puede afectar al backend y viceversa. La gestión de versiones y actualizaciones de n8n puede interferir con el backend.  
**Acción requerida:** Confirmar si n8n está en su propio servicio de Railway o comparte recursos. Si comparte, aislar.

---

### FA-09 — Signal "0 de 0 fuentes activas" — pipeline de captación de leads inoperativo
**Observado directamente:** La pantalla de Signal muestra "0 de 0 fuentes activas". El sistema tiene 493 contactos en base de datos pero no está capturando nuevos leads.  
**Diagnóstico:** Las credenciales de las APIs de fuentes (BORME, LinkedIn/Apollo) no están configuradas o han expirado. El código de captación existe — lo confirman los 493 contactos históricos — pero la configuración operativa falta.  
**Impacto:** Signal es el módulo central acordado en la llamada de onboarding para generar ingresos. Sin fuentes activas, el pipeline de captación está completamente detenido.  
**Acción requerida:** Identificar en `brands.yaml` o en Railway qué variables corresponden a cada fuente de Signal y validar que están presentes y vigentes.

---

### FA-10 — API keys sin rotación con acceso a recursos de coste variable
**Inferido:** Las API keys de Claude (MAX), OpenRouter y Stripe están almacenadas en Railway/Vercel como variables de entorno. No hay evidencia de rotación periódica ni de alertas de gasto anómalo.  
**Riesgo:** Si la Claude API key MAX es exfiltrada (desde Config Sistema, desde Railway, o por un ex-colaborador), el atacante puede generar inferencia masiva a cargo del proyecto sin límite. Con Claude MAX esto puede ser costoso inmediatamente.  
**Acción requerida:** (1) Activar alertas de gasto en los dashboards de Anthropic y OpenRouter. (2) Rotar las keys ahora y establecer rotación trimestral. (3) Enmascarar los valores en Config Sistema para que muestren solo los últimos 4 caracteres.

---

### FA-11 — Stripe webhook sin verificación de firma
**Inferido:** El módulo de Proposals usa Stripe para pagos. Si el endpoint que recibe webhooks de Stripe no implementa `stripe.webhooks.constructEvent()` para verificar la firma, cualquier cliente HTTP puede enviar un POST falso simulando un pago completado.  
**Riesgo:** Activación de propuestas o cambios de estado en el pipeline sin transacción real de pago.  
**Acción requerida:** Verificar en el código de Proposals que el webhook de Stripe valida la firma antes de procesar el evento.

---

### FA-12 — Sin control de concurrencia en ejecución de crews por marca
**Inferido:** CrewAI no tiene control de concurrencia nativo por marca. Si dos usuarios lanzan el mismo crew simultáneamente para la misma marca, ambas instancias corren en paralelo sobre Railway (un solo servidor), consumiendo doble presupuesto y pudiendo generar race conditions en las escrituras a BD.  
**Riesgo:** Doble consumo de presupuesto de IA, posibles datos duplicados o inconsistentes, mayor riesgo de alcanzar el hard stop.  
**Acción requerida:** Implementar mutex por `(brand_id, crew_id)` o una cola de trabajos que limite a una ejecución simultánea por marca.

---

## Deuda técnica media 🟡

### DT-01 — Bus factor = 1 (Oscar como único desarrollador)
**Contexto:** Oscar es el único desarrollador del sistema completo.  
**Riesgo:** Cualquier indisponibilidad de Oscar (enfermedad, vacaciones, salida del proyecto) paraliza completamente el desarrollo y la resolución de incidencias técnicas. No hay documentación suficiente para que otro desarrollador pueda hacerse cargo.  
**Mitigación parcial:** Esta documentación contribuye a reducir el bus factor pero no lo elimina. Se necesita al menos un segundo desarrollador con conocimiento del stack o documentación técnica exhaustiva del backend.

---

### DT-02 — Separación dev/prod incompleta
**Evidencia:** `run_dev_sync` activo en producción (FC-01), Admin config con claves en snake_case expuestas como headings de página.  
**Diagnóstico:** El proceso de promoción de dev a prod no tiene checklist ni automatización que garantice que las configuraciones de desarrollo no lleguen a producción.  
**Acción requerida:** Crear variables de entorno diferenciadas por entorno y un proceso de deploy que valide que no hay configuración de dev activa en prod.

---

### DT-03 — Heterogeneidad de base de datos (PostgreSQL vs SQLite por proyecto)
**Observado:** Distintos proyectos usan bases de datos diferentes: PostgreSQL/Supabase para el núcleo, SQLite para algunos proyectos.  
**Riesgo:** SQLite no soporta escrituras concurrentes. Si múltiples crews intentan escribir simultáneamente a un proyecto con SQLite, habrá errores de bloqueo o pérdida de datos.  
**Acción requerida:** Migrar todos los proyectos activos a PostgreSQL antes de escalar a múltiples usuarios concurrentes.

---

### DT-04 — SSO fragmentado (pd_sso compartido vs NextAuth por proyecto)
**Observado:** La plataforma principal usa pd_sso con Google OAuth. SGNL y PSGN usan NextAuth de forma independiente.  
**Riesgo:** Un usuario puede estar autenticado en la plataforma principal y no en SGNL, o tener sesiones inconsistentes. La gestión de permisos no es centralizada.  
**Acción requerida:** Definir una estrategia de autenticación unificada antes de escalar usuarios.

---

### DT-05 — Brand Brain con completitud desigual (MigraTeam 71%, 58 chunks)
**Observado:** MigraTeam es la única marca con Brand Brain parcialmente completo. Las demás marcas tienen completitud inferior o nula.  
**Riesgo:** Los crews de cualquier marca sin Brand Brain funcionarán sin contexto de marca, produciendo contenido genérico o incorrecto.  
**Acción requerida:** No lanzar crews de IA para ninguna marca hasta que su Brand Brain tenga al menos 80% de completitud.

---

### DT-06 — Ausencia de rate limiting documentado en la API
**Inferido:** No hay evidencia visible de rate limiting en los endpoints de la API de Píldora Digital.  
**Riesgo:** Un crew que falla en un bucle puede hacer decenas de llamadas por segundo sin limitación, agotando el presupuesto de IA y saturando Railway.  
**Acción requerida:** Implementar rate limiting a nivel de API y a nivel de llamadas a LLM por usuario/marca.

---

### DT-07 — 17 crews, 69 agentes sin documentación de casos de uso por crew
**Observado:** Existen 17 crews y 69 agentes totales en el sistema.  
**Diagnóstico:** Sin documentación de qué hace cada crew, para qué marca está diseñado y qué input requiere, cualquier usuario nuevo no puede operar el sistema sin asistencia directa de Oscar.  
**Acción requerida:** Añadir descripción, input esperado y output esperado para cada crew en la plataforma.

---

### DT-08 — Datos de contactos en Signal sin base legal documentada (RGPD/LOPD)
**Contexto:** Signal tiene 493 contactos con nombre de empresa, teléfono y email. Origen probable: BORME, Apollo y/o LinkedIn.  
**Riesgo legal:** BORME es datos públicos (base legal válida para prospección B2B). LinkedIn scraping viola explícitamente sus ToS (sección 8.2). Apollo requiere licencia activa para exportación masiva. El envío de emails comerciales sin consentimiento puede infringir el art. 21 LSSI-CE.  
**Documentación mínima requerida:** Campo `origen` y `fecha_captacion` por contacto. Registro de actividades de tratamiento (art. 30 RGPD). Política de retención: contactos sin actividad en 12 meses → eliminar o anonimizar.  
**Acción requerida:** Antes de cualquier campaña de outreach, revisar el origen de cada lote de contactos y confirmar que la base legal es sólida.

---

### DT-09 — Prompt injection en agentes C-Level y crews sin mitigación documentada
**Contexto:** Los 9+ agentes C-Level y los 17 crews reciben input de usuarios directamente. Un usuario puede escribir instrucciones que manipulen el comportamiento del agente o intenten extraer su configuración interna.  
**Riesgo adicional:** Los datos procesados por crews (registros de Signal, documentos del Brand Brain) pueden contener texto malicioso que el LLM interprete como instrucción si no hay separación entre datos e instrucciones en el prompt.  
**Acción requerida:** Añadir en el system prompt de cada agente instrucción defensiva explícita. Separar el input del usuario del contexto de instrucciones (no interpolar directamente). Activar logging de todas las interacciones para detectar patrones anómalos.

---

## Mejoras de mantenimiento 🟢

### MT-01 — Admin Config expone claves de base de datos sin ofuscación
**Observado:** La página `/admin/settings` muestra claves internas en snake_case como headings visibles (`fallos_informe_chat_ids`, `llm_router_override`, `pm_resumen_hora`). Estas son claves de BD expuestas directamente en la UI de administración.  
**Riesgo bajo pero presente:** Si alguien con acceso Admin hace una captura de pantalla y la comparte, expone la nomenclatura interna del sistema.  
**Mejora:** Mostrar etiquetas descriptivas en lugar de las claves de base de datos crudas.

---

### MT-02 — Email Marketing con 4 contactos (3 de prueba)
**Observado:** El módulo de Email Marketing tiene 4 contactos totales. Al menos 3 son claramente de prueba (`raquenaanden@gmail.com` como "Andrea", `[PRUEBA] Despacho`).  
**Acción:** Limpiar contactos de prueba antes de cualquier uso operativo real del módulo de email.

---

### MT-03 — 6 propuestas internas enviadas a `oscarsao20@gmail.com`
**Observado:** Todas las propuestas en el CRM son tests enviados a la cuenta personal de Oscar.  
**Acción:** Limpiar o archivar antes de que el sistema se use con clientes reales para evitar confusión en el pipeline.

---

## Mapa de riesgos consolidado

| Área | Riesgo principal | Probabilidad de impacto | Momento crítico |
|------|-----------------|------------------------|-----------------|
| Infraestructura | Caída de Railway afecta todo el backend | Media | Cualquier momento |
| Open Design | Apagado del equipo local corta el servicio | Alta | Fin de semana/vacaciones |
| Crews | Hard stop bloquea 6/7 marcas | Certeza | Al lanzar crews |
| LLM Router | No controla costes de crews | Certeza | Al escalar uso |
| Base de datos | SQLite con escrituras concurrentes | Media con escala | Al tener múltiples usuarios |
| Autenticación | SSO fragmentado confunde a usuarios | Media | Al incorporar clientes |

---

## Prioridades de acción para Oscar

**Esta semana (bloqueante para operación):**
1. Desactivar `run_dev_sync` en producción — 30 min
2. Investigar y resolver los 4 checks de Heartbeat en YELLOW
3. Configurar presupuestos mínimos para marcas activas (mínimo las usadas en demos)
4. Corregir display name de Flux 1.1 en Avatar Studio — 10 min
5. Añadir autenticación a webhooks de n8n activos — 1h
6. Activar restricción de dominio en Google OAuth o lista blanca de emails — 1h
7. Activar alertas de gasto en Anthropic y OpenRouter dashboards — 30 min

**Este mes (antes de cualquier cliente externo):**
8. Conectar LLM Router a los crews o eliminar la UI que sugiere que están conectados
9. Resolver la causa raíz de `timeout_stale_executions` (no solo el workaround del cron)
10. Conectar destinos de publicación para al menos una marca piloto
11. Hacer que el Calendario refleje el contenido programado
12. Migrar Open Design a Railway si va a estar en producción
13. Activar fuentes de Signal y verificar pipeline de captación
14. Verificar validación de firma en webhook de Stripe (Proposals)
15. Auditar filtro de brand_id en queries de retrieval del Brand Brain

**Antes de escalar a 10+ usuarios:**
16. Migrar proyectos con SQLite a PostgreSQL
17. Implementar rate limiting en API y concurrencia por marca en crews
18. Definir estrategia de roles — ningún cliente como Admin
19. Completar Brand Brain de marcas activas al 80%+
20. Rotar API keys y establecer rotación trimestral

---

*Informe redactado por Ángel Granados con orquestación de 5 agentes expertos (Arquitectura, Backend/CrewAI, DevOps/Infra, Pipelines de Datos, Seguridad) — Claude Code · 21/10/2026*  
*Los puntos marcados como "inferido" requieren verificación directa en el código fuente con Oscar*  
*Complemento técnico a: `informe-ux-pildora-digital-oscar.md` (40 hallazgos UX)*
