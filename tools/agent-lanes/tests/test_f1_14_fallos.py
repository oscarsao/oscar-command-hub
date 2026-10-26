"""F1-14: fallos corregidos junto a la botonera (rutas ajenas, block con timeout, despliegue externo, avisos del
monitor, títulos de ids). Sin red, sin Telegram ni hermes reales."""
from __future__ import annotations

from agent_lanes.solapes import Ajustes, extraer_rutas

HUB = "C:/Users/oscar/oscar-command-hub"
A = Ajustes()


def test_rutas_absolutas_ajenas_al_repo_no_cuentan_como_fichero_comun():
    t = ("toca C:\\Users\\oscar\\AppData\\Local\\Temp\\x\\notas.md y /tmp/build/salida.py y ~/dev/otro.py "
         "y C:/Users/oscar/dev/otro-repo/src/app.py")
    assert extraer_rutas(t, A, HUB) == set()


def test_ruta_absoluta_dentro_del_repo_se_deja_relativa():
    t = "edita " + HUB + "/tools/agent-lanes/agent_lanes/solapes.py"
    assert extraer_rutas(t, A, HUB) == {"tools/agent-lanes/agent_lanes/solapes.py"}
