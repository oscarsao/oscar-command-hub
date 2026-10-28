"""Los tests nunca leen ni escriben los ajustes reales del hub (ajustes_hub.json): cada test parte de los de fábrica."""
import pytest

from agent_lanes import ajustes_hub, people
from agent_lanes.integrator import Integrator


@pytest.fixture(autouse=True)
def _ajustes_aislados(tmp_path):
    previo = ajustes_hub.usar(ajustes_hub.Ajustes(tmp_path / "_ajustes" / "valores.json", tmp_path / "_ajustes" / "aud.jsonl"))
    yield
    ajustes_hub.usar(previo)


@pytest.fixture(autouse=True)
def _people_de_ejemplo(monkeypatch):
    """Los tests nunca leen el people.yaml real (ids de personas, fuera del repo): usan el ejemplo con ids ficticios."""
    monkeypatch.setenv(people.PEOPLE_ENV, str(people.PEOPLE_EXAMPLE))
    # Nunca se llama a Oscar HQ de verdad: los permisos del equipo son los que tenía el ejemplo antes del 28-10.
    monkeypatch.setattr(people, "hq_desde_entorno", lambda *a, **k: hq_de_ejemplo)


HQ_EJEMPLO = {"personas": [
    {"telegram_id": "5000000001", "permisos": {"*": ["desarrollo.aprobar", "desarrollo.publicar"]}},  # María
    {"telegram_id": "5000000004", "permisos": {m: ["desarrollo.aprobar", "desarrollo.publicar"]  # Angel
                                               for m in ("Píldora", "Signal", "Hub")}},
]}


def hq_de_ejemplo():
    return HQ_EJEMPLO


@pytest.fixture(autouse=True)
def _sin_automatismos(monkeypatch):
    """Back-merge y retro semanal del integrador se prueban en test_alertas_backmerge_retro.py; en el resto de tests
    (con relojes y tableros falsos) no deben colarse mensajes ni tarjetas."""
    monkeypatch.setattr(Integrator, "automatismos", False)


ANSWER_OWNER_ID = "100000001"  # id ficticio de Oscar para validar `RESPUESTA-OSCAR (id=…)` (P07) en los tests


@pytest.fixture(autouse=True)
def _personas_respuestas():
    """P07: `hermes_answer_text` valida el id contra people.yaml; en los tests, un People solo con un Oscar ficticio."""
    from agent_lanes import hermes
    from agent_lanes.people import People
    hermes.set_answer_people(People(ANSWER_OWNER_ID))
    yield
    hermes.set_answer_people(None)


@pytest.fixture(autouse=True)
def _aprobaciones_aisladas(tmp_path, monkeypatch):
    """P08: el registro local de aprobaciones de cada test vive en su tmp_path (nunca en .state del hub)."""
    from agent_lanes import aprobaciones
    monkeypatch.setattr(aprobaciones, "APPROVALS_DIR", tmp_path / "_approvals")
