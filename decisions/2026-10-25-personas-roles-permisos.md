# Decisión 2026-10-25: personas, roles y permisos del sistema

> Registro único (P18 de la auditoría del hub del 25-10). Sustituye en lo que contradiga a
> `2026-09-27-telegram-grupos-temas.md` (p. ej. «María: producción no») y a los comentarios sueltos de
> `tools/telegram/people.yaml`, del SOUL de Hermes y de tarjetas. `people.yaml` y el SOUL deben enlazar aquí.
> Repo público: sin ids de Telegram, correos ni datos personales; los ids viven en `people.yaml` y en Supabase.

Fuentes: respuestas de Oscar con botones a las preguntas D4-D10 de la auditoría del hub y a las 8 preguntas de
arquitectura (25-10), decisiones del 19-10 al 24-10 recogidas en `people.yaml`, y las decisiones nuevas de la tarde
del 25-10. Donde no hay respuesta registrada, la fila lo dice: **pendiente** no es «sí».

## Respuestas D4-D10

| Id | Pregunta | Respuesta | Estado en el código / dónde se aplica |
|---|---|---|---|
| D4 | PR #135 (María decide como Oscar) | **A**: recortarlo a la spec + quitar a María del filtro de colaboradores; APROBADO-MARIA fuera; María decide con los botones en 🎯 Decisiones | Hecho en F1-09 (rama `feat/hub-f1`): `operadores_todas_marcas` sale del filtro de DM; sin copias al DM ni APROBADO-MARIA. Spec `specs/hermes-de-equipo.md` marcada «sustituida» |
| D5 | Acceso de María | **A**: administradora propia en el panel, mismos niveles que Oscar en Avisos, tope de 20 USD | Usuario del panel en F4-04 (acción A10 de Oscar); niveles y tope en Avisos con F0-06 (datos en Supabase) |
| D6 | Qué aprueba María sin Oscar | **Sin respuesta registrada.** El plan aplica de hecho la A (peticiones del equipo y aprobaciones de PR): F0-06 la pone en `peticiones_aprobadores` y F1-09 le deja aprobar con el botón | **Pendiente de confirmar** con Oscar |
| D7 | Ángel en el panel y en Avisos | **A**: editor solo de Píldora y Signal, sin MigraTeam; en Avisos, leer y proponer en Oscar HQ y Signal | Matriz de Ángel en Supabase con F0-06 |
| D8 | Ángel y la producción de MigraTeam | **A**: no; solo en sus marcas | **Desfase conocido:** hoy `people.yaml` le da `aprobar_produccion` «en todas las marcas» y `People.puede_aprobar_produccion` no mira la marca. Se corrige en F4-01 (`hub.permisos`: `aprobar_produccion(marca)` exige la marca). No se toca antes |
| D9 | Dónde decide Ángel | **Sin respuesta registrada** (A: copia con botones en su tema de Desarrollo y su DM; B: grupo Gestión; C: que decidan Oscar o María) | **Pendiente.** Mientras tanto su DM sigue pasando por el filtro de colaboradores (solo t_id, estado y enlace) |
| D10 | Ángel opera el Hub | **A**: crear la marca Hub y dársela | **Pendiente de aplicar:** `people.yaml` le tiene `[Píldora, Signal]`; la marca `hub` llega con la semilla de F4-01 |

## Tabla persona × canal × marcas × acciones (estado al 25-10 y destino del plan)

| Persona | Papel | Marcas | Bot de carriles (Trabajos) | Avisos (Oscar HQ) | Hermes | Panel Oscar HQ | Producción |
|---|---|---|---|---|---|---|---|
| Oscar | Propietario, decisor final | todas | todo (owner) | todo | perfil propio con terminal (su PC) | propietario (#373) | aprueba y despliega |
| María | Socia / estrategia; «María = Oscar a todos los efectos» salvo `produccion` exclusiva del owner | `*` | operadora: comandos, botones y aprobaciones como Oscar; DM sin filtro (F1-09) | mismos niveles que Oscar, tope 20 USD (D5-A) | hoy usa el Hermes de Oscar; el reparto de perfiles está pendiente (ver abajo) | administradora (D5-A, F4-04) | `aprobar_produccion` (OK con botón al integrador, 22-10) |
| Ángel | CTO Oscar HQ + Signal | Píldora, Signal (+ Hub, D10-A, pendiente) | operador acotado por marca; DM filtrado hasta D9 | leer y proponer en Oscar HQ y Signal (D7-A) | sin terminal en cualquier variante; lectura con su identidad (P1) | editor de Píldora y Signal, sin MigraTeam (D7-A) | solo en sus marcas (D8-A; hoy el código no lo acota, ver D8) |
| Ingerber | CMO Píldora | Píldora (`pildora_digital_inbound`) | colaborador: pide por DM, ve t_id/estado/enlace | solo Píldora, `repo:migrateam=nada` (F0-06) | no (`hermes: false`, 24-10) | según su usuario | no |
| Andrea | Marketing, a prueba | Píldora (`pildora_digital_inbound`) | colaboradora, como Ingerber | como Ingerber (F0-06) | no (`hermes: false`, 24-10) | según su usuario | no |
| Mauredy | Dev MigraTeam | MigraTeam | operador acotado, **inactivo** (sin id) | — | — | — | no |

Cuentas del panel sin uso (prueba, visor): se suspenden con OK en F0-05 (P01, P17), con MFA obligatoria para
administradores.

## Decisiones nuevas de Oscar (25-10, tarde)

1. **Interruptores del hub en el panel:** página «Hub» en Ajustes de app.pildoradigital.com, **solo propietario**,
   con doble confirmación y registro de cada cambio. María y Ángel no la ven. Detalle en
   `2026-10-25-arquitectura-hub-v2.md`.
2. **Hermes y Claude en los ordenadores de María y Ángel:** cada uno puede instalar su propio Claude (su suscripción)
   o Hermes, conectado a los datos de Oscar **por MCP con su clave personal** (la del panel, A9 del plan), nunca con
   ficheros del PC de Oscar ni con secretos copiados. Lo que vean y hagan lo limita su clave (sus marcas y niveles).
   Opcionalmente su PC puede ser **worker extra** de los carriles, como el mini PC (F5-05): su propio login de Claude,
   `worker_id` propio y su PAT de push (solo `lane/*`). Encaja en F4-F5, cuando `hub.permisos` y hub-core existan.
3. **Terminal del Hermes de Oscar: pendiente de Oscar.** Oscar ha dicho que *su* Hermes, que corre en su portátil,
   debería conservar la consola. Sobre la mesa hay una propuesta de dos perfiles (perfil de Oscar con terminal, y
   perfil o bot aparte sin terminal para Ángel; María en el de Oscar o en el aparte). Hasta que Oscar lo confirme
   con botón **no se tocan** los toolsets de Hermes (la parte de herramientas de F1-10 y F2-11 espera). Consecuencia si
   María comparte perfil con Oscar: búsqueda de sesiones y memoria no se separan entre ellos (S-04 aceptado para
   María); Ángel quedaría separado.

## Reglas que no cambian

- Merge a `main`/`master`, despliegues, migraciones, borrados y variables de entorno: siempre con botón de Oscar
  (o de quien tenga `aprobar_produccion` en esa marca, cuando F4-01 lo acote).
- La capacidad `produccion` es exclusiva del owner y no sale nunca de `people.yaml`.
- Fuente única de personas: hoy `people.yaml`; desde F4-01, `ohq_people` + `ohq_permissions` en Supabase y
  `people.yaml` pasa a ser generado.
