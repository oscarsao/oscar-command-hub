# Comandos de los bots de Telegram: qué hay, para qué sirve y cómo se usa

Guía para el equipo (28-10-2026, tarjeta t_93e677e8). Escríbela en lenguaje llano: si algo no se entiende, es un fallo de
esta guía. El bot la resume con `/ayuda` (agrupada por tarea y por rol) y los comandos salen en el menú «/» de Telegram.

> **Regla de oro:** si un comando no responde, o responde mal, **dilo**. No lo dejes de usar en silencio: así es como
> estos comandos llevaban semanas sin usarse.

## 1. Qué bot es cada uno

| Bot | Nombre en Telegram | Para qué | Cómo se le habla |
|---|---|---|---|
| **Trabajos** (`@pildora_carriles_bot`) | «Pildora - Trabajos» | Control de las tareas de los carriles: decidir, aprobar, ver cómo va, pedir trabajo, publicar | **Comandos** (lo de este documento) y botones |
| **Hermes** (`@oscarsaoBot`) | «Pildora Digital» | Coordinador con herramientas: archivos, kanban, navegador | Conversación; sus comandos (`/new`, `/status`…) van como `/comando@oscarsaoBot` |
| **Avisos** (`@PildoraD_BOT`) | «Agente Píldora Avisos» | Asistente del equipo (CEO/CMO/COO/CTO) y avisos de negocio | Texto libre; no tiene comandos propios en el repo del hub |

## 2. Quién puede usar los comandos de Trabajos y dónde

- **Operadores** (Oscar y María): todos los comandos.
- **Operador acotado a una marca** (Angel): solo los de su marca. No puede usar `/integrador`, `/encolar`, `/preparar`,
  `/pedir`, `/sesion`, `/flujo`, `/workflow`, `/ajustes`, `/pendiente`, `/personal` ni `/agrupar`, y `/al_dia prod` es solo
  de Oscar (o de quien tenga todas las marcas).
- **Cualquier otra persona** (Andrea, Ingerber…): recibe «Este comando es solo para operadores». Para pedir algo: texto
  libre a Hermes o al bot de Avisos.

| Dónde | ¿Atiende comandos? | Detalle |
|---|---|---|
| **DM del bot Trabajos** | Sí (operadores) | El texto libre también: responde con datos reales o propone una tarea con botones |
| **Gestión** (grupo), cualquier tema | Sí (operadores) | En los temas 230 (MigraTeam) y 231 (Píldora) los listados se filtran por esa marca; en el resto, ven todo. Escríbelos del menú «/» o con `@pildora_carriles_bot` |
| **Marketing** (grupo) | Sí, pero solo para operadores | Andrea e Ingerber reciben «solo para operadores» |
| **Desarrollo** (grupo) | **No** | El bot de Trabajos **no está en el grupo** (Telegram contestó `chat not found` el 22-10). Ahí solo contesta Hermes |
| Texto suelto en cualquier grupo | **No** | Telegram no entrega al bot el texto sin `/`. En grupos, o comando, o responde (reply) a un mensaje suyo |

Por eso, en un grupo, escribe siempre `/comando@pildora_carriles_bot` (el menú «/» lo hace solo).

## 3. Los comandos, por lo que quieres hacer

### 👀 Ver cómo va todo
| Comando | Qué es y para qué sirve | Ejemplo |
|---|---|---|
| `/hoy` | El resumen de las 8:00, cuando quieras | `/hoy` |
| `/estado [marca]` | Una pantalla: lo que corre, lo que te espera a ti y lo pendiente de desplegar | `/estado migrateam` |
| `/falta [marca]` | Checklist de lo que frena una salida (lote, ramas fuera, SQL, variables, deploy), con el siguiente paso arriba | `/falta migrateam` |
| `/tareas [marca]` | Tareas en curso y en cola por carril | `/tareas pildora` |
| `/cola [marca]` | Qué corre y qué sigue en cada carril, por prioridad | `/cola nextjobs` |
| `/tarea t_xxxxxxxx` | Ficha de una tarea con los botones de su estado (acepta un trozo del id) | `/tarea t_3d4a9c10` |
| `/salud` | ¿Está vivo el runner? Hermes, servicios, alertas, RAM y decisiones pendientes | `/salud` |
| `/ram` | RAM ahora y los 5 procesos que más gastan | `/ram` |
| `/integrador` | Estado del integrador, PR de sesiones interactivas y bloqueos de guard | `/integrador` |

### ✅ Decidir y aprobar
| Comando | Qué es y para qué sirve | Ejemplo |
|---|---|---|
| `/pendiente` | **«Lo tuyo» en un solo mensaje**: preguntas, PR por aprobar, tarjetas tuyas, con un botón por línea | `/pendiente` |
| `/decisiones` | Bandeja de preguntas de los workers, con [✅ Aceptar todo lo recomendado] y una tarjeta por tarea | `/decisiones` |
| `/atencion` | Todo lo que espera a Oscar con botones para resolverlo ahí mismo | `/atencion` |
| `/pendientes` | Tus pasos «tuyo» y «decidir» con botones | `/pendientes` |
| `/aprobar` | Tareas listas para integrar: [✅ Aprobar] [🔁 Pedir cambios] [🗄 Aparcar] | `/aprobar` |
| `/preparar t_xxx \| todas` | Claude redacta el contexto y 3 opciones de una tarjeta de decisión | `/preparar t_3d4a9c10` |

