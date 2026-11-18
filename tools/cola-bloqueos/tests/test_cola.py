"""Tests de cola.py con datos de ejemplo: sin red, sin hermes ni gh reales."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cola  # noqa: E402

AHORA = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def epoch(horas_atras):
    return AHORA.timestamp() - horas_atras * 3600


LISTA_DEFAULT = [
    {"id": "t_aaaaaaaa", "title": "Padre · S", "status": "blocked", "assignee": "claude-hub", "body": "x"},
    {"id": "t_bbbbbbbb", "title": "Cita al padre", "status": "ready", "assignee": "claude-hub",
     "body": "Depende de t_aaaaaaaa"},
    {"id": "t_cccccccc", "title": "Espera", "status": "todo", "assignee": "claude-hub",
     "body": "ESPERA_PADRES: t_aaaaaaaa"},
    {"id": "t_dddddddd", "title": "Hecha que cita", "status": "done", "assignee": "x", "body": "t_aaaaaaaa"},
    {"id": "t_eeeeeeee", "title": "Libre", "status": "ready", "assignee": "x", "body": ""},
]
SHOW_A = {"task": {"id": "t_aaaaaaaa", "branch_name": "lane/t_aaaaaaaa", "body": "x"},
          "comments": [{"author": "lane", "body": "Necesita decisión de Oscar", "created_at": epoch(30)}],
          "children": ["t_ffffffff"]}
SHOW_C = {"task": {"id": "t_cccccccc", "body": "ESPERA_PADRES"}, "comments": [], "children": []}
PRS = {"default": [{"number": 7, "baseRefName": "lane/t_aaaaaaaa"}, {"number": 8, "baseRefName": "main"}]}


def items():
    return cola.construir({"default": LISTA_DEFAULT},
                          {("default", "t_aaaaaaaa"): SHOW_A, ("default", "t_cccccccc"): SHOW_C}, PRS, AHORA)


def test_solo_bloqueadas_y_en_espera():
    assert {i["ref"] for i in items()} == {"t_aaaaaaaa", "t_cccccccc"}


def test_cola_suma_enlace_texto_y_pr_sin_contar_hechas():
    a = items()[0]
    assert a["ref"] == "t_aaaaaaaa"
    assert a["cola"] == 4  # hija por enlace + t_bbbbbbbb + t_cccccccc (texto) + PR #7
    assert set(a["bloquea"].split(", ")) == {"t_ffffffff", "t_bbbbbbbb", "t_cccccccc", "PR #7"}


def test_campos_orden_y_urgente():
    its = items()
    for i in its:
        assert set(cola.CAMPOS) <= set(i)
    assert [i["orden"] for i in its] == [1, 2]
    assert its[0]["urgente"] is True and its[0]["resuelve"] == "Oscar" and its[0]["seccion"] == "decision"
    assert its[0]["estima"] == "S" and its[0]["desde"].startswith("2026-11-17T06:00")
    assert its[1]["resuelve"] == "Integradora" and its[1]["urgente"] is False


def test_urgente_por_antiguedad():
    lista = [{"id": "t_11111111", "title": "Vieja", "status": "blocked", "assignee": "c", "body": ""}]
    show = {"task": {}, "comments": [{"body": "algo", "created_at": epoch(25)}]}
    i = cola.construir({"x": lista}, {("x", "t_11111111"): show}, {}, AHORA)[0]
    assert i["cola"] == 0 and i["urgente"] is True and i["resuelve"] == "carril c"


def test_main_escribe_json_y_solo_lee(tmp_path):
    llamadas = []

    def run(cmd):
        llamadas.append(cmd)
        if cmd[0] == "H" and "list" in cmd:
            return json.dumps(LISTA_DEFAULT)
        if cmd[0] == "H" and "show" in cmd:
            return json.dumps(SHOW_A if "t_aaaaaaaa" in cmd else SHOW_C)
        if cmd[0] == "G":
            return json.dumps(PRS["default"])
        raise AssertionError(cmd)

    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"tableros": {"default": ["o/r"]}}), encoding="utf-8")
    assert cola.main(["--salida", str(tmp_path), "--config", str(cfg), "--hermes", "H", "--gh", "G"],
                     run=run, ahora=AHORA) == 0
    datos = json.loads((tmp_path / "ultima.json").read_text(encoding="utf-8"))
    assert [i["ref"] for i in datos["items"]] == ["t_aaaaaaaa", "t_cccccccc"]
    verbos = {c[4] if c[0] == "H" else c[1] for c in llamadas}
    assert verbos <= {"list", "show", "pr"}  # nada de comment/block/unblock/complete


def test_tablero_caido_deja_aviso(tmp_path):
    def run(cmd):
        raise RuntimeError("caído")
    listas, _, _, avisos = cola.recoger("H", "G", {"default": ["o/r"]}, run)
    assert listas == {} and "default" in avisos[0]
    assert cola.main(["--salida", str(tmp_path), "--hermes", "H"], run=run, ahora=AHORA) == 1
