"""F2-02: selector de backend del kanban y paridad de interfaz (sin base de datos)."""
import builtins
import importlib
import inspect
import threading

import pytest

from agent_lanes import config, kanban_api, proc
from agent_lanes.hermes import HermesCLI, HermesError
from agent_lanes.kanban_api import SupabaseKanban, backend_for, backend_name


def test_por_defecto_es_hermes_y_no_cambia_nada(monkeypatch, tmp_path):
    monkeypatch.delenv("KANBAN_BACKEND", raising=False)
    monkeypatch.setattr(config, "ROOT", tmp_path)  # lanes.yaml ausente -> hermes
    assert backend_name() == "hermes"
    h = backend_for("oscarhq", author="auditor")
    assert type(h) is HermesCLI and h.board == "oscarhq" and h.author == "auditor"


def test_lanes_yaml_y_entorno(monkeypatch, tmp_path):
    (tmp_path / "lanes.yaml").write_text("runner:\n  kanban_backend: supabase\n", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.delenv("KANBAN_BACKEND", raising=False)
    assert backend_name() == "supabase"
    assert isinstance(backend_for("default"), SupabaseKanban)
    monkeypatch.setenv("KANBAN_BACKEND", "hermes")  # el entorno manda sobre lanes.yaml (vuelta atrás = 1 variable)
    assert backend_name() == "hermes"


def test_valor_desconocido_no_arranca(monkeypatch):
    monkeypatch.setenv("KANBAN_BACKEND", "supabse")
    with pytest.raises(ValueError, match="no es válido"):
        backend_for("default")


def _public(cls) -> set[str]:
    return {n for n, v in inspect.getmembers(cls) if not n.startswith("_") and callable(v)}


def test_paridad_de_metodos_con_hermes():
    """Todo lo que el hub llama en HermesCLI existe en el backend supabase (si no, AttributeError al cortar)."""
    falta = _public(HermesCLI) - _public(SupabaseKanban)
    assert falta == set()


def test_firmas_compatibles():
    for name in _public(HermesCLI):
        a = inspect.signature(getattr(HermesCLI, name))
        b = inspect.signature(getattr(SupabaseKanban, name))
        for p in a.parameters.values():
            if p.name == "python":  # requeue_triage: el venv de Hermes no aplica a supabase (lo acepta e ignora)
                continue
            assert p.name in b.parameters or any(q.kind is q.VAR_KEYWORD for q in b.parameters.values()), (name, p)


def test_contrato_de_14_operaciones_cubierto():
    ops = {"list": "list_status", "claim": "claim", "heartbeat": "heartbeat", "comment": "comment", "block": "block",
           "request-review": "request_review", "show": "show", "complete": "complete",
           "reopen-review": "reopen_review", "reassign": "reassign", "assign": "assign", "unblock": "unblock",
           "notify-list": "drop_telegram_subs", "notify-unsubscribe": "drop_telegram_subs"}
    from agent_lanes.hermes import CONTRACT
    assert set(ops) == set(CONTRACT)
    assert all(hasattr(SupabaseKanban, m) for m in ops.values())


def test_atributos_que_lee_el_hub():
    k = SupabaseKanban("oscarhq", author="x", dsn="postgresql://nadie@127.0.0.1:1/x")
    assert (k.board, k.author, k.last_error) == ("oscarhq", "x", None)


def test_importa_sin_driver(monkeypatch):
    """El módulo (y el backend hermes) no necesitan psycopg2; sin driver, supabase falla limpio."""
    real_import = builtins.__import__

    def no_psycopg(name, *a, **k):
        if name.startswith("psycopg2"):
            raise ImportError("sin driver")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_psycopg)
    mod = importlib.reload(kanban_api)
    try:
        k = mod.SupabaseKanban("default", dsn="postgresql://x@127.0.0.1:1/x")
        assert k.claim("t_00000001", 60) is False and "psycopg2" in k.last_error
        assert any("psycopg2" in p for p in k.check_contract())  # el arranque del runner lo dice
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        importlib.reload(kanban_api)


class _BrokenConn:
    closed = 0

    def cursor(self):
        raise OSError("red caída")

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


def test_sin_conexion_lectura_lanza_hermeserror_y_escritura_devuelve_false():
    k = SupabaseKanban("default", dsn="x", connect=lambda dsn: _BrokenConn())
    with pytest.raises(HermesError, match="list failed"):
        k.list_status("claude-hub", "ready")
    proc.take_transient()
    assert k.claim("t_00000001", 60) is False
    assert "red caída" in (k.last_error or "") and proc.take_transient()  # la cola de decisiones lo ve transitorio
    assert k.request_review("t_00000001", "s", {})[0] is False
    assert k.complete("t_00000001", "r", {})[0] is False
    assert k.requeue_triage("t_00000001") is None
    assert k.check_contract() and "no disponible" in k.check_contract()[0]


def test_sin_dsn_no_conecta(monkeypatch, tmp_path):
    monkeypatch.delenv("HUB_DB_DSN", raising=False)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    called = []
    k = SupabaseKanban("default", connect=lambda dsn: called.append(dsn))
    assert k.comment("t_00000001", "hola") is False and "HUB_DB_DSN" in k.last_error and not called


def test_create_comparte_el_preprocesado_con_hermes(monkeypatch):
    """La tarjeta nueva pasa por el mismo enrutado/marca GRANDE en ambos backends (preparar_tarjeta)."""
    seen = []
    monkeypatch.setattr(kanban_api, "preparar_tarjeta", lambda b, t, body, a: seen.append((b, t, a)) or (body, a))
    k = SupabaseKanban("default", connect=lambda dsn: _BrokenConn(), dsn="x")
    with pytest.raises(HermesError):
        k.create("[Hub] t", "b", "claude-hub")
    assert seen == [("default", "[Hub] t", "claude-hub")]


def test_hilos_no_comparten_estado_de_error():
    k = SupabaseKanban("default", dsn="x", connect=lambda dsn: _BrokenConn())
    out = []
    ts = [threading.Thread(target=lambda: out.append(k.heartbeat("t_00000001"))) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == [False] * 4
