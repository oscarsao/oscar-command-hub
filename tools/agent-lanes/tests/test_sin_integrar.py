"""Freno por aprobadas sin integrar (t_e4ecacf5): git, gh y Hermes simulados; sin red ni Telegram."""
from __future__ import annotations

from agent_lanes.config import Lane
from agent_lanes.runner import LaneRunner
from agent_lanes.sin_integrar import SinIntegrar, load_ajustes

REPO = "C:/repo"
LANE = Lane(name="claude-hub", board="default", repo=REPO, base="main", role="roles/implementador.md")


class FakeHermes:
    def __init__(self, done_ids, comentarios=None, ready=("t_nuevo",)):
        self.done = [{"id": t} for t in done_ids]
        self.ready = [{"id": t, "title": t, "body": ""} for t in ready]
        self.comentarios = comentarios or {}   # tid -> [cuerpos]
        self.puestos: list[tuple[str, str]] = []

    def list_status(self, assignee, status, sort="priority"):
        return self.done if status == "done" else []

    def list_ready(self, assignee):
        return self.ready

    def show(self, tid):
        return {"comments": [{"body": b} for b in self.comentarios.get(tid, [])]}

    def comment(self, tid, text, author=None):
        self.puestos.append((tid, text))
        self.comentarios.setdefault(tid, []).append(text)
        return True

    def drop_telegram_subs(self, tid):
        return []

    def __getattr__(self, name):  # cualquier otra llamada del runner (bloqueos pendientes…) no hace nada
        return lambda *a, **k: [] if name.startswith("list") else None


class FakeLector:
    def __init__(self, sin_fusionar, con_pr=(), falla=False):
        self.sin_fusionar, self.con_pr, self.falla = set(sin_fusionar), set(con_pr), falla
        self.llamadas = 0

    def ramas_sin_fusionar(self, repo, remote, base):
        self.llamadas += 1
        return None if self.falla else self.sin_fusionar

    def ramas_con_pr(self, repo):
        return None if self.falla else self.con_pr


def montar(tmp_path, n, *, tope=25, aviso=20, integrados=(), con_pr=(), falla=False, ready=("t_nuevo",)):
    ids = [f"t_{i}" for i in range(n)]
    (tmp_path / "s.yaml").write_text(f"tope_sin_integrar: {tope}\naviso_en: {aviso}\n", encoding="utf-8")
    hermes = FakeHermes(ids, {i: ["INTEGRADO abcdef123 lote"] for i in integrados}, ready)
    lector = FakeLector({f"lane/{i}" for i in ids}, {f"lane/{i}" for i in con_pr}, falla)
    avisos: list[str] = []
    freno = SinIntegrar({"claude-hub": LANE}, lambda board: hermes, lector=lector, settings_path=tmp_path / "s.yaml",
                        state_path=tmp_path / "estado.json", send=lambda text, markup=None: avisos.append(text))
    runner = LaneRunner(LANE, hermes=hermes, git=None, worker=None, verifier=None, notify=None, sin_integrar=freno)
    return runner, hermes, avisos, freno


def reclama(runner):
    return [c.tid for c in runner.candidates()]


def test_por_debajo_del_aviso_reclama_y_no_avisa(tmp_path):
    r, h, avisos, _ = montar(tmp_path, 10)
    assert reclama(r) == ["t_nuevo"] and not avisos and not h.puestos


def test_entre_aviso_y_tope_avisa_una_vez_y_reclama(tmp_path):
    r, h, avisos, f = montar(tmp_path, 22)
    assert reclama(r) == ["t_nuevo"]
    f._cache.clear()
    assert reclama(r) == ["t_nuevo"]
    assert len(avisos) == 1 and "22" in avisos[0] and "ultimo.markdown" in avisos[0] and not h.puestos


def test_sobre_el_tope_no_reclama_un_comentario_y_un_aviso(tmp_path):
    r, h, avisos, f = montar(tmp_path, 26)
    assert reclama(r) == []
    f._cache.clear()
    assert reclama(r) == []
    assert len(h.puestos) == 1 and "26" in h.puestos[0][1] and h.puestos[0][1].startswith("SIN-INTEGRAR-FRENO")
    assert len(avisos) == 1 and "tope" in avisos[0]


def test_aviso_se_rearma_al_bajar(tmp_path):
    r, h, avisos, f = montar(tmp_path, 22)
    reclama(r)
    h.done = h.done[:5]
    f._cache.clear()
    reclama(r)
    h.done = [{"id": f"t_{i}"} for i in range(22)]
    f._cache.clear()
    reclama(r)
    assert len(avisos) == 2


def test_tope_cero_y_desconocido_reclaman_como_siempre(tmp_path):
    r, h, avisos, _ = montar(tmp_path, 40, tope=0, aviso=0)
    assert reclama(r) == ["t_nuevo"] and not avisos
    r, h, avisos, _ = montar(tmp_path, 40, falla=True)
    assert reclama(r) == ["t_nuevo"] and not avisos and not h.puestos


def test_integrado_y_pr_no_cuentan(tmp_path):
    r, h, avisos, f = montar(tmp_path, 30, integrados=[f"t_{i}" for i in range(6)], con_pr=["t_6"])
    assert f.recuento(REPO) == 23   # 30 - 6 con INTEGRADO - 1 con PR
    assert reclama(r) == ["t_nuevo"] and len(avisos) == 1


def test_cache_de_30_minutos(tmp_path):
    _, _, _, f = montar(tmp_path, 3)
    f.recuento(REPO)
    f.recuento(REPO)
    assert f.lector.llamadas == 1


def test_linea_de_resumen_y_ajustes_invalidos(tmp_path):
    _, _, _, f = montar(tmp_path, 26)
    assert "26/25" in f.linea() and "🛑" in f.linea()
    (tmp_path / "x.yaml").write_text("tope_sin_integrar: -3\naviso_en: mucho\n", encoding="utf-8")
    a = load_ajustes(tmp_path / "x.yaml")
    assert (a.tope, a.aviso_en) == (25, 20)
