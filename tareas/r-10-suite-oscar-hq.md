# 10 Ejecutar la suite completa de pruebas de oscar-hq y informar (solo lectura)

Id: r-10-suite-oscar-hq
Orden: 8
Riesgo: bajo
Límites: No crees ramas ni PR. No toques bases de datos reales ni variables de entorno de servicios: solo pruebas locales. Si una prueba pide una clave o una base remota, salta esa prueba y anótalo. Una sola ejecución, sin paralelo.

Requiere las tareas 00 a 03 hechas. Contexto: Oscar decidió el 20-11 no pagar GitHub Actions y tener una suite nocturna local de oscar-hq; este equipo la ejecuta primero a mano para medir tiempo y estabilidad.
1. Clona oscarsao/oscar-hq en C:\agent\repos\oscar-hq (solo lectura) y usa la rama master.
2. Lee su CLAUDE.md y sus instrucciones de pruebas; prepara un entorno virtual con py -3.12 y las dependencias de pruebas del backend (nunca copies un .env: no hay ninguno en este equipo). Para el dashboard usa solo lo que el repo documente para sus pruebas.
3. Ejecuta la suite del backend una vez. Anota tiempo total, número de pruebas pasadas, falladas y saltadas, y los nombres de las que fallen con una línea de motivo cada una. No intentes arreglar nada.
4. Informe en notas (máx. 20 líneas) y evento. Estado hecha. Si la máquina se queda sin RAM o la suite pasa de 40 minutos, detenla y anota el punto en que paraste.
