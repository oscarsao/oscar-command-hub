# Railway: incidente del 21-22 oct y alternativas de hosting (Railway vs DigitalOcean + Dokploy)

**Fecha:** 2026-10-22 · **Tarea:** t_0e4b94af · **Estado:** diagnóstico + propuesta. No se ha tocado Railway, DNS ni DigitalOcean.

## 1. Diagnóstico (fuente: status.railway.com, leída el 22-oct)

Incidente **«Railway Platform Outage»**, *Major Outage*, estado «Identified» (sin resolver en el último aviso, 22-oct 09:28 UTC).

| Cuándo (UTC) | Qué |
|---|---|
| 21-oct 07:24 | Primera alerta, problemas en backend |
| 21-oct 11:33 | Dashboard, API y CLI caídos; builds pausados en todas las regiones |
| 21-oct 22:10 | **Aviso de seguridad**: Railway recomienda revocar variables de entorno y revisar logs |
| 22-oct 04:39 | **Todos los tokens de API de Railway invalidados** (también sesiones OAuth y credenciales de buckets) |
| 22-oct 07:11 | Builds de nuevo para Pro y Enterprise |
| 22-oct 09:28 | Builds fallan más de lo normal, colas largas; Hobby/Free/Trial aún pendientes |

Lectura para Oscar:
- La causa raíz **no está publicada**; hay un incidente de seguridad detrás (no es solo una caída).
- La página marca **Payments & Billing al 100%**: no menciona fallo de facturación. Lo que Oscar ve como «no puedo hacer billing» probablemente es el dashboard/sesión invalidada. ⚠ Sin acceso a su cuenta no se puede confirmar; que Oscar diga qué pantalla/error ve.
- Los servicios **ya desplegados suelen seguir corriendo**; lo bloqueado son builds/deploys. ⚠ No verificado para los servicios concretos de Oscar (no se comprobó ninguna URL propia).
- Proyecto afectado: casi seguro **oscar-hq** (único con `railway up` manual) y el Scraper; MigraTeam staging también usa Railway (`projects/migrateam.md`). ⚠ Confirmar.

### Acciones de emergencia (las hace Oscar; nada se ha ejecutado)
1. **No relanzar despliegues repetidos** (Railway lo pide; alarga la cola). No hacer `railway up` hasta que cierren el incidente.
2. **Rotar los secretos** de los servicios Railway: según el aviso, Railway recomienda revocar las variables de entorno. Mínimo: `API_SECRET_KEY` de oscar-hq, `OHQ_API_KEY` (pildora-mcp la usa), claves de Supabase/DB y de LLM que estén en variables de Railway. Revisar logs de uso anómalo en los proveedores.
3. **Crear un token nuevo de Railway** cuando el dashboard vuelva y actualizarlo donde esté el viejo (el `.env` del hub / `railway_dir` en `lanes.yaml`). Hasta entonces `railway up` y los comandos de desplegar del hub fallarán con 401: es esperado, no un bug del hub.
4. Si el problema real es de **facturación** (tarjeta, límite de gasto): contactar a soporte de Railway desde station.railway.com / email; no hay parche temporal desde el hub.

## 2. Comparativa de hosting

**Aviso honesto:** la tabla que Oscar y el integrador montaron en vivo el 21-oct **no se ha podido recuperar** (no consta en el hub ni en tarjetas; este worker no tiene acceso a esa conversación). Lo de abajo es una **reconstrucción nueva** con fuentes secundarias del 22-oct; **no es la tabla original**. Oscar o el integrador deben contrastarla con lo que recuerden y corregirla. Precios sin confirmar en páginas oficiales (⚠).

