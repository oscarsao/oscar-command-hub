"""Freno por aprobadas sin integrar (t_e4ecacf5): git, gh y Hermes simulados; sin red ni Telegram."""
from __future__ import annotations

import json
import threading
import time

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
    def __init__(self, sin_fusionar, con_pr=(), falla=False, contenido=(), fetch_ok=True):
        self.sin_fusionar, self.con_pr, self.falla = set(sin_fusionar), set(con_pr), falla
        self.contenido, self.fetch_ok = set(contenido), fetch_ok   # contenido: ramas cuyo merge-tree no cambia nada
        self.llamadas = 0
        self.fetches = 0

    def fetch(self, repo, remote):
        self.fetches += 1
        return self.fetch_ok

    def integrada_por_contenido(self, repo, remote, base, rama):
        return rama in self.contenido

    def ramas_sin_fusionar(self, repo, remote, base):
        self.llamadas += 1
        return None if self.falla else self.sin_fusionar

    def ramas_con_pr(self, repo):
        return None if self.falla else self.con_pr


def montar(tmp_path, n, *, tope=25, aviso=20, integrados=(), con_pr=(), falla=False, ready=("t_nuevo",),
           contenido=(), yaml=None, fetch_ok=True):
    ids = [f"t_{i}" for i in range(n)]
    if yaml is not False:
        (tmp_path / "s.yaml").write_text(yaml or f"tope_sin_integrar: {tope}\naviso_en: {aviso}\n", encoding="utf-8")
    hermes = FakeHermes(ids, {i: ["INTEGRADO abcdef123 lote"] for i in integrados}, ready)
    lector = FakeLector({f"lane/{i}" for i in ids}, {f"lane/{i}" for i in con_pr}, falla,
                        {f"lane/{i}" for i in contenido}, fetch_ok)
    avisos: list[str] = []
    freno = SinIntegrar({"claude-hub": LANE}, lambda board: hermes, lector=lector, settings_path=tmp_path / "s.yaml",
                        state_path=tmp_path / "estado.json", send=lambda text, markup=None: avisos.append(text),
                        fondo=False)
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
    assert (a.tope, a.aviso_en) == (0, 0)


# --- correcciones de la revisión -------------------------------------------------------------------------------------

def test_nace_apagado_sin_yaml_o_con_yaml_invalido(tmp_path):
    inexistente = load_ajustes(tmp_path / "no_existe.yaml")
    assert (inexistente.tope, inexistente.aviso_en) == (0, 0)
    for contenido in ("::: no es yaml [", "- una\n- lista\n", "tope_sin_integrar: mucho\n"):
        (tmp_path / "s.yaml").write_text(contenido, encoding="utf-8")
        a = load_ajustes(tmp_path / "s.yaml")
        assert (a.tope, a.aviso_en) == (0, 0)
        r, h, avisos, f = montar(tmp_path, 400, yaml=contenido)
        assert reclama(r) == ["t_nuevo"] and not avisos and not h.puestos and f.lector.llamadas == 0
    sub = tmp_path / "sin_yaml"
    sub.mkdir()
    r, h, avisos, f = montar(sub, 400, yaml=False)
    assert reclama(r) == ["t_nuevo"] and not avisos and f.lector.llamadas == 0 and f.linea() == ""


def test_yaml_del_repo_viene_apagado():
    import agent_lanes.sin_integrar as m
    a = load_ajustes(m.SETTINGS_PATH)
    assert (a.tope, a.aviso_en) == (0, 0)


def test_flujo_completo_con_valores_por_defecto_y_git_gh_simulados(tmp_path):
    """Sin YAML (defaults) no frena ni toca git; al encenderlo sí. git y gh simulados bajo el Lector real."""
    from agent_lanes.sin_integrar import Lector
    llamadas: list[list[str]] = []

    class CP:
        def __init__(self, out="", rc=0):
            self.stdout, self.returncode = out, rc

    ids = [f"t_{i}" for i in range(30)]

    def run(args, **kw):
        llamadas.append(args)
        if args[:2] == ["gh", "pr"]:
            return CP(json.dumps([{"headRefName": "lane/t_0", "state": "MERGED"},
                                  {"headRefName": "lane/t_1", "state": "CLOSED"}]))
        if "fetch" in args:
            return CP()
        if "branch" in args:
            return CP("\n".join(f"origin/lane/{i}" for i in ids))
        if "cherry" in args:
            return CP("+ abc\n")
        if "merge-tree" in args:
            return CP("treeX\n" if args[-1] == "origin/lane/t_2" else "treeY\n")
        if "rev-parse" in args:
            return CP("treeX\n")
        return CP("", 1)

    hermes = FakeHermes(ids)
    f = SinIntegrar({"claude-hub": LANE}, lambda b: hermes, lector=Lector(runner=run),
                    settings_path=tmp_path / "no_existe.yaml", state_path=tmp_path / "e.json", fondo=False)
    runner = LaneRunner(LANE, hermes=hermes, git=None, worker=None, verifier=None, notify=None, sin_integrar=f)
    assert reclama(runner) == ["t_nuevo"] and llamadas == []
    (tmp_path / "s.yaml").write_text("tope_sin_integrar: 25\naviso_en: 20\n", encoding="utf-8")
    f.settings_path = tmp_path / "s.yaml"
    assert reclama(runner) == []          # 30 - t_0 (PR fusionado) - t_2 (merge-tree no cambia nada) = 28 >= 25
    assert f.recuento(REPO) == 28
    assert any("fetch" in a for a in llamadas) and any(a[:2] == ["gh", "pr"] and "5000" in a for a in llamadas)


