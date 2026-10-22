"""Plantilla de workflow implementar-con-revision (F0): validador de args, paridad con el guion y --dry-run sin efectos."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_lanes import workflow_plantilla as wp

ARGS = {
    "repo": "C:/x/repo", "remoto": "github.com/o/repo", "base": "main", "worktree": "C:/x/wt", "rama": "lane/t_abcd1234",
    "encargo": "Haz X", "autorizacion": "Tarjeta t_abcd1234: Oscar aprobó el 21-oct «sí»", "test_cmd": "pytest -q",
    "atribucion_commit": "Co-Authored-By: Claude <noreply@anthropic.com>",
    "ficheros_permitidos": ["src/"], "ficheros_vetados": [".github/"],
}

# Arnés mínimo: ejecuta el guion como el motor (globales args/agent/phase/parallel/log) y cuenta las llamadas a agent().
HARNESS = r"""
const fs = require('fs')
const src = fs.readFileSync(process.argv[1], 'utf8').replace('export const meta', 'const meta')
const args = JSON.parse(process.argv[2])
let agentes = 0
const g = { agent: async () => { agentes++; return 'ok' }, phase() {}, parallel: async (fs_) => Promise.all(fs_.map(f => f())), log() {} }
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
new AsyncFunction('args', 'agent', 'phase', 'parallel', 'log', src)(args, g.agent, g.phase, g.parallel, g.log)
  .then(r => console.log(JSON.stringify({ r, agentes })))
  .catch(e => console.log(JSON.stringify({ error: String(e.message), agentes })))
"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")


def run_plantilla(args: dict) -> dict:
    out = subprocess.run(["node", "-e", HARNESS, str(wp.PLANTILLA), json.dumps(args)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_args_validos():
    assert wp.validar_args(ARGS) == []


@pytest.mark.parametrize("campo", wp.REQUERIDOS)
def test_falta_campo_obligatorio(campo):
    a = {k: v for k, v in ARGS.items() if k != campo}
    assert any(campo in e for e in wp.validar_args(a))


def test_autorizacion_obligatoria_y_con_fuente():
    assert any("autorizacion" in e for e in wp.validar_args({**ARGS, "autorizacion": "  "}))
    assert any("autorizacion" in e for e in wp.validar_args({**ARGS, "autorizacion": "me dijeron que sí"}))


@pytest.mark.parametrize("rama", ["main", "master", "feature/x"])
def test_rama_debe_ser_lane_y_no_la_base(rama):
    assert wp.validar_args({**ARGS, "rama": rama})


def test_rama_igual_a_la_base_se_rechaza():
    assert any("base" in e for e in wp.validar_args({**ARGS, "base": "lane/t_abcd1234"}))


def test_listas_deben_ser_listas():
    assert wp.validar_args({**ARGS, "ficheros_vetados": ".github/"})


def test_enmascarar_secretos():
    assert "ghp_" not in wp.enmascarar("token ghp_abcdefgh12345678 fin")
    assert wp.enmascarar("sin secretos") == "sin secretos"


def test_autorizacion_desde_tarjeta_pasa_el_validador():
    aut = wp.autorizacion_desde_tarjeta("t_3cd20f03", "Respuesta de Oscar: Aprobar")
    assert wp.validar_args({**ARGS, "autorizacion": aut}) == []


def test_paridad_con_el_guion():
    js = wp.PLANTILLA.read_text(encoding="utf-8")
    req = re.search(r"const REQUERIDOS = \[(.*?)\]", js).group(1)
    assert tuple(re.findall(r"'(\w+)'", req)) == wp.REQUERIDOS
    lis = re.search(r"const LISTAS = \[(.*?)\]", js).group(1)
    assert tuple(re.findall(r"'(\w+)'", lis)) == wp.LISTAS


def test_guion_sin_rutas_ni_nombres_de_proyecto():
    js = wp.PLANTILLA.read_text(encoding="utf-8")
    for prohibido in ("oscar-hq", "oscarsao", "C:/Users", "unnel"):
        assert prohibido not in js


@needs_node
def test_dry_run_no_lanza_agentes_y_enmascara():
    res = run_plantilla({**ARGS, "dry_run": True, "encargo": "Haz X con ghp_abcdefgh12345678"})
    assert res["agentes"] == 0 and "error" not in res
    plan = res["r"]
    assert plan["fases"] == ["Spec", "Implementar", "Revisar", "Corregir"]
    assert plan["lentes"] == ["correccion", "seguridad", "configurable"]
    assert "ghp_abcdefgh" not in json.dumps(plan)


@needs_node
def test_dry_run_inyecta_la_autorizacion_en_cada_prompt():
    plan = run_plantilla({**ARGS, "dry_run": True})["r"]
    prompts = [plan["prompts"]["spec"], plan["prompts"]["implementar"], plan["prompts"]["corregir"], *plan["prompts"]["revisar"]]
    assert len(prompts) == 6
    assert all(p.startswith("ENCARGO Y AUTORIZACIÓN: Tarjeta t_abcd1234") for p in prompts)
    assert all("Co-Authored-By" in p or "solo lectura" in p for p in prompts)


@needs_node
def test_args_invalidos_abortan_antes_de_lanzar_agentes():
    res = run_plantilla({k: v for k, v in ARGS.items() if k != "autorizacion"})
    assert res["agentes"] == 0 and "autorizacion" in res["error"]


@needs_node
def test_ejecucion_completa_con_dobles_lanza_seis_agentes():
    res = run_plantilla(ARGS)
    assert "error" not in res and res["agentes"] == 2 + 3 + 1
