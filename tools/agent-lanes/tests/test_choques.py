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
    # 25-10 (Oscar): el repo compara solo tarjetas activas; basta con que las fuentes sean válidas y haya alguna.
    assert a.activo and len(a.patrones) >= 2 and a.fuentes and set(a.fuentes) <= set(choques.FUENTES)


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


# --- 23-10: avisos repetidos en bucle (memoria de 500 pares) y choques falsos con ramas ya fusionadas ---

def _par_real():
    return FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
                   reales={frozenset(("lane/t_1", "lane/t_2")): True})


def test_ticker_no_repite_aunque_haya_mas_de_500_pares_recordados(tmp_path):
    estado = tmp_path / "ch.json"
    import json as _json
    viejos = {f"conflicto:x{i}|y{i}": NOW - 60 for i in range(600)}  # más que el tope antiguo
    estado.write_text(_json.dumps(viejos), encoding="utf-8")
    sent, t = [], [0.0]
    tk = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=estado, settings_path=tmp_path / "no.yaml",
                       git=_par_real(), clock=lambda: t[0], wall=lambda: NOW)
    tk.tick()
    assert len(sent) == 1
    for k in range(1, 6):  # varias pasadas: el par ya avisado no vuelve aunque el estado tenga >500 claves
        t[0] = k * 1000
        tk.tick()
    assert len(sent) == 1
    assert len(_json.loads(estado.read_text(encoding="utf-8"))) == 601


def test_ticker_migra_estado_antiguo_en_lista(tmp_path):
    estado = tmp_path / "ch.json"
    import json as _json
    clave_par = "conflicto:lane/t_1|lane/t_2"
    estado.write_text(_json.dumps([clave_par]), encoding="utf-8")
    sent = []
    tk = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=estado, settings_path=tmp_path / "no.yaml",
                       git=_par_real(), clock=lambda: 0.0, wall=lambda: NOW)
    tk.tick()
    assert sent == []  # el par de la lista vieja cuenta como ya avisado


def test_ticker_vuelve_a_avisar_pasado_recordar_dias(tmp_path):
    sent, t, w = [], [0.0], [NOW]
    tk = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=tmp_path / "ch.json",
                       settings_path=tmp_path / "no.yaml", git=_par_real(), clock=lambda: t[0], wall=lambda: w[0])
    tk.tick()
    t[0], w[0] = 1000, NOW + 29 * 86400
    tk.tick()
    assert len(sent) == 1
    t[0], w[0] = 2000, NOW + 31 * 86400
    tk.tick()
    assert len(sent) == 2


def test_ticker_compatibles_no_avisan_por_defecto(tmp_path):
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])}, mt=False)  # sin merge-tree: compatible
    sent = []
    tk = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=tmp_path / "ch.json",
                       settings_path=tmp_path / "no.yaml", git=g, clock=lambda: 0.0, wall=lambda: NOW)
    choques_ = tk.tick()
    assert choques_ and all(c.clase == "compatible" for c in choques_)
    assert sent == []
    assert (tmp_path / "ch.json").exists()  # queda registrado: no se reevalúa como nuevo


def test_rama_con_pr_ya_fusionado_no_cuenta(tmp_path):
    class G(FakeGit):
        def ramas_cerradas(self, repo):
            return {"feat/vieja"}
    g = G({"feat/vieja": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
          reales={frozenset(("feat/vieja", "lane/t_2")): True})
    assert run(g) == []
    g2 = G({"feat/vieja": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
           prs=[{"number": 9, "headRefName": "feat/vieja", "title": "reabierta"}],
           reales={frozenset(("feat/vieja", "lane/t_2")): True})
    assert len(run(g2)) == 1  # con un PR abierto de nuevo, vuelve a contar


def test_load_ajustes_recordar_y_compatibles(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("recordar_dias: 7\navisar_compatibles: true\n", encoding="utf-8")
    a = load_ajustes(p)
    assert a.recordar_dias == 7 and a.avisar_compatibles is True


# --- aviso temprano (24-10): mismos ficheros, merge limpio, alguna rama con PR -----------------------------------------

def _pr(n, rama):
    return {"number": n, "headRefName": rama, "title": "t", "author": {"login": "oscarsao"}}


def _temprano_git(prs, ficheros_b=(CMD,)):
    return FakeGit({"feat/uno": (NOW, "Oscar", [CMD, "a/otro.py"]), "feat/dos": (NOW, "Oscar", list(ficheros_b))},
                   prs=prs, reales={frozenset(("feat/uno", "feat/dos")): False})


def test_temprano_avisa_antes_del_conflicto_y_va_primero_la_del_pr():
    [c] = run(_temprano_git([_pr(280, "feat/uno")]))
    assert c.clase == "temprano" and c.comparten == [CMD]
    assert "feat/uno va primero (ya tiene PR)" in c.propuesta
    assert "aún sin conflicto" in aviso(c)


def test_temprano_sin_pr_no_avisa_y_ficheros_distintos_tampoco():
    assert run(_temprano_git([])) == []
    assert run(_temprano_git([_pr(1, "feat/uno")], ficheros_b=("z/otro_modulo.py",))) == []


def test_temprano_con_dos_pr_va_primero_el_mas_antiguo():
    [c] = run(_temprano_git([_pr(281, "feat/uno"), _pr(280, "feat/dos")]))
    assert "feat/dos va primero" in c.propuesta


def test_temprano_se_apaga_e_ignora_ficheros_por_yaml(tmp_path):
    g = _temprano_git([_pr(1, "feat/uno")])
    assert run(g, aj=Ajustes(temprano=False)) == []
    assert run(g, aj=Ajustes(temprano_ignorar=("*/commands.py",))) == []
    y = tmp_path / "c.yaml"
    y.write_text("temprano: false\nfetch: false\ntemprano_ignorar: ['A/B.md']\n", encoding="utf-8")
    aj = load_ajustes(y)
    assert aj.temprano is False and aj.fetch is False and aj.temprano_ignorar == ("a/b.md",)


def test_detectar_hace_fetch_por_repo_y_se_puede_apagar():
    g = _temprano_git([])
    llamadas = []
    g.fetch = lambda repo, remote: llamadas.append((repo, remote)) or True
    run(g)
    assert llamadas == [(HUB, "origin")]
    run(g, aj=Ajustes(fetch=False))
    assert len(llamadas) == 1


def test_git_fetch_real_con_runner_falso():
    cmds = []

    def runner(cmd, **kw):
        cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="")

    assert Git(runner=runner).fetch("/r", "origin") is True
    assert cmds == [["git", "-C", "/r", "fetch", "--prune", "--quiet", "origin"]]
    assert Git(runner=lambda *a, **k: 1 / 0).fetch("/r", "origin") is False


def test_choques_yaml_trae_los_ajustes_tempranos():
    aj = load_ajustes()
    assert aj.temprano and aj.fetch


# --- aviso temprano con las fuentes del 25-10 (solo tarjetas): el PR se lee aunque `pr` no sea fuente -------------------

def _solo_tarjetas(**kw):
    return Ajustes(fuentes=("tarjetas",), **kw)


def test_temprano_con_solo_tarjetas_lee_el_pr_de_la_rama_lane():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "lane/t_2": (NOW, "y", [CMD])},
                prs=[_pr(280, "lane/t_1")], reales={frozenset(("lane/t_1", "lane/t_2")): False})
    cards = [card("t_1", "uno"), card("t_2", "dos")]
    [c] = run(g, cards, _solo_tarjetas())
    assert c.clase == "temprano" and "va primero (ya tiene PR)" in c.propuesta
    assert (c.a.pr, c.b.pr) in ((280, 0), (0, 280))
    assert run(g, cards, _solo_tarjetas(temprano=False)) == []


