"""Pruebas de tools/ci-local/suite_nocturna.py (dobles: sin red, sin pytest/vitest reales, tmp_path)."""
import importlib.util
import json
import os
import time
from datetime import datetime
from pathlib import Path

RUTA = Path(__file__).resolve().parents[2] / "ci-local" / "suite_nocturna.py"
spec = importlib.util.spec_from_file_location("suite_nocturna", RUTA)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PYTEST_OK = "........\n==== 8 passed in 1.20s ====\n"
PYTEST_KO = ("FAILED tests/test_a.py::test_x - assert 1 == 2\nERROR tests/test_b.py::test_y\n"
             "==== 1 failed, 1 error, 6 passed in 2.00s ====\n")


def test_fallos_pytest_y_resumen():
    assert m.fallos_pytest(PYTEST_KO + "FAILED tests/test_a.py::test_x\n") == [
        "tests/test_a.py::test_x", "tests/test_b.py::test_y"]
    assert m.resumen_pytest(PYTEST_OK) == "8 passed"
    assert m.resumen_pytest("nada") == "sin línea de resumen"


def test_fallos_vitest():
    o = " ✓ a.test.ts\n FAIL  src/x.test.ts > suite > caso\n FAIL  src/x.test.ts > suite > caso\n"
    assert m.fallos_vitest(o) == ["vitest:src/x.test.ts > suite > caso"]


def test_conclusion():
    assert m.conclusion("", [], [], []) == "PASA"
    assert m.conclusion("", ["a"], ["a"], []) == "SIN FALLOS NUEVOS"
    assert m.conclusion("", ["a", "b"], ["a"], []) == "FALLA"
    assert m.conclusion("", [], [], ["pytest"]) == "ERROR"


def test_candado(tmp_path):
    c = tmp_path / "x" / ".candado"
    assert m.candado_tomar(c) is True
    assert m.candado_tomar(c) is False
    viejo = time.time() - 7 * 3600
    os.utime(c, (viejo, viejo))
    assert m.candado_tomar(c) is True  # caducado: se recupera


def _run_falso(pytest_out, salida_pytest_codigo=1):
    llamadas = []

    def run(cmd, cwd, timeout):
        llamadas.append(cmd)
        if cmd[0] == "git":
            if "clone" in cmd:
                Path(cmd[-1]).mkdir(parents=True)
                (Path(cmd[-1]) / "dashboard").mkdir()
                return 0, ""
            if "get-url" in cmd:
                return 0, "https://example.invalid/x.git\n"
            if "rev-parse" in cmd:
                return 0, "abc123def456\n"
            return 0, ""
        if "pytest" in cmd:
            return salida_pytest_codigo, pytest_out
        if "install" in cmd:
            return 0, ""
        return 0, "ok"  # vitest

    return run, llamadas


def _main(tmp_path, run, ram=40.0, extra=()):
    return m.main(["--salida", str(tmp_path / "s"), "--tmp", str(tmp_path / "t"), *extra],
                  ram=lambda: ram, run=run, ahora=lambda: datetime(2026, 11, 21))


def test_primera_pasada_crea_base_y_informe(tmp_path):
    run, _ = _run_falso(PYTEST_KO)
    assert _main(tmp_path, run) == 0
    inf = (tmp_path / "s" / "2026-11-21.markdown").read_text(encoding="utf-8")
    assert "abc123def456" in inf and "SIN FALLOS NUEVOS" in inf
    base = json.loads((tmp_path / "s" / "linea_base.json").read_text(encoding="utf-8"))
    assert base["fallos"] == ["tests/test_a.py::test_x", "tests/test_b.py::test_y"]
    assert not (tmp_path / "t" / "ci_local_ohq_2026-11-21").exists()  # clon borrado
    assert not (tmp_path / "s" / ".candado").exists()


def test_fallo_nuevo_frente_a_la_base(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "linea_base.json").write_text(
        json.dumps({"sha": "old", "fallos": ["tests/test_a.py::test_x"]}), encoding="utf-8")
    run, _ = _run_falso(PYTEST_KO)
    assert _main(tmp_path, run) == 1
    inf = (tmp_path / "s" / "2026-11-21.markdown").read_text(encoding="utf-8")
    assert "FALLA" in inf and "`tests/test_b.py::test_y`" in inf
    assert "`old`" in inf


def test_pasa(tmp_path):
    run, _ = _run_falso(PYTEST_OK, 0)
    assert _main(tmp_path, run) == 0
    assert "**Conclusión:** PASA" in (tmp_path / "s" / "2026-11-21.markdown").read_text(encoding="utf-8")


def test_ram_alta_omite_y_no_lanza_nada(tmp_path):
    run, llamadas = _run_falso(PYTEST_OK, 0)
    assert _main(tmp_path, run, ram=91.0) == 2
    assert llamadas == []
    inf = (tmp_path / "s" / "2026-11-21.markdown").read_text(encoding="utf-8")
    assert "OMITIDO" in inf and "RAM al 91 %" in inf


def test_ram_alta_con_forzar_ejecuta(tmp_path):
    run, llamadas = _run_falso(PYTEST_OK, 0)
    assert _main(tmp_path, run, ram=91.0, extra=["--forzar"]) == 0
    assert llamadas


def test_candado_vivo_no_lanza_segunda_pasada(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / ".candado").write_text("1")
    run, llamadas = _run_falso(PYTEST_OK, 0)
    assert _main(tmp_path, run) == 2
    assert llamadas == []
    assert (tmp_path / "s" / ".candado").exists()  # no borra el candado ajeno


def test_pytest_no_arranca_es_error(tmp_path):
    run, _ = _run_falso("Traceback...", 4)
    assert _main(tmp_path, run, extra=["--sin-vitest"]) == 1
    assert "ERROR" in (tmp_path / "s" / "2026-11-21.markdown").read_text(encoding="utf-8")
    assert not (tmp_path / "s" / "linea_base.json").exists()  # un error no fija base
