"""t_beb8fbf5: límites de la pasada de solapes, caché de relaciones y avisos del drenaje (Hermes y reloj simulados:
cada llamada «cuesta» segundos en un reloj falso, no se duerme ni se toca la BD real)."""
import json
from types import SimpleNamespace

from agent_lanes import drain
from agent_lanes.solapes import Ajustes, SolapeTicker, detectar, load_ajustes
from agent_lanes.vuelta import HiloAparte

HUB = "C:/Users/oscar/oscar-command-hub"
LANES = {"claude-hub": SimpleNamespace(board="default", kind="implement", repo=HUB)}
SIN_LIMITES = Ajustes(presupuesto_s=0, max_fallos=0, relaciones_min=0)  # comportamiento anterior a t_beb8fbf5


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class HermesLento:
    """Tablero con `n` tarjetas que tocan el mismo fichero. `latencia_list`/`latencia_show` en segundos simulados;
    `show_falla`: el `show` agota el timeout (como un `hermes kanban show` de 120 s con la BD ocupada)."""

    def __init__(self, reloj, n=10, latencia_list=2.0, latencia_show=3.0, show_falla=False, show_cost_fail=385.0):
        self.reloj, self.n = reloj, n
        self.latencia_list, self.latencia_show, self.show_falla, self.show_cost_fail = \
            latencia_list, latencia_show, show_falla, show_cost_fail
        self.llamadas = {"list": 0, "show": 0}

    def list_status_all(self, status):
        self.llamadas["list"] += 1
        self.reloj.t += self.latencia_list
        if status != "ready":
            return []
        return [{"id": f"t_{i:08x}", "assignee": "claude-hub", "title": f"T{i}", "status": "ready",
                 "body": "toca agent_lanes/commands.py"} for i in range(self.n)]

    def show(self, tid):
        self.llamadas["show"] += 1
        if self.show_falla:
            self.reloj.t += self.show_cost_fail  # 120 s x 3 intentos + esperas de 5 y 20 s
            raise TimeoutError("sin respuesta")
        self.reloj.t += self.latencia_show
        return {"parents": [], "children": []}


def medir(ajustes, **kw):
    reloj = Reloj()
    h = HermesLento(reloj, **kw)
    grupos = detectar(LANES, lambda b: h, ajustes, clock=reloj)
    return reloj.t, h.llamadas, grupos


def test_medicion_simulada_hermes_con_show_que_agota_timeout():
    """Antes: 10 tarjetas con un `show` colgado cada una = ~1.1 h de pasada. Después: cortada a ~presupuesto."""
    antes_s, antes_n, _ = medir(SIN_LIMITES, show_falla=True)
    despues_s, despues_n, grupos = medir(Ajustes(), show_falla=True)
    print(f"\nshow colgado, 10 tarjetas: antes {antes_s:.0f} s / {antes_n}; después {despues_s:.0f} s / {despues_n}")
    assert antes_s > 3000 and antes_n["show"] == 10
    assert despues_n["show"] <= 2 and despues_s < 1000  # el presupuesto (120 s) o max_fallos=2 cortan tras el 1.º
    assert grupos == []  # relación desconocida: no se da un aviso que podría ser falso


def test_presupuesto_corta_una_pasada_lenta_aunque_no_falle():
    reloj_t, llamadas, _ = medir(Ajustes(presupuesto_s=20, max_fallos=0), n=10, latencia_show=15.0)
    assert llamadas["show"] == 1 and reloj_t < 45  # 8 s de list + 15 s del 1.º show: ya pasa de 20 y corta


def test_sin_problemas_el_resultado_es_el_mismo_que_antes():
    _, _, antes = medir(SIN_LIMITES, n=4)
    _, _, despues = medir(Ajustes(), n=4)
    assert [c.tid for c in antes[0].cards] == [c.tid for c in despues[0].cards] and len(antes[0].cards) == 4


def test_ticker_recuerda_las_relaciones_entre_pasadas(tmp_path):
    reloj = Reloj()
    h = HermesLento(reloj, n=5)
    enviados = []
    t = SolapeTicker(LANES, lambda b: h, send=enviados.append, state_path=tmp_path / "s.json",
                     settings_path=tmp_path / "no.yaml", clock=reloj, descartes_path=tmp_path / "d.json")
    t.tick()
    primeras = h.llamadas["show"]
    reloj.t += 11 * 60  # siguiente pasada (minutos=10)
    t.tick()
    assert primeras == 5 and h.llamadas["show"] == 5  # la 2.ª no repite ningún `show`
    reloj.t += 31 * 60 + 11 * 60  # caducada la caché de 30 min
    t.tick()
    assert h.llamadas["show"] == 10
    assert len(enviados) == 1  # y el aviso sigue siendo uno solo


def test_un_show_fallido_no_se_recuerda_y_se_reintenta(tmp_path):
    reloj = Reloj()
    h = HermesLento(reloj, n=2, show_falla=True, show_cost_fail=1)
    t = SolapeTicker(LANES, lambda b: h, send=lambda x: None, state_path=tmp_path / "s.json",
                     settings_path=tmp_path / "no.yaml", clock=reloj, descartes_path=tmp_path / "d.json")
    assert t.tick() == []
    h.show_falla = False
    reloj.t += 11 * 60
    assert len(t.tick()) == 1  # recuperado Hermes, el par se avisa


def test_ajustes_nuevos_se_leen_y_los_invalidos_quedan_por_defecto(tmp_path):
    p = tmp_path / "solapes.yaml"
    p.write_text("presupuesto_s: 30\nmax_fallos: 3\nrelaciones_min: 0\n", encoding="utf-8")
    a = load_ajustes(p)
    assert (a.presupuesto_s, a.max_fallos, a.relaciones_min) == (30.0, 3, 0.0)
    p.write_text("presupuesto_s: -5\nmax_fallos: mucho\nrelaciones_min: true\n", encoding="utf-8")
    d = Ajustes()
    a = load_ajustes(p)
    assert (a.presupuesto_s, a.max_fallos, a.relaciones_min) == (d.presupuesto_s, d.max_fallos, d.relaciones_min)


def test_hilo_aparte_tick_lanza_sin_esperar():
    visto = []
    h = HiloAparte("x", lambda: visto.append(1))
    h.tick()
    h.esperar(5)
    assert visto == [1]


def test_drenaje_cuenta_quien_lo_retiene(tmp_path):
    """Cada 5 min de espera, `restart --drain` dice qué retiene el drenaje; al vencer el timeout NO reinicia."""
    t = {"now": 0.0}
    salida, llamadas = [], []

    def run(cmd, **kw):
        llamadas.append(cmd[1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def sleep(s):
        t["now"] += s
        drain.write_ack(tmp_path / "ack.json", pid=100, busy=["t_ac4dbbe0", "integrador-pasada"], now=1000.0 + t["now"])

    (tmp_path / "runner.lock").write_text(json.dumps({"pid": 100}), encoding="utf-8")
    r = drain.Restarter(pid_alive=lambda p: p == 100, drain_file=tmp_path / "drain.json",
                        ack_file=tmp_path / "ack.json", lock_file=tmp_path / "runner.lock", run=run, sleep=sleep,
                        clock=lambda: t["now"], now=lambda: 1000.0, out=salida.append, poll=100)
    assert r.restart(timeout=1000) == 1 and llamadas == []  # ni /End ni /Run: el runner sigue vivo
    notas = [s for s in salida if s.startswith("sigo drenando")]
    assert len(notas) >= 2 and "t_ac4dbbe0" in notas[0] and "integrador-pasada" in notas[0]
