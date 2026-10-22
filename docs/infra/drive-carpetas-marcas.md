# Carpetas de MigraTeam y Píldora en Google Drive: inventario y propuesta (22-10-2026)

> Fase 1: solo inventario y propuesta. No se ha movido, copiado, borrado ni compartido nada.
> Decisión de Oscar (22-10, botones): las carpetas viven en **su Mi unidad (oscarsao20)** y se comparten con el
> equipo. Quiere sincronización en los dos sentidos con su PC («si subo algo ahí se sube a la nube y viceversa»).

## 1. Inventario

### Drive (`G:\Mi unidad`)

| Carpeta | Archivos | Tamaño | Contenido de primer nivel |
|---|---|---|---|
| `MIGRATEAM 2027` | 99 | 180 MB | RESIDENCIAS (+ bd), Investigaciones Extranjería y Negocios, IMAGENES MIGRATEAM (lote-01, lote-02-con-texto, plantillas, avatares) |
| `documentacion migrateam` | 560 | 13 MB | 01_AUDITORIA, 03_DOCUMENTACION_CANONICA, 04_SPECS, 05_LEGAL_OPERACIONES (manual, deploy, rgpd, legal) |
| `Pildora Digital 2026` | 178 | 60 MB | Multiverse (LEGACY, Ingerber, Contratos), Pildora Digital (audits, assets, reports, comercial, ads, marketing, content) |
| `pildora-digital` | 7 | 11 MB | plantillas |

Sueltos de marca en la raíz de Mi unidad (17): auditorías DocketWise (×5) y `Configuracion_Settings_DocketWise`,
`Auditoria de todo Migrateam`, `Cambios Migrateram`, `Cambios Ui Migrateam`, `smoke migrateam 11 octubre`,
`Análisis Migratorio y Oportunidades Comerciales`, `Auditoría Immigration Tech EE.UU. para España` (×2, uno «(1)»),
`Trámites Migratorios de EE. UU.` (×2, uno «(1)»), `Directorios Abogados Inmigración España`, `Pildora Digital V4 design.zip`.

**PRIVADO (no se comparte ni se mueve):** todo lo legal/bancario de la LLC y personal de la raíz (`Checklist Chase`,
`PILDORA DIGITAL LLC - Chase`, `Written Consent - COO Designation`, `Factura__Pildora_Digital_LLC`, modelos 303/390,
contratos personales) y la carpeta de Drive `15YM0ehSAeQdPmKE_i9rhUQLHQ_vyiOZg`. Dentro de las carpetas de marca
también hay que revisar antes de compartir: `Pildora Digital 2026/Multiverse/Contratos` y
`documentacion migrateam/05_LEGAL_OPERACIONES/legal`.

### Local (fuera de Drive)

| Ruta | Archivos | Tamaño | Ya en Drive (nombre+tamaño) |
|---|---|---|---|
| `C:\Users\oscar\Pildora-Assets\banco-imagenes-drive` | 553 (424 distintos) | 746 MB | 1 |
| `Downloads\IMAGENES MIGRATEAM` | 44 | 8,5 MB | 0 |
| `Downloads\IMAGENES cHATGPT` (MigraTeam y Píldora) | 19 | 3,8 MB | 0 |
| `Documents\MigraTeam-Presentacion-Comercial.pdf` | 1 | — | — |

`Pildora-Assets` contiene: MigraTeam, MigraTeam-Branding-Oficial, Pildora-ChatGPT-Referencias, Generadas-ChatGPT /
Codex (26-09), Escritorio-Hermes-Generadas y `PROMPTS_CHATGPT_LISTOS.md`.

### Disco E: (archivo y backups, 2 niveles)

- `E:\Pildora Digital 2026`: **copia antigua de la carpeta de Drive** (126 archivos, 110 idénticos a Drive por
  nombre+tamaño; a Drive le sobran `CLAUDE.md`, `CLAUDE (1).md` y la transcripción de la 1.ª reunión). No aporta nada
  nuevo: se puede dejar como backup.
