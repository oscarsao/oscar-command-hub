"""Cola de salida de Telegram (t_695e44b1): cliente falso que falla N veces y luego responde. Sin red ni Telegram real."""
from __future__ import annotations

import pytest
from agent_lanes import cola_salida
from agent_lanes.cola_salida import ColaSalida, contexto
from agent_lanes.telegram import TelegramAPIError, TelegramNotifier

OWNER = "111"


class Reloj:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Cliente:
    """Sustituye a `TelegramNotifier._api`: falla `fallos` veces con `error` y luego responde."""

    def __init__(self, fallos: int = 0, error: Exception | None = None):
        self.fallos, self.error = fallos, error or RuntimeError("telegram no alcanzable (TimeoutError)")
        self.enviados: list[dict] = []
        self.n = 0

    def __call__(self, method: str, payload: dict, timeout=None):
        assert method == "sendMessage"
        if self.fallos > 0:
            self.fallos -= 1
            raise self.error
        self.n += 1
        self.enviados.append(payload)
        return {"message_id": self.n}


@pytest.fixture
def montaje(tmp_path):
    reloj = Reloj()
    nt = TelegramNotifier("1:tok", OWNER)
    nt.salida = ColaSalida(tmp_path / "cola.json", OWNER, clock=reloj, horas=lambda: 6, horas_info=lambda: 2)
    nt.sleep = lambda s: None
    return nt, reloj


def textos(cli):
    return [p["text"] for p in cli.enviados]


def test_sin_cola_el_fallo_sube_como_siempre(montaje, monkeypatch):
    nt, _ = montaje
    nt.salida = None
    monkeypatch.setattr(nt, "_api", Cliente(fallos=1))
    with pytest.raises(RuntimeError):
        nt.send_to(OWNER, None, "hola")


def test_fallo_transitorio_se_retiene_y_llega_una_sola_vez(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=2)
    monkeypatch.setattr(nt, "_api", cli)
    with contexto("needs_input", "t_1"):
        assert nt.send_to(OWNER, None, "¿Qué hago?") is None  # retenido: no lanza
    assert len(nt.salida.pendientes()) == 1
    nt.drenar_salida()  # antes de la espera: no toca Telegram
    assert cli.enviados == [] and cli.fallos == 1
    reloj.t += 61
    nt.drenar_salida()  # segundo fallo: sigue en cola y se alarga la espera
    assert cli.enviados == [] and len(nt.salida.pendientes()) == 1 and cli.fallos == 0
    reloj.t += 30
    nt.drenar_salida()  # aún dentro de la espera: no llama
    assert cli.enviados == []
    reloj.t += 61
    nt.drenar_salida()
    nt.drenar_salida()
    assert textos(cli) == ["¿Qué hago?"] and nt.salida.pendientes() == []


def test_mismo_aviso_dos_veces_no_se_duplica(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=2)
    monkeypatch.setattr(nt, "_api", cli)
    with contexto("needs_input", "t_1"):
        nt.send_to(OWNER, None, "v1")
        nt.send_to(OWNER, None, "v2")  # misma tarjeta y tipo: sustituye al retenido
    assert [e["payload"]["text"] for e in nt.salida.pendientes()] == ["v2"]
    with contexto("needs_input", "t_1"):
        assert nt.send_to(OWNER, None, "v3")["message_id"]  # llega directo: borra el retenido
    assert nt.salida.pendientes() == []
    reloj.t += 3600
    nt.drenar_salida()
    assert textos(cli) == ["v3"]


def test_urgente_sale_antes_que_informativo_y_un_solo_mensaje_de_recuperacion(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=3)
    monkeypatch.setattr(nt, "_api", cli)
    with contexto("progreso", "t_2"):
        nt.send_to(OWNER, None, "info")
    with contexto("needs_input", "t_3"):
        nt.send_to(OWNER, None, "urgente")
    with contexto("monitor"):
        nt.send_to(OWNER, None, "runner parado")
    assert cli.fallos == 0 and len(nt.salida.pendientes()) == 3
    reloj.t += 61
    nt.drenar_salida()
    out = textos(cli)
    assert out[0].startswith("⚠️ Hubo 3 avisos retenidos por una caída de Telegram: aquí van los urgentes (2)")
    assert out[1:] == ["urgente", "runner parado", "info"]  # los urgentes primero; el informativo, el último
    assert sum(t.startswith("⚠️ Hubo") for t in out) == 1
    reloj.t += 3600
    nt.drenar_salida()
    assert sum(t.startswith("⚠️ Hubo") for t in out) == 1 and len(cli.enviados) == 4  # no hay segundo mensaje


