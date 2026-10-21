"""Registro de peticiones y estado derivado (t_211c9902 · A1). Sin red, sin kanban real."""
import json

import pytest

from agent_lanes import peticiones as pt

NOW = 1_800_000_000.0


def reg(tmp_path):
    return pt.Peticiones(tmp_path / "peticiones.json", now=lambda: NOW)


def show(status="running", comments=(), block=None, assignee="claude-hub"):
    events = [{"kind": "blocked", "created_at": block[0], "payload": {"kind": block[1], "reason": block[2]}}] if block else []
    return {"task": {"id": "t_1", "status": status, "assignee": assignee},
            "comments": [{"author": "x", "body": b} for b in comments], "events": events}


def test_ids_secuenciales_y_estables(tmp_path):
    r = reg(tmp_path)
    a = r.crear("Hub", "Avisos", "telegram")
    b = r.crear("Signal", "CSV", "claude-code", tarjetas=["t_a", "t_a"])
    assert (a["id"], b["id"]) == ("req_1", "req_2") and b["tarjetas"] == ["t_a"]
    assert pt.Peticiones(tmp_path / "peticiones.json").crear("Hub", "otra", "telegram")["id"] == "req_3"
    assert [x["id"] for x in r.listar("signal")] == ["req_2"]
    assert set(a) >= {"id", "proyecto", "titulo", "tipo", "tipo_confirmado_por", "urgencia", "modo_oasp", "origen",
                      "tarjetas", "creado", "ultimo_movimiento"}


def test_fichero_ilegible_no_reutiliza_ids_ni_rompe(tmp_path):
    r = reg(tmp_path)
    r.path.write_text("{no es json", encoding="utf-8")
    assert r.listar() == [] and r.crear("Hub", "x", "telegram")["id"] == "req_1"


def test_validacion_de_listas_cerradas_y_tipo_confirmado(tmp_path):
    r = reg(tmp_path)
    for kw in ({"proyecto": "Otro"}, {"origen": "sms"}, {"titulo": " "}, {"tipo": "bug"},
               {"tipo": "feature", "tipo_confirmado_por": "oscar", "urgencia": "P1"},
               {"tipo": "bug", "tipo_confirmado_por": "oscar", "urgencia": "P9"},
               {"tipo": "bug", "tipo_confirmado_por": "oscar", "modo_oasp": "pitch"}):
        args = {"proyecto": "Hub", "titulo": "t", "origen": "telegram", **kw}
        with pytest.raises(ValueError):
            r.crear(**args)
    ok = r.crear("Hub", "t", "telegram", tipo="bug", tipo_confirmado_por="oscar", urgencia="P1")
    assert ok["urgencia"] == "P1" and r.listar()[0]["id"] == ok["id"]


def test_fijar_tipo_vincular_y_ultimo_movimiento(tmp_path):
    t = [NOW]
    r = pt.Peticiones(tmp_path / "p.json", now=lambda: t[0])
    rid = r.crear("Hub", "x", "telegram")["id"]
    t[0] += 60
    assert r.vincular(rid, "t_1")["ultimo_movimiento"] == NOW + 60
    r.vincular(rid, "t_1")
    assert r.get(rid)["tarjetas"] == ["t_1"] and r.get(rid)["tipo"] is None
    with pytest.raises(ValueError):
        r.fijar_tipo(rid, "feature", "")
    assert r.fijar_tipo(rid, "feature", "oscar", modo_oasp="spec-lite")["modo_oasp"] == "spec-lite"
    with pytest.raises(KeyError):
        r.vincular("req_99", "t_2")
    json.loads(r.path.read_text(encoding="utf-8"))  # sigue siendo JSON válido


def test_vinculo_y_tipo_en_la_tarjeta():
    assert pt.vinculo_de("Hazlo\nreq: req_41\nmás") == "req_41" and pt.vinculo_de("sin vínculo") is None
    assert pt.vinculo_de(pt.linea_vinculo("req_7")) == "req_7"
    assert pt.tipo_de("Tipo: bug · Urgencia: P1\nresto") == ("bug", "P1")
    assert pt.tipo_de(pt.linea_tipo("feature", "P1")) == ("feature", None)
    assert pt.tipo_de("nada") == (None, None)


def test_sin_tarjeta_es_atascada():
    e = pt.derivar({"id": "req_1", "tarjetas": [], "creado": NOW}, {}, NOW)
    assert (e.estado, e.motivo, e.quien) == ("atascada", "sin tarjeta", "Oscar") and e.etiqueta == "atascada (sin tarjeta)"


def test_integrado_la_pasa_a_hecha():
    req = {"id": "req_1", "tarjetas": ["t_1", "t_2"]}
    uno = {"t_1": show("done", ["INTEGRADO 3f9a1c0"]), "t_2": show("review", ["PR #5 abierto"])}
    assert pt.derivar(req, uno, NOW).estado == "en curso"
    dos = {"t_1": show("done", ["INTEGRADO 3f9a1c0"]), "t_2": show("done", ["INTEGRADO abcdef1"])}
    e = pt.derivar(req, dos, NOW)
    assert (e.estado, e.paso) == ("hecha", pt.TOTAL_PASOS)


def test_esperandote_y_atasco_por_antiguedad():
    req = {"id": "req_1", "tarjetas": ["t_1"]}
    reciente = show("blocked", block=(NOW - 3600, "needs_input", "¿A o B?"))
    e = pt.derivar(req, {"t_1": reciente}, NOW)
    assert (e.estado, e.quien, e.desde) == ("esperándote", "Oscar", NOW - 3600)
    vieja = show("blocked", block=(NOW - 30 * 3600, "needs_input", "¿A o B?"))
    assert pt.derivar(req, {"t_1": vieja}, NOW).motivo.startswith("sin respuesta tuya")


def test_atascada_por_guard_triage_pr_y_tarjeta_ilegible():
    req = {"id": "req_1", "tarjetas": ["t_1"]}
    guard = show("blocked", block=(NOW - 60, "needs_input", "LIMITACION-GUARD: no puedo instalar"))
    assert pt.derivar(req, {"t_1": guard}, NOW).motivo == "guard bloqueado"
    assert pt.derivar(req, {"t_1": show("triage", block=(NOW, "transient", "x"))}, NOW).motivo \
        == "bloqueada dos veces por lo mismo"
    assert pt.derivar(req, {"t_1": show("blocked", block=(NOW, "transient", "x"))}, NOW).motivo == "carril caído"
    fuera = show("review", ["INTEGRADOR: PR #52 queda FUERA del lote 3: choca con main"])
    e = pt.derivar(req, {"t_1": fuera}, NOW)
    assert (e.estado, e.motivo, e.quien) == ("atascada", "PR sin salida", "el integrador")
    assert pt.derivar(req, {"t_1": None}, NOW).motivo.startswith("tarjeta t_1 no legible")


def test_en_curso_y_prioridad_atascada_sobre_espera():
    req = {"id": "req_1", "tarjetas": ["t_1", "t_2"]}
    e = pt.derivar(req, {"t_1": show("running"), "t_2": show("ready")}, NOW)
    assert (e.estado, e.quien) == ("en curso", "el worker")
    mixto = {"t_1": show("blocked", block=(NOW, "needs_input", "?")), "t_2": show("triage", block=(NOW, "x", "y"))}
    assert pt.derivar(req, mixto, NOW).estado == "atascada"
