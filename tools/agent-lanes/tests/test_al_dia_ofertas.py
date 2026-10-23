"""/al_dia, ronda de revisión: «🛠 Resolver» para gate rojo / deploy error / health KO, develop detrás de master,
oferta desde el monitor (compartida con /hoy) y veto de marca. Sin red ni gh."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_lanes.al_dia import AlDia
from tests.test_al_dia import pr, tid
from tests.test_al_dia_prod import SHA, Integ, boton, cc_for
from tests.test_commands import OSCAR, command, press, texts

# --- «Resolver» de gate rojo, deploy con error y health KO ----------------------------------------------------

FALLOS = {
    "gate_rojo": {"status": "failed", "head_sha": SHA, "pr": 4, "pr_url": "https://gh/4", "title": "PR 4",
                  "fail_reasons": ["tests rojos en la suite"]},
    "deploy_error": {"status": "merged", "merge_sha": SHA, "title": "PR 5",
                     "deploy_fallo": {"sha": SHA, "motivo": "dokploy deployment d1 error"}},
    "health_ko": {"status": "merged", "merge_sha": SHA, "title": "PR 6",
                  "deploy_fallo": {"sha": SHA, "motivo": "dokploy deployment d2 health HTTP 500"}},
}
PISTA = {"gate_rojo": "tests rojos en la suite", "deploy_error": "dokploy deployment d1 error",
         "health_ko": "health HTTP 500"}


@pytest.mark.parametrize("fallo", sorted(FALLOS))
def test_resolver_for_each_failure_kind_creates_one_card_with_the_log(tmp_path, fallo):
    integ = Integ([], tmp_path, states={tid(7): {"lane": "claude-oscarhq", **FALLOS[fallo]}})
    cc, desk, bot, ad = cc_for(tmp_path, integ)
    command(desk, "/al_dia oscarhq")
    ficha, n = boton(bot, "Resolver")
    press(desk, ficha, n)
    [(titulo, cuerpo, carril, kw, _)] = integ.hermes.created
    assert carril == "claude-oscarhq" and kw["priority"] == 10 and PISTA[fallo] in cuerpo
    assert kw["key"] == f"resolver-{fallo}-{SHA[:12]}" and f"Rama-origen: lane/{tid(7)}" in cuerpo
    st = ad.diagnose("oscarhq")
    assert "Ya existe la tarjeta" in ad.resolver_fallo(st, {"task_id": tid(7), "fallo": fallo})   # (fallo, SHA)
    assert len(integ.hermes.created) == 1


def test_resolver_says_so_when_the_failure_is_gone(tmp_path):
    integ = Integ([], tmp_path)
    ad = AlDia(integ, ajustes_path="no-existe.yaml")
    assert "ya no consta" in ad.resolver_fallo(ad.diagnose("oscarhq"), {"task_id": tid(7), "fallo": "gate_rojo"})


# --- develop detrás de master -----------------------------------------------------------------------------------

def with_promoter(integ, behind):
    cfg = SimpleNamespace(repo="oscarsao/mig", base="master", head="develop")
    integ.desk = SimpleNamespace(promoter=SimpleNamespace(configs={"oscarhq": cfg}))
    gh = integ._gh

    def _gh(*args):
        if args[0] == "api" and "oscarsao/mig" in args[1]:
            assert args[1].endswith("compare/master...develop") and args[-1] == ".behind_by"
            return SimpleNamespace(returncode=0, stdout=str(behind[0]), stderr="")
        return gh(*args)
    integ._gh = _gh


def test_develop_behind_master_is_offered_once_with_threshold_from_yaml(tmp_path):
    integ = Integ([], tmp_path)
    behind = [3]
    with_promoter(integ, behind)
    ad = AlDia(integ, ajustes_path="no-existe.yaml")
    [(clave, texto, _)] = ad.ofertas(ahora=1000.0)
    assert clave == "oscarhq" and "develop va 3 commit(s) por detrás de master" in texto and "/promover" in texto
    behind[0] = 9                                     # la cuenta crece sola: mismo hecho, no se repite
    assert ad.ofertas(ahora=2000.0) == []
    behind[0] = 0                                     # al día: se olvida
    assert ad.ofertas(ahora=3000.0) == []
    ajustes = tmp_path / "a.yaml"
    ajustes.write_text("develop_atras_commits: 5\n", encoding="utf-8")
    ad5 = AlDia(integ, ajustes_path=ajustes, estado_dir=tmp_path / "otro")
    behind[0] = 4
    assert ad5.ofertas(ahora=4000.0) == []            # por debajo del umbral configurado
    behind[0] = 5
    assert len(ad5.ofertas(ahora=5000.0)) == 1


# --- monitor del runner + /hoy: misma clave, una sola oferta ---------------------------------------------------

def ofertas_enviadas(bot):
    return [m for m in bot.sent if "se ha quedado atrás" in m["text"]]


def test_monitor_then_hoy_offers_once_and_goes_to_oscar_dm(tmp_path):
    integ = Integ([pr(1)], tmp_path, approved={tid(1)}, behind={f"lane/{tid(1)}": 12})
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    cc._brief = lambda brand: "resumen del día"
    cc._now = lambda: 10_000.0
    cc.al_dia_tick()
    [oferta] = ofertas_enviadas(bot)
    assert str(oferta["chat_id"]) == str(cc.owner_id) and "Poner al día" in texts(oferta["markup"])[0]
    command(desk, "/hoy")
    assert len(ofertas_enviadas(bot)) == 1             # /hoy no repite lo que ya ofreció el monitor


def test_hoy_then_monitor_offers_once(tmp_path):
    integ = Integ([pr(1)], tmp_path, approved={tid(1)}, behind={f"lane/{tid(1)}": 12})
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    cc._brief = lambda brand: "resumen del día"
    cc._now = lambda: 10_000.0
    command(desk, "/hoy")
    cc._now = lambda: 10_000.0 + 3 * 3600
    cc.al_dia_tick()
    assert len(ofertas_enviadas(bot)) == 1


def test_monitor_runs_at_most_once_per_interval_from_yaml(tmp_path):
    integ = Integ([pr(1)], tmp_path, approved={tid(1)}, behind={f"lane/{tid(1)}": 12})
    cc, desk, bot, ad = cc_for(tmp_path, integ)
    calls = []
    real = ad.ofertas
    ad.ofertas = lambda *a, **k: calls.append(1) or real(*a, **k)
    cc._now = lambda: 10_000.0
    cc.al_dia_tick()
    cc._now = lambda: 10_000.0 + 59 * 60
    cc.al_dia_tick()
    assert len(calls) == 1
    cc._now = lambda: 10_000.0 + 61 * 60
    cc.al_dia_tick()
    assert len(calls) == 2


# --- marcas: ofertas y «Poner al día» ----------------------------------------------------------------------------

def restringir(desk, marcas):
    desk.restringido = lambda uid: str(uid) != str(OSCAR)
    desk.es_operador = lambda uid: True
    desk.puede_en = lambda uid, marca: str(uid) == str(OSCAR) or marca in marcas


def test_hoy_offers_only_the_actors_brands_and_keeps_the_rest_for_oscar(tmp_path):
    integ = Integ([pr(1)], tmp_path, approved={tid(1)}, behind={f"lane/{tid(1)}": 12})
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    cc._brief = lambda brand: "resumen del día"
    restringir(desk, {"MigraTeam"})                    # oscarhq es de Píldora
    command(desk, "/hoy", user=777)
    assert ofertas_enviadas(bot) == []                 # ni se ofrece ni se marca como ofrecida
    command(desk, "/hoy")                              # Oscar sí la recibe
    assert len(ofertas_enviadas(bot)) == 1


def test_poner_al_dia_button_is_vetoed_by_brand(tmp_path):
    integ = Integ([pr(1)], tmp_path, approved={tid(1)})
    cc, desk, bot, _ = cc_for(tmp_path, integ)
    restringir(desk, {"MigraTeam"})
    rec = {"kind": "al_dia", "lane": "claude-oscarhq", "board": "default", "key": "oscarhq", "task_id": tid(1)}
    where = {"chat_id": "1", "thread_id": "0", "message_id": 5, "user_id": 777}
    assert cc.al_dia_choice("ad_poner", rec, where, desk) is True
    assert integ.calls == [] and bot.sent and "Píldora" in bot.sent[-1]["text"]


# --- plantilla de la tarjeta «Resolver»: título bien formado y límites que no disparan el clasificador -----------

def test_resolver_title_has_one_prefix_and_card_body_does_not_trip_the_classifier(tmp_path):
    from agent_lanes import clasificador
    from agent_lanes.al_dia_prod import sin_prefijo
    from agent_lanes.titulos import problems

    assert sin_prefijo("[Hub] [Hub] Carril integrador") == "Carril integrador"
    fallo = {"status": "failed", "head_sha": SHA, "pr": 4, "pr_url": "https://gh/4",
             "title": "[Hub] Carril integrador desde Telegram", "fail_reasons": ["posible secreto en el código"]}
    integ = Integ([], tmp_path, states={tid(7): {"lane": "claude-oscarhq", **fallo}})
    cc, desk, bot, ad = cc_for(tmp_path, integ)
    command(desk, "/al_dia oscarhq")
    ficha, n = boton(bot, "Resolver")
    press(desk, ficha, n)
    [(titulo, cuerpo, carril, kw, _)] = integ.hermes.created
    assert titulo.count("[") == 1 and "Gate rojo: Carril integrador" in titulo and titulo.endswith("· implementar")
    assert not problems(titulo)
    veredicto = clasificador.evaluar(SimpleNamespace(kind="implement"), {"title": titulo, "body": cuerpo})
    assert not veredicto.va_al_integrador and not veredicto.dudas
