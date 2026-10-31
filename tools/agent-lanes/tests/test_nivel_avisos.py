"""Nivel de avisos `avisos.nivel` (decisión de Oscar 30-10): esencial / normal / todo. Notificador falso, ajustes en
tmp_path: nada sale a Telegram y no se toca ajustes_hub.json real."""
from __future__ import annotations

import pytest

from agent_lanes import ajustes_hub, avisos_revision, canal_avisos, nivel_avisos
from agent_lanes.ajustes_hub import AjusteInvalido, Ajustes
from agent_lanes.canal_avisos import CLASES, GRACIA, CanalAvisos
from agent_lanes.notices import MessageStore, TaskNotices
from agent_lanes.paradas import ParadasRunner
from agent_lanes.avisos_revision import AvisosRevision
from tests.test_canal_avisos import GESTION, KB, OWNER, TEMA, TEMA_CARRIL, TG, _kb, canal, fijado

APAGADOS = {"empezando", "revision", "choque", "solape", "paradas", "limpieza", "reconcile", "board_guard",
            "pr_conflicto"}
# Lo que el nivel esencial NO puede apagar (decisión de Oscar 30-10): problemáticas, finalizadas, integraciones,
# urgentes, partes fijos y respuestas al equipo.
ENCENDIDOS = {"needs_input", "blocked", "bloqueo_urgente", "progreso", "aprobar", "integrar", "workflow", "hazlo",
              "monitor", "prodwatch", "resumen", "recordatorio", "equipo", "team_notice", "fijado", "al_dia"}


@pytest.fixture
def nivel(tmp_path):
    """Almacén de ajustes en tmp_path; devuelve una función que fija el nivel."""
    store = Ajustes(tmp_path / "aj.json", tmp_path / "aud.jsonl")
    previo = ajustes_hub.usar(store)
    yield lambda n: store.set("avisos.nivel", n, "test")
    ajustes_hub.usar(previo)


# --- ajuste -------------------------------------------------------------------------------------------------------

def test_por_defecto_es_normal_y_se_edita_en_caliente_con_registro(nivel, tmp_path):
    assert nivel_avisos.nivel() == "normal"
    nivel("esencial")
    assert nivel_avisos.nivel() == "esencial"
    nivel("todo")
    assert nivel_avisos.nivel() == "todo"
    assert [h["despues"] for h in ajustes_hub.almacen().historial("avisos.nivel")] == ["esencial", "todo"]
    with pytest.raises(AjusteInvalido):
        nivel("ruidoso")
    assert nivel_avisos.nivel() == "todo"


def test_el_ajuste_es_de_los_avisos_y_sale_como_botones():
    s = ajustes_hub.SETTINGS["avisos.nivel"]
    assert s.grupo == "Avisos" and s.default == "normal" and s.opciones == ("esencial", "normal", "todo")
    assert not s.reinicio  # rige al momento


# --- tabla tipo -> nivel -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tipo", sorted(set(CLASES) | APAGADOS))
@pytest.mark.parametrize("n", ["esencial", "normal", "todo"])
def test_cada_tipo_en_cada_nivel(tipo, n):
    esperado = not (n == "esencial" and tipo in APAGADOS)
    assert nivel_avisos.activo(tipo, n) is esperado


def test_la_tabla_cubre_lo_que_pide_la_tarjeta():
    assert set(nivel_avisos.APAGADOS_ESENCIAL) == APAGADOS
    for tipo in ENCENDIDOS:
        assert nivel_avisos.activo(tipo, "esencial"), tipo
    assert "empezando" in CLASES  # el «▶️ empezando» tiene su propio tipo
    assert all(c.startswith("/") for c in nivel_avisos.COMANDO_DE.values())


def test_tipo_de_estado_separa_el_empezando_de_lo_demas():
    assert canal_avisos.tipo_de_estado("running") == "empezando"
    assert canal_avisos.tipo_de_estado("running", True) == "progreso"  # p. ej. «en pausa» por cuota: alerta
    for st in ("review", "done", "changes", "stuck"):
        assert canal_avisos.tipo_de_estado(st) == "progreso"
    assert canal_avisos.tipo_de_estado("needs_input") == "needs_input"
    assert canal_avisos.tipo_de_estado("blocked") == "blocked"


# --- empezando / terminada ----------------------------------------------------------------------------------------

def _ciclo(tmp_path, tg):
    store = MessageStore()
    c = canal(tmp_path, tg, messages=store)
    n = TaskNotices(tg, store)
    for tipo, texto in (("empezando", "▶️ t_1 · empezando"), ("progreso", "🔍 t_1 · terminada")):
        canal_avisos.publicar(c, n, tipo, "t_1", texto, None, TEMA_CARRIL, alert=False)


def test_esencial_empieza_avanza_y_termina_con_un_solo_mensaje(tmp_path, nivel):
    nivel("esencial")
    tg = TG()
    _ciclo(tmp_path, tg)
    textos = [p["text"] for p in tg.sends(GESTION)]
    assert len(textos) == 1 and "terminada" in textos[0] and not tg.edits()


@pytest.mark.parametrize("n", ["normal", "todo"])
def test_normal_y_todo_siguen_como_antes_un_mensaje_editado(tmp_path, nivel, n):
    nivel(n)
    tg = TG()
    _ciclo(tmp_path, tg)
    assert len(tg.sends(GESTION)) == 1 and "empezando" in tg.sends(GESTION)[0]["text"]
    assert len(tg.edits()) == 1 and "terminada" in tg.edits()[0]["text"]