def test_solo_tarjetas_no_crea_unidades_de_ramas_ni_pr_sueltos():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD]), "feat/otra": (NOW, "y", [CMD])},
                prs=[_pr(9, "feat/otra")], reales={frozenset(("lane/t_1", "feat/otra")): False})
    assert run(g, [card("t_1", "uno")], _solo_tarjetas()) == []


def test_temprano_con_solo_tarjetas_no_llama_a_gh_si_esta_apagado():
    g = FakeGit({"lane/t_1": (NOW, "x", [CMD])})
    g.prs = lambda repo: (_ for _ in ()).throw(AssertionError("gh pr list no debería llamarse"))
    run(g, [card("t_1", "uno")], _solo_tarjetas(temprano=False))


# --- F1-05 (D30-A ampliado): PR de tarjetas vivas, también fix/* y feat/* -------------------------------------

class BoardShow(Board):
    def show(self, tid):
        c = next((c for c in self.cards if c["id"] == tid), None)
        return {"task": c} if c else {}


def test_pr_de_consola_ligado_a_tarjeta_done_cuenta_frente_a_tarjeta_activa():
    cards = [card("t_aaaaaaaa", "Ola 1 consola", status="done"), card("t_bbbbbbbb", "Carril", status="running")]
    g = FakeGit({"feat/ola-1": (NOW, "oscar", [CMD]), "lane/t_bbbbbbbb": (NOW, "w", [CMD]),
                 "feat/suelta": (NOW, "x", [CMD])},
                prs=[{"number": 21, "headRefName": "feat/ola-1", "title": "Ola 1", "body": "Cierra: t_aaaaaaaa",
                      "url": "u21"},
                     {"number": 22, "headRefName": "feat/suelta", "title": "sin tarjeta", "body": ""}],
                reales={frozenset(("feat/ola-1", "lane/t_bbbbbbbb")): True})
    aj = Ajustes(fuentes=("tarjetas", "pr_tarjetas"), estados=("running",))
    [c] = detectar(LANES, lambda b: BoardShow(cards), g, aj, ahora=NOW)
    assert c.clase == "conflicto" and {c.a.uid, c.b.uid} == {"t_aaaaaaaa", "t_bbbbbbbb"}
    ola = c.a if c.a.uid == "t_aaaaaaaa" else c.b
    assert ola.rama == "feat/ola-1" and ola.pr == 21


def test_pr_ligado_a_tarjeta_archivada_no_cuenta():
    cards = [card("t_aaaaaaaa", "vieja", status="archived"), card("t_bbbbbbbb", "Carril", status="running")]
    g = FakeGit({"fix/x": (NOW, "o", [CMD]), "lane/t_bbbbbbbb": (NOW, "w", [CMD])},
                prs=[{"number": 5, "headRefName": "fix/x", "title": "x", "body": "Cierra: t_aaaaaaaa"}],
                reales={frozenset(("fix/x", "lane/t_bbbbbbbb")): True})
    aj = Ajustes(fuentes=("tarjetas", "pr_tarjetas"), estados=("running",))
    assert detectar(LANES, lambda b: BoardShow(cards), g, aj, ahora=NOW) == []
