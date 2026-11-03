# Signal: te avisa antes de que un cliente se vaya

> Documento de venta para prospectos (agencias). Versión 2026-11-03.
> Origen: tarjeta t_11ee7436 (petición de Angel). Los datos salen de la descripción del panel «Salud de clientes» de Oscar HQ.
> **Antes de compartir fuera:** revisa los puntos marcados «por confirmar» al final.

## En una frase

Signal vigila automáticamente cuánto usan la plataforma tus clientes y te avisa por email cuando uno entra en riesgo, antes de que se dé de baja.

## El problema

En una agencia, el cliente que se va casi nunca avisa. Primero deja de entrar, deja de alimentar su marca, deja de lanzar trabajo y deja de abrir los emails. Cuando te das cuenta, ya ha decidido. Vigilar eso a mano, cliente por cliente, no escala.

## Qué hace Signal

El panel **Salud de clientes** puntúa cada cuenta según su uso real de la plataforma y la clasifica por nivel de riesgo. Cuando una cuenta entra en **rojo** (churn inminente), Signal manda **un email automático** de aviso. No hay que abrir ningún panel ni acordarse de revisar nada.

## Cómo se calcula la salud (cuatro señales)

Cada cuenta recibe una puntuación sobre 100 que combina cuatro señales de engagement, con estos pesos:

| Señal | Peso | Qué mide, en claro |
|---|---|---|
| Accesos | 35 | Cada cuánto entra el cliente a la plataforma |
| Brand Brain | 25 | Si mantiene viva la información de su marca (la base con la que trabaja la IA) |
| Lotes | 25 | Si sigue lanzando trabajo (lotes de contenido/datos) |
| Emails | 15 | Si abre y reacciona a las comunicaciones |

Los accesos pesan más porque son el primer síntoma: quien deja de entrar, ya se está yendo.

## Qué alerta dispara

- **Churn inminente (rojo):** la cuenta cae al nivel más bajo de salud. Signal envía automáticamente un email de aviso.
- Está en funcionamiento con **pesos reales y cuentas reales**: hoy ya hay alertas en curso sobre cuentas en «Churn inminente». No es una maqueta.

## Qué ganas como agencia

- Detectas la fuga **semanas antes** de la baja, cuando todavía hay margen para llamar, ayudar o reenganchar.
- Sabes **qué parte falla** (no entra, no usa la marca, no lanza trabajo, no lee) y adaptas la conversación.
- Dejas de depender de la intuición del equipo: el aviso llega solo.
- Proteges la recurrencia: retener un cliente cuesta mucho menos que conseguir uno nuevo.

## Frases listas para usar

- «Signal monitoriza el engagement de tus clientes con la plataforma y te avisa antes de que se vayan.»
- «No te enteras de la baja: te enteras del riesgo, mientras aún puedes hacer algo.»
- «Cuatro señales, una puntuación y un email cuando una cuenta se pone en rojo.»

## Por confirmar antes de compartir fuera

La tarjeta no incluye el código del panel (vive en Oscar HQ, no en este repo), así que este documento **no inventa** lo siguiente. Confírmalo con quien mantiene el panel:

1. Los **umbrales** exactos de cada nivel (qué puntuación es rojo, naranja, verde).
2. Si hay **más niveles de alerta** además del rojo y si se avisa por otros canales (Telegram, etc.).
3. A quién llega el email. Hoy se sabe que llega al correo de Oscar; si se vende como aviso al responsable de cada agencia cliente, hay que confirmar que está configurable por cuenta.
4. La **ventana de tiempo** de las señales (por ejemplo, últimos 7 o 30 días).
5. Si se puede **ajustar la ponderación** por agencia (regla de Oscar: nada hardcodeado, configurable desde la UI).
6. La frase «semanas antes» es una expectativa comercial, no un dato medido: valídala con casos reales.