- `E:\02_Negocio_Pildora`: CLAUDE MKT, **LEGAL-LLC (privado)**, OSCARSAOWEB, PILDORA DIGITAL,
  `Pildora Digital (C - 20260920)`, WEBS.
- `E:\03_Negocio_Clientes`: ASESORIAS, BBDD, CLIENTES, CLOUDWAYS, IMPRESIONES DTF,
  **`_SENSIBLE_documentos_clientes_CyA` (privado, datos de clientes de Cohen y Aguirre: no subir)**.
- `E:\04_Proyectos_Dev_Archivo`: repos antiguos y backups de la reinstalación (`backup-*-20260920/22`), incluido
  `backup-gdrive-PildoraDigital2026-20260922`. `E:\reinstall-backup-2026-09`: planes y listas de la reinstalación.
  Son archivo técnico: **no se suben a Drive**.

### Configuración actual de Drive para escritorio

- **Mi unidad está en modo STREAMING** (unidad virtual `G:` montada por DriveFS; no existe carpeta duplicada en
  `C:\Users\oscar`). Los archivos se descargan al abrirlos; un agente que lea muchos archivos de `G:` irá lento y
  no funciona sin conexión.
- **`C:\Users\oscar\Documents` está en copia de seguridad** hacia Drive («Otros ordenadores», `is_my_drive=false`).
  Eso NO es colaborativo: lo que se guarde ahí no aparece en Mi unidad ni se puede compartir como carpeta de equipo.
- En el equipo hay una cuenta `@pildoradigital.com` (usuario `oscar_admin` de Oscar HQ): si ese dominio tiene Google
  Workspace, una **unidad compartida** sería el sitio natural a medio plazo (los archivos serían de la empresa).

## 2. Propuesta

### Estructura objetivo (en Mi unidad)

```
G:\Mi unidad\MigraTeam\
  Comercial\            ← presentación comercial (Documents), Directorios Abogados, Análisis Migratorio
  Marketing\Imagenes\   ← MIGRATEAM 2027\IMAGENES MIGRATEAM + Downloads\IMAGENES MIGRATEAM + Pildora-Assets\MigraTeam(+Branding-Oficial)
  Marketing\Referencias\← lote-01 (referencias de estilo elegidas)
  Legal-producto\       ← documentacion migrateam\05_LEGAL_OPERACIONES (rgpd, manual; «legal» revisar antes)
  Documentacion\        ← documentacion migrateam (01, 03, 04), auditorías DocketWise, Cambios UI, smokes
  Investigacion\        ← MIGRATEAM 2027\Investigaciones Extranjería y Negocios, RESIDENCIAS, Immigration Tech EE.UU., Trámites EE.UU.
  Clientes\             ← (vacía; datos de clientes solo si Oscar lo decide, nunca _SENSIBLE_)
  Entregables generados\← salida de Oscar HQ, carriles y Claude
G:\Mi unidad\Píldora Digital\
  Comercial\            ← Pildora Digital 2026\Pildora Digital\comercial
  Marketing\Imagenes\   ← assets, ads, content, Pildora-Assets\Pildora-ChatGPT-Referencias, Generadas-*, pildora-digital\plantillas
  Marketing\Referencias\← Downloads\IMAGENES cHATGPT (las de Píldora), referencias Liquid Glass elegidas, V4 design.zip
  Documentacion\        ← audits, reports
  Clientes\Multiverse\  ← Pildora Digital 2026\Multiverse (sin Contratos → privado o compartir solo con quien decida Oscar)
  Entregables generados\
```

Los duplicados «(1)» de la raíz se quedan uno (el más reciente) y el otro a la papelera, con OK de Oscar.
`E:` no se toca (sigue como archivo/backup). Las carpetas antiguas de Drive se vacían con «Mover» de Drive (conserva
enlaces y permisos), no copiando, para no romper enlaces ya compartidos.

