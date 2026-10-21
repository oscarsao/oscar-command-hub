"""max_workers: lanes.yaml manda (2) y `--max-workers` puede sobrescribirlo."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runner  # noqa: E402
from agent_lanes.config import load_lanes, load_runner_settings  # noqa: E402


def test_lanes_yaml_real_pide_dos_workers():
    assert load_runner_settings()["max_workers"] == 2


def test_max_workers_se_lee_del_yaml(tmp_path):
    yml = tmp_path / "lanes.yaml"
    yml.write_text("runner:\n  max_workers: 2\n", encoding="utf-8")
    assert load_runner_settings(yml)["max_workers"] == 2


def test_sin_clave_el_defecto_es_uno(tmp_path):
    yml = tmp_path / "lanes.yaml"
    yml.write_text("runner: {}\n", encoding="utf-8")
    assert load_runner_settings(yml)["max_workers"] == 1


def test_argumento_cero_usa_el_yaml_y_positivo_lo_sobrescribe():
    settings = {"max_workers": 2}
    assert runner.effective_max_workers(0, settings) == 2
    assert runner.effective_max_workers(1, settings) == 1
    assert runner.effective_max_workers(3, settings) == 3


def test_max_parallel_por_carril_no_supera_uno():
    assert all(lane.max_parallel == 1 for lane in load_lanes().values())
