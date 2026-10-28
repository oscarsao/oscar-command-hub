"""Prevención de choques al lanzar (23-10): tableros y git falsos, sin git/gh/hermes reales."""
import dataclasses
import json

from agent_lanes.prevencion import LIBERADA, MARCA, Prevencion
from agent_lanes.runner import LaneRunner

from .test_choques import CMD, DEC, HUB, NOW, FakeGit
from .test_runner_state import LANE as BASE_LANE
from .test_runner_state import FakeNotifier, FakeVerifier, FakeWorker, ok_outcome

LANE = dataclasses.replace(BASE_LANE, name="claude-hub", board="default", repo=HUB, base="main")
LANES = {"claude-hub": LANE}
YAML = "prevenir_al_lanzar: true\nprevenir_cache_minutos: 0\n"


class Board:
    def __init__(self, cards):
        self.cards = cards
        self.comments: dict[str, list] = {}

    def list_status_all(self, status):
        return [c for c in self.cards if c["status"] == status]

    def list_ready(self, assignee):
        return [c for c in self.cards if c["status"] == "ready" and c["assignee"] == assignee]

    def show(self, tid):
        task = next((c for c in self.cards if c["id"] == tid), None)
        return {"task": task, "comments": list(self.comments.get(tid, []))} if task else {}

    def comment(self, tid, text, author=None):
        self.comments.setdefault(tid, []).append({"author": author, "body": text})
        return True

    def drop_telegram_subs(self, tid):
        return []

    def notas(self, tid, marca):
        return [c["body"] for c in self.comments.get(tid, []) if c["body"].startswith(marca)]


def card(tid, ruta, status="ready", title=None):
    return {"id": tid, "assignee": "claude-hub", "status": status, "title": title or f"tarea {tid}",
            "body": f"Tocar {ruta}"}


def _reloj_que_avanza():
    t = [0.0]

    def clock():
        t[0] += 1000  # cada lectura caduca las cachés (Hermes y git): los tests ven el tablero al momento
        return t[0]
    return clock


def prev(tmp_path, board, git=None, yaml=YAML, w=None, clock=None):
    (tmp_path / "c.yaml").write_text(yaml, encoding="utf-8")
    return Prevencion(LANES, lambda b: board, git=git or FakeGit(), settings_path=tmp_path / "c.yaml",
                      silenciadas_path=tmp_path / "sil.json", ignorados_path=tmp_path / "ign.json",
                      descartes_path=tmp_path / "desc.json", state_path=tmp_path / "prev.json",
                      wall=w or (lambda: NOW), clock=clock or _reloj_que_avanza())


def ids(ts):
    return [t["id"] for t in ts]


def ready(board):
    return board.list_ready("claude-hub")


def test_ready_que_choca_con_una_en_curso_espera_y_se_libera_al_terminar(tmp_path):
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    p = prev(tmp_path, b)
    assert p.filtrar(LANE, ready(b), b) == []
    [nota] = b.notas("t_00000002", MARCA)
    assert "t_00000001" in nota and "commands.py" in nota and "se lanza sola" in nota
    assert p.filtrar(LANE, ready(b), b) == []
    assert len(b.notas("t_00000002", MARCA)) == 1                       # una sola vez
    assert json.loads((tmp_path / "prev.json").read_text())["t_00000002"]["con"] == "t_00000001"
    b.cards[0]["status"] = "done"                                         # la otra termina
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000002"]
    assert b.notas("t_00000002", LIBERADA)
    assert json.loads((tmp_path / "prev.json").read_text()) == {}


def test_tambien_frena_contra_review(tmp_path):
    b = Board([card("t_00000001", CMD, "review"), card("t_00000002", CMD)])
    assert prev(tmp_path, b).filtrar(LANE, ready(b), b) == []


def test_review_escalada_sin_worker_vivo_no_frena(tmp_path):
    b = Board([card("t_00000001", CMD, "review"), card("t_00000002", CMD)])
    p = prev(tmp_path, b)
    p.escaladas_dir = tmp_path / "rb"
    (tmp_path / "rb").mkdir()
    assert p.filtrar(LANE, ready(b), b) == []                       # en review normal sigue frenando
    (tmp_path / "rb" / "t_00000001.json").write_text('{"head_sha": "a"}')  # el carril review la aparcó
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000002"]
    (tmp_path / "rb" / "t_00000001.json").unlink()                  # cabeza nueva: vuelve a contar
    b.cards[1]["status"] = "ready"
    assert ids(p.filtrar(LANE, ready(b), b)) == []


