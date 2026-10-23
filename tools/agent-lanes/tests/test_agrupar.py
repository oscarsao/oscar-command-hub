"""/agrupar (t_a0110f3d, A1): duplicadas, encadenado por ficheros, misma decisión, lote, bandeja, botones y descartes."""
from __future__ import annotations

from agent_lanes import agrupar as A
from agent_lanes import solapes
from agent_lanes.config import Lane
from agent_lanes.fusion import FUS_CHAIN, FUS_MERGE, FUS_OK, Fusion
from tests.test_commands import LANES, MIG, OHQ, center, command, press, texts

BODY = "## Objetivo\nAlgo.\n"


def tj(tid, title, *, assignee="claude-migrateam", status="ready", body="", created=10):
    return {"id": tid, "title": title, "body": body or BODY, "assignee": assignee, "status": status, "created_at": created}


def items(*tasks, board="migrateam"):
    return [{"board": board, "task": t, "since": t.get("created_at")} for t in tasks]


def grupos(*tasks, descartes=None, preguntas=None, lanes=LANES):
    cards, bandeja = A.preparar(items(*tasks), lanes, A.Ajustes())
    return A.proponer(cards, A.Ajustes(), descartes, preguntas=preguntas), bandeja


def test_duplicadas_por_titulo_parecido_ignora_prefijo_id_y_paso():
    g, _ = grupos(tj("t_00000001", "[MigraTeam] Arreglar login con Google · implementar"),
                  tj("t_00000002", "[MigraTeam] Arreglar el login con Google · implementar"),
                  tj("t_00000003", "[MigraTeam] Exportar facturas a PDF · implementar"))
    assert [(x.tipo, [c.tid for c in x.cards]) for x in g] == [(A.DUPLICADAS, ["t_00000001", "t_00000002"])]


def test_duplicadas_exige_mismo_repo():
    g, _ = grupos(tj("t_00000001", "Arreglar login con Google"),
                  tj("t_00000002", "Arreglar login con Google", assignee="claude-oscarhq"))
    assert g == []


