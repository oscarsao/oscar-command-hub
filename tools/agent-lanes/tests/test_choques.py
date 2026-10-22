"""Choques entre tareas (E1): git y tableros falsos, sin git/gh/hermes reales ni Telegram."""
from types import SimpleNamespace

from agent_lanes import choques
from agent_lanes.choques import Ajustes, ChoquesTicker, Git, aviso, detectar, load_ajustes

HUB = "C:/Users/oscar/oscar-command-hub"
LANES = {"claude-hub": SimpleNamespace(board="default", kind="implement", repo=HUB, base="main", remote="origin")}
NOW = 1_800_000_000
CMD = "tools/agent-lanes/agent_lanes/commands.py"
DEC = "tools/agent-lanes/agent_lanes/decisions.py"


class FakeGit(Git):
    """ramas: {rama: (epoch, autor, ficheros)}; choques: {frozenset({a, b}): True|False|None}."""

    def __init__(self, ramas=None, prs=(), reales=None, mt=True, roto=False):
        super().__init__(runner=None)
        self.ramas, self._prs, self.reales, self.mt, self.roto = ramas or {}, list(prs), reales or {}, mt, roto

    def ramas_remotas(self, repo, remote, base):
        if self.roto:
            return []
        return [(r, ts, autor) for r, (ts, autor, _) in self.ramas.items()]

    def ficheros(self, repo, remote, base, rama):
        return None if self.roto else set(self.ramas[rama][2])

    def prs(self, repo):
        return [] if self.roto else self._prs

    def merge_tree_disponible(self, repo):
        return self.mt

    def choque_real(self, repo, remote, a, b):
        return self.reales.get(frozenset((a, b))) if self.mt else None


class Board:
    def __init__(self, cards):
        self.cards = cards

    def list_status_all(self, status):
        return [c for c in self.cards if c["status"] == status]


def hermes(cards):
    return lambda b: Board(cards)


def card(tid, title, body="", status="ready"):
    return {"id": tid, "assignee": "claude-hub", "title": title, "body": body, "status": status}


def run(git, cards=(), aj=None):
    return detectar(LANES, hermes(list(cards)), git, aj or Ajustes(), ahora=NOW)


def test_aditivo_ficheros_distintos_no_avisa():
    g = FakeGit({"lane/t_1": (NOW, "x", ["a/b/uno.py"]), "lane/t_2": (NOW, "y", ["c/d/dos.py"])})
    assert run(g) == []


def test_aditivo_mismo_fichero_merge_tree_limpio():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
                reales={frozenset(("lane/t_1", "lane/t_2")): False})
    assert run(g) == []


def test_conflicto_real_propone_quien_va_primero():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
                prs=[{"number": 7, "headRefName": "lane/t_1", "title": "x", "author": {"login": "oscarsao"}}],
                reales={frozenset(("lane/t_1", "lane/t_2")): True})
    [c] = run(g)
    assert c.clase == "conflicto" and "lane/t_1 primero (ya tiene PR)" in c.propuesta
    txt = aviso(c)
    assert "PR #7" in txt and "Propuesta:" in txt and "no se frena" in txt.lower()


def test_compatible_tarjeta_sin_rama_contra_rama():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD])})
    [c] = run(g, [card("t_9", "Mejorar", f"Toca {CMD} para pulir el aviso")])
    assert c.clase == "compatible" and "fusionar" in c.propuesta


def test_git_viejo_sin_merge_tree_cae_a_compatible():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])}, mt=False)
    assert [c.clase for c in run(g)] == ["compatible"]


def test_contradiccion_de_intencion_es_revisar_incluso_misma_persona():
    cards = [card("t_1", "Añadir el aviso de solape", f"Crea el aviso en {CMD}", status="done"),
             card("t_2", "Quitar el aviso de solape", f"Elimina el aviso de {CMD}")]
    g = FakeGit()
    assert run(g, cards) == []  # done no se vigila
    cards[0]["status"] = "blocked"
    [c] = run(g, cards)
    assert c.clase == "revisar" and "no se resuelve solo" in c.propuesta


def test_rama_de_persona_no_lane_con_dueno_y_antiguedad():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "angel/fix": (NOW - 3 * 86400, "Angel", [CMD]),
                 "vieja/rama": (NOW - 40 * 86400, "Ana", [CMD])},
                reales={frozenset(("lane/t_1", "angel/fix")): True})
    [c] = run(g)
    assert {c.a.uid, c.b.uid} == {"lane/t_1", "angel/fix"}
    assert "Angel" in aviso(c) and "persona" in aviso(c)


