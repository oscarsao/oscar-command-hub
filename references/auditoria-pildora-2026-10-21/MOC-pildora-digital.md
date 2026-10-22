# MOC — Píldora Digital Knowledge Base
**Map of Content** | Actualizado: 21/10/2026

---

## Documentos principales

### Biblia de la plataforma
- [[pildora-digital-biblia]] — Wiki completa de Píldora Digital. Módulos, arquitectura, usuarios, datos operativos, Brief Ejecutivo Multi-Angular (scores CTO/CMO/CSO/CFO/COO)

### Auditorías
- [[informe-ux-pildora-digital-oscar]] — 40 hallazgos UX/UI verificados. 7 críticos · 16 altos · 15 medios · 2 bajos
- [[informe-tecnico-fallos-oscar]] — 20 fallos técnicos. 5 críticos · 8 altos · 7 deuda técnica · 3 mantenimiento

### Documentación técnica
- [[documentacion-tecnica-pildora-digital]] — Stack, arquitectura, módulos en detalle, flujos de trabajo, integraciones, glosario, hoja de ruta. Preparado para NotebookLM.

---

## Índice de temas clave

### Arquitectura
- Stack → [[documentacion-tecnica-pildora-digital#PARTE II — ARQUITECTURA TÉCNICA]]
- Backend Oscar HQ (FastAPI) → [[pildora-digital-biblia]]
- Frontend Next.js → [[documentacion-tecnica-pildora-digital]]
- Railway + Vercel + Cloudflare → [[documentacion-tecnica-pildora-digital#2.2 Arquitectura de despliegue]]

### Fallos y problemas
- Fallos técnicos críticos → [[informe-tecnico-fallos-oscar#Fallos críticos]]
- Fallos UX críticos → [[informe-ux-pildora-digital-oscar]]
- run_dev_sync en producción → [[informe-tecnico-fallos-oscar#FC-01]]
- Heartbeat YELLOW → [[informe-tecnico-fallos-oscar#FC-02]]
- LLM Router desconectado → [[informe-tecnico-fallos-oscar#FC-03]]
- Hard Stop $0 en 6 marcas → [[informe-tecnico-fallos-oscar#FC-04]]

### Módulos de la plataforma
- Brand Brain (RAG) → [[documentacion-tecnica-pildora-digital#3.1 Brand Brain]]
- Crews + Agentes → [[documentacion-tecnica-pildora-digital#3.2 Crews]]
- Signal (leads) → [[documentacion-tecnica-pildora-digital#3.3 Signal]]
- CRM → [[documentacion-tecnica-pildora-digital#3.4 CRM]]
- Avatar Studio → [[documentacion-tecnica-pildora-digital#3.5 Avatar Studio]]
- Heartbeat → [[documentacion-tecnica-pildora-digital#3.6 Heartbeat]]

### Datos operativos
- Métricas actuales → [[documentacion-tecnica-pildora-digital#5.1 Métricas actuales]]
- Usuarios y roles → [[documentacion-tecnica-pildora-digital#PARTE IV]]
- Marcas gestionadas → [[documentacion-tecnica-pildora-digital#5.2 Marcas gestionadas]]

### Prioridades de acción
- Acciones técnicas esta semana → [[informe-tecnico-fallos-oscar#Prioridades de acción para Oscar]]
- Hoja de ruta técnica → [[documentacion-tecnica-pildora-digital#PARTE XI]]
- Critical path 60 días → [[pildora-digital-biblia]] (Brief Ejecutivo)

---

## Estado del conocimiento

| Área | Completitud | Fuente |
|------|-------------|--------|
| UX/UI | Alta — 40 hallazgos verificados | Navegación directa |
| Arquitectura | Media — inferida + observada en UI | UI admin + síntomas |
| Backend/código | Baja — sin acceso al repositorio | Inferida |
| Datos operativos | Alta | Módulos de la plataforma |
| Integraciones | Media | Settings + admin |
| Crews/Agentes | Media — nombres y modelo, no contenido | UI de crews |

**Pendiente de acceso:** Repositorio de código Oscar HQ. Con acceso al código se puede completar la documentación de endpoints, modelos de datos, lógica de negocio y tests.

---

*MOC creado por Ángel Granados — 21/10/2026*
