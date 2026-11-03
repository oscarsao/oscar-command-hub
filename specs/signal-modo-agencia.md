# Spec · Signal modo agencia (panel multi-cliente)

**Estado: SPEC, sin implementar (3-nov).** Tarjeta t_4551b520. Tamaño: Pitch (1-2 semanas). Por OASP el worker no
implementa. Además el código NO está en este repo (`oscar-command-hub`): Signal vive en `oscar-hq` (API + dashboard +
consola) y `Scraper` (motor, worker). Hace falta tarjeta en carril `claude-oscarhq` (y quizá `claude-scraper`).

## Petición
Rol **Agencia** con panel que lista todos sus clientes de Signal: estado del motor, empresas por semana, créditos
usados y salud (reutilizando el cálculo de t_5b023060, health score por cliente). Botones «Ver detalles» y
«Generar informe PDF» por cliente. Extras mencionados: facturación consolidada por cliente, copiar configuración entre
clientes, informe de agencia.
**Hecho:** el rol Agencia ve la tabla con datos reales de todos sus clientes y genera un PDF de al menos uno.

## Dependencias
- t_5b023060 (health score) debe estar integrado antes: se reutiliza su función, sin duplicar el cálculo.
- Modelo de datos: relación agencia→clientes (marca/tenant) y rol `agencia` en los permisos de oscar-hq. Hoy Ángel
  está «acotado por marca»; hay que decidir si Agencia = usuario con N marcas.

## Diseño propuesto (v1)
1. Backend oscar-hq: `GET /api/signal/agency/clients` (fila por marca: motor on/off + último job, empresas de los
   últimos 7 días desde `ohq_signal_jobs`/resultados, créditos usados del periodo, health score). Filtrado por las
   marcas del usuario.
2. Backend: `GET /api/signal/agency/clients/{brand}/report.pdf` (informe de un cliente: periodo, empresas, créditos,
   salud). Generación en servidor, sin servicios externos.
3. Dashboard: página «Agencia» solo para rol agencia/admin; tabla + «Ver detalles» (vista existente de la marca).
4. Tests: permisos (un usuario no ve marcas ajenas), agregados, PDF no vacío.
Fuera de v1: facturación consolidada, copiar configuraciones, informe global de agencia (v2).

## Preguntas para Oscar
1. ¿Repo/carril? Recomendado: `claude-oscarhq` (tarjeta nueva) tras integrar t_5b023060.
2. ¿Agencia = rol global nuevo o permiso por marca sobre el sistema actual? Recomendado: rol global `agencia` + lista de marcas.
3. ¿De dónde sale hoy «créditos usados» (Apollo/Supabase)? Si no hay contador por marca, v1 lo muestra «n/d».
