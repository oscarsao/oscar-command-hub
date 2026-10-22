"""/al_dia (dev): diagnóstico, poner al día, integrar todo lo listo, destino prod rechazado y permisos. Sin red ni gh."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent_lanes.al_dia import AlDia, load_ajustes, parse_args
from tests import test_commands as tc
from tests.test_commands import command, press, texts

LANE = SimpleNamespace(name="claude-oscarhq", base="main", board="default")
POL = SimpleNamespace(deploy="dokploy", batch="daily")


def pr(n, *, checks=("SUCCESS",), mergeable="MERGEABLE", draft=False, head=None):
    return {"number": n, "title": f"PR {n}", "url": f"https://gh/{n}", "mergeable": mergeable, "isDraft": draft,
            "headRefName": f"lane/t_{n:08x}", "headRefOid": head or f"{n:040x}", "isCrossRepository": False,
            "statusCheckRollup": [{"conclusion": c} for c in checks]}


class FakeIntegrator:
    def __init__(self, prs, behind=None, approved=(), update_ok=True, conflict="sent_back"):
        self.settings = SimpleNamespace(enabled=True, policies={"claude-oscarhq": POL})
        self.lanes = {"claude-oscarhq": LANE}
        self.links = SimpleNamespace(repo_slug=lambda lane: "oscarsao/oscar-hq")
        self.prs, self.behind, self.approved = prs, behind or {}, set(approved)
        self.update_ok, self.conflict = update_ok, conflict
        self.calls, self.rebound, self.conflicts = [], [], []
        self.batches = SimpleNamespace(assemble=self._assemble)
        self.assembled = []

    def _gh(self, *args):
        self.calls.append(args)
        out, rc = "", 0
        if args[:2] == ("pr", "list"):
            out = json.dumps(self.prs)
        elif args[0] == "api" and "compare" in args[1]:
            out = str(self.behind.get(args[1].split("...")[1], 0))
        elif args[0] == "api" and "update-branch" in " ".join(args):
            rc = 0 if self.update_ok else 1
        return SimpleNamespace(returncode=rc, stdout=out, stderr="boom")

    def _approved_record(self, tid):
        return {"head_sha": "x"} if tid in self.approved else {}

    def _merged_states(self, lane, exclude):
        return []

    def _pr_view(self, slug, number):
        return {"headRefOid": f"{number + 1000:040x}", "number": number}

    def rebind_approval(self, tid, sha):
        self.rebound.append((tid, sha))

    def _conflict_pass(self, lane, policy, tid, number, url, pr_, task, about):
        self.conflicts.append((tid, number))
        return self.conflict

    def _assemble(self, lane, policy, manual=False):
        self.assembled.append((lane.name, manual))
        return "lote montado"


def tid(n):
    return f"t_{n:08x}"


def make(prs, **kw):
    integ = FakeIntegrator(prs, **kw)
    return integ, AlDia(integ, ajustes_path="no-existe.yaml")


# --- parseo y ajustes --------------------------------------------------------------------------------

def test_parse_args_defaults_and_order():
    proj = {"oscarhq": 1, "hub": 2}
    assert parse_args("", proj) == ("todos", "dev", None)
    assert parse_args("prod oscarhq", proj) == ("oscarhq", "prod", None)
    assert parse_args("hub dev", proj) == ("hub", "dev", None)
    assert parse_args("nada", proj)[2].startswith("No conozco «nada»")


def test_threshold_is_10_by_default_and_comes_from_yaml(tmp_path):
    assert load_ajustes("no-existe.yaml")["rama_atras_commits"] == 10
    f = tmp_path / "a.yaml"
    f.write_text("rama_atras_commits: 4\nmax_prs: basura\n", encoding="utf-8")
    assert load_ajustes(f) == {"rama_atras_commits": 4, "max_prs": 20, "pr_listo_horas": 24, "sin_desplegar_horas": 24}


# --- diagnóstico ---------------------------------------------------------------------------------------

def test_diagnose_classifies_each_pr():
    prs = [pr(1), pr(2, checks=("SUCCESS", "FAILURE")), pr(3, mergeable="CONFLICTING"), pr(4, checks=("PENDING",)),
           pr(5), pr(6, draft=True)]
    integ, ad = make(prs, approved={tid(1), tid(2), tid(3), tid(4)}, behind={f"lane/{tid(1)}": 12})
    st = ad.diagnose("oscarhq")
    assert {p.number: p.estado for p in st.prs} == {1: "listo", 2: "ci_rojo", 3: "conflicto", 4: "ci_en_curso",
                                                    5: "sin_aprobar", 6: "borrador"}
    assert st.prs[0].atras == 12
    resumen = ad.resumen(st, "dev")
    assert "PR de carril: 6" in resumen and "1 listos" in resumen and "1 CI rojo" in resumen
    assert "Atrasadas (≥10 commits): #1 (−12)" in resumen


def test_diagnose_is_read_only_and_ignores_foreign_branches():
    other = pr(7)
    other["headRefName"] = "feature/x"
    integ, ad = make([pr(1), other], approved={tid(1)})
    st = ad.diagnose("oscarhq")
    assert [p.number for p in st.prs] == [1]
    assert not any("update-branch" in " ".join(c) or "PUT" in c for c in integ.calls)


# --- poner al día ---------------------------------------------------------------------------------------

def test_clean_behind_branch_is_updated_and_approval_rebound():
    integ, ad = make([pr(1), pr(2)], approved={tid(1), tid(2)},
                      behind={f"lane/{tid(1)}": 11, f"lane/{tid(2)}": 3})
    out = ad.poner_al_dia(ad.diagnose("oscarhq"))
    assert len(out) == 1 and out[0].startswith("🔄 #1")      # la #2 está por debajo del umbral (10)
    puts = [c for c in integ.calls if "PUT" in c]
    assert len(puts) == 1 and "repos/oscarsao/oscar-hq/pulls/1/update-branch" in puts[0]
    assert integ.rebound == [(tid(1), f"{1001:040x}")]


def test_update_failure_leaves_branch_alone_and_says_so():
    integ, ad = make([pr(1)], approved={tid(1)}, behind={f"lane/{tid(1)}": 20}, update_ok=False)
    [line] = ad.poner_al_dia(ad.diagnose("oscarhq"))
    assert line.startswith("⚠️ #1") and integ.rebound == []


def test_conflict_goes_through_conflict_pass_trivial_or_card():
    integ, ad = make([pr(3, mergeable="CONFLICTING")], approved={tid(3)}, conflict="autoresolved")
    assert "resuelto" in ad.poner_al_dia(ad.diagnose("oscarhq"))[0]
    integ, ad = make([pr(3, mergeable="CONFLICTING")], approved={tid(3)}, conflict="sent_back")
    assert "tarjeta de rebase" in ad.poner_al_dia(ad.diagnose("oscarhq"))[0]
    assert integ.conflicts == [(tid(3), 3)]
    integ, ad = make([pr(3, mergeable="CONFLICTING")], conflict=None)
    assert "no se resuelve solo" in ad.poner_al_dia(ad.diagnose("oscarhq"))[0]


# --- integrar --------------------------------------------------------------------------------------------

def test_integrar_todo_assembles_batch_only_for_batch_projects():
    integ, ad = make([])
    assert ad.integrar_todo("oscarhq") == "lote montado" and integ.assembled == [("claude-oscarhq", True)]
    POL_sin = SimpleNamespace(deploy="none", batch="")
    integ.settings.policies["claude-oscarhq"] = POL_sin
    assert "no usa lote" in ad.integrar_todo("oscarhq") and len(integ.assembled) == 1


# --- comando de Telegram -------------------------------------------------------------------------------------

def command_center(tmp_path, integ):
    cc, desk, bot, h, _ = tc.center(tmp_path, {})
    cc._al_dia = lambda: AlDia(integ, ajustes_path="no-existe.yaml")
    cc._spawn = lambda fn: fn()
    return cc, desk, bot


def test_command_dev_reports_and_offers_integrate_all_with_one_button_per_pr(tmp_path):
    integ, _ = make([pr(1), pr(2)], approved={tid(1), tid(2)})
    cc, desk, bot = command_center(tmp_path, integ)
    command(desk, "/al_dia oscarhq dev")
    ficha = next(m for m in bot.sent if m.get("markup"))
    labels = texts(ficha["markup"])
    assert labels[0].startswith("✅ Integrar todo lo listo (2)") and len(labels) == 3
    assert any("terminado" in e["text"] for e in bot.edits)
    press(desk, ficha, 0)
    assert integ.assembled == [("claude-oscarhq", True)]
    assert any(m["text"] == "lote montado" for m in bot.sent)



def test_command_unknown_project_explains_usage(tmp_path):
    integ, _ = make([])
    cc, desk, bot = command_center(tmp_path, integ)
    command(desk, "/al_dia marte")
    assert bot.sent[-1]["text"].startswith("No conozco «marte»")
