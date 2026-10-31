# Spec-Lite — Health score por cliente (churn inminente)

Tarjeta t_5b023060 · pidió Angel · solo visible para Oscar/Admin.

## Problema
Detectar clientes a punto de irse (churn) con un score 0-100 calculado con uso real, y avisar a Oscar por email al entrar en zona roja.

## Aviso de carril
La tarjeta cae en el carril `claude-hub`, pero los datos (logins, lotes revisados, emails enviados, Brand Brain) y la vista Admin viven en **Oscar HQ**. Debería ir al carril `claude-oscarhq`. Es además una feature de días (Spec-Lite), no un bugfix.

## Propuesta
- Score = suma ponderada (pesos y umbrales configurables desde la UI, no hardcodeados):
  - Logins 7 d: 0 = rojo, 1-2 = amarillo, 3+ = verde.
  - Lotes revisados 14 d.
  - Emails enviados 30 d.
  - % Brand Brain completado: <50 % = rojo.
- Zonas: verde = activo, amarillo = en riesgo, rojo = churn inminente.
- Tarea diaria que recalcula y guarda el histórico por cliente.
- Alerta por email a Oscar solo en la **transición** a rojo (sin repetir cada día), con cliente, score y métrica que falla.
- Vista solo Oscar/Admin con semáforo por cliente.

## Preguntas abiertas
1. Umbrales de lotes y emails para verde/amarillo/rojo.
2. Pesos de cada métrica.
3. Servicio de email y destinatario (Resend / correo de Oscar).
4. Dónde se registran los logins (¿tabla de sesiones/auditoría?).

## Criterio de hecho
Score calculado con datos reales; una cuenta que pasa a rojo dispara el email a Oscar; tests con datos de ejemplo para cada zona y para la no-repetición.