def test_sin_canal_esencial_tambien_calla_el_empezando(tmp_path, nivel):
    nivel("esencial")
    tg, store = TG(), MessageStore()
    canal_avisos.publicar(None, TaskNotices(tg, store), "empezando", "t_1", "▶️ t_1", None, TEMA_CARRIL, alert=False)
    assert tg.sends(GESTION) == []


# --- avisos sueltos y contador ------------------------------------------------------------------------------------

def test_esencial_un_choque_no_manda_mensaje_pero_sigue_contando(tmp_path, nivel):
    nivel("esencial")
    tg, vivos = TG(), {"tokA00001", "tokB00001"}
    c = canal(tmp_path, tg, vivos=vivos)
    assert c.enviar("choque", "⛔ Choque t_a / t_b", _kb("tokA00001")) is None
    assert c.enviar("solape", "🔀 Solape t_c / t_d", _kb("tokB00001")) is None
    assert tg.sends(GESTION) == [] and tg.sends(OWNER) == []  # ni al tema ni al DM
    texto = fijado(tg)
    assert texto.startswith("🎯 <b>2 decisiones pendientes</b>")
    assert "• 1 choque entre tarjetas" in texto and "• 1 solape de tarjetas" in texto
    c.enviar("choque", "⛔ Choque t_a / t_b", _kb("tokA00001"))  # el mismo par otra vez: no cuenta doble
    assert len(c.pendientes()) == 2


def test_lo_silenciado_se_suelta_al_gastarse_el_teclado_tras_la_gracia(tmp_path, nivel):
    nivel("esencial")
    tg, vivos, now = TG(), {"tokA00001"}, [1_000_000.0]
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: t in vivos, clock=lambda: now[0])
    c.enviar("choque", "⛔ Choque t_a / t_b", _kb("tokA00001"))
    vivos.discard("tokA00001")
    c.refrescar()
    assert len(c.pendientes()) == 1  # dentro de la gracia
    now[0] += GRACIA + 1
    c.refrescar()
    assert c.pendientes() == [] and fijado(tg).startswith("🎯 <b>Sin decisiones pendientes")
    assert tg.edits() == [] or all("silenciada" not in p["text"] for p in tg.edits())  # no hay mensaje que editar


@pytest.mark.parametrize("n", ["normal", "todo"])
def test_normal_el_choque_sale_como_siempre(tmp_path, nivel, n):
    nivel(n)
    tg = TG()
    canal(tmp_path, tg, vivos={"tokA00001"}).enviar("choque", "⛔ Choque t_a / t_b", _kb("tokA00001"))
    assert len(tg.sends(GESTION, "777")) == 1


def test_esencial_limpieza_reconcile_y_guarda_no_salen_ni_al_dm(tmp_path, nivel):
    nivel("esencial")
    tg = TG()
    c = canal(tmp_path, tg)
    for tipo in ("limpieza", "reconcile", "board_guard"):
        canal_avisos.emisor(c, tg, tipo, OWNER)("texto", None)
    assert tg.sends() == []


def test_esencial_conserva_decisiones_urgentes_y_partes(tmp_path, nivel):
    nivel("esencial")
    tg, store = TG(), MessageStore()
    c = canal(tmp_path, tg, messages=store, vivos={"tok123456"})
    n = TaskNotices(tg, store)
    canal_avisos.publicar(c, n, "needs_input", "t_1", "❓ t_1 · ¿Cuál?", None, TEMA_CARRIL, alert=True, reply_markup=KB)
    canal_avisos.publicar(c, n, "aprobar", "t_2", "✅ t_2 · lista para aprobar", None, TEMA_CARRIL, alert=True,
                          reply_markup=KB)
    c.enviar("monitor", "🔴 runner parado")
    c.enviar("resumen", "☀️ parte de las 9:00")
    textos = " | ".join(p["text"] for p in tg.sends())
    assert "¿Cuál?" in textos and "lista para aprobar" in textos and "runner parado" in textos and "parte de las 9" in textos
    assert [p for p in tg.sends(GESTION, "777") if p.get("reply_markup")]  # las decisiones conservan sus botones


# --- resumen de revisión y paradas --------------------------------------------------------------------------------

class Reloj:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def test_esencial_no_manda_el_resumen_de_revision_pero_queda_en_revision(tmp_path, nivel):
    enviados, reloj = [], Reloj()
    av = AvisosRevision(lambda text, markup=None: enviados.append(text) or {"message_id": 1}, "-100",
                        state_path=tmp_path / "a.json", clock=reloj)
    for n in (1, 2, 3):
        av.registrar({"id": f"t_{n}", "title": f"Tarea {n}"}, "hecho")
    nivel("esencial")
    assert av.tick() == [] and enviados == []
    assert "t_1" in av.pendientes_texto() and "3 tarjetas" in av.pendientes_texto()  # a demanda
    nivel("normal")
    assert av.tick() == ["t_1", "t_2", "t_3"] and len(enviados) == 1  # nada se pierde al volver


def test_esencial_no_publica_las_paradas(tmp_path, nivel):
    enviados = []
    pr = ParadasRunner({}, lambda b: None, lambda text, markup=None: enviados.append(text),
                       state_path=tmp_path / "p.json")
    pr.paradas.encontrar = lambda horas=None: pytest.fail("no debe ni calcular")
    nivel("esencial")
    assert pr.publicar() == [] and enviados == []
