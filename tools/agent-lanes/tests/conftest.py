"""Los tests nunca leen ni escriben los ajustes reales del hub (ajustes_hub.json): cada test parte de los de fábrica."""
import pytest

from agent_lanes import ajustes_hub
from agent_lanes.integrator import Integrator


@pytest.fixture(autouse=True)
def _ajustes_aislados(tmp_path):
    previo = ajustes_hub.usar(ajustes_hub.Ajustes(tmp_path / "_ajustes" / "valores.json", tmp_path / "_ajustes" / "aud.jsonl"))
    yield
    ajustes_hub.usar(previo)


@pytest.fixture(autouse=True)
def _sin_automatismos(monkeypatch):
    """Back-merge y retro semanal del integrador se prueban en test_alertas_backmerge_retro.py; en el resto de tests
    (con relojes y tableros falsos) no deben colarse mensajes ni tarjetas."""
    monkeypatch.setattr(Integrator, "automatismos", False)