def test_avalancha_de_informativos_se_recorta(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=7)
    monkeypatch.setattr(nt, "_api", cli)
    for i in range(6):
        with contexto("progreso", f"t_{i}"):
            nt.send_to(OWNER, None, f"info {i}")
    with contexto("aprobar", "t_9"):
        nt.send_to(OWNER, None, "aprobación lista")
    reloj.t += 61
    nt.drenar_salida()
    out = textos(cli)
    assert "3 informativos ya no se envían" in out[0]
    assert out[1] == "aprobación lista"
    assert len(out) == 1 + 1 + cola_salida.RECUPERACION_INFO_MAX
    fila = nt.salida._load()["registro"]
    hoy = next(iter(fila.values()))
    assert hoy["progreso"]["caducados"] == 3 and hoy["progreso"]["reintentados"] == 3


def test_caducidad_informativo_antes_que_urgente(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=2)
    monkeypatch.setattr(nt, "_api", cli)
    with contexto("progreso", "t_1"):
        nt.send_to(OWNER, None, "info")
    with contexto("needs_input", "t_2"):
        nt.send_to(OWNER, None, "pregunta")
    reloj.t += 3 * 3600  # info caduca a las 2 h; la pregunta aguanta 6 h
    nt.salida._save({**nt.salida._load(), "pausa_hasta": 0})
    nt.drenar_salida()
    assert textos(cli) == ["pregunta"]  # una sola entrada viva: sin mensaje de recuperación
    reg = next(iter(nt.salida._load()["registro"].values()))
    assert reg["progreso"]["caducados"] == 1


def test_rechazo_definitivo_no_se_retiene(montaje, monkeypatch):
    nt, _ = montaje
    monkeypatch.setattr(nt, "_api", Cliente(fallos=1, error=TelegramAPIError("telegram HTTP 400: bad", "bad")))
    with pytest.raises(TelegramAPIError):
        nt.send_to(OWNER, None, "x")
    assert nt.salida.pendientes() == []


def test_502_si_se_retiene(montaje, monkeypatch):
    nt, _ = montaje
    monkeypatch.setattr(nt, "_api", Cliente(fallos=1, error=TelegramAPIError("telegram HTTP 502: Bad Gateway",
                                                                              "Bad Gateway")))
    assert nt.send_to(OWNER, None, "x") is None
    assert len(nt.salida.pendientes()) == 1


def test_descarte_si_en_la_cola_falla_definitivamente(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=1)
    monkeypatch.setattr(nt, "_api", cli)
    with contexto("needs_input", "t_1"):
        nt.send_to(OWNER, None, "x")
    cli.fallos, cli.error = 1, TelegramAPIError("telegram HTTP 403: blocked", "blocked")
    reloj.t += 61
    out = nt.drenar_salida()
    assert out["fallidos"] == 1 and nt.salida.pendientes() == []


def test_linea_resumen_de_ayer(montaje, monkeypatch):
    nt, reloj = montaje
    cli = Cliente(fallos=1)
    monkeypatch.setattr(nt, "_api", cli)
    assert nt.salida.linea_resumen() == ""
    with contexto("needs_input", "t_1"):
        nt.send_to(OWNER, None, "x")
    reloj.t += 61
    nt.drenar_salida()
    with contexto("progreso", "t_2"):
        nt.send_to(OWNER, None, "y")
    reloj.t += 86400  # al día siguiente
    linea = nt.salida.linea_resumen()
    assert "1 enviados · 1 reintentados · 0 caducados · 0 fallidos" in linea and "\n" not in linea


def test_contexto_por_defecto_es_info():
    assert cola_salida.contexto_actual() == ("info", None)
    with contexto("aprobar", "t_1"):
        assert cola_salida.contexto_actual() == ("aprobar", "t_1")
    assert cola_salida.contexto_actual() == ("info", None)


def test_es_transitorio():
    es = cola_salida.es_transitorio
    assert es(TimeoutError("The read operation timed out"))
    assert es(RuntimeError("telegram no alcanzable (TimeoutError)"))
    assert es(TelegramAPIError("telegram HTTP 429: Too Many Requests", "Too Many Requests: retry after 3"))
    assert not es(TelegramAPIError("telegram HTTP 403: Forbidden", "Forbidden"))
    assert not es(ValueError("x"))


def test_estado_corrupto_no_rompe(tmp_path):
    (tmp_path / "c.json").write_text("{no json", encoding="utf-8")
    cola = ColaSalida(tmp_path / "c.json", OWNER)
    assert cola.pendientes() == [] and cola.drenar(lambda p: None)["enviados"] == 0
