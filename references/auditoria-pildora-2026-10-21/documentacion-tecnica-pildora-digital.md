# Documentación Técnica Extensa — Píldora Digital
**Para:** NotebookLM / Estudio interno  
**Autor:** Ángel Granados  
**Fecha:** 21/10/2026  
**Versión:** 1.0 — basada en auditoría directa de la plataforma

---

## PARTE I — VISIÓN GENERAL DEL SISTEMA

### 1.1 Qué es Píldora Digital

Píldora Digital es una plataforma interna de automatización de marketing con IA desarrollada por Oscar. Funciona como un command center centralizado donde un equipo puede gestionar múltiples marcas simultáneamente usando agentes de IA orquestados (crews). No es un producto de software convencional: es una herramienta operativa privada diseñada para el flujo de trabajo interno del equipo.

**Estado actual (21/10/2026):** En desarrollo activo. €0 de ingresos. Todos los clientes en el sistema son internos o de prueba. La plataforma tiene funcionalidades completas en algunas áreas y funcionalidades en beta o no operativas en otras.

**URL de acceso:** `https://app.pildoradigital.com/`  
**Autenticación:** Google OAuth SSO (`/api/auth/google`)

### 1.2 Propuesta de valor

La plataforma promete automatizar el ciclo completo de marketing digital de una marca:
1. Descubrir leads (Signal)
2. Generar contenido adaptado a la voz de la marca (Crews + Brand Brain)
3. Publicar en destinos configurados (módulo de Contenido + Destinos)
4. Gestionar propuestas y clientes (CRM + Propuestas)
5. Medir resultados (Calendar, Dashboard)

En la práctica, los pasos 1 y 2 son los más desarrollados. Los pasos 3, 4 y 5 tienen funcionalidades incompletas o sin configurar.

---

## PARTE II — ARQUITECTURA TÉCNICA

### 2.1 Stack tecnológico

#### Frontend
- **Framework:** Next.js (React)
- **Hosting:** Vercel (proyectos: Píldora Recursos, MKTDB, OCON)
- **Auth en frontend:** pd_sso (Google OAuth) para la plataforma principal; NextAuth para SGNL y PSGN
- **Estilo visual:** Design system interno, fondo oscuro predominante, sidebar de navegación izquierda

#### Backend principal — "Oscar HQ"
- **Framework:** FastAPI (Python)
- **Hosting:** Railway
- **BD principal:** PostgreSQL/Supabase
- **BD por proyecto:** SQLite (algunos proyectos, no todos)
- **Jobs/Crones:** Sistema de jobs periódicos visible en `/admin/crons`

#### IA y Orquestación
- **Framework de agentes:** CrewAI
- **LLM por defecto (crews):** DeepSeek V4 Flash via OpenRouter ($0.09/$0.18 por millón de tokens)
- **LLM premium:** Claude API (cuenta Claude MAX de Oscar)
- **Enrutador de modelos:** `task_model_map` en `/settings` — DESCONECTADO de los crews en la práctica
- **Workflows de automatización:** n8n (en Railway)

#### Servicios externos integrados
- **Pagos:** Stripe (configurado en Settings > Stripe)
- **Comunicación interna:** Telegram (alertas de agentes, notificaciones de crews)
- **Dominio especial:** Open Design via Cloudflare Tunnel (daemon local, no Railway)
- **Generación de imágenes:** Avatar Studio con modelos de OpenRouter (Flux, Gemini)

### 2.2 Arquitectura de despliegue

```
┌─────────────────────────────────────────────────────────┐
│                     USUARIO (browser)                    │
│              https://app.pildoradigital.com              │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                    VERCEL (frontend)                      │
│  Next.js — Píldora Digital App                           │
│  Otros: Píldora Recursos, MKTDB, OCON                    │
└─────────────────────────┬───────────────────────────────┘
                          │ API calls
┌─────────────────────────▼───────────────────────────────┐
│                   RAILWAY (backend)                       │
│  FastAPI "Oscar HQ" — API REST                           │
│  n8n — automatizaciones                                  │
│  Jobs crónicos — crones del sistema                      │
└──────┬──────────────────┬──────────────────┬────────────┘
       │                  │                  │
┌──────▼──────┐  ┌────────▼───────┐  ┌──────▼──────────┐
│  PostgreSQL  │  │ OpenRouter API │  │  Telegram API   │
│  /Supabase   │  │ (DeepSeek,     │  │  (notificaciones│
│              │  │  Flux, Gemini) │  │   y alertas)    │
└─────────────┘  └────────────────┘  └─────────────────┘
                          │
                 ┌────────▼───────┐
                 │   Claude API   │
                 │ (Claude MAX)   │
                 └────────────────┘

SEPARADO:
┌─────────────────────────────────────────────────────────┐
│              EQUIPO LOCAL (máquina física)                │
│  cloudflared daemon → Open Design                        │
│  (si el equipo se apaga, Open Design cae)               │
└─────────────────────────────────────────────────────────┘
```

