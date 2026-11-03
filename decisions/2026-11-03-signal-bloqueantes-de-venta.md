# Signal: bloqueantes de venta antes de la siguiente demo comercial

Fecha: 2026-11-03 · Pidió: Angel · Tarjeta: t_e45855d9

## Veredicto
La auditoría completa de Signal/Multiverse concluye que **la arquitectura es vendible**
(ICPs por señales, barrido + enriquecimiento, historial, ICPs compartidos, Telegram,
estimación de costes, entrada privacidad-first, Hub). Los fallos son de **integración**, no de concepto.
Con los 3 primeros arreglos de abajo, Signal se puede enseñar en demo a un cliente nuevo.

## Orden de arreglo (de más a menos bloqueante)

| # | Bug | Efecto | Bloquea la demo | Tarjeta |
|---|-----|--------|-----------------|---------|
| 1 | `SIGNAL_CORE_SUPABASE_WRITE` sin configurar | Ningún enriquecimiento se guarda | **Sí (grave)** | _pendiente de enlazar_ |
| 2 | Las pestañas de Ajustes no cambian de contenido | Ajustes inutilizable; Créditos inaccesible | **Sí** | _pendiente de enlazar_ |
| 3 | 0 fuentes activas en Descubrimiento (hay 20 en «Motor nuevo») | El barrido no encuentra nada | **Sí** | _pendiente de enlazar_ |
| 4 | Créditos inaccesible | Mismo origen que el nº 2: se arregla con él, no es trabajo aparte | Sí, vía nº 2 | misma que nº 2 |
| 5 | Placeholders/datos de otro cliente visibles en la sesión | Riesgo de privacidad y de imagen delante del cliente | No impide usar; **limpiar antes de enseñar** | _pendiente de enlazar_ |

Nota: el análisis habla de «5 bugs» y de «3 primeros», pero el 2 y el 4 son el mismo fallo,
así que hay **4 trabajos distintos**. Los 3 primeros (1, 2+4, 3) desbloquean la demo; el 5 es
requisito de higiene antes de cada demo (usar una sesión limpia mientras no esté arreglado).

## Qué hacer
1. Arreglar 1 → 2 (incluye 4) → 3, en ese orden (el 1 es el que más rompe: sin él el resto no produce datos).
2. Hasta arreglar el 5: demo siempre con una cuenta/sesión sin datos de otros clientes.
3. No convocar la siguiente demo comercial hasta cerrar 1, 2 y 3.

## Pendiente
Esta tarjeta no traía los identificadores de las tarjetas concretas de cada bug.
Quien las tenga (Angel/Oscar) debe rellenar la columna «Tarjeta»; si alguna no existe, crearla con `tarjeta-carril`.
