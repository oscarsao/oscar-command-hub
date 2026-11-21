# 00b Instalar las herramientas de Python para las pruebas

Id: r-00b-herramientas-python
Orden: 2
Riesgo: bajo
Límites: Solo en el ámbito de usuario (--user). Solo estos paquetes. No ejecutes ningún script de instalación descargado.

Requiere r-00a hecha.
1. py -3.12 -m pip install --user --upgrade pip
2. py -3.12 -m pip install --user pytest ruff pyyaml
3. Comprueba: py -3.12 -m pytest --version y py -3.12 -m ruff --version.
4. Notas con las versiones. Estado hecha.