### 2.3 Navegación y módulos de la plataforma

La plataforma se divide en las siguientes secciones accesibles desde el sidebar:

| Ruta | Módulo | Estado |
|------|--------|--------|
| `/` | Dashboard — "¿Qué hago hoy?" | Beta (spinner infinito) |
| `/brands` | Gestión de marcas | Operativo |
| `/brand-brain` | Base de conocimiento RAG por marca | Parcialmente operativo |
| `/crews` | Orquestación de agentes IA | Operativo (17 crews, 69 agentes) |
| `/signal` | Descubrimiento de leads | Operativo (493 empresas) |
| `/signal/hub` | Reglas de enrutamiento de leads | Operativo |
| `/content` | Gestión de contenido generado | Operativo |
| `/calendar` | Calendario de publicación | Bug: no muestra contenido programado |
| `/crm` | Pipeline de ventas | Disponible |
| `/crm/secuencias` | Secuencias de emails | Disponible |
| `/email-marketing` | Campañas de email | 4 contactos, 3 de prueba |
| `/proposals` | Propuestas comerciales | 6 propuestas, todas internas |
| `/avatar-studio` | Generación de imágenes/vídeo | Bug: labels duplicados |
| `/chat` | Chat con agentes IA | Operativo |
| `/settings` | Configuración del sistema | Operativo (LLM Router desconectado) |
| `/admin/settings` | Config avanzada (DB keys) | Solo Admin |
| `/admin/crons` | Jobs crónicos | Solo Admin |
| `/admin/budgets` | Presupuestos por marca | Solo Admin |
| `/admin/users` | Gestión de usuarios | Solo Admin |
| `/heartbeat` | Estado del sistema | YELLOW (4 checks fallando) |

---

## PARTE III — MÓDULOS EN DETALLE

### 3.1 Brand Brain — Sistema RAG de conocimiento de marca

**Función:** Base de conocimiento semántica (RAG) para cada marca. Alimenta a todos los agentes y crews con el contexto específico de la marca: tono de voz, público objetivo, propuesta de valor, casos de uso, etc.

**Estado por marca:**
- MigraTeam: 71% completitud, 58 chunks indexados
- Resto de marcas: completitud inferior, no especificada en la auditoría

**Cómo funciona:** Los chunks de texto se vectorizan y se almacenan para recuperación semántica. Cuando un crew ejecuta una tarea, el Brand Brain devuelve los chunks más relevantes como contexto.

**Bloqueante:** Sin Brand Brain completo (>80%), los crews generan contenido genérico sin voz de marca. Esto hace que el contenido no sea comercializable.

### 3.2 Crews — Orquestación multi-agente con CrewAI

**Función:** Flujos de trabajo de múltiples agentes de IA que ejecutan tareas de marketing en secuencia o en paralelo.

**Escala:**
- 17 crews configurados
- 69 agentes totales
- Modelo por defecto: DeepSeek V4 Flash (OpenRouter) — $0.09/$0.18 por M tokens

**Configuración de modelo:** Hay dos selectores independientes en Settings:
1. Selector para agentes de chat (conversaciones)
2. Selector para crews (ejecuciones automatizadas) — actualmente: DeepSeek V4 Flash

**Problema conocido:** El LLM Router (`task_model_map`) que aparece en Settings está desconectado de la ejecución real de los crews. Los cambios en el Router no afectan a los crews.

**Presupuesto:** El Hard Stop de presupuesto mensual está configurado en $0 para 6 de 7 marcas. Solo MigraTeam tiene presupuesto activo. Lanzar un crew para cualquier otra marca resulta en un bloqueo inmediato.

### 3.3 Signal — Descubrimiento de leads

**Función:** Herramienta de prospección que agrega y clasifica empresas como potenciales clientes.

**Datos actuales:**
- 493 empresas en total
- 317 con teléfono
- 329 con email
- 211 personas identificadas
- 116 sin escanear

**Signal Hub:** Motor de reglas de enrutamiento. Pestañas: Salud, Reglas, Entregas, Borrado RGPD. Permite definir qué sucede con cada lead según criterios.

