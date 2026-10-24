"""Botones de TODO aviso de choque (23-10): 🙈 Ignorar par, 🔇 Silenciar rama, 🔎 Ver PR; sin git/gh/Telegram reales."""
import json
import re
from types import SimpleNamespace

from agent_lanes import choques, fusion
from agent_lanes.choques import CHO_IGNORAR, CHO_SILENCIAR, ChoquesTicker
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.notices import MessageStore

from .test_choques import CMD, LANES, NOW, FakeGit, card, hermes

HUB_GH = {"claude-hub": SimpleNamespace(**{**vars(LANES["claude-hub"]), "github": "oscarsao/oscar-command-hub"})}


def _ticker(tmp_path, g, cards=(), lanes=LANES, w=None):
    sent, store = [], CallbackStore(tmp_path / "cb")
    tk = ChoquesTicker(lanes, hermes(list(cards)), send=lambda text, markup=None: sent.append((text, markup)),
                       state_path=tmp_path / "ch.json", settings_path=tmp_path / "no.yaml", git=g,
                       clock=lambda: 0.0, wall=w or (lambda: NOW), issue=store.issue,
                       silenciadas_path=tmp_path / "sil.json", ignorados_path=tmp_path / "ign.json")
    return tk, sent, store


def _ramas_en_conflicto(prs=()):
    return FakeGit({"angel/fix": (NOW, "Angel", [CMD]), "lane/t_00000002": (NOW, "y", [CMD])}, prs=prs,
                   reales={frozenset(("angel/fix", "lane/t_00000002")): True})


def _botones(markup):
    return [b for fila in markup["inline_keyboard"] for b in fila]


def test_choque_entre_ramas_sin_tarjeta_lleva_botones(tmp_path):
    pr = {"number": 12, "headRefName": "angel/fix", "title": "fix", "author": {"login": "angel"}}
    tk, sent, store = _ticker(tmp_path, _ramas_en_conflicto([pr]), lanes=HUB_GH)
    tk.tick()
    [(_texto, markup)] = sent
    assert markup is not None, "antes salía sin botones"
    textos = [b["text"] for b in _botones(markup)]
    assert "🙈 Ignorar este par" in textos
    assert any(t.startswith("🔇 Silenciar angel/fix") for t in textos)
    assert any(t.startswith("🔇 Silenciar lane/t_00000002") for t in textos)
    url = [b for b in _botones(markup) if "url" in b]
    assert url == [{"text": "🔎 Ver PR #12", "url": "https://github.com/oscarsao/oscar-command-hub/pull/12"}]
    rec = store.get(markup["inline_keyboard"][0][0]["callback_data"].split(":")[0])
    assert re.fullmatch(r"choque-[0-9a-f]{10}", rec["task_id"]) and rec["par"] == "angel/fix|lane/t_00000002"
    assert "board" not in rec and "lane" not in rec  # par solo de ramas: lo decide Oscar (sin marca)


def test_url_del_pr_viene_de_gh_si_la_da():
    pr = {"number": 3, "headRefName": "angel/fix", "url": "https://github.com/x/y/pull/3"}
    [c] = choques.detectar(LANES, hermes([]), _ramas_en_conflicto([pr]), choques.Ajustes(), ahora=NOW)
    assert c.a.url == "https://github.com/x/y/pull/3" or c.b.url == "https://github.com/x/y/pull/3"


def test_par_de_tarjetas_conserva_botones_de_fusion_y_suma_ignorar(tmp_path):
    g = FakeGit({"lane/t_00000001": (NOW, "x", [CMD]), "lane/t_00000002": (NOW, "y", [CMD])},
                reales={frozenset(("lane/t_00000001", "lane/t_00000002")): True})
    cards = [card("t_00000001", "uno", status="running"), card("t_00000002", "dos", status="running")]
    tk, sent, store = _ticker(tmp_path, g, cards)
    tk.tick()
    [(_, markup)] = sent
    token = markup["inline_keyboard"][0][0]["callback_data"].split(":")[0]
    rec = store.get(token)
    acciones = [b["action"] for b in rec["buttons"]]
    assert {fusion.FUS_MERGE, fusion.FUS_CHAIN, fusion.FUS_OK, fusion.FUS_DUP, fusion.FUS_PAUSE} <= set(acciones)
    assert CHO_IGNORAR in acciones and CHO_SILENCIAR not in acciones  # las ramas tienen tarjeta: no se silencian
    assert rec["solape"] and len(rec["cards"]) == 2 and rec["task_id"] == "t_00000001"


