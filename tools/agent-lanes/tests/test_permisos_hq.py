"""Quién aprueba y despliega sale de Oscar HQ (GET /api/equipo/permisos, caché de 60 s); falla cerrado. Sin red."""
from __future__ import annotations

from agent_lanes.people import HQ_CACHE_S, People, Person, hq_desde_entorno, load_people

OSCAR, MARIA, ANGEL = "100000001", "6000000001", "6000000002"
APR, PUB = "desarrollo.aprobar", "desarrollo.publicar"


def _gente(hq, reloj=lambda: 0.0):
    personas = [Person("maria", "María", MARIA, "coo"), Person("angel", "Angel", ANGEL, "dev")]
    return People(OSCAR, personas, hq=hq, reloj=reloj)


def _resp(**por_persona):
    return {"personas": [{"telegram_id": uid, "permisos": p} for uid, p in por_persona.items()]}


class Reloj:
    t = 0.0

    def __call__(self):
        return self.t


def test_un_cambio_de_permiso_en_hq_rige_tras_expirar_la_cache():
    reloj, estado = Reloj(), {"r": _resp(**{ANGEL: {"Píldora": [APR]}})}
    g = _gente(lambda: estado["r"], reloj)
    assert g.puede_en(ANGEL, "Píldora") and not g.puede_aprobar_produccion(ANGEL)
    estado["r"] = _resp(**{ANGEL: {"Píldora": [APR, PUB]}})
    reloj.t = HQ_CACHE_S - 1  # aún en caché: no se entera
    assert not g.puede_aprobar_produccion(ANGEL)
    reloj.t = HQ_CACHE_S  # caducada
    assert g.puede_aprobar_produccion(ANGEL)
    estado["r"] = _resp()  # le quitan todo
    reloj.t = 2 * HQ_CACHE_S
    assert not g.es_operador(ANGEL) and not g.puede_en(ANGEL, "Píldora") and not g.puede_aprobar_produccion(ANGEL)


def test_la_cache_evita_llamar_a_hq_en_cada_consulta():
    llamadas = []
    g = _gente(lambda: llamadas.append(1) or _resp(**{MARIA: {"*": [APR]}}), Reloj())
    for _ in range(5):
        g.es_operador(MARIA), g.puede_en(MARIA, "MigraTeam"), g.lookup(ANGEL)
    assert len(llamadas) == 1


def test_con_hq_caido_o_sin_clave_solo_aprueba_oscar():
    def caido():
        raise OSError("HQ no responde")
    for hq in (caido, lambda: None, lambda: {"personas": "basura"}, lambda: ["x"]):
        g = _gente(hq)
        assert g.es_operador(OSCAR) and g.puede_en(OSCAR, "MigraTeam") and g.puede_aprobar_produccion(OSCAR)
        for uid in (MARIA, ANGEL, "999"):
            assert not g.es_operador(uid) and not g.puede_en(uid, "Píldora")
            assert not g.puede_aprobar_produccion(uid) and not g.puede_aprobar_produccion(uid, "Píldora")


def test_persona_no_enlazada_en_hq_no_actua():
    g = _gente(lambda: _resp(**{"123456": {"*": [APR, PUB]}}))  # HQ conoce otro telegram_id
    assert not g.es_operador(MARIA) and not g.puede_aprobar_produccion(MARIA)


def test_angel_no_actua_en_migrateam_y_publicar_es_por_marca():
    g = _gente(lambda: _resp(**{ANGEL: {"Píldora": [APR, PUB], "MigraTeam": [APR]}, MARIA: {"*": [APR, PUB]}}))
    assert g.puede_en(ANGEL, "OscarHQ") and g.puede_aprobar_produccion(ANGEL, "OscarHQ")
    assert g.puede_en(ANGEL, "MigraTeam") and not g.puede_aprobar_produccion(ANGEL, "MigraTeam")  # aprueba, no publica
    assert g.puede_aprobar_produccion(MARIA, "MigraTeam") and g.puede_aprobar_produccion(MARIA, "Signal")
    solo_pildora = _gente(lambda: _resp(**{ANGEL: {"Píldora": [APR, PUB]}}))
    assert not solo_pildora.puede_en(ANGEL, "MigraTeam") and not solo_pildora.puede_aprobar_produccion(ANGEL, "MigraTeam")
    assert not solo_pildora.puede_en(ANGEL, None)  # destino sin marca conocida: rechazo


def test_operador_es_tener_algun_aprobar():
    g = _gente(lambda: _resp(**{MARIA: {"Signal": [PUB]}, ANGEL: {"Hub": [APR]}}))
    assert not g.es_operador(MARIA) and g.es_operador(ANGEL)


def test_load_people_pide_los_permisos_a_hq_y_el_yaml_ya_no_los_concede(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(f"people:\n  a: {{name: A, user_id: '{ANGEL}', role: dev, operador: true, brands: ['*'],\n"
                 "     permisos: [aprobar_produccion]}\n", encoding="utf-8")
    g = load_people(OSCAR, p, hq=lambda: _resp())
    assert not g.es_operador(ANGEL) and not g.puede_aprobar_produccion(ANGEL) and g.lookup(ANGEL).brands == ()
    g = load_people(OSCAR, p, hq=lambda: _resp(**{ANGEL: {"Hub": [APR, PUB]}}))
    assert g.es_operador(ANGEL) and g.puede_aprobar_produccion(ANGEL, "Hub")


def test_cliente_http_usa_x_api_key_del_entorno_y_sin_clave_no_llama():
    vistas = []

    def http(url, clave):
        vistas.append((url, clave))
        return _resp()
    env = {"OHQ_API_URL": "https://hq.example/", "OHQ_API_KEY": "k-de-prueba"}
    assert hq_desde_entorno(env, http)() == _resp()
    assert vistas == [("https://hq.example/api/equipo/permisos", "k-de-prueba")]
    assert hq_desde_entorno({"OHQ_API_URL": "https://hq.example"}, http)() is None
    assert len(vistas) == 1
