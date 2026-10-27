"""tools/hermes-sync/hermes_sync.py con config falsa en tmp_path, API y reinicio falsos (sin red, sin hermes real)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "hermes-sync" / "hermes_sync.py"
CONFIG = ("# config de prueba\nmodel:\n  default: modelo-a  # comentario\n  provider: prov-a\nother:\n  x: 1\n")
ENV = {"OHQ_API_URL": "http://ohq.test", "OHQ_API_KEY": "SECRETO-FALSO"}


@pytest.fixture
def hs(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("hermes_sync", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG, encoding="utf-8", newline="")
    return p


def api(datos):
    return lambda url, clave: datos


class Reinicio:
    def __init__(self, falla=False):
        self.llamadas, self.falla = 0, falla

    def __call__(self):
        self.llamadas += 1
        if self.falla:
            raise RuntimeError("no arranca")


def correr(hs, cfg, datos, aplicar=True, reinicio=None, registro=None):
    r = reinicio or Reinicio()
    cod, msg = hs.sincronizar(cfg, ENV, aplicar, registro=registro, http=api(datos), reiniciar=r,
                              ahora=lambda: datetime(2026, 10, 27, 12, 0, 0))
    return cod, msg, r


def test_cambia_modelo_y_proveedor_con_bak(hs, cfg, tmp_path):
    reg = tmp_path / "log.jsonl"
    cod, msg, r = correr(hs, cfg, {"provider": "prov-b", "model": "modelo-b"}, registro=reg)
    assert cod == 0 and r.llamadas == 1
    d = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert d["model"] == {"default": "modelo-b", "provider": "prov-b"} and d["other"] == {"x": 1}
    bak = tmp_path / "config.yaml.bak-20261027-120000"
    assert bak.read_text(encoding="utf-8") == CONFIG
    assert "# config de prueba" in cfg.read_text(encoding="utf-8")
    linea = json.loads(reg.read_text(encoding="utf-8").splitlines()[-1])
    assert linea["resultado"] == "aplicado" and "SECRETO" not in reg.read_text(encoding="utf-8") + msg


def test_solo_cambia_lo_que_difiere(hs, cfg):
    cod, _, r = correr(hs, cfg, {"provider": "prov-a", "model": "modelo-b"})
    d = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert cod == 0 and d["model"] == {"default": "modelo-b", "provider": "prov-a"}


def test_sin_cambio_no_toca_nada(hs, cfg, tmp_path):
    cod, msg, r = correr(hs, cfg, {"provider": "prov-a", "model": "modelo-a"})
    assert cod == 0 and r.llamadas == 0 and cfg.read_text(encoding="utf-8") == CONFIG
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"] and "Sin cambios" in msg


def test_ensayo_por_defecto_no_escribe(hs, cfg, tmp_path):
    cod, msg, r = correr(hs, cfg, {"model": "modelo-b"}, aplicar=False)
    assert cod == 0 and r.llamadas == 0 and cfg.read_text(encoding="utf-8") == CONFIG and "ENSAYO" in msg
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_ajuste_vacio_no_toca(hs, cfg):
    cod, _, r = correr(hs, cfg, {"provider": "", "model": None})
    assert cod == 0 and r.llamadas == 0 and cfg.read_text(encoding="utf-8") == CONFIG


def test_aborta_si_el_yaml_queda_invalido(hs, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "editar", lambda texto, cambios: texto + "\n  : [roto\n")
    cod, msg, r = correr(hs, cfg, {"model": "modelo-b"})
    assert cod == 1 and "ABORTADO" in msg and r.llamadas == 0
    assert cfg.read_text(encoding="utf-8") == CONFIG and [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_rechaza_valores_con_yaml_inyectado(hs, cfg):
    cod, msg, r = correr(hs, cfg, {"model": "x\nplatforms: {evil: 1}"})
    assert cod == 1 and "ABORTADO" in msg and cfg.read_text(encoding="utf-8") == CONFIG


def test_reinicio_fallido_restaura(hs, cfg):
    cod, msg, r = correr(hs, cfg, {"model": "modelo-b"}, reinicio=Reinicio(falla=True))
    assert cod == 1 and r.llamadas == 1 and cfg.read_text(encoding="utf-8") == CONFIG


def test_error_de_red_no_toca(hs, cfg):
    def roto(url, clave):
        raise OSError("sin red")
    cod, msg = hs.sincronizar(cfg, ENV, True, http=roto, reiniciar=Reinicio())
    assert cod == 1 and "OSError" in msg and cfg.read_text(encoding="utf-8") == CONFIG


def test_sin_clave_aborta(hs, cfg):
    cod, msg = hs.sincronizar(cfg, {}, True, http=api({}), reiniciar=Reinicio())
    assert cod == 1 and "OHQ_API_KEY" in msg


def test_config_sin_bloque_model_no_se_toca(hs, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("model: x\n", encoding="utf-8")
    cod, msg = hs.sincronizar(p, ENV, True, http=api({"model": "y"}), reiniciar=Reinicio())
    assert cod == 1 and p.read_text(encoding="utf-8") == "model: x\n"


def test_conserva_crlf_y_anade_claves_que_faltan(hs, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b"model:\r\n  default: a\r\nother: 1\r\n")
    cod, _ = hs.sincronizar(p, ENV, True, http=api({"provider": "prov-b"}), reiniciar=Reinicio(),
                            ahora=lambda: datetime(2026, 10, 27))
    assert cod == 0
    assert p.read_bytes() == b"model:\r\n  provider: prov-b\r\n  default: a\r\nother: 1\r\n"


def test_acepta_respuesta_anidada_en_hermes(hs, cfg):
    cod, _, _ = correr(hs, cfg, {"hermes": {"model": "modelo-b"}})
    assert cod == 0 and yaml.safe_load(cfg.read_text(encoding="utf-8"))["model"]["default"] == "modelo-b"