class _Notifier:
    def __init__(self):
        self.edits = []

    def edit(self, chat, msg, text, **kw):
        self.edits.append(text)


def _desk(tmp_path, tk):
    notifier = _Notifier()
    desk = DecisionDesk(notifier, CallbackStore(tmp_path / "cb2"), lanes={}, hermes_for=lambda b: None)
    desk.choques = tk
    return desk, notifier


def _rec_y_boton(store, markup, accion, index=None):
    token = markup["inline_keyboard"][0][0]["callback_data"].split(":")[0]
    rec = store.get(token)
    boton = next(b for b in rec["buttons"] if b["action"] == accion and (index is None or b["index"] == index))
    return rec, boton


def test_ignorar_par_por_el_despachador_y_no_vuelve_aunque_cambie_la_clase(tmp_path):
    g = _ramas_en_conflicto()
    w = [NOW]
    tk, sent, store = _ticker(tmp_path, g, w=lambda: w[0])
    tk.tick()
    rec, boton = _rec_y_boton(store, sent[0][1], CHO_IGNORAR)
    desk, notifier = _desk(tmp_path, tk)
    assert desk._act(rec, boton, {"chat_id": "1", "message_id": 9}) is True
    assert "ignorado" in notifier.edits[-1]
    assert "angel/fix|lane/t_00000002" in json.loads((tmp_path / "ign.json").read_text(encoding="utf-8"))
    # El mismo par pasa a «revisar»/otra clase (clave distinta en choques.json): sigue sin avisar.
    (tmp_path / "ch.json").write_text("{}", encoding="utf-8")
    tk._last = None
    assert tk.tick() == [] and len(sent) == 1
    # Caduca con recordar_dias (30 por defecto).
    w[0] = NOW + 31 * 86400
    tk._last = None
    tk.tick()
    assert len(sent) == 2


def test_silenciar_rama_la_saca_de_los_choques(tmp_path):
    g = FakeGit({"angel/fix": (NOW, "Angel", [CMD]), "lane/t_00000002": (NOW, "y", [CMD]),
                 "lane/t_00000003": (NOW, "z", [CMD])},
                reales={frozenset(("angel/fix", "lane/t_00000002")): True, frozenset(("angel/fix", "lane/t_00000003")): True,
                        frozenset(("lane/t_00000002", "lane/t_00000003")): False})
    tk, sent, store = _ticker(tmp_path, g)
    tk.tick()
    assert len(sent) == 2
    rec, boton = _rec_y_boton(store, sent[0][1], CHO_SILENCIAR, index=0)
    assert rec["silenciables"][0] == "angel/fix"
    desk, notifier = _desk(tmp_path, tk)
    assert desk._act(rec, boton, {"chat_id": "1", "message_id": 9}) is True
    assert json.loads((tmp_path / "sil.json").read_text(encoding="utf-8")) == ["angel/fix"]
    assert "silenciada" in notifier.edits[-1]
    (tmp_path / "ch.json").write_text("{}", encoding="utf-8")
    tk._last = None
    assert tk.tick() == []  # angel/fix ya no cuenta; las dos lane/* se fusionan limpias


def test_silenciar_con_indice_raro_no_rompe(tmp_path):
    tk, _sent, _store = _ticker(tmp_path, _ramas_en_conflicto())
    desk, notifier = _desk(tmp_path, tk)
    assert tk.on_button(CHO_SILENCIAR, {"silenciables": []}, {"chat_id": "1", "message_id": 1}, desk, 5) is True
    assert "No reconozco" in notifier.edits[-1]
    assert not (tmp_path / "sil.json").exists()


def test_sin_issue_sigue_mandando_texto(tmp_path):
    sent = []
    tk = ChoquesTicker(LANES, hermes([]), send=sent.append, state_path=tmp_path / "ch.json",
                       settings_path=tmp_path / "no.yaml", git=_ramas_en_conflicto(), clock=lambda: 0.0,
                       wall=lambda: NOW, silenciadas_path=tmp_path / "s.json", ignorados_path=tmp_path / "i.json")
    tk.tick()
    assert len(sent) == 1 and isinstance(sent[0], str)