def test_mismo_modulo_distinto_fichero_no_frena(tmp_path):
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", DEC)])
    assert ids(prev(tmp_path, b).filtrar(LANE, ready(b), b)) == ["t_00000002"]
    assert not b.comments


def test_dos_ready_de_la_misma_zona_en_la_misma_pasada_sale_solo_la_primera(tmp_path):
    b = Board([card("t_00000001", CMD), card("t_00000002", CMD), card("t_00000003", DEC)])
    assert ids(prev(tmp_path, b).filtrar(LANE, ready(b), b)) == ["t_00000001", "t_00000003"]
    assert "t_00000001" in b.notas("t_00000002", MARCA)[0]
    assert "también lista, va primero" in b.notas("t_00000002", MARCA)[0]


def test_recien_lanzada_frena_aunque_hermes_aun_no_la_vea_en_running(tmp_path):
    b = Board([card("t_00000001", CMD)])
    p = prev(tmp_path, b, clock=lambda: 5.0)  # reloj quieto: la lista running de Hermes queda en caché (vacía)
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000001"]
    b.cards[0]["status"] = "running"            # el planificador la lanzó
    b.cards.append(card("t_00000002", CMD))
    assert p.filtrar(LANE, ready(b), b) == []
    assert "t_00000001" in b.notas("t_00000002", MARCA)[0]


def test_rama_lane_sin_fusionar_frena_hasta_que_su_pr_se_cierra(tmp_path):
    class G(FakeGit):
        cerradas = frozenset()

        def ramas_cerradas(self, repo):
            return set(self.cerradas)
    g = G({"lane/t_0000000a": (NOW, "x", [CMD])},
          prs=[{"number": 40, "headRefName": "lane/t_0000000a", "title": "x"}])
    b = Board([card("t_00000002", CMD)])
    p = prev(tmp_path, b, g)
    assert p.filtrar(LANE, ready(b), b) == []
    assert "PR #40" in b.notas("t_00000002", MARCA)[0]
    g._prs, g.cerradas = [], frozenset({"lane/t_0000000a"})                          # PR fusionado o cerrado
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_rama_lane_vieja_sin_pr_no_frena(tmp_path):
    g = FakeGit({"lane/t_0000000a": (NOW - 40 * 86400, "x", [CMD])})
    b = Board([card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b, g).filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_rama_de_persona_no_frena(tmp_path):
    g = FakeGit({"angel/fix": (NOW, "Angel", [CMD])})
    b = Board([card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b, g).filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_respeta_se_complementan_ignorar_y_silenciar(tmp_path):
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    (tmp_path / "desc.json").write_text(json.dumps(["t_00000001|t_00000002"]), encoding="utf-8")
    assert ids(prev(tmp_path, b).filtrar(LANE, ready(b), b)) == ["t_00000002"]
    (tmp_path / "desc.json").unlink()
    (tmp_path / "ign.json").write_text(json.dumps({"t_00000001|t_00000002": NOW}), encoding="utf-8")
    assert ids(prev(tmp_path, b).filtrar(LANE, ready(b), b)) == ["t_00000002"]
    g = FakeGit({"lane/t_0000000a": (NOW, "x", [CMD])})
    b2 = Board([card("t_00000003", CMD)])
    (tmp_path / "sil.json").write_text(json.dumps(["lane/t_0000000a"]), encoding="utf-8")
    assert ids(prev(tmp_path, b2, g).filtrar(LANE, ready(b2), b2)) == ["t_00000003"]


def test_con_las_dos_ramas_y_merge_limpio_no_frena(tmp_path):
    g = FakeGit({"lane/t_00000001": (NOW, "x", [CMD]), "lane/t_00000002": (NOW, "y", [CMD])},
                reales={frozenset(("lane/t_00000001", "lane/t_00000002")): False})
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b, g).filtrar(LANE, ready(b), b)) == ["t_00000002"]
    g.reales[frozenset(("lane/t_00000001", "lane/t_00000002"))] = True
    assert prev(tmp_path, b, g).filtrar(LANE, ready(b), b) == []


def test_tope_de_horas_la_lanza_igualmente(tmp_path):
    w = [NOW]
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    p = prev(tmp_path, b, yaml=YAML + "prevenir_max_horas: 2\n", w=lambda: w[0])
    assert p.filtrar(LANE, ready(b), b) == []
    w[0] = NOW + 3 * 3600
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000002"]
    assert b.notas("t_00000002", f"{LIBERADA} (tope)")
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000002"]          # no vuelve a frenarla


