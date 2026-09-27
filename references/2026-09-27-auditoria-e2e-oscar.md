# Auditoría E2E — "error en casi todas las páginas" (2026-09-27)

**Rol:** Revisor (solo lectura). **Worktree:** `C:/tmp/audit-e2e-migrateam-20260927`, detached en
`origin/master@b948fe7a` (ya incluye PR #89). **No se editó ni commiteó código.**

## Resumen para Oscar (sin jerga)

No he podido reproducir "error en casi todas las páginas" desde fuera, y **no encontré ninguna causa
en el código o la infraestructura que explique un fallo así de amplio**. Lo que sí confirmé, con
evidencia real (no solo lectura de código):

- El backend de producción **está sano**: responde `/health` con `status: healthy`, base de datos
  `ok`, y corre exactamente el commit que se acaba de fusionar hoy (`b948fe7a`, PR #89).
- El frontend de Vercel **también está desplegado y en `READY`** para ese mismo commit — no es el
  caso (ya visto otras veces) de que el build de Vercel fallara en silencio y siguiera sirviendo una
  versión vieja.
- La URL del backend que usa el frontend en producción está bien configurada (`api.migrateam.com`) —
  descarté el fallo típico documentado en el runbook (`/api-backend` cayendo a 404 por variable de
  entorno mal puesta). Probé esa ruta y sí da 404, pero es el fallback correcto cuando no hay URL — no
  es lo que usa producción.
- Sentry (el sistema que registra errores reales de la app) **no muestra ningún error nuevo ni
  extendido hoy** — solo 1 aviso de QuickBooks sin relación, con 0 usuarios afectados.
- Los últimos 4 commits fusionados hoy (PR #89) son un arreglo muy acotado: solo tocan tests
  automáticos y el permiso de subir documentos con IA (OCR). No tocan ninguna pantalla del panel ni
  nada que se use en "casi todas las páginas".

**Lo que NO pude comprobar**, porque exige entrar con tu usuario real y esta auditoría es de solo
lectura sin credenciales de cliente: cómo se ve el panel una vez logueado. Por eso no puedo decir con
certeza al 100% "es esto" — doy 2 hipótesis, ordenadas por probabilidad, con cómo confirmarlas en 30
segundos cada una.

---

## Evidencia recogida

### 1. Backend — sano y en el commit correcto

```
GET https://api.migrateam.com/health → 200
{"status":"healthy","database":"ok",
 "cron":{"age_seconds":151,"stale":false},
 "sentry":true,"storage":"supabase",
 "commit_sha":"b948fe7a4afdf532d6fb757e49c21b19ab3170e5"}
```

`commit_sha` coincide exactamente con `origin/master` (PR #89). Cron no está parado (`stale:false`).

### 2. Frontend Vercel — deployment de producción correcto

Vía MCP de Vercel (`list_deployments`, proyecto `migrateam`), el deployment `target:production` más
reciente:

```
state: READY
githubCommitSha: b948fe7a4afdf532d6fb757e49c21b19ab3170e5
githubCommitRef: master
```

Mismo commit que el backend. No hay desincronía entre los dos despliegues (el fallo real que pasó el
2026-09-05: backend al día, frontend con build roto sirviendo la versión vieja sin avisar — **no es
el caso hoy**).

### 3. Configuración de la URL del API — correcta

`frontend/lib/config.ts`:
```ts
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api-backend";
```

Descargué y grepeé los chunks JS reales servidos por `https://www.migrateam.com/` — el valor
`https://api.migrateam.com` está inlineado en el bundle de producción (chunk con esa URL
encontrado). Es decir, `NEXT_PUBLIC_API_URL` SÍ está puesta en Vercel producción y el frontend no cae
al fallback `/api-backend` (que si lo pruebo a mano da 404 — correcto, es el comportamiento esperado
del fallback cuando no hay URL, no lo que usa producción).

### 4. Sentry — sin incidentes que expliquen esto

```
search_issues(org=oscarsao, project=migrateam-backend, query="is:unresolved", period=24h)
→ 1 issue: "QuickBooks refresh failed: 401 invalid_client", 0 usuarios afectados, 1 evento
```

Nada relacionado con rutas del panel, ni volumen alto, ni afectación a Mosquera.

**Hueco real encontrado aquí (no es la causa, pero es una carencia):** solo existen 2 proyectos en
Sentry (`cartera`, `migrateam-backend`) — **no hay proyecto de Sentry para el frontend**. Si el error
que ves es una excepción de React/JavaScript en el navegador (no un fallo del backend), hoy **no
queda registrado en ningún sitio automáticamente**. Esto es un gap real de observabilidad, con
independencia de qué esté pasando hoy.

### 5. Últimos commits fusionados (PR #89, hoy) — alcance muy acotado

```
06d5dc99 fix(tests): isolate database commits per test
fe9989a9 fix(tests): fixture db usa snapshot/restore en vez de transaccion persistente
79bd0911 fix(tests): elimina 2 dependencias implícitas de orden expuestas por el aislamiento de DB
b948fe7a Merge PR #89
```

Los únicos archivos de PRODUCCIÓN tocados (no tests) son:
- `backend/auth_ocr_consent.py` — cuándo se exige consentimiento de IA antes de procesar un documento.
- `backend/visa_processor.py` — mismo tema, en el motor de OCR.

Ambos cambios son sobre el **gate de consentimiento de subida de documentos con IA**, un flujo muy
concreto (subir un documento a un expediente). No topan `despacho/layout.tsx`, ni el cliente HTTP
compartido (`lib/api/http.ts`), ni ninguna pantalla de uso general. Es técnicamente imposible que este
PR, por sí solo, rompa "casi todas las páginas" — a menos que el error que ves sea específicamente al
subir/ver documentos OCR (ver Hipótesis 2 abajo).

### 6. Páginas públicas — renderizan bien

```
GET https://www.migrateam.com/          → 200
GET https://www.migrateam.com/despacho  → 307 → /login (correcto, exige sesión)
GET https://www.migrateam.com/login     → 200, sin texto de error, tamaño normal (24KB)
```

No pude entrar al panel (`/despacho/*` real) porque exige tu sesión — no manejo tus credenciales por
regla dura del proyecto.

### 7. Ledger y hallazgos documentados — sin coincidencia exacta

Revisé `docs/kb/LEDGER_ESTATUS.md` y `docs/kb/archivo/HALLAZGOS_PENDIENTES.md` buscando algo que
describa un fallo tan amplio como "toda página falla". No hay ningún ítem abierto que describa eso.
Sí hay, sin relación aparente con hoy:
- Un gate de "errores crudos sin traducir" (`gate-errores-explican.mjs`) — **hoy está prácticamente
  resuelto** (5 casos permitidos, no cientos), así que no explica un fallo extendido.
- Una auditoría de ayer (`VERIFICACION_FLUJO_COBRO_MOSQUERA_2026-09-26.md`, PR #88) sobre el flujo de
  cobro presupuesto→contrato→factura de Mosquera — es sobre EVIDENCIA de que un cobro llegó bien, no
  sobre un bug de la app; no toca código.

---

## Hipótesis, ordenadas por probabilidad (no confirmadas — necesito que las verifiques tú)

### Hipótesis 1 (más probable): caché del navegador con el deploy de hoy

Hoy se desplegó un cambio (PR #89) unas horas antes de este informe. Si tenías el panel abierto en una
pestaña **antes** de ese despliegue y navegaste **después**, el navegador puede intentar cargar un
fragmento de JavaScript (`chunk`) de la versión vieja que ya no existe en el servidor — Next.js suele
mostrar esto como un error genérico en cada navegación, en casi cualquier página, hasta que se recarga.

**Cómo confirmarlo en 30 segundos:** cierra todas las pestañas de MigraTeam, abre una ventana nueva
(o modo incógnito) y entra de nuevo. Si el error desaparece, era esto — no hace falta ningún cambio de
código, es un efecto normal de cualquier despliegue.

### Hipótesis 2: el error es específico de subir/ver documentos (OCR), no de "todas" las páginas

El único cambio de producción de hoy es el gate de consentimiento de IA al subir documentos
(`auth_ocr_consent.py`, `visa_processor.py`). Si lo que ves como "error en casi todas las páginas" es
en realidad el mismo aviso repetido cada vez que el panel intenta cargar/mostrar documentos de un
expediente (que aparecen en muchas pantallas: ficha del cliente, expediente, dashboard con widgets de
documentos pendientes), esto SÍ podría venir de ese cambio.

**Cómo confirmarlo:** la próxima vez que veas el error, mira si menciona "consentimiento",
"documento" o un código HTTP 451 — o simplemente dime en qué pantalla exacta aparece y qué texto
muestra (una captura vale más que la descripción). Con eso puedo acotar en segundos si es este cambio.

### Hipótesis 3 (menos probable, pero fácil de descartar): 402 de facturación en bucle

El frontend redirige automáticamente a "Facturación" en CUALQUIER pantalla si el backend responde 402
(sin suscripción activa/piloto). Si el estado de la suscripción de Mosquera cambió, verías esto en
todas partes. No pude verificarlo sin leer producción, y leer Supabase de producción —aunque sea un
SELECT— exige tu confirmación explícita antes de hacerlo.

**Cómo confirmarlo:** ¿lo que ves es literalmente que te manda siempre a la pantalla de Facturación, o
es un mensaje de "Error" genérico que se queda ahí? Si es lo primero, dime y hago (con tu OK) una
lectura de solo consulta al estado del plan de Mosquera.

---

## Qué NO se tocó ni se cambió

- Ningún archivo de código.
- Ningún commit ni push.
- Ninguna lectura a producción de Supabase (ni siquiera SELECT).
- No se usaron tus credenciales de cliente en ningún momento.

## Siguiente paso recomendado

1. Antes de nada: cierra y reabre el navegador (Hipótesis 1) — es gratis y resuelve la mayoría de
   estos casos tras un deploy.
2. Si persiste, dime: página exacta, texto exacto del error (o captura), y si aparece también en
   incógnito. Con eso reduzco las 3 hipótesis a una en el siguiente paso.