### Sincronización en los dos sentidos (lo que pidió Oscar)

Opción recomendada, sin cambiar todo Mi unidad a duplicado: en `G:\Mi unidad`, clic derecho sobre `MigraTeam` y
`Píldora Digital` → **«Acceso sin conexión» → «Disponible sin conexión»**. Así esas dos carpetas tienen copia real
en disco, se sincronizan en los dos sentidos (lo que se guarde en `G:\Mi unidad\MigraTeam\...` sube solo, y lo que
suba otro baja solo) y Claude/carriles las leen rápido y sin conexión, sin ocupar disco con el resto de Mi unidad.
Alternativa: Preferencias de Drive → Google Drive → «Duplicar archivos» (todo Mi unidad en `C:\Users\oscar\Mi unidad`),
más disco y cambia la ruta.

No usar «Otros ordenadores» (copia de seguridad de carpetas del PC) para esto: no es colaborativo.

### Matriz de acceso (propuesta; decide Oscar)

| Persona | MigraTeam | Píldora Digital |
|---|---|---|
| Oscar | propietario | propietario |
| Ingerber (CMO Píldora) | — | Editor (todo salvo Clientes/Multiverse contratos) |
| Andrea (marketing) | — | Editor solo en `Marketing\` |
| Angel (desarrollo) | Lector en `Documentacion\` | Lector en `Documentacion\` (propuesta: subir a Editor si trabaja en ello) |
| María (socia, Gestión) | Editor (propuesta) | Editor (propuesta) |
| Linda (COO) | Lector (propuesta) | Lector (propuesta) |
| César (mnee.es, viewer) | — (propuesta: sin acceso) | — |

Correos: los de `ohq_users` (gmail salvo César, mnee.es). Compartir por carpeta, no por archivo.

### «Que todo se genere desde ahí» (qué cambiar, no cambiado)

- **Carriles y Claude en el PC:** ruta de entregables = `G:\Mi unidad\<Marca>\Entregables generados\`. Cambio: una
  clave `entregables` por marca en `tools/agent-lanes/lanes.yaml` (o en el cuerpo de tarjeta como `Destino:`) y
  documentarla en `docs/convenciones-proceso.md`; las skills de marketing de pildora-cmo-workspace igual.
- **Oscar HQ corre en el servidor (pildora-01), no ve `G:`.** Hoy no tiene integración con Drive (ni
  `googleapiclient` ni variables `DRIVE_*`). Para que sus imágenes, carruseles y propuestas caigan en Drive hace falta
  una integración nueva: subida por API de Google Drive (cuenta de servicio con la carpeta `Entregables generados`
  compartida con ella, o OAuth de Oscar) con `DRIVE_FOLDER_<MARCA>_ENTREGABLES` en el entorno de Dokploy. Es una
  tarjeta de carril (Oscar HQ), no un cambio de config.
- **Hermes / bot de Marketing:** los adjuntos generados se guardarían igual vía esa integración de Oscar HQ.

## 3. Decisiones para Oscar

1. ¿Estructura propuesta (MigraTeam / Píldora Digital con 6-7 subcarpetas)? → Sí / Ajustarla.
2. ¿Sincronización? → Las 2 carpetas «disponibles sin conexión» (recomendado) / Todo Mi unidad en «Duplicar».
3. ¿Accesos? → Matriz propuesta / Solo Ingerber y Andrea por ahora / La ajusto yo.
4. ¿Multiverse/Contratos y 05_LEGAL_OPERACIONES/legal? → Privados (recomendado) / Compartir con María.
5. ¿Integración de Oscar HQ con Drive para entregables? → Tarjeta al carril de Oscar HQ / Más adelante.
6. ¿Unidad compartida de Workspace (@pildoradigital.com) a medio plazo? → Investigar / No.

Fase 2 (con OK): mover con «Mover» de Drive en el orden de la tabla, subir lo local (Pildora-Assets, Downloads), compartir
según la matriz, y marcar sin conexión.