def test_apagada_por_yaml_o_sin_yaml_no_frena(tmp_path):
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b, yaml="prevenir_al_lanzar: false\n").filtrar(LANE, ready(b), b)) == ["t_00000002"]
    assert ids(prev(tmp_path, b, yaml=": no es yaml [").filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_sin_informacion_se_lanza(tmp_path):
    class Rota(Board):
        def list_status_all(self, status):
            raise RuntimeError("hermes caído")
    b = Rota([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b).filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_estado_de_otro_carril_no_se_borra(tmp_path):
    (tmp_path / "prev.json").write_text(json.dumps({"t_0000ffff": {"con": "x", "desde": NOW, "lane": "otro"}}),
                                        encoding="utf-8")
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    prev(tmp_path, b).filtrar(LANE, ready(b), b)
    assert set(json.loads((tmp_path / "prev.json").read_text())) == {"t_0000ffff", "t_00000002"}


# --- enganche en el planificador: LaneRunner.candidates ---------------------------------------------------------

def runner(b, prevencion):
    return LaneRunner(LANE, hermes=b, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                      notify=FakeNotifier(), prevencion=prevencion)


def test_candidates_no_ofrece_la_que_choca(tmp_path):
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD), card("t_00000003", DEC)])
    assert [c.tid for c in runner(b, prev(tmp_path, b)).candidates()] == ["t_00000003"]


def test_candidates_si_la_prevencion_falla_lanza_todo(tmp_path):
    class Boom:
        def filtrar(self, *a):
            raise RuntimeError("bug")
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    assert [c.tid for c in runner(b, Boom()).candidates()] == ["t_00000002"]


def test_carril_que_no_es_de_implementacion_no_se_filtra(tmp_path):
    b = Board([card("t_00000001", CMD, "running"), card("t_00000002", CMD)])
    ops = dataclasses.replace(LANE, kind="ops")
    assert ids(prev(tmp_path, b).filtrar(ops, ready(b), b)) == ["t_00000002"]


# --- 24-10: solo frenan las ramas lane/* de tarjetas activas o con PR abierto --------------------------------------

def test_rama_de_tarjeta_done_o_archived_sin_pr_no_frena(tmp_path):
    # t_98928c3a/lane/t_e7d17dba (done, integrada por otro PR) y t_0383722d/lane/t_89163bdb (archived)
    g = FakeGit({"lane/t_0000000a": (NOW, "x", [CMD]), "lane/t_0000000b": (NOW, "x", [CMD])})
    b = Board([card("t_0000000a", "otra cosa", "done"), card("t_0000000b", "otra cosa", "archived"),
               card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b, g).filtrar(LANE, ready(b), b)) == ["t_00000002"]
    assert not b.notas("t_00000002", MARCA)


def test_rama_de_tarjeta_done_con_pr_abierto_frena_mientras_siga_abierto(tmp_path):
    # t_88fc167f/lane/t_05c99181: tarjeta done con su PR #116 aún abierto
    g = FakeGit({"lane/t_0000000a": (NOW, "x", [CMD])},
                prs=[{"number": 116, "headRefName": "lane/t_0000000a", "title": "x"}])
    b = Board([card("t_0000000a", "otra cosa", "done"), card("t_00000002", CMD)])
    p = prev(tmp_path, b, g)
    assert p.filtrar(LANE, ready(b), b) == []
    assert "PR #116" in b.notas("t_00000002", MARCA)[0]
    g._prs = []                                                                       # el PR se fusiona o se cierra
    assert ids(p.filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_rama_de_tarjeta_blocked_sin_pr_frena(tmp_path):
    g = FakeGit({"lane/t_0000000a": (NOW, "x", [CMD])})
    b = Board([card("t_0000000a", "otra cosa", "blocked"), card("t_00000002", CMD)])
    assert prev(tmp_path, b, g).filtrar(LANE, ready(b), b) == []


def test_rama_sin_tarjeta_conocida_ni_pr_no_frena(tmp_path):
    g = FakeGit({"lane/t_0000000a": (NOW, "x", [CMD])})
    b = Board([card("t_00000002", CMD)])
    assert ids(prev(tmp_path, b, g).filtrar(LANE, ready(b), b)) == ["t_00000002"]


def test_si_hermes_no_lee_los_activos_vuelve_a_la_regla_de_antiguedad(tmp_path):
    class Media(Board):
        def list_status_all(self, status):
            if status == "blocked":
                raise RuntimeError("hermes caído")
            return super().list_status_all(status)
    g = FakeGit({"lane/t_0000000a": (NOW, "x", [CMD])})
    b = Media([card("t_0000000a", "otra cosa", "done"), card("t_00000002", CMD)])
    assert prev(tmp_path, b, g).filtrar(LANE, ready(b), b) == []                     # rama reciente: frena como antes
