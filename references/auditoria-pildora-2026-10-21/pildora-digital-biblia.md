# 🧠 Píldora Digital — Biblia del Sistema
> Documentación interna exhaustiva del Command Center con IA
> Última actualización: 2026-10-21
> Autor: Ángel Granados

---

## 📋 Índice

1. [[#Hub de Aplicaciones]]
2. [[#Tour de Onboarding (Modo Operador)]]
3. [[#Estructura de Navegación Completa]]
4. [[#Command Center (Inicio)]]
5. [[#Brand Brain (Marca)]]
6. [[#Calendario]]
7. [[#Contenido / Piezas]]
8. [[#Blog]]
9. [[#Biblioteca de Medios]]
10. [[#Estudio Visual (Creative Studio)]]
11. [[#Campañas Ads]]
12. [[#Email]]
13. [[#WhatsApp]]
14. [[#CRM]]
15. [[#Captación - Signal]]
16. [[#Signal Hub]]
17. [[#Propuestas]]
18. [[#Ingresos (Revenue)]]
19. [[#Resultados / Crews]]
20. [[#Automatizaciones]]
21. [[#Chat IA]]
22. [[#Configuración]]
23. [[#Destinos de Publicación]]
24. [[#Marcas (Brands)]]
25. [[#Proyectos]]
26. [[#Perfil de Usuario]]
27. [[#Admin — Panel de Administración]]
28. [[#Dashboard]]
29. [[#Sistema de Marcas (Brands Context)]]
30. [[#Arquitectura General del Sistema]]

---

## 🏠 Hub de Aplicaciones

**URL:** `https://app.pildoradigital.com/hub`
**Descripción:** Pantalla inicial post-login. Es el selector de aplicaciones dentro del ecosistema Píldora Digital.

### Elementos de la pantalla

| Elemento | Tipo | Descripción |
|---|---|---|
| Campo de búsqueda | Input | "Buscar herramienta…" — filtra las apps disponibles |
| **Píldora Digital** | Botón/App | Acceso al Command Center principal (IA, Crews, CRM, Signal) |
| **Open Design** | Botón/App | Herramienta de diseño visual |
| **Píldora Recursos** | Botón/App | Biblioteca de recursos |
| **Multiverse Console** | Botón/App | Consola para el proyecto Multiverse Computing |
| **Píldora Signal Console** | Botón/App | Consola independiente de captación Signal |
| **n8n** | Botón/App | Acceso directo a n8n (automatizaciones externas) |
| **Gestionar clientes →** | Link | Navega a gestión de clientes |

### Notas
- Las apps se muestran como iconos con nombre debajo
- La barra de búsqueda filtra en tiempo real
- Hay más apps si se hace scroll horizontal (la pantalla tiene scroll)

---

## 🚀 Tour de Onboarding (Modo Operador)

**Trigger:** Aparece al entrar por primera vez a Píldora Digital desde el Hub
**Tag:** `MODO OPERADOR` (badge naranja en la esquina superior izquierda del modal)

### Slides del tour (5 pasos)

| Paso | Título | Descripción |
|---|---|---|
| 1 | **Hola Angel, bienvenida a Píldora Digital** | "Tu centro de mando con IA. 24 agentes especializados ejecutan tareas reales mientras tú decides estrategia. Listo en menos de 10 minutos." |
| 2 | **BrandStripe arriba de cada página** | "El pill que ves arriba con el nombre de la marca es tu contexto activo. Cambia entre marcas con un click. Todo lo que veas y ejecutes aplica a la marca seleccionada." |
| 3 | **Home Operador — feed-first** | "Empiezas viendo qué pasó automáticamente: notificaciones, decisiones pendientes, últimas ejecuciones. No tienes que recordar nada — el inbox te dice qué requiere atención." |
| 4 | **Crews — el corazón del sistema** | "Ve a /crews. Cada crew es un flujo pre-armado de varios agentes (ej: content_creation = SEO → Copy → QC → CMO). Eliges crew + marca + contexto, el sistema ejecuta y guarda el output." |
| 5 | **Chat — pregunta cuando dudes** | "En /chat puedes hablar directo con cualquier C-Level. Describe lo que quieres lograr, te sugiere qué crew lanzar." |
| 6 | **Listo para empezar** | "Revisa tu playbook (en docs/playbooks) para el ritmo recomendado. Hoy: lanza tu primer crew para MigraTeam (probaste full_business_audit, content_creation o sales_pipeline_review). Bienvenida al equipo." |

### Botones del modal
- **Saltar tour** (X) — cierra el modal sin completar
- **Anterior** — vuelve al slide previo
- **Siguiente** → navega al siguiente slide
- **Empezar a operar** — botón final que cierra el modal

---

## 🗺️ Estructura de Navegación Completa

**Sidebar izquierdo** — siempre visible en desktop

### Selector de Marca (BrandStripe)
```
[Selector dropdown] "Todas las marcas"
  ├── Todas las marcas
  ├── MigraTeam           → value="migrateam"
  ├── NextJobs AutoApply  → value="nextjobs"
  ├── Marketing Dashboard → value="marketing_dashboard"
  ├── Píldora Digital     → value="pildora_digital_inbound"
  ├── Aterrizaje Médico en España → value="aterrizaje_medico"
  ├── Mi Negocio En España → value="mnee"
  └── Multiverse Computing → value="multiverse"

[+ Nueva brand] → modal para crear nueva marca
```

### Menú principal (agrupado por sección)

```
📍 Centro de mando
  └── Inicio               /

📣 Marketing y contenido
  ├── Marca               /brand-brain
  ├── Calendario          /calendar
  │
  ├── [Contenido]
  │   ├── Piezas          /content
  │   └── Blog            /content?channel=blog
  │
  ├── Biblioteca          /media
  │
  ├── [Estudio visual]
  │   ├── Estudio visual  /tools/creative-studio
  │   └── Campañas Ads    /tools/ads-builder
  │
  └── [Canales]
      ├── Email           /tools/email
      └── WhatsApp        /whatsapp

💰 Ventas
  ├── CRM                 /crm
  ├── Captación (Signal)  /signal
  ├── Signal Hub          /signal/hub
  ├── Propuestas          /proposals
  └── Ingresos            /revenue

⚙️ Sistema
  ├── Resultados          /documents?tab=crews
  ├── Automatizaciones    /automatizaciones
  └── Chat IA             /chat

🛠️ Configuración
  ├── Configuración       /settings
  ├── Destinos de publicación /publicacion
  ├── Marcas              /brands
  ├── Proyectos           /projects
  └── Nuevo Proyecto      /projects/new

[Mostrar en desarrollo] — toggle para mostrar herramientas beta/WIP/en desuso

👨‍💼 Admin (solo admins)
  ├── Usuarios            /admin/users
  ├── Prompt Optimizer    /admin/prompt-optimizer
  ├── Config Sistema      /admin/settings
  ├── Presupuestos        /admin/budgets
  ├── Crons               /admin/crons
  └── Activity Log        /admin/activity-log
```

### Header (barra superior)
| Elemento | Función |
|---|---|
| 🔔 Notificaciones | Badge con conteo sin leer (actualmente: 214 sin leer, muestra "99+") |
| Buscar (Ctrl+K) | Búsqueda global por toda la plataforma |
| Motor Online | Indicador de estado del motor IA |
| ☀️ Modo claro | Toggle de tema claro/oscuro |
| Mi perfil | Acceso a /profile |
| Cerrar sesión | Logout |

### Navegación móvil (bottom bar)
```
Inicio | Chat | CRM | Creative | Admin
```

---

## 🎯 Command Center (Inicio)

**URL:** `https://app.pildoradigital.com/`
**Descripción:** Panel principal de operaciones. Vista tipo "feed-first" con métricas, CEO AI, proyectos activos y actividad reciente.

### Métricas del header
| Métrica | Valor actual | Descripción |
|---|---|---|
| Proyectos | 7 | Proyectos/marcas activos |
| Ejecuciones hoy | 182 | Crews/tareas ejecutados en el día |
| OK | 133 | Ejecuciones completadas con éxito |
| Corriendo | 0 | Crews en ejecución en este momento |

### CEO AI
- **Tag:** `listo` (badge verde)
- **Campo de texto:** "¿En qué trabajamos hoy? Puedo ejecutar crews, analizar métricas, generar contenido…"
- **Acciones rápidas predefinidas (chips):**
  - 📧 Campaña email
  - ❓ ¿Qué hago hoy?
  - 🔍 Auditoría completa

### Sección: Mis Proyectos
- **"Ver todos" →** `/brands`
- Cards de proyectos activos con acceso directo:
  - MigraTeam → `/brands?brand=migrateam`
  - NextJobs AutoApply → `/brands?brand=nextjobs`
  - Marketing Dashboard → `/brands?brand=marketing_dashboard`
  - Píldora Digital → `/brands?brand=pildora_digital_inbound`
- **+ Nuevo proyecto** → `/projects/new`

### Sección: Actividad Reciente
- **"Ver todo" →** `/documents?tab=crews`
- Lista de últimas ejecuciones con:
  - Nombre de marca
  - Estado (success / error)
- Marcas vistas: `pildora_digital_inbound`, `migrateam`, `mnee`

### Acciones Rápidas (4 botones)
| Botón | Destino |
|---|---|
| Chat con CEO | /chat |
| Lanzar Crew | /crews |
| Nueva Brand | /projects/new |
| Dashboard | /dashboard |

### Panel lateral derecho: Actividad reciente
- Auto-refresh cada 15 segundos
- Muestra últimas 10 ejecuciones
- "Ver todas →" — link a log completo

---

## 🧠 Brand Brain (Marca)

**URL:** `https://app.pildoradigital.com/brand-brain`
**Descripción:** Base de conocimiento semántica de cada marca. Alimenta automáticamente a todos los agentes y crews con el contexto de la empresa. Requiere marca seleccionada en el BrandStripe.

### Indicador de completitud
- **"Brand Brain · 71% codificado"** (para MigraTeam)
- **"58 chunks"** — fragmentos de conocimiento indexados
- **"5/7 secciones completas"**

### Tabs principales
| Tab | Función |
|---|---|
| 🧠 Conocimiento | Vista de las secciones de conocimiento (activo por defecto) |
| ✨ Alimentar IA | Herramienta para añadir nuevo contexto al Brand Brain |
| 🎨 Brandkit | Activos de marca (colores, logo, tipografía, etc.) |
| Avanzado | Configuración avanzada |
| Editor .md (avanzado) | Editor raw en formato Markdown del Brand Brain |

### Secciones de conocimiento (7 secciones)
| Sección | Estado (MigraTeam) |
|---|---|
| Quiénes somos | ✓ Completo |
| Cliente ideal (ICP) | ✓ Completo |
| Tono de voz | ✓ Completo |
| Canales | ⚠ Incompleto |
| Pricing | ✓ Completo |
| Competidores | ⚠ Incompleto |
| Objetivos | ✓ Completo |

### Botones de acción
| Botón | Función |
|---|---|
| Añadir contexto | Abre formulario para añadir nuevo conocimiento |
| Exportar contexto | Descarga el Brand Brain en un formato exportable |
| Re-analizar | Fuerza un re-análisis semántico de todo el contenido |
| Confirmar contexto | Guarda la versión actual como contexto oficial para todos los crews |
| Config técnica (brands.yaml) | Accede al archivo de configuración técnica YAML |

### Flujo de funcionamiento
```
Brand Brain → alimenta → Agentes IA → Crews → Output de contenido
```
El Brand Brain es el "cerebro" de la marca. Cada vez que un crew se ejecuta, los agentes consultan el Brand Brain para conocer: tono de voz, ICP, pricing, competidores, objetivos y canal.

---

## 📅 Calendario

**URL:** `https://app.pildoradigital.com/calendar`
**Descripción:** Calendario unificado que muestra piezas de contenido, hitos, crew runs, tareas recurrentes y citas de la marca activa.

### Vistas disponibles
| Vista | Descripción |
|---|---|
| Mes | Calendario mensual (vista por defecto) |
| Semana | Vista semanal |
| Agenda | Lista de eventos próximos |

### Botones de creación
- **Nueva pieza** — crea una pieza de contenido y la asigna al calendario
- **Nuevo hito** — añade un hito o fecha clave al proyecto
- **Tarea recurrente** — crea tarea repetitiva con periodicidad

### Filtros de visualización (MOSTRAR)
| Filtro | Descripción |
|---|---|
| Crew runs | Muestra las ejecuciones de crews programadas |
| Recurrentes | Muestra tareas recurrentes |
| Citas | Muestra citas con clientes |
| System crons | Muestra los cron jobs del sistema |

### Panel lateral derecho
- **PRÓXIMOS 7 DÍAS** — eventos de la próxima semana
- **RECURRENTES** — tareas configuradas como recurrentes

### Navegación
- `← Anterior / Siguiente →` — navega entre meses/semanas
- `Hoy` — regresa a la fecha actual
- Cabecera: `Octubre de 2026` (mes y año actuales)

---

## 📝 Contenido / Piezas

**URL:** `https://app.pildoradigital.com/content` → redirige a `/content?brand=migrateam`
**Descripción:** Hub central de gestión de contenido. Flujo completo: Generar → Revisar → Programar → Publicar.

### Botones de creación
| Botón | Función |
|---|---|
| **Generar artículo** | Lanza la IA para generar un artículo nuevo basado en el Brand Brain |
| **Nuevo artículo (pegar contenido)** | Crea una pieza pegando contenido externo |
| **Crear contenido** | Creación manual libre |

### Filtros de estado
```
Todos | Borrador | Aprobado | Programado | Publicado | Más…
```

### Filtro por Canal
```
Todos los canales | Blog | LinkedIn | Instagram | Facebook | Email | WhatsApp | X
```

### Flujo de una pieza de contenido
```
Generar (IA) → Borrador → Revisión → Aprobado → Programado → Publicado
```

---

## 📰 Blog

**URL:** `https://app.pildoradigital.com/content?channel=blog`
**Descripción:** Filtro del módulo de Contenido que muestra solo las piezas del canal Blog. Misma interfaz que Piezas pero pre-filtrada por `channel=blog`.

---

## 🖼️ Biblioteca de Medios

**URL:** `https://app.pildoradigital.com/media`
**Descripción:** Repositorio centralizado de todos los assets generados: imágenes, videos y documentos. Las URLs son permanentes (no caducan).

### Filtros de tipo
```
Todos | Imágenes | Videos | Audio | Documentos
```

### Botones
- **Backfill de URLs legacy** — (solo admin) regenera URLs para assets antiguos sin URL permanente

### Características
- URLs permanentes que no caducan
- Organizado por tipo de media
- Centraliza outputs del Creative Studio, Image Generator y Crews

---

## 🎨 Estudio Visual (Creative Studio)

**URL:** `https://app.pildoradigital.com/tools/creative-studio`
**Descripción:** Generador visual con IA integrado con el Brand Brain de la marca activa. Genera imágenes, videos, carruseles y contenido escrito.

### Tabs principales
| Tab | Función |
|---|---|
| **Generador** | Interfaz de generación de assets (activo por defecto) |
| **Canvas** | Editor visual tipo canvas |

### Categorías de contenido

#### VISUAL
| Tipo | Descripción |
|---|---|
| **Imagen** | Generación de imágenes estáticas |
| **Video** | Generación de clips de video |
| **Carrusel** | Carruseles para redes sociales |
| **Logo** | Generación/variantes de logo |

#### ESCRITO
| Tipo | Descripción |
|---|---|
| **Post social** | Posts para redes sociales |
| **Blog post** | Artículos de blog |
| **Email** | Cuerpo de emails |
| **Script video** | Guiones para video |

#### PACKS — LOTE (generación múltiple)
| Pack | Descripción |
|---|---|
| **Social pack** | Genera varios formatos de redes a la vez |
| **Campaign pack** | Genera un pack completo de campaña |

### Configuración de generación (para Imagen)
| Parámetro | Opciones |
|---|---|
| **Brand Brain** | Activo automáticamente (brand activa) |
| **Prompt** | Campo libre + botón `Sugerir prompt` |
| **Modelo** | Selector de proveedor (se carga dinámicamente) |
| **Botón** | `Generar (1)` |

#### Estilos disponibles
```
Fotorrealista | Ilustración | Minimalista | 3D | Abstracto | Producto
```

#### Ratios de imagen
```
1:1 (cuadrado) | 4:5 (vertical feed) | 9:16 (stories/reels) | 16:9 (horizontal)
```

#### Cantidad por generación
```
1 | 2 | 4
```

---

## 📣 Campañas Ads

**URL:** `https://app.pildoradigital.com/tools/ads-builder`
**Descripción:** Constructor de campañas publicitarias paso a paso con IA. Soporta Google Ads y Meta Ads en paralelo.

### Tabs de plataforma
```
Google Ads | Meta Ads
```

### Modos de creación
| Modo | Descripción |
|---|---|
| **Creación rápida — Nuevo** | Wizard visual de 5 pasos con preview en vivo |
| **Crear desde Cero (Recomendado)** | Empieza con una idea y construye paso a paso con IA + aprobación manual en cada fase |
| **Optimizar Existente** | Importa campaña en curso y recibe sugerencias de mejora basadas en data real |
| **Desde Plantilla** | Plantillas probadas: Lead Gen, E-commerce, Brand Awareness, Retargeting |

### Secciones adicionales
| Sección | Función |
|---|---|
| **Creative Brief Studio** | Genera imagen para el ad con los colores de la marca |
| **Flow Profiles** | Perfiles de flujo de campaña guardados |
| **Plantillas** | Biblioteca de plantillas de campañas |
| **Prompt Editor** | Edita los prompts de IA que generan las campañas |
| **Stats & Ops** | Estadísticas y operaciones de campañas |

### BUILDS RECIENTES (GOOGLE ADS)
- Historial de builds de Google Ads generados (se carga dinámicamente)

---

## 📧 Email

**URL:** `https://app.pildoradigital.com/tools/email`
**Descripción:** Módulo completo de Email Marketing integrado. Gestiona contactos, campañas, secuencias, plantillas y analítica.

### Sub-navegación
```
Contactos | Campañas | Secuencias | Plantillas | Analytics | Ajustes
```

### Tab: Contactos

#### KPIs del header
| Métrica | Valor actual |
|---|---|
| Total | 4 |
| Activos | 3 |
| Baja | 0 |
| Rebotes | 0 |

#### Filtros de estado
```
Todos los estados | Activos | Baja | Rebote | Spam
```

#### Acciones
- **Importar** — importa lista de contactos
- **Sincronizar CRM** — sincroniza contactos desde el CRM
- **Añadir** — añade contacto manualmente

#### Campos de cada contacto
| Campo | Descripción |
|---|---|
| Email | Dirección de correo |
| Nombre | Nombre del contacto |
| Empresa | Empresa asociada |
| Estado | Activo / Baja / Rebote / Spam / Pending |
| Etiquetas | Tags del contacto |
| Último email | Fecha del último email enviado |

#### Contactos actuales (MigraTeam)
| Email | Nombre | Empresa | Estado |
|---|---|---|---|
| requenaanden@gmail.com | Andrea | — | Activo |
| oscarsao20+diego@gmail.com | Diego Serrano | [PRUEBA] Despacho Alcántara & Asociados | Activo |
| oscarsao20+lucia@gmail.com | Lucía Alcántara | [PRUEBA] Despacho Alcántara & Asociados | Activo |
| hola@gestoradeextranjeria.com | Gretell | — | Pending (tag: newsletter) |

---

## 💬 WhatsApp

**URL:** `https://app.pildoradigital.com/whatsapp`
**Descripción:** Módulo de WhatsApp Business vía Cloud API de Meta. Gestiona conversaciones, plantillas de mensajes y configuración de la cuenta.

### Sub-navegación
```
Conversaciones | Plantillas | Configuración
```

### Botones
- **Ver automatizaciones** → navega a `/automatizaciones` con filtro WhatsApp
- **Actualizar** → refresca el estado de las conversaciones

### Estado actual
- HILOS: 0 conversaciones activas
- "Sin conversaciones todavía. Aparecerán aquí en cuanto llegue el primer mensaje."

---

## 👥 CRM

**URL:** `https://app.pildoradigital.com/crm`
**Descripción:** Sistema de gestión de relaciones con clientes integrado en la plataforma. Vista por defecto es el dashboard de métricas. Requiere marca seleccionada.

### Sub-navegación del CRM (tabs internos)
```
Inicio | Contactos | Empresas | Pipeline | Actividad | Secuencias | Listas e importación | Citas | Ajustes
```

### Dashboard de inicio — KPIs
| Métrica | Descripción |
|---|---|
| NUEVOS 7 DÍAS | Contactos creados esta semana |
| CUALIFICADOS | Listos para revisar y contactar |
| EN NEGOCIACIÓN | Oportunidades activas (actual: 0) |
| GANADOS DEL MES | Oportunidades cerradas ganadas (actual: 0) |
| SEGUIMIENTOS DE HOY | Tareas que vencen hoy o antes |
| RESPUESTAS PENDIENTES | Botón "Ver" → filtra la actividad por respuestas pendientes |

### Sub-secciones

#### Contactos (`/crm` → tab Contactos)
- Lista de contactos con filtros
- Campos: nombre, empresa, estado, último contacto

#### Empresas (`/crm` → tab Empresas)
- Vista de empresas/cuentas

#### Pipeline (`/crm` → tab Pipeline)
- Vista kanban de oportunidades por etapa
- Etapas identificadas: Cualificados → En Negociación → Ganados

#### Actividad (`/crm` → tab Actividad)
- Log de todas las acciones (emails, llamadas, notas)
- Se puede filtrar por "Respuestas pendientes"

#### Secuencias (`/crm` → tab Secuencias)
- Secuencias de outreach automatizado (cadencias de emails/mensajes)

#### Listas e importación (`/crm` → tab Listas e importación)
- Importación masiva de contactos
- Creación de listas segmentadas

#### Citas (`/crm` → tab Citas)
- Gestión de citas y reuniones

#### Ajustes (`/crm` → tab Ajustes)
- Configuración específica del CRM

---

## 📡 Captación — Signal

**URL:** `https://app.pildoradigital.com/signal`
**Descripción:** Herramienta de descubrimiento, limpieza y enriquecimiento de prospectos (despachos y personas) para alimentar el CRM. Es el módulo de captación activa (outbound).

**Contexto activo en la sesión:** Marca Migrateam

### Sub-navegación
```
Píldora | Multiverse | Entrada | Cuentas | Revisión | Ajustes
```

### Tab: Entrada (Añadir empresas o personas)

#### Método 1: Texto libre
- **Campo:** "Una entrada por línea"
- **Formatos aceptados:** nombre de empresa, URL web, `Persona - Empresa`, `Empresa, dominio.es`
- **Límite:** hasta 500 filas por lote
- **Botón:** `Entender y estimar` — analiza lo ingresado y muestra estimación de coste ANTES de buscar. Nada se ejecuta hasta confirmar.

#### Método 2: Importar archivo
- **Formatos:** CSV, Excel (.xlsx), tabla copiada de Excel/Google Sheets
- **Nota de privacidad:** "El archivo se lee en tu navegador: no se sube ni se guarda; solo se envían las filas ya mapeadas"
- **Límite:** 500 filas válidas, máx. 2 MB
- **Botón:** `Elegir archivo` o arrastrar
- **Campo alternativo:** "Pega aquí una tabla copiada de Excel o Google Sheets (con la fila de cabeceras si la tiene)"
- **Botón:** `Leer tabla pegada`

#### Lotes recientes
- Historial de los últimos lotes procesados (visible bajo el formulario)

### Descubrimiento automático (piloto automático)
- **Función:** Busca empresas nuevas en las fuentes de ofertas activas automáticamente
- **Botón:** `Descubrir ahora` — lanza el descubrimiento manualmente
- **Estado actual:** 0 de 0 fuentes activas
- **Link:** "Ver cuáles y por qué" → explica el estado de las fuentes
- **Última ejecución:** nunca se ha lanzado

### Flujo de procesamiento Signal
```
Entrada (texto/archivo/auto)
  → "Entender y estimar" (preview del coste)
    → Confirmación del usuario
      → Búsqueda y enriquecimiento
        → Tab "Cuentas" (resultados limpios)
          → Tab "Revisión" (aprobación manual)
            → CRM (exportación de los aprobados)
```

---

## 🔭 Signal Hub

**URL:** `https://app.pildoradigital.com/signal/hub`
**Descripción:** Centro de control de las reglas de enrutamiento de Signal. Decide a dónde va cada cosa que captura Signal (CRM, ICP, proyecto específico) sin tocar código.

### Sub-navegación
```
Salud | Reglas | Entregas | Borrado RGPD
```

| Tab | Función |
|---|---|
| **Salud** | Estado general del sistema Signal, métricas de captación |
| **Reglas** | Define reglas de enrutamiento: si empresa cumple X → va a Y |
| **Entregas** | Histórico de entregas realizadas al CRM u otros destinos |
| **Borrado RGPD** | Gestión de peticiones de borrado (cumplimiento GDPR) |

---

## 📄 Propuestas

**URL:** `https://app.pildoradigital.com/proposals`
**Descripción:** Sistema de presupuestos y propuestas con precio custom y pago Stripe integrado. Etiquetado como "Píldora Digital LLC".

### Sub-navegación
```
Paquetes | Plantillas | Facturas
```

### Botones
- **Nueva propuesta** — crea una propuesta en blanco

### Filtros de estado
```
Todas | Borrador | Enviada | Aceptada | Pagada | Rechazada | Caducada
```

### Tabla de propuestas
| Columna | Descripción |
|---|---|
| Cliente | Nombre del cliente + email |
| Concepto | Nombre del paquete/producto |
| Total | Importe en € |
| Estado | Estado actual de la propuesta |
| Fecha | Fecha de creación/envío |
| Acciones | Botones de acción por propuesta |

### Propuestas activas (MigraTeam — captura 2026-10-21)
| Cliente | Concepto | Total | Estado | Fecha |
|---|---|---|---|---|
| Despacho Prueba (oscarsao20@gmail.com) | PRUEBA v2 — Pro (España, recomendado) | 199,00 € | Enviada | 15 oct 2026 |
| Despacho Prueba (oscarsao20@gmail.com) | PRUEBA v2 — Pro (España, recomendado) | 199,00 € | Enviada | 14 oct 2026 |
| Despacho Prueba (oscarsao20@gmail.com) | PRUEBA v2 — Pro (España, recomendado) | 199,00 € | Enviada | 14 oct 2026 |
| Despacho Prueba (oscarsao20@gmail.com) | PRUEBA v2 — Pro (España, recomendado) | 199,00 € | Enviada | 14 oct 2026 |
| Despacho Prueba (oscarsao20@gmail.com) | PRUEBA v2 — Pro (España, recomendado) | 199,00 € | Enviada | 13 oct 2026 |
| Despacho Prueba (oscarsao20@gmail.com) | PRUEBA — Pro (España, recomendado) | 199,00 € | **Aceptada** | 13 oct 2026 |

### Notas
- El precio de "Pro España" es 199€/mes
- Pagos procesados vía Stripe
- Hay una propuesta aceptada (confirmación del primer lead real)

---

## 💰 Ingresos (Revenue)

**URL:** `https://app.pildoradigital.com/revenue`
**Descripción:** Dashboard de ingresos cross-producto. Agrega pagos confirmados de Propuestas, Reservas y futuros productos desde el CRM.

### Filtros de período
```
30 días | 90 días | 12 meses | Todo
```

### Fuentes de ingresos
- Propuestas (pagadas con Stripe)
- Reservas/Citas (bookings pagados)
- Futuros productos

### Estado actual
- Cargando datos dinámicamente al acceder

---

## ⚡ Resultados / Crews

**URL:** `https://app.pildoradigital.com/documents?tab=crews`
**Descripción:** Biblioteca de todos los documentos y outputs generados por crews y archivos de los proyectos. Centro de resultados.

### Tabs
```
Generados por crews | Todos los archivos
```

### Ordenación
- **Fecha ↓** — más reciente primero (por defecto)
- **Nombre** — alfabético

### Filtros de categoría
```
Todas las categorías | chat_outputs | content | images | reports
```

### Estadísticas actuales (MigraTeam)
- **42 documentos** en total (42 de 42 mostrados)

### Tipos de crews identificados (por nombre de documento)
| Crew | Categoría | Descripción |
|---|---|---|
| `sales_pipeline_review` | reports | Revisión del pipeline de ventas |
| `demo_follow_up` | reports | Follow-up post-demo |
| `content_creation` | content | Generación de contenido semanal |
| `email_body_writer` | chat_outputs | Redacción de cuerpos de email |
| `email_subject_lines` | chat_outputs | Generación de asuntos de email |
| `image_prompt_creator` | chat_outputs | Prompts para generación de imágenes |
| `headline_generator` | chat_outputs | Generación de titulares |
| `market_research` | chat_outputs | Investigación de mercado |
| `content_strategy` | chat_outputs | Estrategia de contenido |
| `cto_dashboard` | chat_outputs | Dashboard técnico del CTO |
| `content_creation_deliverable` | content | Deliverable final de contenido |

### Historial de ejecuciones (MigraTeam, muestra)
- `content_creation` — ejecutado semanalmente desde marzo 2026
- `sales_pipeline_review` — último: hace 2 días
- `demo_follow_up` — último: hace 4 días
- `image_generation` — últimas: hace 2 días

---

## 🤖 Automatizaciones

**URL:** `https://app.pildoradigital.com/automatizaciones`
**Descripción:** Motor de reglas y automatizaciones conectadas al CRM, WhatsApp, GTM y el resto del sistema. Permite crear reglas con IA.

### Botones
- **Nueva regla** — crea una nueva automatización manualmente

### Filtros de categoría
```
Todas | crm | whatsapp | gtm | bookings | payments | otros
```

### Creación con IA
- **Campo de texto:** "Describe la automatización que quieres"
- **Nota:** "La IA propone un borrador — nunca se guarda directamente, lo revisas en el modal antes de confirmar."
- **Botón:** `Generar con IA`

### Automatizaciones existentes (MigraTeam)
| Nombre | Trigger | Estado | Acciones | Disparos |
|---|---|---|---|---|
| Post-pago: Bienvenida despacho MigraTeam | `payment.completed` | inactiva | 1 acción | 0 disparos |

### Panel: Actividad reciente
- Historial de disparos de automatizaciones

---

## 💭 Chat IA

**URL:** `https://app.pildoradigital.com/chat`
**Descripción:** Interfaz de chat directo con los agentes C-Level del sistema. Permite conversar con el CEO y delegar tareas a cualquier agente especializado.

### Estructura de la interfaz
- **Panel izquierdo:** lista de conversaciones pasadas
- **Panel derecho:** interfaz de chat activa

### Panel de conversaciones (izquierdo)
| Elemento | Función |
|---|---|
| **Nueva conversación** | Inicia una nueva sesión de chat |
| **Buscar...** | Filtro de búsqueda en conversaciones |
| Historial | Lista de conversaciones anteriores con nombre y count de mensajes |
| **Doble click** | Renombra una conversación |
| **Renombrar** | Botón para renombrar |
| **Eliminar** | Borra la conversación |
| **Ver conversaciones** | Botón para cargar más conversaciones |

### Historial de conversaciones (muestra)
- 30+ conversaciones, principalmente con CEO y CMO
- Rango de mensajes: 2 a 24 mensajes por conversación

### Selector de agente
**Dropdown con 9 agentes disponibles:**
| ID | Agente | Especialización |
|---|---|---|
| `ceo` | **CEO — Chief Executive Officer** | Orquestación general, 21 sub-agentes |
| `coo` | **COO — Chief Operating Officer** | Operaciones |
| `financial_analyst` | **Financial Analyst** | Análisis financiero |
| `cmo` | **CMO — Chief Marketing Officer** | Marketing |
| `seo` | **SEO Specialist** | SEO |
| `copywriter` | **Copywriter** | Redacción de contenido |
| `cto` | **CTO — Chief Technology Officer** | Tecnología |
| `data_analyst` | **Data Analyst** | Análisis de datos y reporting |
| `architect` | **Architect** | Arquitectura de software |

### Interfaz de chat (CEO por defecto)
| Elemento | Descripción |
|---|---|
| **Cabecera** | Nombre del agente + marca activa (`MigraTeam`) |
| **Subtítulo** | Crew principal del agente (`full_business_audit · Chief Executive Officer`) |
| **Presentación** | "Soy el CEO de Píldora Digital. Puedo orquestar a mi equipo de 21 agentes para cualquier tarea." |
| **Ver sugerencias** | Muestra acciones sugeridas en el contexto actual |
| **Campo de texto** | "Escribe a CEO... ( / comandos, 📎 adjuntar)" |
| **📎 Adjuntar archivo** | Sube archivos al chat |
| **🎤 Dictar por voz** | Input de voz |
| **/ comandos** | Activa comandos slash en el chat |
| **Enviar** | Envía el mensaje |

### Comandos especiales
- `/` — activa el menú de comandos slash
- 📎 — adjunta archivos a la conversación

### Flujo de uso recomendado
```
Abrir Chat → Seleccionar agente → Describir tarea
  → Agente sugiere el crew adecuado
    → Confirmar → Crew se ejecuta
      → Output disponible en /documents
```

---

## ⚙️ Configuración

**URL:** `https://app.pildoradigital.com/settings`
**Descripción:** Panel central de configuración del sistema. Controla el motor IA, modelos, integraciones, almacenamiento y monitoreo.

### Secciones de la página

#### 1. Configuración LLM (Chat directo / Optimizer)
Controla el modelo usado en el chat directo y el prompt optimizer. **No afecta a los crews.**

| Campo | Valor actual / Opciones |
|---|---|
| Modelo | `claude-sonnet-4-20250514` (activo), `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-7`, `gpt-4o-mini`, `deepseek/deepseek-chat` |
| Temperatura | Slider: Preciso (0) ↔ Creativo (1) |
| Servidor API Host | Campo de texto |
| Puerto | Campo de texto |
| CORS Origins | Campo de texto |

#### 2. Almacenamiento de salida
| Opción | Descripción |
|---|---|
| Formato Markdown (.md) | Guarda outputs en markdown |
| JSON (.json) | Guarda outputs en JSON |
| Ambos | Guarda en ambos formatos |
| Activar history.jsonl | Toggle para retroalimentación del sistema |

#### 3. Cuenta
| Campo | Valor |
|---|---|
| Email | (email del usuario) |
| GitHub | `oscarsao` — autenticado |
| Claude Subscription | **MAX** |

#### 4. Configuraciones rápidas (links a subsecciones)
| Link | Descripción |
|---|---|
| Plantillas de documentos | Personaliza el estilo de PDFs e informes por marca |
| Reservas / Citas | Zona horaria, colchón entre citas, avisos y ventanas de pago por marca |
| Funnel Studio | Tipos de activo habilitados, tono por defecto y umbral de borradores por marca |
| WhatsApp | Conexión por marca, plantillas y conversaciones (Cloud API de Meta) |
| Destinos de publicación | Dónde se publica el contenido de cada marca |
| Integraciones | Conexiones con servicios externos |
| Actividad del equipo | Accesos al panel, país, dispositivo y accesos nuevos por confirmar |
| Cambiar contraseña | Actualiza contraseña y cierra otras sesiones |

#### 5. Modelo LLM para agentes (CrewAI) ⚠️ CRÍTICO
**Este sí controla el coste real de las crews.** Afecta a todos los agentes (CEO, CMO, copywriter, etc.).

| Opción | Precio |
|---|---|
| Automático (Claude por agente, `agent_model_tiers.yaml`) | Variable por agente |
| DeepSeek V4 Flash — vía OpenRouter | $0.09/$0.18 por M tokens |
| Gemini 2.5 Flash — vía OpenRouter | $0.30/$2.50 por M tokens |
| Claude Sonnet 4.5 — vía OpenRouter | precio OpenRouter |

**Botón:** `Guardar modelo de agentes`

#### 6. Avatar Studio — Proveedores por defecto
Usado por `content_engine.py` cuando no se especifica proveedor.

**Proveedores de Imagen:**
| Proveedor | Precio | Velocidad |
|---|---|---|
| Gemini 3 Pro Image (OpenRouter) | $0.04/img | ~12s |
| Gemini 3.1 Flash Image (OpenRouter) | $0.02/img | ~6s |
| Gemini 2.5 Flash Image (OpenRouter) | $0.01/img | ~5s |
| GPT-5 Image Mini (OpenRouter) | $0.05/img | ~15s |
| GPT-5 Image (OpenRouter) | $0.10/img | ~20s |
| GPT Image 1 (OpenAI — recomendado) | $0.04/img | ~20s |
| GPT Image 1 Mini (OpenAI, más rápido) | $0.02/img | ~10s |
| Flux Dev (fal.ai) | $0.03/img | ~5s |
| Freepik Mystic | $0.03/img | ~20s |

**Proveedores de Vídeo:**
| Proveedor | Precio | Velocidad |
|---|---|---|
| Automático (sin default) | — | — |
| Seedance 2.0 Fast | $0.05/clip | ~30s |
| Seedance 2.0 Pro | $0.10/clip | ~60s |
| Seedance 2.0 (imagen a vídeo) | $0.08/clip | ~45s |
| Kling 3.0 Pro | $0.20/clip | ~90s |
| Google Veo 3 | $0.30/clip | ~120s |
| MiniMax Hailuo 2.3 Fast | $0.05/clip | ~30s |
| HeyGen Avatar III (avatar) | $1.00/clip | ~120s |
| fal.ai AI Avatar (1 persona) | $0.15/clip | ~280s |
| fal.ai AI Avatar (2 personas) | $0.18/clip | ~280s |

**Botón:** `Guardar proveedores de Avatar Studio`

#### 7. Router de tareas LLM (referencia — no conectado a crews)
Permite asignar modelos distintos por tipo de tarea. **Nota:** persiste en `llm_router_override` pero ningún agente de CrewAI lo consulta hoy.

| Tipo de tarea | Modelo asignable |
|---|---|
| Chat (complejo) | Selector de modelo |
| Creativo | Selector de modelo |
| Estrategia | Selector de modelo |
| Compacto (económico) | Selector de modelo |
| Relleno de formulario | Selector de modelo |
| Clasificar | Selector de modelo |
| Resumir | Selector de modelo |

**Botón:** `Guardar router`

#### 8. Monitoreo Heartbeat
- **Estado actual:** YELLOW
- **Frecuencia:** cada 30 minutos
- **Última comprobación:** 21/10/2026, 4:00:21 p.m. — 4 comprobaciones
- Edita la lista de verificación que el servicio monitorea
- **Botón:** `Guardar Heartbeat`

### Botón principal
`Guardar configuración` — guarda todos los cambios de la sección LLM principal

---

## 🌐 Destinos de Publicación

**URL:** `https://app.pildoradigital.com/publicacion`
**Descripción:** Configura dónde se publican los artículos del blog de cada marca. Un tab por marca.

### Tabs (una por marca)
```
MigraTeam | NextJobs AutoApply | Marketing Dashboard | Píldora Digital | Aterrizaje Médico en España | Mi Negocio En España | Multiverse Computing
```

### Por cada marca
- **Destinos de publicación web** — lista de destinos configurados
- **Añadir destino** — botón para añadir un nuevo endpoint de publicación (webhook, API, etc.)

---

## 🏷️ Marcas (Brands)

**URL:** `https://app.pildoradigital.com/brands`
**Descripción:** Gestión completa de todas las marcas/proyectos. Vista ecosistema con perfil detallado de cada marca: estrategia, tono, stack, audiencia y próximos pasos.

### KPIs del header
| Métrica | Valor |
|---|---|
| Total marcas | 7 |
| Activas | 7 |
| Con audiencia | 6 |
| Presupuesto total | 500 € |

### Botones
- **Crear con asistente** — asistente paso a paso para crear marca nueva
- **Añadir rápido** — formulario rápido para añadir marca

### Búsqueda
- Campo: "Buscar marcas por nombre, código o sector…"

### Proactive Actions
Panel de sugerencias de acciones pendientes por marca, con prioridad:
- `critical` — crítica
- `medium` — media
- `high` — alta
- Botón `Run` para ejecutar cada acción

### Perfil completo de cada marca

---

#### MigraTeam (`migrateam`)
- **Sector:** LegalTech · Vertical SaaS B2B (despachos de inmigración)
- **Estado:** activa
- **Plataformas:** 7 (LinkedIn, Email, Instagram, Facebook, Blog, WhatsApp, Google)
- **Stack:** Next.js 16 + FastAPI + Supabase (EU) + Stripe Subscriptions + OCR con Claude
- **Presupuesto:** 500 €/mes
- **Pricing:** 99/199/399 EUR (Pro: 199€, sin permanencia, 14 días gratis)
- **KPI norte:** demos agendadas (hasta 10 despachos de pago)
- **Audiencia B2B España:** despachos extranjería 1-10 abogados, Madrid/BCN/Valencia/Sevilla/Málaga
- **Audiencia B2B US:** immigration law firms (1-10 attorneys), USCIS/EOIR cases
- **Tono:** B2B profesional, orientado ROI con cifras verificables. Arquetipo: El Sabio + El Protector. Sin emojis en B2B, sin "AI-powered"
- **Motion:** LinkedIn del fundador + email frío + demo 15-30 min
- **En producción desde:** 26 mayo 2026, jurisdicciones: España + USA

---

#### NextJobs AutoApply (`nextjobs`)
- **Sector:** HR Tech / Automatización de empleo
- **Estado:** activa
- **Plataformas:** 3 (LinkedIn, Email, Web)
- **Stack:** React + Vite + FastAPI + Playwright + Claude API
- **Presupuesto:** Sin configurar
- **Descripción:** Automatiza búsqueda y postulación de empleos con IA: scraping, scoring, generación CV/cover letter y aplicación automática
- **Audiencia:** Profesionales de marketing digital buscando empleo remoto/híbrido en España
- **Tono:** Técnico pero accesible, orientado a resultados, data-driven

---

#### Marketing Dashboard (`marketing_dashboard`)
- **Sector:** MarTech / Business Intelligence
- **Estado:** activa
- **Plataformas:** 2 (Web, Email)
- **Stack:** Next.js 14 + Tremor + TanStack Query + Supabase
- **Presupuesto:** Sin configurar
- **Descripción:** Dashboard multi-tenant de inteligencia de marketing. Unifica datos de ads, social y CRM en una sola vista
- **Audiencia:** Directores de marketing y media buyers gestionando múltiples marcas; agencias
- **Tono:** Analítico, preciso, ejecutivo, orientado a KPIs

---

#### Píldora Digital (`pildora_digital_inbound`)
- **Sector:** Agencia de Marketing + Consultoría IA para PYMEs España
- **Estado:** activa
- **Plataformas:** 6 (Instagram, LinkedIn, Google, Facebook, Email, Web)
- **Stack:** Next.js + FastAPI + Oscar HQ + Claude API
- **Presupuesto:** Sin configurar
- **Descripción:** Agencia marketing IA para PYMEs. Modelo inbound: visitantes → diagnóstico IA → lead → cliente
- **Audiencia primaria:** PYMEs España (1-50 empleados) que quieren crecer con marketing digital e IA
- **Audiencia secundaria:** Abogados de extranjería y despachos en España
- **Tono:** Cercano pero profesional. Español de España. Orientado a resultados. Sin tecnicismos
- **Acciones críticas pendientes:** Configurar agentes del proyecto

---

#### Aterrizaje Médico en España (`aterrizaje_medico`)
- **Sector:** Infobusiness / mentoría médica de alto ticket
- **Estado:** activa
- **Plataformas:** 3 (Instagram, LinkedIn, Web)
- **Presupuesto:** Sin configurar
- **Descripción:** Mentoría 1:1 para médicos latinoamericanos que homologan título y buscan empleo en España. 3 programas: homologación/trámites, empleo médico, marca personal médica
- **Funnel actual:** webinar gratis + 7 guías descargables → llamada de diagnóstico
- **Propuesta Píldora:** funnel captación segmentado + agente IA de cualificación en DMs + ads retargeting (piloto 45 días)
- **Cross-sell potencial:** MigraTeam/Legal Brain

---

#### Mi Negocio En España (`mnee`)
- **Estado:** activa
- **Plataformas:** mencionadas en GHL
- **Descripción:** Consultoría de inversión y negocios en España. Asesoría integral para emprendedores e inversores
- **Notas:** Usa GoHighLevel como plataforma

---

#### Multiverse Computing (`multiverse`)
- **Estado:** activa
- **Descripción:** Cliente tech (computación cuántica). Tiene consola Signal independiente (`SGNL`)
- **Acciones críticas pendientes:** Configurar agentes del proyecto, Seleccionar plataformas

---

## 📁 Proyectos

**URL:** `https://app.pildoradigital.com/projects`
**Descripción:** Vista ecosistema de todos los proyectos técnicos del sistema. Muestra 11 proyectos registrados (más que las 7 marcas, porque incluye proyectos de infraestructura).

### Botón
- **Nuevo proyecto** → `/projects/new`

### Los 11 proyectos activos

| Código | Nombre | Stack principal | Descripción |
|---|---|---|---|
| **MGRT** | MigraTeam | Next.js, FastAPI, PostgreSQL +2 | SaaS B2B2C para extranjería. MigraCheck (gratis) → validación IA → derivación a Abogados Partners |
| **NJOBS** | NextJobs AutoApply | React, FastAPI, SQLite +1 | Sistema automatizado de búsqueda y postulación con IA. Scraping, matching, CVs personalizados |
| **MKTDB** | Marketing Dashboard | Next.js, Next.js, Supabase +1 | Dashboard multi-tenant de inteligencia de marketing. Unifica ads, social y CRM |
| **MNEE** | Mi Negocio En España | Web, GoHighLevel, GoHighLevel +1 | Consultoría inversión y negocios en España. Asesoría para emprendedores e inversores |
| **OHQD** | Píldora Digital | Next.js, FastAPI, PostgreSQL +1 | El propio Command Center — sistema de agentes IA (CrewAI + FastAPI + Claude API) |
| **ODSN** | Open Design | Node.js, Cloudflare | Generación de carruseles/creatividades. Daemon local con auth-proxy + túnel Cloudflare en `design.pildoradigital.com` |
| **PRCS** | Píldora Recursos | Next.js, Vercel | Web-producto de recursos gratuitos y biblioteca de descargas de Píldora Digital (imán de leads) |
| **SGNL** | Multiverse Console | Python, multiverse-console | Consola Signal Engine para cliente Multiverse (icp_multiverse). NextAuth propio, aislado del pd_sso |
| **PSGN** | Píldora Signal Console | Python, pildora-signal-console | Consola Signal Engine para prospección propia de Píldora (icp_pildora). NextAuth propio, aislado |
| **N8N** | n8n | n8n, Railway | Automatizaciones propias de Oscar. Instancia self-hosted en Railway. No es repo git, solo Dockerfile |
| **OCON** | Oscar Console | Next.js, Supabase, Vercel | Torre de control interna. Salud de consular_intel (Scraper) y corpus Legal Brain (RAG). Evolución prevista: Legal Brain de MigraTeam |

### Notas de arquitectura
- Cada proyecto tiene botón **Abrir** → abre el proyecto en una nueva vista
- Los proyectos de infraestructura (n8n, Signal Console) son independientes del Command Center principal
- `pd_sso` es el sistema de Single Sign-On compartido entre proyectos (algunos están aislados de él)

---

## 👤 Perfil de Usuario

**URL:** `https://app.pildoradigital.com/profile`
**Descripción:** Configuración del perfil personal del usuario logueado.

---

## 🔐 Admin — Panel de Administración

### Usuarios
**URL:** `https://app.pildoradigital.com/admin/users`
**Descripción:** Crear subcuentas, asignar proyectos y gestionar roles.

#### KPIs
| Métrica | Valor actual |
|---|---|
| Total usuarios | 9 |
| Activos | 9 |
| Editores | 2 |
| Viewers | 2 |

#### Tabs
```
Usuarios | Invitaciones
```

#### Botones
- **Invitar** — envía invitación por email
- **Crear Usuario** — crea usuario directamente

#### Usuarios registrados
| Usuario | Handle | Rol | Proyectos asignados |
|---------|--------|-----|---------------------|
| Angel Granados | @Angel | Admin | — |
| Admin | @Admin | Admin | — |
| Andrea Requema | @Andrea | Editor | MigraTeam, Píldora Digital, Open Design |
| Cliente Viewer | @viewer | Viewer | Píldora Digital |
| César Gil | @cesar | Viewer | Mi Negocio En España |
| Linda Bernal | @linda | Editor | MigraTeam, Mi Negocio En España |
| Ingerber Azuaje | @ingerber | Admin | MigraTeam, Mi Negocio En España, Píldora Digital |
| Maria | @oscar_admin | Admin | — |
| Oscar Alcántara | @oscar | Admin | MigraTeam, NextJobs, Marketing Dashboard, MNEE, Píldora Digital |

#### Roles disponibles
- **Admin** — acceso total
- **Editor** — puede crear y editar contenido en sus proyectos
- **Viewer** — solo lectura en sus proyectos

---

### Prompt Optimizer
**URL:** `https://app.pildoradigital.com/admin/prompt-optimizer`
**Descripción:** Editor de prompts de los agentes del sistema. Los cambios se aplican al próximo crew. Edita el archivo `src/config/agents.yaml`.

#### Agentes disponibles (7 C-Level)
| ID | Agente | Sub-agentes |
|---|---|---|
| `ceo` | CEO — Chief Executive Officer | 6 sub-agentes |
| `cmo` | CMO — Chief Marketing Officer | 9 sub-agentes |
| `cto` | CTO — Chief Technology Officer | — |
| `coo` | COO — Chief Operations Officer | 3 sub-agentes |
| `cfo` | CFO — Chief Financial Officer | 1 sub-agente |
| `cso` | CSO — Chief Sales Officer | — |
| `chro` | CHRO — Chief Human Resources Officer | — |

---

### Presupuestos
**URL:** `https://app.pildoradigital.com/admin/budgets`
**Descripción:** Control de gasto mensual en IA por marca. Sistema de prioridades:

**Jerarquía de prioridad:**
```
Fila en esta tabla > budget_monthly en brands.yaml > fallback global ($0/mes)
```

**⚠️ IMPORTANTE:** Una marca sin fila y con `budget_monthly=0` en el YAML cae en el fallback global — que es un "hard stop" duro, no un aviso. Cualquier marca en "Fallback global" tiene ese tope activo ahora mismo.

---

### Crons
**URL:** `https://app.pildoradigital.com/admin/crons`
**Descripción:** Gestión de tareas recurrentes del sistema. Dos secciones: tareas personalizadas (cross-brand) y System Jobs (solo lectura).

#### Tareas personalizadas (4 configuradas)
| Nombre | Marca | Recurrencia | Estado |
|--------|-------|-------------|--------|
| Revisión semanal pipeline — MigraTeam | migrateam | Semanal | Activa |
| Revisión semanal pipeline — Píldora | pildora_digital_inbound | Semanal | Activa |
| Revisión semanal pipeline — MNEE | mnee | Semanal | Activa |
| Contenido semanal MigraTeam (LinkedIn + email) | migrateam | Semanal | Inactiva |

Filtros: Todas las marcas / Todas / Activas / Inactivas

#### System Jobs (26 jobs — solo lectura)
| Job | Intervalo | Función |
|---|---|---|
| `timeout_stale_executions` | 5 min | Limpia ejecuciones colgadas |
| `dispatch_scheduled_campaigns` | 5 min | Lanza campañas programadas |
| `dispatch_due_tasks` | 5 min | Lanza tareas pendientes |
| `enqueue_due_jobs` | 5 min | Encola jobs programados |
| `check_new_leads` | 5 min | Revisa nuevos leads |
| `sweep_expired_pending_bookings` | 5 min | Limpia reservas caducadas |
| `run_dev_sync` | 10 min | Sincronización de desarrollo |
| `check_run_liveness` | 10 min | Verifica que los runs estén vivos |
| `ejecutar_reparto` | 10 min | Distribuye tareas entre agentes |
| `sincronizar_tras_corridas` | 15 min | Sincroniza después de ejecuciones |
| `run_heartbeat` | 30 min | Verificación de salud del sistema |
| `dispatch_due_enrollments` | 1 hora | Lanza inscripciones programadas |
| `reclaim_stale_jobs` | 30 min | Reclama jobs abandonados |
| `run_daily_signal_diagnostics` | 1 hora | Diagnóstico diario de Signal |
| `sync_scraper_accounts` | 1 hora | Sincroniza cuentas del scraper |
| `run_scraper_outreach` | 30 min | Ejecuta outreach del scraper |
| `check_critical_consular_changes` | 1 hora | Monitorea cambios consulares críticos |
| `send_booking_reminders` | 1 hora | Envía recordatorios de citas |
| `run_memory_update` | diario (02:00) | Actualiza la memoria del sistema |
| `purge_job` | diario (04:15) | Limpieza de jobs antiguos |
| `check_stale_contacts` | diario (07:00) | Revisa contactos sin actividad |
| `run_due_reminders` | diario (09:00) | Ejecuta recordatorios pendientes |
| `job_purga_crudo` | 1 día | Purga de datos crudos |
| `run_auto_optimize` | lunes 03:00 | Auto-optimización semanal |
| `send_weekly_report` | lunes 08:00 | Envía reporte semanal |
| `run_migration_drift_check` | 7 días | Verifica drift en migraciones |

---

### Activity Log
**URL:** `https://app.pildoradigital.com/admin/activity-log`
**Descripción:** Crew runs + CRM interactions — **83 entradas** en total.

#### Controles
- **Export CSV** — exporta el log completo
- **Filtro de marca** — pildora_digital_inbound | migrateam | mnee | todas
- **Filtro de tipo** — Todo / Crew Runs / CRM

#### Historial reciente (selección)
| Fecha | Tipo | Marca | Crew/Acción |
|-------|------|-------|-------------|
| 20 oct 15:09 | Crew Run | pildora_digital_inbound | demo_follow_up |
| 19 oct | Crew Run | migrateam | image_generation (×varios) |
| 19 oct 02:04 | Crew Run | mnee/pildora/migrateam | sales_pipeline_review |
| 17 oct 08:39 | Crew Run | migrateam | demo_follow_up |
| 13 oct | Crew Run | pildora/mnee/migrateam | email_campaign / sales_pipeline_review |
| 06 oct | CRM | migrateam | Llamada y deje un mensaje |

---

## 🚀 Crews — El Corazón del Sistema

**URL:** `https://app.pildoradigital.com/crews`
**Descripción:** Flujos multi-agente pre-construidos que encadenan agentes para completar tareas complejas de forma autónoma.

### Stats del sistema
| Métrica | Valor |
|---------|-------|
| Flujos disponibles | 17 |
| Agentes totales (en todos los crews) | 69 |
| Duración media | 5s |
| Crews custom configurados | 12 |
| Plantillas de inicio rápido | 6 |

### Botón principal
- **Nuevo crew** — crea un crew personalizado

---

### Plantillas de inicio rápido (6)

| Plantilla | Agentes | Descripción |
|-----------|---------|-------------|
| 🔄 Repurposing semanal | data_analyst, planner, copywriter | Analiza contenido top, adapta para distintas plataformas |
| 🔍 Auditoría de marca | seo, data_analyst, cmo +1 | Análisis competitivo, gaps de contenido, oportunidades SEO |
| 📣 Campaña de Ads | ads, copywriter, compliance +1 | Investiga audiencia, crea variantes de copy, estructura campaña |
| ⚙️ Sprint de revisión técnica | code_reviewer, architect, devops +1 | Revisión código, arquitectura, seguridad, plan despliegue |
| 📧 Campaña de email | email_marketer, copywriter, qc +1 | Crea campaña email: asuntos, cuerpo, segmentación |
| 🐛 Respuesta a incidentes | debugger, code_reviewer, devops +1 | Reproduce, aísla, corrige y verifica problemas producción |

---

### Catálogo completo de Crews (17 flows por categoría)

#### Operaciones (1)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| Incident Response | CTO, DevOps, Debugger, CEO | 6-10 min | Respuesta ante incidentes técnicos o de reputación |

#### Estrategia (1)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| 🔬 Full Business Audit | CFO, CMO, CTO, COO, CEO | 12-20 min | Evaluación completa: marketing, tech, ops, finanzas |

#### Content (3)
| Crew | Agentes | Tiempo est. | Última ejecución | Estado |
|------|---------|-------------|------------------|--------|
| ✍️ Content Creation | SEO, Planner, Copywriter, Compliance, QC, CMO | 4-8 min | 5/10/2026 | OK 877s |
| 🔄 Content Curation | Data Analyst, Planner, Copywriter | 3-5 min | — | — |
| Email Campaign | Planner, Copywriter, Email Marketer, Compliance, QC | 5-8 min | 13/10/2026 | OK 742s |

#### Marketing (3)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| Content Factory | Planner, Copywriter, Ads, QC | 3-6 min | Repurpose 1 pieza hero en 20+ formatos |
| SEO/GEO Engine | SEO, Planner, Copywriter, QC | 3-6 min | Keyword research → GEO → copy → QC |
| Funnel Builder | Funnel Strategist, Funnel Copywriter | 4-8 min | Copy TOFU/MOFU/BOFU completo |

#### Analytics (1)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| 📊 Analytics & Optimization | Data Analyst, Ads, CMO | 3-5 min | Performance, KPIs y recomendaciones |

#### Projects (2)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| ⚡ Project Optimizer | Code Reviewer, DevOps, CMO, Financial Analyst, Process Optimizer, CEO | 8-12 min | Audita y optimiza proyecto existente |
| Brand Onboarding | CEO, CMO, CTO | 5-8 min | Análisis inicial nueva brand + primeras 3 acciones |

#### Finance (1)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| Financial Review | Financial Analyst, COO, CEO | 4-7 min | Burn rate, runway, proyecciones |

#### Sales (3)
| Crew | Agentes | Tiempo est. | Última ejecución | Estado |
|------|---------|-------------|------------------|--------|
| Sales Pipeline Review | Data Analyst, CMO, CEO | 3-6 min | 19/10/2026 | OK 57.8s |
| Demo Follow-Up | Email Marketer, Copywriter, Compliance, QC | 4-6 min | 20/10/2026 | OK 269.6s |
| MigraTeam Outbound Sequence | Email Marketer, Copywriter, Compliance, QC | 6-10 min | — | — |

#### Tech (1)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| Design System Migration | Architect, Frontend Engineer, Code Reviewer, Compliance, DevOps, DS Migration Coordinator | 10-20 min | Hybrid CrewAI + Claude Code: migra página a DS v2 tras feature flag |

#### Operations (1)
| Crew | Agentes | Tiempo est. | Descripción |
|------|---------|-------------|-------------|
| Create & Deploy Project | Architect, DevOps, CTO, CEO | 5-15 min | Scaffold, code y deploy de proyecto nuevo |

---

### Agentes identificados en crews
```
SEO Specialist · Planner · Copywriter · Compliance · QC · CMO
Data Analyst · Email Marketer · Ads · Funnel Strategist · Funnel Copywriter
Code Reviewer · Architect · DevOps · Debugger · CTO · COO · CFO · CEO
Process Optimizer · Financial Analyst · Frontend Engineer · DS Migration Coordinator
```

### ⚠️ Nota importante: acceso desde Telegram
Las crews lanzables desde Telegram tienen una lista cerrada (configurada en Admin → Config Sistema → `telegram_crews_permitidos`). **Excluidas:** `financial_review` y `full_business_audit`.

---

## 📊 Dashboard

**URL:** `https://app.pildoradigital.com/dashboard`
**Descripción:** Vista de control operativo con estado del sistema, KPIs y acceso rápido a crews.

**Saludo:** "Buenas tardes, Angel · Sistema operativo · agentes · crews"

#### Acciones rápidas (2 botones)
- **Nueva pieza** → /content
- **Abrir Chat** → /chat

#### Widget "Esta semana"
- Ver piezas → /content
- Nueva pieza
- Estado: "Calculando la semana…"

#### Acciones directas (4 tiles)
| Tile | Descripción |
|------|-------------|
| Lanzar Crew | Content, análisis, auditoría |
| Marketing | Campañas, ads, social |
| Kanban | Tareas del equipo |
| Analítica | KPIs y resultados |

#### Inbox
- **10 notificaciones** → "Ver todas"
- Ejemplos recientes: "Crew iniciado: chat_ceo", "Crew completado: email_campaign", "Crew iniciado: image_generation"

#### KPIs en tiempo real
| KPI | Valor |
|-----|-------|
| HOY — Ejecuciones del día | 0 |
| Marca activa | MigraTeam |

#### Últimas ejecuciones
| Crew | Marca | Cuándo |
|------|-------|--------|
| demo_follow_up | pildora_digital_inbound | Hace 1D |
| image_generation | MigraTeam | Hace 2D (×3) |
| sales_pipeline_review | Mnee | Hace 2D |

---

## 🏗️ Arquitectura General del Sistema

### Marcas activas en el sistema
| Marca | ID interno | Descripción |
|---|---|---|
| MigraTeam | `migrateam` | Servicios de migración/extranjería |
| NextJobs AutoApply | `nextjobs` | Auto-aplicación a empleos |
| Marketing Dashboard | `marketing_dashboard` | Dashboard interno de marketing |
| Píldora Digital | `pildora_digital_inbound` | La propia plataforma (inbound) |
| Aterrizaje Médico en España | `aterrizaje_medico` | Servicios para médicos extranjeros |
| Mi Negocio En España | `mnee` | Servicios para emprendedores |
| Multiverse Computing | `multiverse` | Cliente tech (Multiverse) |

### Stack tecnológico identificado
| Componente | Tecnología |
|---|---|
| Automatizaciones externas | n8n (actualmente) → migrando a infraestructura propia |
| Motor IA | Anthropic Claude (API + plan Max, 200€/mes en créditos) |
| Modelos | Claude Haiku (operaciones frecuentes), Claude API |
| Alternativa de routing | OpenRouter (abandonado, ahora créditos propios) |
| Infraestructura | Railway |
| Outreach | Apollo, LinkedIn |
| CRM externo | GHL (GoHighLevel) |
| Datos empresariales | BORME (registro de nuevas empresas España) |
| Comunicaciones internas | Telegram (bot + grupos) |

### Flujo principal del sistema
```
Signal (captación) 
  → CRM (procesamiento de leads)
    → Propuestas/Email (envío)
      → Ingresos (seguimiento)

Brand Brain (contexto)
  → Crews de agentes (ejecución)
    → Output (contenido, propuestas, análisis)
      → Resultados (archivo)
```

### Jerarquía de agentes (según tour)
- **CEO AI** — agente de coordinación general (Command Center)
- **C-Level agents** — agentes por departamento (Chat IA)
- **24 agentes especializados** — ejecutan tareas reales
- **Crews** — flujos pre-armados de múltiples agentes

### Modelo de Crews
```
Crew = [SEO Agent] → [Copy Agent] → [QC Agent] → [CMO Agent]
Ejemplo: content_creation
  Input: marca + contexto
  Output: contenido guardado en /documents
```

---

## 📌 Notas de Estado del Sistema (2026-10-21)

- **Ejecuciones hoy:** 182 (133 exitosas, 0 en curso)
- **Notificaciones sin leer:** 214
- **Brand Brain MigraTeam:** 71% completo (5/7 secciones)
- **Marcas con chunks indexados (MigraTeam):** 58 chunks
- **Estado general:** plataforma funcional, varias piezas en beta

---

*Documento generado por Ángel Granados con Claude Code — actualizar con cada exploración de la plataforma*

---

---

# 🎯 BRIEF EJECUTIVO MULTI-ANGULAR — PÍLDORA DIGITAL
> Auditoría de 5 maestrías en paralelo · 21 octubre 2026
> Agentes: CTO · CMO · CSO · CFO/BI · COO

---

## MARCADOR GLOBAL POR DIMENSIÓN

| Dimensión | Auditor | Puntuación | Síntesis en una línea |
|-----------|---------|------------|----------------------|
| 🔧 Técnica/Arquitectura | CTO | **5.5 / 10** | Funcional pero prototype-grade; hard stop en 6/7 marcas y Heartbeat YELLOW sin resolver |
| 📣 Marketing/Contenido | CMO | **4.5 / 10** | La maquinaria existe pero trabaja al 30%; Signal→Email bridge roto, 5/7 crews de contenido sin ejecutar |
| 💼 Comercial/GTM | CSO | **3.5 / 10** | €0 revenue, 0 clientes reales, 0 proceso de venta activo a pesar de 493 despachos en el embudo |
| 💰 Financiero/BI | CFO | **3.0 / 10** | Burn €315-620/mes sin ingresos; LTV/CAC proyectado 9:1 excelente pero completamente sin validar |
| ⚙️ Operaciones | COO | **5.5 / 10** | 12/17 crews dormidos, 0 automatizaciones post-venta activas, conocimiento concentrado en 1 persona |

**Media global: 4.4 / 10**
La plataforma tiene arquitectura sólida y diferenciada pero está en modo pre-comercial con brechas operativas críticas que impiden monetizarse.

---

## 🔥 TOP 10 HALLAZGOS CRÍTICOS (cross-dimensión)

### 1. HARD STOP FINANCIERO — 6 de 7 marcas bloqueadas (Prioridad: CRÍTICA)
**Problema:** El sistema de presupuestos tiene un fallback global de $0/mes. Las 6 marcas sin fila en `/admin/budgets` tienen un hard stop automático — cualquier crew cortará sin aviso.
**Solo MigraTeam tiene presupuesto (500€/mes).** NextJobs, Marketing Dashboard, Aterrizaje Médico, Píldora Digital, MNEE y Multiverse están paralizadas para IA.
**Fix:** 30 minutos. Configurar budget mínimo ($10-50/mes) en `/admin/budgets` para cada marca.

### 2. REVENUE = €0 / CLIENTES REALES = 0 (Prioridad: CRÍTICA)
**Problema:** Las 6 propuestas del sistema son autopruebas internas a `oscarsao20@gmail.com`. La única propuesta "Aceptada" es un test del propio Oscar. No hay ni un euro de revenue externo confirmado.
**Sin embargo:** 493 despachos en Signal con 329 emails disponibles. El activo existe; la ejecución comercial, no.
**Fix:** Lanzar outreach real a los 90 despachos ya enriquecidos con contacto esta semana.

### 3. HEARTBEAT YELLOW — alarma activa sin diagnóstico (Prioridad: ALTA)
**Problema:** El sistema lleva tiempo en estado YELLOW (4 comprobaciones el 21/10) sin que se haya identificado qué falla. Un sistema con clientes no puede operar con un monitor en alerta permanente.
**Señal adicional:** `timeout_stale_executions` cada 5 min y `reclaim_stale_jobs` cada 30 min son jobs de limpieza que deberían ser excepcionales, no crónicos. Indican ejecuciones colgadas como problema sistémico.
**Fix:** Auditar los 4 checks de Heartbeat. Identificar cuál falla. Documentar si es falso positivo o problema real.

### 4. ACUERDO ECONÓMICO OSCAR-ÁNGEL SIN FORMALIZAR (Prioridad: CRÍTICA)
**Problema:** No hay contrato ni acuerdo formal de roles, compensación, equity o condiciones de salida entre los dos fundadores. El primer cliente pagador hará visible esta ausencia de forma dolorosa.
**El mayor riesgo no-técnico del proyecto.**
**Fix:** Esta semana. Definir por escrito: % de revenue, participación futura, rol de cada uno, salario diferido.

### 5. SIGNAL → EMAIL: EL PUENTE ROTO MÁS CRÍTICO (Prioridad: ALTA)
**Problema:** Signal tiene 329 emails de despachos cualificados. El módulo de Email Marketing tiene **4 contactos** (3 de prueba). No hay puente operativo entre el motor de captación y el motor de outreach.
**Esta es la brecha que convierte 493 prospectos en 0 conversaciones.**
**Fix:** Importar los 329 emails de Signal al módulo Email. Lanzar `MigraTeam Outbound Sequence` (crew disponible, nunca ejecutado, 6-10 min).

### 6. LLM ROUTER DESCONECTADO — coste de inferencia no optimizado (Prioridad: MEDIA-ALTA)
**Problema:** Existe una configuración de `task_model_map` en `/settings` que asigna modelos por tipo de tarea, pero **no está conectada a ningún crew de CrewAI**. Todos los crews usan el modelo global. `content_creation` (877s) con Claude Sonnet puede costar 10x más que con Haiku o DeepSeek Flash.
**Estimación:** conectar el Router podría reducir el coste de inferencia un 50-80%.
**Fix:** Conectar `task_model_map` al dispatcher de CrewAI (trabajo de dev, 1-3 días).

### 7. 12/17 CREWS NUNCA EJECUTADOS (Prioridad: MEDIA)
**Problema:** Solo 5 crews tienen evidencia de uso real. Los 12 restantes — incluyendo `SEO/GEO Engine`, `Funnel Builder`, `Content Curation`, `Analytics & Optimization`, `MigraTeam Outbound Sequence` — nunca se han lanzado. La inversión en infraestructura de IA no se está aprovechando.
**Crews con mayor ROI inmediato sin ejecutar:** `SEO/GEO Engine` (posicionamiento IA), `MigraTeam Outbound Sequence` (outreach directo), `Content Curation` (variedad de contenido).

### 8. AUTOMATIZACIÓN POST-PAGO INACTIVA (Prioridad: ALTA)
**Problema:** Existe la automatización "Post-pago: Bienvenida despacho MigraTeam" con trigger `payment.completed` configurada. Tiene 0 disparos porque está **apagada**. Un cliente que pague 199€ no recibe nada automatizado.
**Fix:** 5 minutos. Toggle en `/automatizaciones` → activar.

### 9. CONCENTRACIÓN DE CONOCIMIENTO EN OSCAR (Prioridad: ALTA)
**Problema:** Oscar es el único que entiende, puede modificar y mantiene el sistema. `brands.yaml`, `agents.yaml`, `agent_model_tiers.yaml`, la arquitectura Railway, el código FastAPI — todo está en su cabeza y en un repositorio al que Ángel aún no tiene acceso.
**Bus factor = 1.** Si Oscar no está disponible, el sistema se congela.
**Fix:** Acceso de Ángel a repositorios (prometido en llamada, no ejecutado). Sesiones de transferencia técnica. Documentación de procesos críticos.

### 10. 5 DE 9 USUARIOS SON ADMIN (Prioridad: MEDIA)
**Problema:** Angel, @Admin, Oscar, Maria (@oscar_admin) e Ingerber Azuaje son todos Admin — el 56% de los usuarios tienen acceso privilegiado total, incluyendo a la Config Sistema, Presupuestos, y Usuarios. Superficie de seguridad excesiva.
**Fix:** Revisar roles. @Admin y @oscar_admin probablemente son cuentas de sistema que no necesitan rol Admin activo.

---

## 📊 MATRIZ DE IMPACTO × ESFUERZO — Quick Wins

| Acción | Impacto | Esfuerzo | Dimensión |
|--------|---------|----------|-----------|
| Configurar presupuestos 6 marcas bloqueadas | 🔴 Crítico | 30 min | Tech + Marketing |
| Activar automatización post-pago | 🔴 Alto | 5 min | Ops + Comercial |
| Activar cron "Contenido semanal MigraTeam" | 🟠 Alto | 5 min | Marketing |
| Importar Signal emails → Email Marketing | 🔴 Crítico | 30 min | Comercial |
| Formalizar acuerdo Oscar-Ángel | 🔴 Crítico | 1-2 días | Estratégico |
| Completar Brand Brain (Canales + Competidores) | 🟠 Alto | 1 hora | Marketing |
| Resolver Heartbeat YELLOW | 🔴 Alto | 2-4 horas | Tech |
| Lanzar MigraTeam Outbound Sequence | 🟠 Alto | 10 min | Comercial |
| Lanzar SEO/GEO Engine | 🟡 Medio | 10 min | Marketing |
| Acceso de Ángel a repositorios | 🟠 Alto | 1 hora Oscar | Ops |

---

## 💡 ANÁLISIS ESTRATÉGICO CONSOLIDADO

### Lo que funciona bien (activos reales del proyecto)
1. **Signal** — 493 despachos, 329 con email, piloto automático activo. Es el activo más valioso del ecosistema. Funciona.
2. **Brand Brain + Crews** — el concepto es diferenciador. MigraTeam tiene 7 meses de cadencia de `content_creation`. La arquitectura RAG por marca es correcta.
3. **Infraestructura de propuestas** — el flujo Propuesta → Stripe → pago existe y está técnicamente validado.
4. **17 crews construidos** — más que suficientes para cubrir todas las necesidades de marketing, ventas y operaciones de cualquier PYME.
5. **ICP MigraTeam es correcto** — lead real web confirmado en la llamada. El mercado (despachos extranjería) tiene dolor, presupuesto y ROI demostrable.

### Lo que no funciona (brechas que impiden monetización)
1. **0 proceso comercial activo** — nadie está vendiendo. Signal llena el CRM pero nadie convierte.
2. **Brechas de integración** — Signal y Email no están conectados. CRM y Propuestas no tienen flujo automático. Post-pago no tiene automatización.
3. **Deuda técnica no resuelta** — Heartbeat YELLOW, LLM Router desconectado, n8n sin migrar, hard stop en 6 marcas.
4. **Base humana frágil** — 2 personas, 1 de ellas nueva, todo el conocimiento técnico en 1.
5. **Sin validación de mercado real** — todas las métricas son internas. Ningún precio, flujo o propuesta ha sido validada con un cliente externo.

### El camino crítico hacia primer revenue (60 días)

```
Semana 1 (esta semana):
  ✓ Configurar presupuestos de las 6 marcas bloqueadas (30 min)
  ✓ Activar automatización post-pago (5 min)
  ✓ Formalizar acuerdo económico Oscar-Ángel (reunión)
  ✓ Resolver Heartbeat YELLOW (diagnóstico técnico)
  
Semana 2:
  → Puente Signal → Email: importar 329 emails
  → Lanzar MigraTeam Outbound Sequence (primer outreach real)
  → Completar Brand Brain: Canales + Competidores
  → Activar cron de content_creation
  
Semana 3-4:
  → Seguimiento activo de respuestas al outreach
  → Agendar primeras 3-5 demos reales
  → Ejecutar demo_follow_up crew post-demo
  
Semana 5-8:
  → Enviar propuesta Pro (199€) a leads positivos
  → Primer cliente pagador real
  → Caso de éxito → iteración → siguiente cliente
```

**Objetivo realista: 1 cliente pagador en 30 días. 3-5 clientes en 60-90 días. MRR de 597-995€.**

---

## 🎯 POSICIÓN DE ÁNGEL EN EL PROYECTO

### Qué necesita Ángel para ser efectivo
1. **Acceso a repositorios** (prometido, no ejecutado) — para entender qué se puede cambiar
2. **Acceso a grupos de Telegram** — canal operativo del sistema
3. **Sesiones de transferencia técnica con Oscar** — al menos 3h en las primeras 2 semanas
4. **Claridad en el acuerdo económico** — sin esto, no hay base para comprometerse

### Dónde puede generar más valor inmediato
- **Operación comercial** — Signal tiene los datos; alguien necesita convertirlos en conversaciones. Ángel puede ser ese puente.
- **Orquestación de crews** — lanzar los 12 crews dormidos para las marcas adecuadas
- **Documentación y procesos** — la biblia que estás leyendo es el inicio. Crear playbooks de operación.
- **Puente con clientes** — ser el punto de contacto en demos y onboarding

### Lo que NO debe hacer en la fase inicial
- Cambiar código sin acceso a repositorios y sin entender la arquitectura
- Asumir que el sistema funciona como está documentado — hay muchos estados YELLOW y "inactivo"
- Tomar decisiones de presupuesto sin el acuerdo económico formalizado

---

## 📋 CHECKLIST PRE-ONBOARDING COMPLETO

### Técnico (Oscar debe habilitar)
- [ ] Acceso de Ángel a repositorios GitHub
- [ ] Acceso a grupos de Telegram operativos
- [ ] Credenciales de Railway, Vercel, Cloudflare
- [ ] Configurar presupuestos en `/admin/budgets` para todas las marcas

### Comercial (ejecutar en semana 1-2)
- [ ] Importar 329 emails de Signal al módulo Email
- [ ] Lanzar `MigraTeam Outbound Sequence` (primer outreach)
- [ ] Definir buyer persona definitivo (¿el dueño del despacho o el abogado?)
- [ ] Agendar primera demo real con un despacho de Madrid

### Estratégico (esta semana)
- [ ] Formalizar acuerdo económico Ángel-Oscar (roles, % revenue, condiciones)
- [ ] Resolver Heartbeat YELLOW (diagnóstico)
- [ ] Activar automatización "Post-pago: Bienvenida despacho MigraTeam"

### Marketing (semana 1-2)
- [ ] Completar Brand Brain MigraTeam: secciones Canales y Competidores
- [ ] Activar cron "Contenido semanal MigraTeam (LinkedIn + email)"
- [ ] Lanzar SEO/GEO Engine para MigraTeam (primero)
- [ ] Configurar destinos de publicación para las 6 marcas restantes

---

*Brief generado por 5 agentes especializados (CTO, CMO, CSO, CFO/BI, COO) el 21/10/2026 · Ángel Granados + Claude Code*