| | Railway (actual) | DigitalOcean + Dokploy | Hetzner / OVH + Dokploy | Mini PC Minisforum |
|---|---|---|---|---|
| Coste/mes | Pro $20 + uso (RAM $10/GB, CPU $20/vCPU, vol. $0,15/GB). Hoy oscar-hq ≈ $12/mes (`CONTEXT.md`) | Droplet 4 GB/2 vCPU ≈ **$24**; 8 GB/4 vCPU ≈ **$48**; backups automáticos +20%. Dokploy: gratis (open source) | Ver `pitches/servidor-carriles.md` §1: Hetzner subió precios 2026 (⚠ stock CX); OVH VPS ≈ €14-23 (⚠ specs dispares) | €0 recurrente (ya pagado) |
| Operación | Cero SO, deploy por CLI/Git | Mantienes SO, parches, backups, firewall; Dokploy da UI de deploy, SSL y DB con backups | Igual | Igual + casa/luz/red sin SLA |
| Riesgo | **Acaba de tener incidente de seguridad + outage de ~1 día** | Tú eres el ops; 1 sola máquina = punto único de fallo | Igual | Sin SLA; no apto para producción de clientes (`decisions/2026-09-24-compra-minisforum…`) |
| Migración | — | Docker/Nixpacks desde GitHub; recrear variables y Postgres | Igual | — |

Conclusión provisional: DigitalOcean+Dokploy es plausible para **oscar-hq** (carga pequeña) por ~$24/mes frente a ~$12 de Railway: **no ahorra**, compra independencia del proveedor. Dokploy en un solo Droplet de 4 GB para varios proyectos sí puede igualar o mejorar el coste total si hay 3+ servicios en Railway. ⚠ Falta inventario real de servicios Railway y su gasto (Oscar: exportar de la factura de Railway cuando abra el dashboard).

## 3. Propuesta de plan de migración (SOLO PROPUESTA, requiere OK explícito de Oscar)

Orden recomendado (de menor a mayor riesgo):

| # | Proyecto | Por qué en este orden | Downtime esperado | Complejidad / riesgo |
|---|---|---|---|---|
| 0 | Inventario | Listar servicios Railway, variables, volúmenes, dominios, gasto | 0 | Bajo. Bloqueado hasta que vuelva el dashboard |
| 1 | MigraTeam **staging** | Es staging, sin clientes; sirve de ensayo de Dokploy | 0 (prod no cambia) | Bajo-medio. Backend FastAPI + Alembic; BD en Supabase (no migra) |
| 2 | Scraper (si está en Railway) | Procesos batch, tolera paradas | Minutos | Medio. Crons y volúmenes ⚠ por confirmar |
| 3 | oscar-hq | Cambia `API_SECRET_KEY`/URL que usa pildora-mcp y los crews; hay crons | 5-30 min con cambio de DNS/URL + TTL bajo previo | Medio-alto: crons, crews, tokens de clientes MCP a actualizar |
| 4 | MigraTeam **producción** | Último; hoy despliega por Git a Railway sin pasar por el hub | Casi 0 con DNS previo calentado, pero **no recomendado migrar a la vez que se rota todo** | Alto: producto con clientes, Alembic en cada arranque |

Pasos comunes: Droplet + Dokploy, firewall, backups externos → desplegar copia en paralelo → probar con URL temporal → bajar TTL DNS → cambiar DNS solo con OK de Oscar → mantener Railway 1-2 semanas como respaldo.

Alternativa de mínimo esfuerzo: **esperar a que Railway cierre el incidente**, rotar secretos y decidir la migración con calma (la urgencia real hoy es rotación de secretos, no migrar).

## 4. Decisiones que necesita de Oscar
1. ¿Qué proyecto y qué pantalla/error exacto ve en Railway («no puedo hacer billing»)?
2. ¿Rotas ya los secretos de oscar-hq (recomendado hoy)?
3. ¿Qué recuerdas de la tabla de ayer (opciones y cifras)? Para sustituir la reconstrucción de §2.
4. ¿Apruebas empezar por el paso 0-1 (inventario + ensayo con staging) o esperas al cierre del incidente?

Fuentes: [status.railway.com](https://status.railway.com), [TemperStack DO](https://www.temperstack.com/plans/digitalocean/), [onedollarvps DO](https://onedollarvps.com/pricing/digitalocean-pricing), [dokploy.com](https://dokploy.com/self-hosted-paas).