**Observación:** Signal tiene datos reales y parece el módulo más maduro operativamente. La pregunta es si las reglas del Hub están configuradas para que los leads lleguen a algún destino útil.

### 3.4 CRM — Pipeline y secuencias

**Módulos:**
- Pipeline visual de oportunidades
- Secuencias de emails automatizadas

**Estado:** Los módulos existen en la navegación pero no hay datos de uso real. Las 6 propuestas del sistema son todas internas (enviadas a oscarsao20@gmail.com).

**Integración con Stripe:** Configurada en Settings > Stripe para procesar pagos de propuestas aceptadas. La automatización post-pago existe: "Post-pago: Bienvenida despacho MigraTeam · payment.completed · inactiva · 1 acción · 0 disparos". Está inactiva.

### 3.5 Avatar Studio — Generación de imágenes y vídeo

**Función:** Interfaz para generar imágenes y vídeos usando modelos de IA vía OpenRouter.

**Modelos configurados:**
- `openrouter:flux-1.1-pro` — muestra como "Gemini 3 Pro Image (OpenRouter)" [BUG: label incorrecto]
- `openrouter:gemini-3-pro` — muestra como "Gemini 3 Pro Image (OpenRouter)"

**Bug:** Dos modelos con el mismo display name. El usuario no puede distinguir cuál está seleccionando.

### 3.6 Heartbeat — Monitorización del sistema

**Función:** Dashboard de estado de salud del sistema. Comprueba endpoints críticos, conexiones a BD, servicios externos.

**Estado (21/10/2026):** YELLOW — 4 checks fallando.

**Significado:** Un Heartbeat en YELLOW permanente pierde utilidad como sistema de alerta. Si todo está siempre en YELLOW, nadie mira el dashboard cuando hay un problema real.

---

## PARTE IV — USUARIOS Y ROLES

### 4.1 Usuarios registrados

Total: 9 usuarios. 5 con rol Admin.

| Usuario | Rol | Observación |
|---------|-----|-------------|
| Ángel (angel.granados@cohenyaguirre.es) | Admin | Usuario auditor |
| @Admin | Admin | Cuenta genérica |
| Oscar | Admin | Desarrollador principal |
| Maria @oscar_admin | Admin | Cuenta de Oscar (secundaria) |
| Ingerber Azuaje | Admin | |
| + 4 usuarios más | Rol no Admin | No detallados en auditoría |

**Problema:** 5/9 Admins es excesivo. En un sistema multi-tenant no hay diferenciación posible entre roles de cliente y roles internos mientras todos sean Admins.

### 4.2 Sistema de autenticación

- **Plataforma principal:** Google OAuth via pd_sso (`/api/auth/google`)
- **SGNL y PSGN:** NextAuth de forma independiente

**Implicación:** Un usuario puede estar autenticado en la plataforma principal pero no en SGNL o PSGN, o tener sesiones inconsistentes entre proyectos.

---

## PARTE V — DATOS OPERATIVOS

### 5.1 Métricas actuales (21/10/2026)

| Métrica | Valor |
|---------|-------|
| Ingresos | €0 |
| Clientes reales | 0 |
| Propuestas en sistema | 6 (todas internas) |
| Leads en Signal | 493 empresas |
| Contactos email marketing | 4 (3 de prueba) |
| Usuarios | 9 |
| Crews | 17 |
| Agentes | 69 |
| Marcas gestionadas | 7 |
| Marcas con presupuesto IA | 1 (MigraTeam) |
| Destinos de publicación configurados | 0 (en todas las marcas) |
| Piezas de contenido programadas | 7 (no visibles en calendario) |
| Brand Brain más completo | MigraTeam — 71%, 58 chunks |

### 5.2 Marcas gestionadas

7 marcas en el sistema. Solo MigraTeam tiene Brand Brain parcialmente completo y presupuesto IA activo. Las demás están configuradas como entidades pero sin operatividad de IA real.

---

## PARTE VI — CONFIGURACIÓN Y ADMINISTRACIÓN

### 6.1 Settings (/settings)

Configuración general accesible a usuarios con permisos. Incluye:
- Selector de modelo para chat de agentes
- Selector de modelo para crews (DeepSeek V4 Flash activo)
- LLM Router (`task_model_map`) — DISCONNECTED de crews
- Configuración de Stripe
- Configuración de Telegram

### 6.2 Admin Config (/admin/settings)

Panel de administración avanzado. Expone claves de base de datos en snake_case directamente como headings de página:
- `fallos_informe_chat_ids`
- `llm_router_override`
- `pm_resumen_hora`
- (y otras)

