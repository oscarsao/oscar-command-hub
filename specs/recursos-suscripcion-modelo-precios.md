# Recursos · suscripción — propuesta de modelo y precios (N-W2)

Tarjeta t_8b7dd4c7 · Estado: **PROPUESTA PARA REVISAR, sin código** (Oscar, 1-nov: «preparar propuesta de modelo y precios»).
Origen: workflow N-W2 «Suscripción a recursos» (aprobado 23-10). Depende de N-W1, t_2f33cc18 (sacar o no al mercado) y t_f66eb0d0 (Stripe).

> **Qué sé y qué no.** Parto de lo que dice el PR #7 de `pildora-recursos` (cuotas reales en `src/config/quotas.ts`, 3 precios de prueba en Stripe,
> SKU por área desactivados). No he podido leer el código ni los precios de prueba (el entorno del carril no me deja consultar `gh api`), así que
> **las cifras en euros de abajo son hipótesis mías, no datos**. Compruébalas contra los 3 precios de prueba antes de decidir.

## 1. Lo que ya existe (según PR #7, abierto)
- Gratis: **5 descargas**, con doble opt-in por email.
- Pago único: **10 descargas más** (no ilimitado, no permanente). Suscripción: **ilimitada**.
- Checkout con renuncia al desistimiento, webhook fiable, correo de compra (necesita Resend), `/aviso-legal` con [COMPLETAR].
- No hay aún: área de miembros, novedades mensuales, SKU por área activos.

## 2. Modelo propuesto: escalera de tres peldaños
| Plan | Qué da | Para quién | Precio propuesto (IVA incl.) |
|---|---|---|---|
| **Gratis** | 5 descargas, a cambio del email | captar el lead (alimenta N-W1 y el CRM) | 0 € |
| **Pack** (pago único) | +10 descargas, sin caducidad | quien solo necesita un par de plantillas | **19 €** |
| **Suscripción mensual** | descargas ilimitadas + novedades del mes + área de miembros | uso continuo | **9 €/mes** |
| **Suscripción anual** | lo mismo, 2 meses gratis y precio fijo | el que ya sabe que se queda | **79 €/año** (≈6,6 €/mes) |

Razonamiento:
- El Pack a 19 € cuesta lo mismo que ~2 meses de suscripción: empuja a suscribirse en cuanto se quiere más de 10 recursos.
- El anual (−27 % frente a 12 × 9 €) reduce abandono y adelanta caja; es el plan que más conviene empujar.
- Precios bajos a propósito: es un imán de captación, no el negocio principal. Subir es fácil; bajar enfada.
- **Oferta de lanzamiento (opcional):** primeros 100 suscriptores anuales a 59 € de por vida mientras no cancelen. Da prueba social y datos reales.

## 3. Números orientativos (hipótesis)
- Comisión Stripe en tarjeta europea ≈ 1,5 % + 0,25 €: en 9 € se queda ≈ 0,39 € (4,3 %); en 79 € ≈ 1,44 € (1,8 %). Otro motivo para empujar el anual.
- IVA 21 % incluido en el precio: 9 € → 7,44 € netos; 79 € → 65,29 €; 19 € → 15,70 €.
- Coste fijo de infraestructura bajo (Vercel, Supabase, Resend): decenas de €/mes. **El coste real es producir novedades mensuales** (tiempo de María / carriles).
- Punto de equilibrio solo de infraestructura: ~10 suscriptores mensuales. Para que pague la producción de contenido hay que fijar antes cuántas horas/mes se dedican; **eso no lo sé**.
- Gasto recurrente dentro de tu autoridad (≤ 2.000 USD/mes recurrentes): no requiere más firmas.

## 4. Novedades mensuales y área de miembros (alcance mínimo)
- Área de miembros = la biblioteca actual filtrada por plan + historial de descargas + gestión de suscripción (portal de cliente de Stripe, sin construir facturación propia).
- «Novedades mensuales» = **un compromiso editorial**: p. ej. 3 recursos nuevos/mes, anunciados por correo. Si no se puede sostener, prometer «novedades periódicas», no «mensuales».
- Todo el diseño con el sistema de diseño vigente; estados vacíos y errores incluidos.

## 5. Decisiones que necesito de ti (con recomendada)
1. **Precios** 19 € / 9 € mes / 79 € año — *recomendada: sí, ajustando a los precios test ya creados en Stripe.*
2. **Oferta fundadores** (100 × 59 €) — *recomendada: sí, con tope de plazas.*
3. **Compromiso de novedades**: 3 recursos/mes vs. «periódicas» — *recomendada: 2 al mes al empezar.*
4. **Entidad vendedora e IVA/OSS** (LLC vs. SL) — *bloqueante para activar cobros reales; lo decides tú antes de lanzar.*
5. **SKU por área** (hoy desactivados): dejarlos fuera de la primera versión — *recomendada: sí*, porque hoy arrastran el texto «acceso permanente» incompatible con la suscripción.
6. Reembolsos/disputas: hoy **no revocan** el acceso. Definir política antes de lanzar.

## 6. Plan de entrega (cuando apruebes; no empieza hasta entonces)
Tamaño estimado: **Spec-Lite (1-3 días)** de implementación en `pildora-recursos`, tras fusionar PR #7 y N-W1.
1. Activar plan anual y portal de cliente (Stripe, precios live creados por Oscar con su botón).
2. Área de miembros mínima y página `/suscripcion` con los 4 planes.
3. Correo de novedades mensual (Resend) y primera tanda de contenido.
4. Pruebas de humo en preview con claves test; producción solo con tu botón.
Al entregar: tabla «cambio → URL exacta». Migraciones, si hicieran falta, solo escritas y con número reservado.

## 7. Riesgos
- Prometer novedades que no se producen → bajas y reclamaciones.
- Cobrar sin entidad/IVA resueltos → problema fiscal.
- Cifras sin contrastar con los precios test actuales (ver aviso arriba).