def test_frenado_no_calcula_y_el_hilo_de_fondo_lo_rellena(tmp_path):
    r, h, avisos, f = montar(tmp_path, 30)
    f.fondo = True
    gate = threading.Event()
    orig = f.lector.ramas_sin_fusionar
    f.lector.ramas_sin_fusionar = lambda *a: (gate.wait(5), orig(*a))[1]
    t0 = time.monotonic()
    assert reclama(r) == ["t_nuevo"]      # sin dato aún: desconocido, no frena y no espera al cálculo
    assert time.monotonic() - t0 < 1
    gate.set()
    for _ in range(100):
        if f.recuento(REPO) is not None:
            break
        time.sleep(0.05)
    assert f.recuento(REPO) == 30 and reclama(r) == []


def test_fallo_se_cachea_poco_y_no_cambia_el_estado_de_aviso(tmp_path):
    now = [1000.0]
    r, h, avisos, f = montar(tmp_path, 26)
    f._clock = lambda: now[0]
    assert reclama(r) == [] and len(avisos) == 1
    f.lector.falla = True
    now[0] += 31 * 60
    assert reclama(r) == ["t_nuevo"]      # fallo: desconocido, no frena
    estado = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert estado[REPO] == 2 and len(avisos) == 1
    llam = f.lector.llamadas
    now[0] += 2 * 60
    reclama(r)
    assert f.lector.llamadas == llam      # el fallo se recuerda menos de 5 min
    f.lector.falla = False
    now[0] += 4 * 60
    assert reclama(r) == []               # pasados 5 min reintenta y vuelve a frenar
    assert len(avisos) == 1               # sin aviso repetido


def test_fetch_fallido_es_desconocido_y_contenido_ya_integrado_no_cuenta(tmp_path):
    r, h, avisos, f = montar(tmp_path, 30, fetch_ok=False)
    assert reclama(r) == ["t_nuevo"] and f.lector.fetches == 1 and f.lector.llamadas == 0
    r, h, avisos, f = montar(tmp_path, 30, contenido=[f"t_{i}" for i in range(10)])
    assert f.recuento(REPO) == 20 and f.lector.fetches == 1


def test_comentadas_persisten_entre_reinicios(tmp_path):
    r, h, avisos, f = montar(tmp_path, 26)
    assert reclama(r) == [] and len(h.puestos) == 1
    f2 = SinIntegrar({"claude-hub": LANE}, lambda b: h, lector=f.lector, settings_path=tmp_path / "s.yaml",
                     state_path=tmp_path / "estado.json", fondo=False)
    h.comentarios.clear()   # sin comentarios visibles en Hermes, la memoria en disco evita repetir
    f2.comentar_espera(h, "t_nuevo", REPO, 26)
    assert len(h.puestos) == 1


# --- segunda revisión ------------------------------------------------------------------------------------------------

def _con_sha(f, base="B1"):
    """Dota al FakeLector de sha() y cuenta los juicios por contenido."""
    f.lector.base_sha = base
    f.lector.juicios = 0
    orig = f.lector.integrada_por_contenido

    def juzga(repo, remote, b, rama):
        f.lector.juicios += 1
        return orig(repo, remote, b, rama)
    f.lector.integrada_por_contenido = juzga
    f.lector.sha = lambda repo, ref: f.lector.base_sha if ref.endswith("/main") else "R:" + ref
    return f


def test_aviso_se_envia_fuera_del_lock(tmp_path):
    r, h, avisos, f = montar(tmp_path, 26)
    visto = []

    def send(text, markup=None):
        visto.append(f._lock.acquire(blocking=False))   # si el lock estuviera tomado, daría False
        if visto[-1]:
            f._lock.release()
    f.send = send
    assert reclama(r) == []
    assert visto == [True]


