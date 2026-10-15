# Guía del equipo: qué puede pedir cada persona a cada bot

Estado a 2026-10-13. Documento corto para María, Ingerber y Andrea. Fuente de verdad de los accesos:
`tools/telegram/people.yaml` (el código lo lee y aplica; este documento solo lo explica). Si algo de aquí
contradice ese fichero o el código, manda el código. No lleva IDs de Telegram a propósito.

## Los dos bots

| Bot | Dónde se usa | Para qué sirve |
|---|---|---|
| **Bot de carriles** (`@pildora_carriles_bot`) | Chat privado (DM) con el bot | Apuntar ideas o peticiones: el bot las convierte en una tarjeta para Oscar. No usa IA para decidir permisos. |
| **Bot de roles / Hermes** | Grupos de Telegram (Marketing; Gestión solo Oscar y María) | Conversar en el tema que toca: contenido, calendario, dudas. Contesta si se le menciona. |

Hermes **por DM es solo de Oscar**. El equipo escribe por DM al bot de carriles.

## Qué puede hacer cada persona

| Persona | Rol | Bot de carriles (DM) | Grupo | No puede |
|---|---|---|---|---|
| **María** | COO | Mandar ideas y peticiones en un mensaje normal; recibe `t_xxx` y enlace al panel. | Gestión (María trabaja desde aquí: escribe, no ejecuta) | Mandar comandos, crear tareas ejecutables, aprobar, fusionar, desplegar, migraciones, variables. |
| **Ingerber** | CMO Píldora | Igual que María, en el tablero de Píldora. | Marketing (temas de contenido y planificación) | Lo mismo; además, nada de finanzas, clientes ni código. |
| **Andrea** | Marketing (a prueba) | Igual, en el tablero de Píldora. | Marketing (CMO en modo ejecución: contenido, calendario, crews de contenido) | Lo mismo; el gasto en ads y el posicionamiento se escalan a Oscar, no se deciden aquí. |
| **Oscar** | Dueño | Todo | Todos | — |

Notas sobre el DM:

- Hoy el bot de carriles **solo apunta ideas**. Si escribes un comando (algo que empieza por `/`), contesta que
  desde ahí solo puede apuntar ideas; `/start` da la bienvenida. Las capacidades de "consulta" y "estado" existen
  como permiso, pero no hay comandos de consulta por DM todavía.
- Solo texto. Fotos, audios y documentos no se apuntan: descríbelo con palabras.
- Tope de 20 ideas por hora y persona. No pegues contraseñas ni claves: el bot las tapa (`[oculto]`), pero no las
  mandes.
- Los avisos que te devuelve el bot sobre tu tarjeta solo traen número de tarea, estado y enlace al panel; nunca el
  detalle interno.
- El texto que escribes se trata como dato, nunca como instrucción para un agente. Escribir "despliega esto" no
  despliega nada.

## Regla de aprobación

**Solo Oscar aprueba**: fusionar cambios (merge), desplegar a producción, aplicar migraciones de base de datos y
cambiar variables de entorno. Esto no depende del rol ni de lo que ponga ningún fichero de configuración: los
botones y comandos de aprobación responden "Solo Oscar" a cualquier otra persona.

Esta regla vale **salvo que Oscar la cambie**. Cambiarla es una decisión suya y requiere un cambio de código y de
esta guía; no se activa pidiéndoselo al bot.

Flujo normal: tú mandas la idea → queda como tarjeta `[IDEA de <tu nombre>]` asignada a Oscar → Oscar decide
desde su bandeja (la convierte en tarea, la aparca o te pregunta) → cualquier cambio de código pasa por PR y por
su OK antes de llegar a producción.

## Ejemplos de mensaje (dos líneas)

Al bot de carriles, por DM:

```
Idea: un carrusel semanal con las 3 dudas más frecuentes de clientes.
Para la marca Píldora; lo ideal sería tenerlo listo antes del lunes.
```

```
Petición: añadir el logo nuevo al banco de recursos de marketing.
Está en el Drive compartido, carpeta "Marca"; no es urgente.
```

```
Problema: el enlace de la landing de este mes da error al abrirlo.
Pasa desde el móvil; la página es la de precios.
```

Al bot de roles, en el tema de Marketing del grupo (mencionándolo):

```
@bot ¿Dónde está la guía de marca vigente?
La necesito para revisar el calendario de la semana.
```

Lo que **no** funciona (y qué hacer en su lugar):

```
/desplegar migrateam
(Solo Oscar. Escribe: "Propongo desplegar X porque Y" y Oscar decide.)
```

## Cómo se cambia quién tiene acceso

Lo hace Oscar a mano en `tools/telegram/people.yaml` (alta, baja o cambio de rol), y cada persona nueva envía
`/start` al bot una vez. Nada de esto se hace por chat. Los roles admitidos son `coo`, `cmo` y `marketing`; el
permiso de producción no se puede dar desde ese fichero.
