# Pitch · Signal: asistente de configuración, mejoras del panel del equipo y de Multiverse, y ciclo cerrado

Modo OASP: **Pitch** · estado: **pendiente del OK de Oscar** (nada implementado) · autor: sesión de orquestación (oscar-f8, 18-11-2026).
Idea de Oscar (17-11-2026, texto libre): Signal es un servicio integral (CRM/ARM y todo integrado), puede cobrarse más si se pule; hace falta un
**wizard sencillo** para la configuración inicial y para lanzar un ICP, **sin perder la parte avanzada**; y quiere saber **qué obtendrá cuando
termine lo que se está integrando**. No hay plan de referencia ni «100 %»: se ve cómo avanza.
Evidencia visual: artefacto «Signal antes y después» (https://claude.ai/artifact/DzCuAswG5bJn6yQQ5DQ7mT) y capturas en
`C:\Users\oscar\integrador-docs\signal_benchmark\capturas\` (datos personales de ejemplo, solo uso local).

## 1. Problema

- Oscar no puede saber qué tendrá cuando acabe lo que la Integradora está fusionando: hay muchas piezas y ninguna vista única de «qué cambia para mí».
- La UI de Signal es difícil de digerir a primera vista: dos árboles distintos (Multiverse, 10 pestañas; Píldora/MigraTeam, una página más 11
  apartados en Ajustes), «Lanzar ejecución» repetida y sin «pendiente por etapa» (inventario de DISENO_SIGNAL §1).
- El ciclo captar → CRM → resultado **no está cerrado** (auditoría 17-11): la respuesta de Smartlead no vuelve al CRM, la entrega al CRM es parcial
  y no hay facturación por uso.
- Los costes de datos son céntimos; el margen lo deciden horas humanas y automatización, y hoy el código declara Hunter a 0,10 $/crédito
  (6-12× los planes reales).

## 2. Qué obtendrá Oscar cuando termine lo que se está integrando (hechos del 18-11)

**Portal del cliente Multiverse (ya casi completo).** Fusionado: Mi cuenta y equipo (T5), series semanales y mensuales del Resumen (T7), Reuniones (T8),
Informes semanal, mensual y descargas con cierre mensual (T9), «Qué ve el cliente» por marca y rol (T10), seguridad P1 y P2 (solo el responsable
rechaza; sin autorización que falle abierta) y la API de aprobación de cargos equivalentes (#669, #683). En borrador, esperando revisión: Alcance con
4 pestañas y aprobación de cargos (T6, #685) y textos es/en con selector de idioma (T11, #684). Al fusionarse esos dos, el cliente tiene portal completo
y bilingüe.

**Panel del equipo (`/signal`).** Fusionado: paso 0 («Qué ve el cliente» en pestaña propia), T1 (variables por marca y navegación nueva) y T3 (estado en
un hook). Es base técnica: **no se ve nada nuevo con la variable apagada**. T4 (armazón: cabecera, zonas vacías ya montadas y una sola bifurcación) está
en marcha; T5-T8 (llenar las zonas) esperan a T4. Encender la variable por marca es un botón de Oscar en Vercel; con ella se ve **un panel único**: Hoy ·
Captación y enriquecimiento (embudo + 8 etapas) · Cliente y contrato · Opciones de Signal, con «pendiente por etapa» y un modo demo que no escribe.

**Lo que NO tendrá aún:** asistente de configuración, verificación de email propia, respuesta → CRM, cobro por uso, portal para otro cliente (hoy solo
`portal_brands`, por defecto Multiverse).

## 3. Lo medido (17-11, mismas 20 empresas de Panamá; muestra pequeña, tendencia no estadística)

| Herramienta | Persona objetivo con email | Coste por contacto útil |
|---|---|---|
| Apollo (API) | 15 de 20; 10-12 verificados | ≈ 0,04 $ |
| Hunter | 9 revelados: 7 «Valid», 2 «Accept all» | ≈ 0,013-0,022 € |
| Clay | 4 correctos de 13 buscadas | ≈ 0,06 $ por fila |
| Signal | no medido (faltan las claves de Hunter y SAM.gov en el motor) | – |

Lecciones: Hunter confirma como «Valid» emails que Apollo daba como adivinados (descubrir con uno y verificar con otro es barato); «sin email» de Apollo
no es fiable; las agencias pequeñas rinden poco en todas; Hunter enseña la verificación **antes** de gastar. Smartlead: asistente con perfil rellenado desde
la web, objetivo primero, una pregunta por pantalla, volumen traducido a infraestructura y ajustes por defecto con lo avanzado aparte; además vende
dominios y buzones.

## 4. Propuesta (80/20)

**Panel del equipo** (se añade al panel único, tras T4-T8)
- A1 «Pendiente por etapa» y Hoy corto (ya diseñados).
- A2 **Estado del email visible antes de gastar** (verificado / dudoso / sin dato) en Decisores y Revisión.
- A3 **«Nuevo» frente a «ya guardado»** antes de pagar un dato (Signal guarda todo append-only; falta mostrarlo).
- A4 **Coste estimado antes de lanzar y tope mensual en dinero** (hoy el tope va en cuentas).
- A5 Modo demo con bloqueo de escrituras (ya diseñado).

**Panel de Multiverse (cliente) y siguientes clientes**
- B1 «Pendientes de aprobar» y «Consumo del mes» (reutiliza la cola de aprobación y el control de créditos que ya existen para el equipo).
- B2 «Respuestas» (depende del ciclo cerrado, punto D).
- B3 Servir el portal a otras marcas (hoy solo `portal_brands`): necesario para Pahoy.

**C. Asistente de configuración (5 pasos, mismo modelo de datos; interruptor guiado/avanzado)**
1. Quién eres: marca, país, idioma y qué vendes, **rellenado leyendo la web** (Firecrawl) y solo revisado.
2. A quién buscas: texto libre → filtros editables (sector, palabras clave, país) y plantillas por sector.
3. Fuentes y tope: fuentes por defecto según ICP, de pago apagadas, **tope en dinero** con aviso al 80 %.
4. Simulacro con 10-20 cuentas: coste real y resultado esperado antes del lote (ya existen vista previa de coste y ejecución en seco).
5. Revisar y lanzar: aprobador, destino al CRM, cadencia y un lead de prueba entregado.
Reutiliza: alta de marca desde la UI, catálogo de ICP, estado de fuentes, topes, revisión humana, reglas hub → CRM.

**D. Ciclo cerrado**
- Cascada **descubrir con Apollo y verificar con Hunter** (las dos piezas ya están en signal_core; falta la clave de Hunter en la Bóveda y su lectura, tarjeta t_43297a78).
- Respuesta de Smartlead → CRM (webhook) y **un solo sistema de secuencias**.
- Recalibrar el coste de Hunter en signal_core (0,10 $ → coste real del plan).

**E. Precios y márgenes (decisión de Oscar)**
Opciones, de menos a más ambiciosa: cuota de plataforma con créditos incluidos; exceso con recargo (exige estimación de coste y consumo visible); tramo por
resultado (lead cualificado o reunión; exige ciclo cerrado). La calculadora del artefacto sirve para probar con horas humanas reales.

## 5. Appetite (a ojo, sin tarjetas parecidas: «sin referencia» salvo lo indicado)

| Entrega | Contenido | Tamaño |
|---|---|---|
| 1 | A2 + A3 + A4 dentro del panel nuevo | S-M |
| 2 | Asistente de configuración (C) | M |
| 3 | Cascada Apollo → Hunter y recalibrado de costes (D, parte 1) | S-M |
| 4 | Respuesta → CRM y secuencia única (D, parte 2) | M-L |
| 5 | Portal para quien aprueba y para otras marcas (B) | M |
| 6 | Cobro por uso (E) | M-L, depende de 1 y 4 |

Tiempos de referencia disponibles: una tarjeta S/M del carril de dashboard tardó 1,3-1,7 h de trabajo (T1, T3); la espera de revisión e integración fue de
~30 h porque el PR no se abría solo (tarjeta t_6744e1ef).

## 6. Rabbit holes y no-gos

- No construir buzones ni dominios propios: es el negocio de Smartlead; se integra, no se copia.
- El asistente **no** es un producto simple aparte: es un camino sobre el mismo modelo de datos.
- No encender nada sin botón de Oscar; no medir contra un «100 %».
- No contactar a nadie ni gastar créditos fuera de los topes que fije Oscar.

## 7. Opción por defecto y decisiones

**Por defecto (si Oscar no decide):** no se implementa nada de este pitch; solo se mantiene el documento. Cuando T4-T8 estén fusionadas, se propone empezar por
las entregas 1 y 3 (S-M cada una, reversibles, sin tocar producción hasta el botón).

Decisiones que necesito de Oscar: (a) ¿entregas 1 y 3 como siguientes?; (b) ¿qué modelo de cobro probar con Pahoy?; (c) ¿Smartlead para el envío, o seguir con
Resend/CRM?

## 8. No verificado

Estado real en producción del motor y de las migraciones; cifras reales de cuentas, créditos y envíos; qué secciones del portal v2 están pintadas; el
resultado de Signal en el test de cobertura; la política de Meta sobre bots en WhatsApp (si Pahoy lo exige).