### ➕ Pedir trabajo
| Comando | Qué es y para qué sirve | Ejemplo |
|---|---|---|
| `/hazlo <texto>` | Crea **ya** una tarea pequeña (prioridad alta, tope 15 min); el carril sale del texto, y si hay duda, botones | `/hazlo revisar el aviso de despliegue` |
| `/pedir <texto>` | Pide algo con tipo (bug o feature) y unas preguntas | `/pedir el botón Aprobar no responde` |
| `/flujo <texto>` | Una petición grande → hasta 8 tarjetas encadenadas. **No crea nada** hasta pulsar [Crear todo] | `/flujo migrar el login a SSO` |
| `/workflow <texto>` | Trabajo largo: plan y coste; nada se crea hasta aprobar | `/workflow auditoría del sitio` |
| `/sesion [nombre]` | **Solo en el DM.** Abre una sesión de correcciones: lo que escribas se acumula como puntos y `/sesion fin` lo junta en UNA tarjeta | `/sesion web` … `/sesion fin` |

### 🚀 Publicar
| Comando | Qué es y para qué sirve | Ejemplo |
|---|---|---|
| `/lote <proyecto>` | Monta ya el lote con las ramas aprobadas | `/lote migrateam` |
| `/desplegar <proyecto>` | Ficha de lo fusionado y sin desplegar; **dos toques** (🚀 y ✅ Sí) | `/desplegar oscarhq` |
| `/promover migrateam` | Prepara el PR develop→master y pide doble confirmación | `/promover migrateam` |
| `/al_dia [proyecto\|todos] [dev]` (`/aldia`) | Pone al día las ramas, enseña lo listo y lo integra | `/al_dia oscarhq` |
| `/encolar <repo> <PR>` | Mete el PR de una sesión interactiva en la cola del integrador para tu OK | `/encolar oscar-hq 123` |

### 🧹 Ordenar el kanban
| Comando | Qué es y para qué sirve | Ejemplo |
|---|---|---|
| `/prioridad t_xxx <0-99\|sube\|baja>` | Cambia el orden de una tarjeta en la cola | `/prioridad t_3d4a9c10 sube` |
| `/agrupar [proyecto\|todo]` | Propone fusionar, encadenar o juntar tarjetas duplicadas; nada se aplica sin botón | `/agrupar todo` |
| `/personal` | Propone sacar tus tarjetas personales del kanban de carriles; confirmas con [✅ Mover] | `/personal` |
| `/epicas`, `/retenidas`, `/backlog` | Vistas aparte de lo que no entra en la cola de decisiones | `/retenidas` |

### 🔔 Avisos y ajustes
| Comando | Qué es y para qué sirve | Ejemplo |
|---|---|---|
| `/avisos off [1h\|3h\|mañana\|siempre]` / `/avisos on` | Apaga o enciende los avisos de «listo para revisar» | `/avisos off 3h` |
| `/silenciar <clave> <horas>` | Calla una alerta repetida del monitor (0 = quitar el silencio); sin argumentos lista las claves | `/silenciar ram 2` |
| `/ajustes` | Ajustes del hub con botones: avisos, atascos, lotes y carriles | `/ajustes` |
| `/ayuda` (`/help`, `/start`) | Esta guía resumida, por tarea y por rol | `/ayuda` |

### Cosas que parecen comandos y no lo son
- **Preferencias:** no hay `/preferencias`. Salen solas de las sesiones `/sesion` cerradas; Oscar las revisa en
  `.state/preferencias.md` y solo las aprobadas se usan.
- **Retro:** no hay `/retro`. El informe de fricción se manda solo los lunes al DM de Oscar.

## 4. El bot te sugiere el comando

Si en el DM escribes algo que un comando ya hace («¿qué falta para salir?», «cuánta RAM queda», «crea una tarea…»), el
bot responde lo de siempre y añade una línea `💡 Eso ya lo hace /falta…`. Son reglas fijas por palabras (sin IA) en
`tools/agent-lanes/sugerencias.yaml`; se pueden editar en caliente. Máximo un aviso por comando y chat cada 10 minutos.
En grupos no funciona: el bot no lee el texto suelto.

## 5. Para quien mantiene el bot (integrador)

- El menú «/» se registra solo al arrancar el runner (`setMyCommands` en `default`, `all_private_chats` y
  `all_group_chats`); el de grupos no lleva `/sesion`. Si el menú no cambia tras reiniciar, mira el log
  («comandos del bot registrados en…»).
- **Avisos** (`@PildoraD_BOT`) no tenía menú el 22-10 (`getMyCommands` = 0); su código no está en este repo. Propuesta:
  `/ayuda`, `/marca`, `/rol`, `/imagen`. Es una tarjeta del carril de Oscar HQ.
- Prueba de humo: `tests/test_comandos_humo.py` lanza cada comando en el DM y en un tema de grupo con dobles y exige
  respuesta sin «No pude…». `tests/test_ayuda.py` exige que cada comando salga en `/ayuda` y en esta guía.