**Observación de seguridad:** Este panel no debería ser accesible a clientes o usuarios no técnicos. Las claves internas de BD no deberían mostrarse en la UI de producción sin etiquetas descriptivas.

### 6.3 Crones del sistema (/admin/crons)

Jobs periódicos identificados:
- `run_dev_sync` — cada 10 minutos — ACTIVO EN PRODUCCIÓN (bug)
- `timeout_stale_executions` — limpieza de ejecuciones colgadas (workaround)
- `reclaim_stale_jobs` — reclamación de jobs zombie (workaround)
- (Posiblemente otros no detallados en la auditoría)

### 6.4 Presupuestos (/admin/budgets)

Sistema de Hard Stop por marca. Limita el gasto mensual de IA por marca.

**Estado:**
- MigraTeam: presupuesto configurado (valor no auditado)
- 6 marcas restantes: $0/mes (bloquea cualquier crew)

---

## PARTE VII — FLUJOS DE TRABAJO CLAVE

### 7.1 Flujo de generación de contenido con IA

```
Usuario selecciona marca
    → Sistema recupera Brand Brain (chunks RAG)
    → Usuario selecciona crew de contenido
    → Sistema verifica presupuesto de la marca
    → Si presupuesto OK: Crew ejecuta
        → Agentes en secuencia generan contenido
        → Resultado aparece en módulo de Contenido
    → Si presupuesto = $0: Hard Stop, crew bloqueado
```

**Problema actual:** El paso de verificación de presupuesto bloquea 6/7 marcas.

### 7.2 Flujo de Lead en Signal

```
Signal descubre empresa (BORME, LinkedIn, Apollo, etc.)
    → Empresa añadida a la lista (493 actualmente)
    → Signal Hub evalúa reglas de enrutamiento
    → Según reglas: empresa va a Salud, Entregas, etc.
    → Lead puede entrar al CRM como oportunidad
```

**Problema actual:** No se sabe si las reglas del Hub están configuradas y activas, o si hay destinos de entrega reales configurados.

### 7.3 Flujo de propuesta a cliente

```
Oportunidad en CRM
    → Creación de propuesta en /proposals
    → Envío de propuesta (por Stripe)
    → Cliente acepta y paga
    → Automatización post-pago (actualmente inactiva)
        → Debería crear acceso del cliente a la plataforma
        → Debería iniciar onboarding
```

**Problema actual:** La automatización post-pago existe pero está inactiva. No hay clientes reales que hayan pasado por este flujo.

---

## PARTE VIII — INTEGRACIONES EXTERNAS

### 8.1 OpenRouter

Proxy de LLMs. Permite acceder a múltiples modelos con una sola API key.

**Modelos en uso:**
- DeepSeek V4 Flash — crews (por defecto)
- Flux 1.1 Pro — Avatar Studio (display name incorrecto)
- Gemini 3 Pro — Avatar Studio

**Coste:** $0.09/$0.18 por millón de tokens (input/output) para DeepSeek V4 Flash.

### 8.2 Claude API (Anthropic)

Cuenta Claude MAX de Oscar. Usada para tareas que requieren mayor capacidad de razonamiento.

**Riesgo:** Dependencia de una cuenta personal MAX. Si la cuenta tiene problemas o se agota, el fallback no está documentado.

### 8.3 Telegram

Comunicación interna del sistema. Los agentes envían alertas, resultados de crews y notificaciones por Telegram. Configurado en `/admin/settings` via `fallos_informe_chat_ids`.

### 8.4 Stripe

Procesamiento de pagos para propuestas. Configurado en Settings > Stripe. La automatización post-pago está inactiva (0 disparos).

### 8.5 Google OAuth

Autenticación SSO. Todos los usuarios se autentican con Google. El sistema usa `pd_sso` como middleware de autenticación.

### 8.6 Cloudflare Tunnel

Exposición de Open Design al exterior desde un equipo local. Requiere daemon `cloudflared` activo en el equipo que aloja Open Design. Sin migración a Railway o Vercel, la disponibilidad depende del equipo físico.

---

## PARTE IX — FALLOS CONOCIDOS Y PENDIENTES

### Fallos confirmados en auditoría directa

1. **Dashboard — spinner infinito** en widget "¿Qué hago hoy?" (el endpoint no responde o el frontend no maneja el error)
2. **Activity log** — muestra brand_id y booleano de éxito, no nombres legibles
3. **Avatar Studio** — dos opciones con el mismo display name "Gemini 3 Pro Image"
4. **Calendario** — vacío aunque hay 7 piezas de contenido programadas en el módulo de Contenido
5. **`run_dev_sync`** — activo en producción (audita el listado de crones)
6. **Heartbeat** — estado YELLOW con 4 checks fallando (observado en la UI)
7. **Hard Stop $0** — bloquea crews en 6 de 7 marcas
8. **LLM Router** — desconectado de los crews (confirmado en Settings)
9. **Destinos de publicación** — 0 configurados en todas las marcas
10. **Automatización post-pago** — inactiva, 0 disparos