def test_ajustes_de_prevencion_desde_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("prevenir_al_lanzar: true\nprevenir_nivel: modulo\nprevenir_estados: [running]\n"
                 "prevenir_max_horas: 3\nprevenir_cache_minutos: 0\nprevenir_ramas_lane: false\n", encoding="utf-8")
    a = choques.load_ajustes(p)
    assert (a.prevenir_al_lanzar, a.prevenir_nivel, a.prevenir_estados, a.prevenir_max_horas,
            a.prevenir_cache_minutos, a.prevenir_ramas_lane) == (True, "modulo", ("running",), 3, 0.0, False)
    assert choques.Ajustes().prevenir_al_lanzar is False  # sin YAML: apagada (ante la duda, se lanza)
    assert choques.load_ajustes(choques.SETTINGS_PATH).prevenir_al_lanzar is True


class _TgNotifier(_Notifier):
    def __init__(self):
        super().__init__()
        self.answers = []

    def answer_callback(self, cid, text, alert=False):
        self.answers.append(text)

    def edit_markup(self, *a, **k):
        pass


def test_pulsar_ignorar_por_telegram_de_verdad_con_message_store(tmp_path):
    """Camino completo de producción: _callback -> consume -> _safe (MessageStore en disco) -> _act -> on_button."""
    store = CallbackStore(tmp_path / "cb")
    sent = []
    tk = ChoquesTicker(LANES, hermes([]), send=lambda text, markup=None: sent.append(markup),
                       state_path=tmp_path / "ch.json", settings_path=tmp_path / "no.yaml", git=_ramas_en_conflicto(),
                       clock=lambda: 0.0, wall=lambda: NOW, issue=store.issue,
                       silenciadas_path=tmp_path / "sil.json", ignorados_path=tmp_path / "ign.json")
    tk.tick()
    notifier = _TgNotifier()
    desk = DecisionDesk(notifier, store, lanes={}, hermes_for=lambda b: None, owner_id="1",
                        spawn=lambda fn: fn(), messages=MessageStore(tmp_path / "msgs"))
    desk.choques = tk
    data = sent[0]["inline_keyboard"][0][0]["callback_data"]
    desk._callback({"id": "q", "from": {"id": 1}, "data": data,
                    "message": {"chat": {"id": 1}, "message_id": 9}})
    assert "angel/fix|lane/t_00000002" in json.loads((tmp_path / "ign.json").read_text(encoding="utf-8"))
    assert "ignorado" in notifier.edits[-1]


def test_aviso_dice_que_se_frena_con_prevencion():
    [c] = choques.detectar(LANES, hermes([]), _ramas_en_conflicto(), choques.Ajustes(), ahora=NOW)
    assert "no se lanza en paralelo" in choques.aviso(c, choques.Ajustes(prevenir_al_lanzar=True))


def test_temprano_lleva_boton_confirmar_orden_y_lo_anota(tmp_path):
    from agent_lanes.choques import CHO_ORDEN
    pr = {"number": 280, "headRefName": "feat/uno", "title": "uno", "author": {"login": "oscarsao"}}
    g = FakeGit({"feat/uno": (NOW, "Oscar", [CMD]), "feat/dos": (NOW, "Oscar", [CMD])}, prs=[pr],
                reales={frozenset(("feat/uno", "feat/dos")): False})
    tk, sent, store = _ticker(tmp_path, g, lanes=HUB_GH)
    tk.orden_path = tmp_path / "ord.json"
    tk.tick()
    [(texto, markup)] = sent
    assert "aún sin conflicto" in texto
    assert _botones(markup)[0]["text"] == "✅ Confirmar: PR #280 primero, la otra espera"
    rec, boton = _rec_y_boton(store, markup, CHO_ORDEN)
    assert rec["primero"] == "feat/uno" and rec["despues"] == "feat/dos"
    desk, notifier = _desk(tmp_path, tk)
    assert desk._act(rec, boton, {"chat_id": "1", "message_id": 9}) is True
    assert "feat/uno va primero" in notifier.edits[-1]
    guardado = json.loads((tmp_path / "ord.json").read_text(encoding="utf-8"))
    assert guardado["feat/dos|feat/uno"]["primero"] == "feat/uno"
    assert tk.on_button(CHO_ORDEN, {}, {"chat_id": "1", "message_id": 1}, desk) is False