def test_umbral_configurable_en_yaml(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("duplicadas_umbral: 0.5\nmax_grupos: 2\nestados: [ready]\n", encoding="utf-8")
    aj = A.load_ajustes(p)
    assert (aj.duplicadas_umbral, aj.max_grupos, aj.estados) == (0.5, 2, ("ready",))
    assert A.load_ajustes(tmp_path / "no_existe.yaml") == A.Ajustes()
    p.write_text("duplicadas_umbral: 7\nactivo: 'x'\n", encoding="utf-8")
    assert A.load_ajustes(p) == A.Ajustes()


def test_mismo_fichero_encadena_primero_la_en_curso_y_luego_la_mas_antigua():
    g, _ = grupos(tj("t_00000001", "Cambiar avisos", body="Toca `agent_lanes/commands.py`", created=30),
                  tj("t_00000002", "Nuevo comando", body="Toca agent_lanes/commands.py", created=10),
                  tj("t_00000003", "Otra cosa", body="Toca agent_lanes/commands.py", created=20, status="running"))
    [x] = g
    assert x.tipo == A.FICHEROS and "commands.py" in x.motivo
    assert [c.tid for c in x.cards] == ["t_00000003", "t_00000002", "t_00000001"]


def test_tarjetas_encadenadas_padre_hija_no_se_vuelven_a_proponer():
    cards, _ = A.preparar(items(tj("t_00000001", "Uno", body="x/commands.py"), tj("t_00000002", "Dos", body="x/commands.py")),
                          LANES, A.Ajustes())
    assert A.proponer(cards, A.Ajustes(), enlazadas=lambda a, b: True) == []
    assert len(A.proponer(cards, A.Ajustes())) == 1


def test_misma_decision_pendiente_y_lote_de_review():
    g, _ = grupos(tj("t_00000001", "Publicar web", status="blocked"), tj("t_00000002", "Subir precios", status="blocked"),
                  tj("t_00000003", "Panel de métricas", status="review"), tj("t_00000004", "Informe semanal", status="review"),
                  preguntas={"t_00000001": "¿Publicar ya?", "t_00000002": "¿publicar YA?"})
    assert {x.tipo: [c.tid for c in x.cards] for x in g} == {A.DECISION: ["t_00000001", "t_00000002"],
                                                              A.LOTE: ["t_00000003", "t_00000004"]}


def test_lote_no_mezcla_repos_distintos():
    g, _ = grupos(tj("t_00000001", "Panel", status="review"),
                  tj("t_00000002", "Informe", status="review", assignee="claude-oscarhq"))
    assert g == []


def test_personas_epicas_retenidas_y_tuyo_van_a_la_bandeja_y_no_se_agrupan():
    g, bandeja = grupos(tj("t_00000001", "[Hub] Roadmap · épica", body="x/commands.py"),
                        tj("t_00000002", "[Hub] Roadmap · retenida", body="x/commands.py"),
                        tj("t_00000003", "Renovar DNI", assignee="oscar"),
                        tj("t_00000004", "Renovar DNI", assignee="linda"),
                        tj("t_00000005", "[Hub] Pagar · tuyo"),
                        tj("t_00000006", "Hay que decidir el precio"),
                        tj("t_00000007", "Mismo trabajo", body="x/commands.py"))
    assert g == []
    assert sorted(c.tid for c, _ in bandeja) == [f"t_0000000{i}" for i in range(1, 7)]


def test_descartes_evitan_que_vuelva_a_proponerse():
    tareas = (tj("t_00000001", "Arreglar login con Google"), tj("t_00000002", "Arreglar login con Google"))
    assert len(grupos(*tareas)[0]) == 1
    assert grupos(*tareas, descartes={solapes.par("t_00000001", "t_00000002")})[0] == []


def test_botones_por_tipo():
    def botones(tipo):
        return [b["text"] for r in A.teclado(A.GrupoProp(tipo, [])) for b in r]
    assert botones(A.DUPLICADAS) == ["🔗 Fusionar", "✋ Dejar"]
    assert botones(A.FICHEROS) == ["⛓ Encadenar", "🔗 Fusionar", "✋ Dejar"]
    assert botones(A.LOTE) == ["📦 Mismo lote", "✋ Dejar"]
    assert botones(A.DECISION) == ["📌 Una sola pregunta", "✋ Dejar"]


# --- comando y botones --------------------------------------------------------------------------------------------

class Kanban:
    def __init__(self, tasks):
        self.tasks = {t["id"]: t for t in tasks}
        self.calls = []

    def list_status_all(self, status):
        return [t for t in self.tasks.values() if t["status"] == status]

    def list_status(self, assignee, status, sort="priority"):
        return [t for t in self.list_status_all(status) if t["assignee"] == assignee]

    def show(self, tid):
        return {"task": self.tasks[tid], "comments": [], "events": [], "runs": []}

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def create(self, title, body, assignee, *, key=None, **kw):
        self.calls.append(("create", title))
        return "t_00000099"

    def archive(self, tid):
        self.calls.append(("archive", tid))
        return True

    def link(self, parent, child):
        self.calls.append(("link", parent, child))
        return True


def montar(tmp_path, tasks):
    cc, desk, bot, _, _ = center(tmp_path, {})
    k = Kanban(tasks)
    cc.hermes_for = desk.hermes_for = lambda b: k
    cc.agrupar_settings = tmp_path / "sin_yaml.yaml"
    desk.fusion = Fusion(LANES, lambda b: k, descartes_path=tmp_path / "descartes.json")
    return cc, desk, bot, k


DUPS = (tj("t_00000001", "Arreglar login con Google"), tj("t_00000002", "Arreglar login con Google"))


def test_comando_envia_un_mensaje_por_grupo_con_botones_y_no_toca_nada(tmp_path):
    cc, desk, bot, k = montar(tmp_path, DUPS + (tj("t_00000009", "Pagar IVA", assignee="oscar"),))
    command(desk, "/agrupar")
    cabecera, grupo, bandeja = bot.sent
    assert "1 grupos propuestos" in cabecera["text"] and "Nada se aplica sin botón" in cabecera["text"]
    assert texts(grupo["markup"]) == ["🔗 Fusionar", "✋ Dejar"]
    assert "t_00000001" in grupo["text"] and bandeja["markup"] is None and "t_00000009" in bandeja["text"]
    assert k.calls == []


def test_fusionar_y_dejar_reutilizan_los_botones_de_fusion(tmp_path):
    cc, desk, bot, k = montar(tmp_path, DUPS)
    command(desk, "/agrupar")
    press(desk, bot.sent[1], 0)
    assert ("create", "Arreglar login con Google (+ fusión con t_00000002)") in k.calls
    cc2, desk2, bot2, k2 = montar(tmp_path, DUPS)
    command(desk2, "/agrupar")
    press(desk2, bot2.sent[1], 1)                                   # ✋ Dejar
    assert k2.calls == [] and solapes.par("t_00000001", "t_00000002") in solapes.load_descartes(tmp_path / "descartes.json")
    n = len(bot2.sent)
    command(desk2, "/agrupar", uid=2)
    assert "no hay nada que agrupar" in bot2.sent[n]["text"]


def test_encadenar_y_mismo_lote(tmp_path):
    ficheros = (tj("t_00000001", "Uno", body="x/commands.py", created=10), tj("t_00000002", "Dos", body="x/commands.py", created=20))
    cc, desk, bot, k = montar(tmp_path, ficheros)
    command(desk, "/agrupar")
    assert texts(bot.sent[1]["markup"]) == ["⛓ Encadenar", "🔗 Fusionar", "✋ Dejar"]
    press(desk, bot.sent[1], 0)
    assert ("link", "t_00000001", "t_00000002") in k.calls
    lote = (tj("t_00000001", "Panel", status="review"), tj("t_00000002", "Informe", status="review"))
    cc, desk, bot, k = montar(tmp_path, lote)
    command(desk, "/agrupar")
    press(desk, bot.sent[1], 0)
    assert [c[1] for c in k.calls if c[0] == "comment"] == ["t_00000001", "t_00000002"]
    assert "mismo lote" in k.calls[0][2]


def test_proyecto_desconocido_y_filtro_por_proyecto(tmp_path):
    cc, desk, bot, k = montar(tmp_path, DUPS)
    command(desk, "/agrupar nadie")
    assert "Proyecto desconocido" in bot.sent[-1]["text"]
    command(desk, "/agrupar oscarhq", uid=2)
    assert "no hay nada que agrupar" in bot.sent[-1]["text"]
    command(desk, "/agrupar migrateam", uid=3)
    assert "1 grupos propuestos" in bot.sent[-2]["text"] or "1 grupos propuestos" in bot.sent[-3]["text"]