### Fallos inferidos por síntoma (requieren verificación)

- Ejecuciones de crews que quedan colgadas de forma habitual (evidencia: crones de limpieza periódica)
- SQLite no tolerante a escrituras concurrentes en proyectos que lo usan
- n8n sin aislamiento de recursos en Railway

---

## PARTE X — GLOSARIO TÉCNICO

| Término | Definición |
|---------|-----------|
| Oscar HQ | Nombre del backend principal (FastAPI/Python) |
| pd_sso | Middleware de autenticación SSO compartido de la plataforma principal |
| Brand Brain | Sistema RAG (Retrieval Augmented Generation) de conocimiento por marca |
| Crew | Flujo de trabajo multi-agente orquestado con CrewAI |
| Agent | Agente IA individual dentro de un crew. 69 agentes en 17 crews |
| Signal | Módulo de descubrimiento y gestión de leads |
| Signal Hub | Motor de reglas de enrutamiento de leads |
| Hard Stop | Límite de presupuesto mensual de IA por marca. En $0 bloquea todos los crews |
| LLM Router | `task_model_map` — sistema centralizado de enrutamiento de modelos (desconectado) |
| Heartbeat | Servicio de monitorización de salud del sistema |
| run_dev_sync | Cron de sincronización de desarrollo, activo incorrectamente en producción |
| OpenRouter | Proxy de acceso a múltiples LLMs con una API unificada |
| Avatar Studio | Módulo de generación de imágenes y vídeo |
| Cloudflare Tunnel | Exposición de servicio local al exterior sin necesidad de servidor |
| DeepSeek V4 Flash | Modelo LLM por defecto para crews via OpenRouter |
| Railway | Plataforma de hosting del backend (FastAPI + n8n) |
| Vercel | Plataforma de hosting del frontend (Next.js) |
| Supabase | Backend-as-a-Service para PostgreSQL (algunas funcionalidades) |
| CrewAI | Framework de orquestación de agentes IA multi-rol |

---

## PARTE XI — HOJA DE RUTA TÉCNICA RECOMENDADA

### Fase 0 — Estabilización (semanas 1-2, antes de cualquier cliente)

**Objetivo:** El sistema no debe estar roto cuando alguien lo use.

- Desactivar `run_dev_sync` en producción
- Resolver los 4 checks del Heartbeat
- Corregir el display name de Flux 1.1 en Avatar Studio
- Corregir el spinner infinito del dashboard (timeout + estado de error)
- Configurar presupuestos mínimos para marcas activas

### Fase 1 — Operatividad básica (semanas 3-6)

**Objetivo:** Al menos una marca puede hacer el ciclo completo de marketing con IA.

- Conectar LLM Router a los crews (o documentar que son independientes)
- Completar Brand Brain de una marca piloto al 80%+
- Configurar destinos de publicación para esa marca
- Hacer que el Calendario refleje el contenido programado
- Activar automatización post-pago para poder recibir el primer cliente real

### Fase 2 — Hardening para escala (mes 2-3)

**Objetivo:** El sistema aguanta múltiples usuarios concurrentes sin degradarse.

- Migrar proyectos con SQLite a PostgreSQL
- Implementar rate limiting en la API
- Migrar Open Design a Railway
- Aislar n8n en su propio servicio de Railway
- Definir estrategia de roles y permisos para clientes externos
- Documentar todos los crews (descripción, input, output esperado)

### Fase 3 — Primer cliente real (mes 3+)

**Objetivo:** Primer contrato de pago real con flujo completo.

- Seleccionar una marca que tenga Brand Brain completo, presupuesto configurado y destinos de publicación
- Crear un usuario cliente con rol no Admin
- Ejecutar el flujo completo de propuesta → pago → onboarding → primer crew
- Medir costes reales de IA por cliente para validar pricing

---

*Documentación técnica redactada por Ángel Granados con Claude Code — 21/10/2026*  
*Basada en auditoría exhaustiva de la plataforma app.pildoradigital.com*  
*Actualizar tras cada sesión de exploración o cambio significativo en la plataforma*  
*Para uso en NotebookLM y como referencia interna del equipo Píldora Digital*
