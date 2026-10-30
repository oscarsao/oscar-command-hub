"""Humo de `lanes.py status`: el 30-10 faltaba el import de `cuota` y la orden se rompía con NameError sin que ningún
test lo viera. Solo comprueba que la línea del runner se construye."""
import importlib.util
from pathlib import Path

LANES = Path(__file__).resolve().parents[1] / "lanes.py"


def _cargar():
    spec = importlib.util.spec_from_file_location("lanes_cli", LANES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_line_se_construye_sin_nameerror():
    assert isinstance(_cargar().runner_line(), str)
