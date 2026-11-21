# 00 Verificar el equipo y registrarlo (solo lectura)

Id: r-00-verificar-equipo
Orden: 4
Riesgo: bajo
Límites: Solo lectura. No instales nada. No muestres NUNCA el valor de una variable de entorno, token ni clave: solo nombres. Si un paso pide administrador o una credencial, PARA y pídeselo a Oscar.

Requiere r-00a, r-00b y r-00c hechas. Objetivo: comprobar que el equipo está listo y dar de alta su estado en la cola.
1. Sistema: versión de Windows; RAM total y libre en GB (Get-CimInstance Win32_ComputerSystem y Win32_OperatingSystem); espacio libre del disco C: en GB.
2. Herramientas y versiones: git --version, node --version, py -3.12 --version, gh --version, claude --version. Anota las que falten (no las instales).
3. GitHub: gh auth status (sin mostrar el token). Debe estar iniciada sesión con la cuenta técnica acordada; si no, para y avisa.
4. Seguridad: lista SOLO los NOMBRES de variables de entorno cuyo nombre contenga VERCEL, DOKPLOY, SUPABASE, RESEND, GMAIL, APOLLO, TELEGRAM, STRIPE o SERVICE_ROLE. Debe salir una lista vacía. Busca también ficheros .env bajo C:\agent y tu carpeta de usuario y di solo cuántos hay y dónde (rutas), sin abrirlos.
5. Registra el equipo: anota en resultados/r-00.md (no hay colección hosts) los valores de ramTotalGB, ramLibreGB, slotsMax (máximo 2 si la RAM total es 16 GB o menos), slotsUsados 0, so, modelo sonnet, cuenta (el nombre de la cuenta, nunca claves) y ultimaSenal con la hora ISO actual.
6. Informe: deja en notas de esta tarea un resumen de 10 líneas con lo que falta . Pasa la tarea a estado hecha si todo está en orden o a bloqueada con la pregunta concreta para Oscar.
