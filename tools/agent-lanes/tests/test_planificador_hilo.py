"""fix/runner-tick: el planificador corre en su propio hilo; un mantenimiento colgado del bucle principal no lo para."""
import threading
import time

import runner as runner_mod


class FakeService:
    def __init__(self, boom_first=False):
        self.wake = threading.Event()
        self.ticks = 0
        self.boom_first = boom_first

    def tick(self):
        self.ticks += 1
        if self.boom_first and self.ticks == 1:
            raise OSError("hermes caído")
        return []

    def slots_summary(self):
        return {}


def start(svc, paused=lambda: False, interval=30):
    stop = threading.Event()
    th = threading.Thread(target=runner_mod.run_scheduler, args=(svc, paused, interval, stop), daemon=True)
    th.start()
    return stop, th


def finish(svc, stop, th):
    stop.set()
    svc.wake.set()
    th.join(2)
    assert not th.is_alive()


def wait_for(cond, secs=2):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_tick_al_arrancar_y_al_despertar_sin_esperar_el_intervalo():
    svc = FakeService()
    stop, th = start(svc)  # intervalo 30 s: si esperara el intervalo, el test fallaría
    assert wait_for(lambda: svc.ticks == 1)
    svc.wake.set()  # un worker terminó
    assert wait_for(lambda: svc.ticks == 2)
    finish(svc, stop, th)


def test_un_error_en_el_tick_no_mata_el_hilo():
    svc = FakeService(boom_first=True)
    stop, th = start(svc)
    assert wait_for(lambda: svc.ticks == 1)
    svc.wake.set()
    assert wait_for(lambda: svc.ticks == 2)  # sigue vivo tras la excepción
    finish(svc, stop, th)


def test_en_drenaje_no_lanza_ni_gira_en_vacio():
    svc = FakeService()
    stop, th = start(svc, paused=lambda: True)
    svc.wake.set()
    time.sleep(0.2)
    assert svc.ticks == 0  # no lanza
    finish(svc, stop, th)
