# Spec: aviso de carril libre y reposición (t_ebd2b253)

Decisión de Oscar (30-10): que el hub avise cuando un carril lleva libre un tiempo y recomiende una tarea corta y/o larga, o la asigne solo por proyecto.
Tamaño: L (3-5 días en 3 PR). Estado: OK de Oscar (31-10). **Esta tarjeta (t_ebd2b253) queda reducida al PR 1 (detección + `/libres`); las piezas 2-7 (PR 2 y PR 3) pasan a tarjetas nuevas.**

## Piezas
1. `agent_lanes/ociosos.py` (nuevo): por pasada del runner, carril «libre» = sin tarjetas running ni ready. Persiste `desde` en `.state/ociosos.json`; reloj inyectado. Umbral: ajuste `carriles.ocioso_minutos` (30).
2. Candidatas: tarjetas en frío asignadas a oscar con `Carril: <nombre>` y `Tamaño:` en el cuerpo. Se documenta en `contract/task-template.md`. Migración única (script en `tools/agent-lanes/scripts/`, dry-run por defecto) que añade `Carril:` a las ~90 en frío desde `frio_mapa.tsv`.
3. Recomendación: CORTA (S/M; mayor prioridad y antigüedad; sin choque de `Zona:` vía prevencion.py; padres integrados) y LARGA (L/XL/[GRANDE], solo si hay hueco grande libre). Exclusiones duras: decisión pendiente de Oscar, seguridad/GDPR sin revisar, migraciones; MigraTeam solo con su carril en `auto` explícito. Sin candidatas: «cola vacía en <carril>» + proponer generar tarjetas desde retro/inventario.
4. Modos `carriles.<carril>.reposicion` = `aviso` (defecto) | `auto` | `apagado`. `aviso`: mensaje con [Asignar corta] [Asignar larga] [Ambas] [Más tarde 2 h] (botonera.py). `auto`: asigna y avisa en una línea.
5. Frenos: sin pausa de cuota (cuota.py); tope de repuestas/hora; ventana horaria para `auto` (08:00-24:00); un aviso por carril y ventana.
6. `/libres` en commands.py; aviso «primordial» (sale con `avisos.nivel=esencial`, vía canal_avisos.py). Todos los ajustes visibles en la UI de ajustes (regla «todo configurable»).
7. Retirar `goteo.py` tras validar (nota de cambio en docs).

## Plan de PR
- PR1: ociosos.py + detección + `/libres` solo lectura + ajustes.
- PR2: recomendación + modo `aviso` con botones + frenos.
- PR3: modo `auto` + migración de `Carril:` + doc + retirada de goteo.

## Pruebas
Reloj inyectado, carriles/tarjetas falsos, choque por Zona, tope por hora, cada modo, cuota, sin candidatas, no repetir aviso.

## Preguntas abiertas
- ¿Un PR o tres?
- ¿La migración de `Carril:` la ejecuto yo en dry-run y la aplicas tú, o la lanzas tú entera?