def test_fuentes_se_apagan_por_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("fuentes:\n  ramas_personas: false\n", encoding="utf-8")
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "angel/fix": (NOW, "Angel", [CMD])},
                reales={frozenset(("lane/t_1", "angel/fix")): True})
    assert run(g, aj=load_ajustes(p)) == []


def test_fallo_de_git_es_sin_informacion():
    assert run(FakeGit(roto=True), [card("t_1", "a", CMD), card("t_2", "b", CMD)]) == []


def test_yaml_sobrescribe_y_ausente_son_defaults(tmp_path):
    assert load_ajustes(tmp_path / "no.yaml") == Ajustes()
    p = tmp_path / "c.yaml"
    p.write_text("minutos: 3\nramas_personas_dias: 5\nmax_avisos: 0\ncontradiccion:\n  patrones:\n    - [foo, bar]\n    - [mal(]\n",
                 encoding="utf-8")
    a = load_ajustes(p)
    assert a.minutos == 3 and a.ramas_personas_dias == 5 and a.max_avisos == 6 and a.patrones == (("foo", "bar"),)
    p.write_text("- no es dict", encoding="utf-8")
    assert load_ajustes(p) == Ajustes()


def test_choques_yaml_del_repo_es_valido():
    a = load_ajustes(choques.SETTINGS_PATH)
    assert a.activo and len(a.patrones) >= 2 and set(a.fuentes) == set(choques.FUENTES)


def test_ticker_avisa_una_vez_por_par_y_clase(tmp_path):
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
                reales={frozenset(("lane/t_1", "lane/t_2")): True})
    sent, t = [], [0.0]
    tk = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=tmp_path / ".state" / "ch.json",
                       settings_path=tmp_path / "no.yaml", git=g, clock=lambda: t[0])
    assert len(tk.tick()) == 1 and len(sent) == 1
    assert tk.tick() == [] and len(sent) == 1      # dentro de `minutos`
    t[0] = 1000
    tk.tick()
    assert len(sent) == 1                            # mismo par y clase: idempotente
    tk2 = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=tmp_path / ".state" / "ch.json",
                        settings_path=tmp_path / "no.yaml", git=g, clock=lambda: 0.0)
    tk2.tick()
    assert len(sent) == 1                            # y sobrevive a un reinicio


def test_ticker_no_lanza_si_el_envio_falla(tmp_path):
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
                reales={frozenset(("lane/t_1", "lane/t_2")): True})

    def boom(_):
        raise RuntimeError("telegram caído")
    tk = ChoquesTicker(LANES, hermes([]), send=boom, state_path=tmp_path / "ch.json", settings_path=tmp_path / "no.yaml",
                       git=g)
    assert tk.tick() == []
    assert not (tmp_path / "ch.json").exists()       # sin aviso enviado no se recuerda: se reintenta


def test_git_real_con_runner_falso_parsea_ramas_y_merge_tree():
    def runner(cmd, **kw):
        txt = " ".join(cmd)
        out, rc = "", 0
        if "--no-merged" in txt:
            out = "origin/lane/t_1\norigin/angel/fix\n"
        elif "for-each-ref" in txt:
            out = "origin/HEAD|1|x\norigin/main|1|x\norigin/lane/t_1|1700|Oscar\norigin/angel/fix|1800|Angel\norigin/vieja|5|Z\n"
        elif "--version" in txt:
            out = "git version 2.51.0.windows.1"
        elif "merge-tree" in txt:
            rc = 1
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")
    g = Git(runner)
    assert g.ramas_remotas("r", "origin", "main") == [("lane/t_1", 1700, "Oscar"), ("angel/fix", 1800, "Angel")] \
        or sorted(g.ramas_remotas("r", "origin", "main")) == [("angel/fix", 1800, "Angel"), ("lane/t_1", 1700, "Oscar")]
    assert g.choque_real("r", "origin", "lane/t_1", "angel/fix") is True
    old = Git(lambda c, **k: SimpleNamespace(returncode=0, stdout="git version 2.30.0", stderr=""))
    assert old.choque_real("r", "origin", "a", "b") is None
    assert Git(lambda c, **k: (_ for _ in ()).throw(OSError("sin git"))).prs("r") == []