def test_recuento_largo_converge_con_juicios_memorizados(tmp_path):
    now = [0.0]
    r, h, avisos, f = montar(tmp_path, 30, tope=100, aviso=0, yaml="tope_sin_integrar: 100\nplazo_segundos: 10\n")
    _con_sha(f)
    f._clock = lambda: now[0]
    orig = f.lector.integrada_por_contenido

    def lenta(repo, remote, b, rama):
        now[0] += 1.0            # cada juicio tarda 1 s; el plazo es 10 s
        return orig(repo, remote, b, rama)
    f.lector.integrada_por_contenido = lenta
    assert f.recuento(REPO) is None and f.lector.juicios == 11      # plazo agotado: parcial = desconocido
    juzgadas = f.lector.juicios
    now[0] += 6 * 60                                               # pasa el TTL del fallo
    assert f.recuento(REPO) is None and f.lector.juicios - juzgadas == 11   # solo las nuevas
    now[0] += 6 * 60
    assert f.recuento(REPO) == 30                                  # converge a la tercera pasada
    juzgadas = f.lector.juicios
    now[0] += 31 * 60
    assert f.recuento(REPO) == 30 and f.lector.juicios == juzgadas  # misma base y ramas: nada que recalcular
    f.lector.base_sha = "B2"                                       # la base avanza: se rejuzga
    now[0] += 31 * 60
    f.recuento(REPO)
    assert f.lector.juicios > juzgadas   # se rejuzga (aquí otra vez limitado por el plazo)
    assert (tmp_path / "estado_juicios.json").exists()


def test_valor_viejo_caduca_y_hilo_colgado_se_relanza(tmp_path):
    now = [1000.0]
    r, h, avisos, f = montar(tmp_path, 26)
    f._clock = lambda: now[0]
    assert reclama(r) == []
    now[0] += 31 * 60
    f._en_curso[REPO] = now[0]                 # hilo colgado: el valor sigue vigente a medias
    assert f.recuento(REPO) == 26              # 31 min < 3 x 30: aún se sirve
    fetches = f.lector.fetches
    f.lector.fetch_ok = False                  # el refresco relanzado falla: desconocido
    now[0] += 100 * 60                         # > 90 min sin refrescar y sin que el hilo termine
    assert f.recuento(REPO) is None
    assert f.lector.fetches == fetches + 1     # se relanzó el refresco aunque el anterior no terminó


def test_sin_send_no_se_marca_avisado_y_comentario_fallido_no_se_marca(tmp_path):
    r, h, avisos, f = montar(tmp_path, 26)
    f.send = None
    assert reclama(r) == []
    assert not (tmp_path / "estado.json").exists() or json.loads((tmp_path / "estado.json").read_text()).get(REPO, 0) == 0
    f.send = lambda text, markup=None: avisos.append(text)
    f._cache.clear()
    reclama(r)
    assert len(avisos) == 1                    # el aviso no se perdió
    # send que falla: sigue sin marcar
    r, h, avisos, f = montar(tmp_path / "otro", 26) if (tmp_path / "otro").mkdir() is None else None
    f.send = lambda text, markup=None: (_ for _ in ()).throw(OSError("telegram caído"))
    reclama(r)
    f.send = lambda text, markup=None: avisos.append(text)
    f._cache.clear()
    reclama(r)
    assert len(avisos) == 1
    # comentario que no se escribe: se reintenta
    h2 = FakeHermes([], ready=())
    h2.comment = lambda tid, text, author=None: False
    f.comentar_espera(h2, "t_x", REPO, 26)
    assert "t_x" not in f._comentadas
    h2.comment = lambda tid, text, author=None: h2.puestos.append((tid, text)) or True
    f.comentar_espera(h2, "t_x", REPO, 26)
    assert "t_x" in f._comentadas and len(h2.puestos) == 1


def test_lector_git_sin_prompt_ni_stdin_y_merge_tree_129_es_desconocido(tmp_path):
    from agent_lanes.sin_integrar import Lector
    import subprocess
    vistos = []

    class CP:
        def __init__(self, out="", rc=0):
            self.stdout, self.returncode = out, rc

    def run(args, **kw):
        vistos.append(kw)
        if "cherry" in args:
            return CP("+ abc\n")
        if "merge-tree" in args:
            return CP("usage: git merge-tree\n", 129)
        return CP()
    lec = Lector(runner=run)
    assert lec.integrada_por_contenido("C:/r", "origin", "main", "lane/t_1") is None
    assert lec.fetch("C:/r", "origin")
    assert vistos and all(k["stdin"] == subprocess.DEVNULL and k["env"]["GIT_TERMINAL_PROMPT"] == "0" for k in vistos)
    # y un conflicto real (rc 1) sigue siendo «no integrada»
    lec2 = Lector(runner=lambda a, **k: CP("+ x\n") if "cherry" in a else CP("", 1))
    assert lec2.integrada_por_contenido("C:/r", "origin", "main", "lane/t_1") is False


def test_candidata_desconocida_hace_el_recuento_desconocido(tmp_path):
    r, h, avisos, f = montar(tmp_path, 30)
    f.lector.integrada_por_contenido = lambda *a: None
    assert f.recuento(REPO) is None and reclama(r) == ["t_nuevo"]
