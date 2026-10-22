---
name: rotar-clave
description: ESQUELETO, NO INSTALAR - esperar a que la Bóveda de claves (oscar-hq PR #254) esté en master con su migración aplicada. Rotará una clave de proveedor desde la Bóveda con solape y verificación.
---

# Rotar una clave (esqueleto: esperar Bóveda en master)

Estado: pendiente. Se completa cuando la Bóveda esté fusionada y desplegada, con la migración 20261039 aplicada y el «paso 0» (clave de cifrado nueva) hecho.

Esquema previsto:
1. Oscar crea la clave nueva en el proveedor y la pega en la Bóveda (`/settings/integrations?tab=boveda`); Claude no la ve en claro.
2. «Rotar» con solape (la vieja sigue válida un tiempo) → «Probar».
3. Comprobar uso real: Centro de IA y logs sin 401; los servicios externos con clave de servicio (Signal, NextJobs, OpenDesign, pildora-mcp) recogen la nueva por `/api/vault/fetch`.
4. Oscar revoca la vieja en el proveedor; se marca en la Bóveda.
5. Sitios fuera de la Bóveda que aún la tengan en variables (Vercel, Dokploy, `.env` de Hermes, hub o pildora-mcp): listar por NOMBRE y proponer limpieza.
6. Actualizar el doc «Pendientes de Oscar», pestaña «Rotación de claves».
