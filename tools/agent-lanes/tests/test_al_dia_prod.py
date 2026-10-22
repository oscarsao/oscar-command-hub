"""/al_dia prod (doble confirmación, solo Oscar), «🛠 Resolver» idempotente y oferta proactiva. Sin red ni gh."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_lanes.al_dia import AlDia
from tests import test_commands as tc
from tests.test_al_dia import FakeIntegrator, pr, tid
from tests.test_commands import OSCAR, command, press, texts

SHA = "a" * 40


class Hermes:
    def __init__(self):
        self.created = []

    def create(self, title, body, assignee, **kw):
        if any(c[3].get("key") == kw.get("key") for c in self.created):  # idempotencia del kanban
            return next(c[4] for c in self.created if c[3].get("key") == kw.get("key"))
        self.created.append((title, body, assignee, kw, f"t_{len(self.created) + 1:08x}"))
        return self.created[-1][4]


class Integ(FakeIntegrator):
    def __init__(self, prs, tmp_path, merged=(), migracion=None, states=None, **kw):
        super().__init__(prs, **kw)
        self.states = states or {}
        self.state_dir = tmp_path / ".state" / "integrator"
        self.merged = {t: {"title": "x", "pr": 5, "merge_sha": SHA, "migration": migracion} for t in merged}
        self.hermes, self.deploys = Hermes(), []
        self.settings.policies["claude-oscarhq"] = SimpleNamespace(deploy="dokploy", batch="daily")

    def _merged_states(self, lane, exclude):
        return list(self.merged.items())

    def _state(self, t):
        return self.merged.get(t, {})

    def _estados_de(self, lane):
        return list(self.states.items())

    def hermes_for(self, board):
        return self.hermes

    def on_button(self, action, rec, where, desk):
        self.deploys.append((action, rec["task_id"]))
        return True


def cc_for(tmp_path, integ):
    cc, desk, bot, h, _ = tc.center(tmp_path, {})
    desk.integrator = integ
    ad = AlDia(integ, ajustes_path="no-existe.yaml")
    cc._al_dia = lambda: ad
    cc._spawn = lambda fn: fn()
    return cc, desk, bot, ad


def boton(bot, texto):
    ficha = next(m for m in bot.sent[::-1] if m.get("markup") and any(texto in t for t in texts(m["markup"])))
    return ficha, next(i for i, t in enumerate(texts(ficha["markup"])) if texto in t)


# --- prod: doble confirmación y permisos ---------------------------------------------------------------------

def test_prod_needs_two_taps_and_deploys_via_integrator(tmp_path):
    integ = Integ([], tmp_path, merged=[tid(1)])
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    command(desk, "/al_dia oscarhq prod")
    ficha, n = boton(bot, "Desplegar producción")
    press(desk, ficha, n)
    assert integ.deploys == []                       # 1.er toque: solo pregunta
    assert "¿Seguro?" in bot.edits[-1]["text"]
    assert texts(bot.edits[-1]["markup"]) == ["✅ Sí, desplegar", "✖ No"]
    press(desk, {**ficha, "markup": bot.edits[-1]["markup"]}, 0, cid="cq2")   # ✅ Sí, desplegar
    assert integ.deploys == [("int_deploy", tid(1))]


def test_dev_never_offers_the_prod_button(tmp_path):
    integ = Integ([pr(1)], tmp_path, merged=[tid(2)], approved={tid(1)})
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    command(desk, "/al_dia oscarhq dev")
    assert not any("Desplegar producción" in t for m in bot.sent if m.get("markup") for t in texts(m["markup"]))


def test_prod_blocked_by_pending_migration_and_lists_it(tmp_path):
    integ = Integ([], tmp_path, merged=[tid(1)], migracion="supabase")
    cc, desk, bot, ad = cc_for(tmp_path, integ)
    plan = ad.prod_plan("oscarhq")
    assert "pedir al integrador" in plan["texto"] and "rec" not in plan


def test_prod_denied_to_restricted_operator_in_command_and_button(tmp_path):
    integ = Integ([], tmp_path, merged=[tid(1)])
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    desk.restringido = lambda uid: str(uid) != str(OSCAR)
    desk.es_operador = lambda uid: True
    desk.puede_con = lambda uid, rec: True
    assert cc._veto_marca(777, "al_dia", "oscarhq prod", None)          # comando
    command(desk, "/al_dia oscarhq prod")
    ficha, n = boton(bot, "Desplegar producción")
    press(desk, ficha, n, user=777)                                     # botón pulsado por otro
    assert integ.deploys == [] and "solo la despliega Oscar" in bot.sent[-1]["text"]


# --- Resolver ------------------------------------------------------------------------------------------------

def test_resolver_creates_one_high_priority_card_with_log_per_failure_and_sha(tmp_path):
    p = pr(3, checks=("FAILURE",))
    p["statusCheckRollup"] = [{"conclusion": "FAILURE", "name": "tests", "detailsUrl": "https://ci/1"}]
    integ = Integ([p], tmp_path, approved={tid(3)})
    cc, desk, bot, ad = cc_for(tmp_path, integ)
    command(desk, "/al_dia oscarhq")
    ficha, n = boton(bot, "Resolver #3")
    press(desk, ficha, n)
    [(titulo, cuerpo, carril, kw, _)] = integ.hermes.created
    assert carril == "claude-oscarhq" and kw["priority"] == 10 and "tests — https://ci/1" in cuerpo
    assert f"Rama-origen: lane/{tid(3)}" in cuerpo and "arreglar" in titulo
    st = ad.diagnose("oscarhq")
    assert "Ya existe la tarjeta" in ad.resolver_ci(st, st.prs[0])       # mismo fallo y SHA: no duplica
    assert len(integ.hermes.created) == 1


# --- oferta proactiva ----------------------------------------------------------------------------------------

def test_offer_once_per_state_and_again_after_state_changes(tmp_path):
    integ = Integ([pr(1)], tmp_path, merged=[tid(9)], approved={tid(1)}, behind={f"lane/{tid(1)}": 12})
    ad = AlDia(integ, ajustes_path="no-existe.yaml")
    assert len(ad.ofertas(ahora=1000.0)) == 1          # atrasada ≥10: oferta (lo fusionado aún es reciente)
    assert ad.ofertas(ahora=1000.0) == []              # mismo estado: no se repite
    integ.behind[f"lane/{tid(1)}"] = 40                # sigue atrasada, más commits: mismo hecho estable
    assert ad.ofertas(ahora=2000.0) == []
    [(clave, texto, rec)] = ad.ofertas(ahora=1000.0 + 25 * 3600)   # ahora lo fusionado lleva >24 h sin desplegar
    assert clave == "oscarhq" and tid(9) in texto and rec["task_id"] == tid(1)
    assert (tmp_path / ".state" / "al_dia" / "ofertas.json").exists()
    integ.behind[f"lane/{tid(1)}"], integ.merged = 0, {}   # al día: se olvida y la deriva futura vuelve a ofrecerse
    assert ad.ofertas(ahora=1000.0 + 26 * 3600) == []
    integ.behind[f"lane/{tid(1)}"] = 15
    assert len(ad.ofertas(ahora=1000.0 + 27 * 3600)) == 1


def test_hoy_appends_the_offer_with_poner_al_dia_button(tmp_path):
    integ = Integ([pr(1)], tmp_path, approved={tid(1)}, behind={f"lane/{tid(1)}": 12})
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    cc._brief = lambda brand: "resumen del día"
    command(desk, "/hoy")
    assert bot.sent[0]["text"] == "resumen del día"
    ficha, n = boton(bot, "Poner al día")
    command(desk, "/hoy")
    assert sum("se ha quedado atrás" in m["text"] for m in bot.sent) == 1   # idempotente
