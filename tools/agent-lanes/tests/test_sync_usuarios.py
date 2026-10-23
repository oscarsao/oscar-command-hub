"""sync_usuarios: filas de ohq_user_external_changes -> people.yaml, TELEGRAM_ALLOWED_CHATS y lista de Hermes."""
from __future__ import annotations

import io
import json

from agent_lanes import sync_usuarios as su
from agent_lanes.people import load_people

OWNER = "6744452215"
YAML = """# cabecera
people:
  # maria
  maria:
    name: María
    user_id: "111"   # María
    role: coo
    brands: [Píldora]
    can: []

  mauredy:
    name: Mauredy
    user_id: null   # PENDIENTE
    role: dev
    brands: [MigraTeam]
    can: []
"""


def fila(i, accion, destino="people_yaml", **kw):
    return {"id": i, "action": accion, "destination": destino, "person_key": "mauredy", "telegram_user_id": "222", **kw}


def aplicar(filas, texto=YAML):
    return su.aplicar_people(texto, filas)


def test_link_rellena_user_id_y_load_people_lo_ve(tmp_path):
    nuevo, ok, man = aplicar([fila("c1", "link")])
    assert ok == ["c1"] and not man
    assert 'user_id: "222"   # Oscar HQ c1' in nuevo and "# cabecera" in nuevo and "# maria" in nuevo
    f = tmp_path / "p.yaml"
    f.write_text(nuevo, encoding="utf-8")
    assert load_people(OWNER, f).lookup("222").name == "Mauredy"


def test_suspender_quita_acceso_y_reactivar_lo_devuelve(tmp_path):
    f = tmp_path / "p.yaml"
    sus, ok, _ = aplicar([fila("c2", "suspend", person_key="maria", telegram_user_id="111")])
    f.write_text(sus, encoding="utf-8")
    assert ok == ["c2"] and "suspendido: true" in sus and load_people(OWNER, f).lookup("111") is None
    rea, _, _ = aplicar([fila("c3", "reactivate", person_key="maria", telegram_user_id="111")], sus)
    f.write_text(rea, encoding="utf-8")
    assert "suspendido" not in rea and load_people(OWNER, f).lookup("111").name == "María"


def test_desvincular_pone_null_y_eliminar_conserva_el_bloque(tmp_path):
    f = tmp_path / "p.yaml"
    unl, _, _ = aplicar([fila("c4", "unlink", person_key="maria", telegram_user_id="111")])
    assert "user_id: null   # Oscar HQ c4" in unl
    f.write_text(unl, encoding="utf-8")
    assert load_people(OWNER, f).lookup("111") is None
    borrado, ok, _ = aplicar([fila("c5", "delete", person_key="maria", telegram_user_id="111")])
    assert ok == ["c5"] and "mauredy:" in borrado and "# cabecera" in borrado
    # No se borra en silencio: el bloque sigue (con su rol y marcas) pero sin acceso y con la nota.
    assert "maria:" in borrado and "role: coo" in borrado and "brands: [Píldora]" in borrado
    assert "user_id: null   # Oscar HQ c5: eliminado en Oscar HQ" in borrado
    f.write_text(borrado, encoding="utf-8")
    assert load_people(OWNER, f).lookup("111") is None


def test_eliminar_conserva_el_operador(tmp_path):
    texto = YAML.replace("    can: []\n\n  mauredy", "    can: []\n    operador: true\n\n  mauredy")
    assert "operador: true" in texto
    borrado, ok, _ = aplicar([fila("c6", "delete", person_key="maria", telegram_user_id="111")], texto)
    assert ok == ["c6"] and "operador: true" in borrado
    f = tmp_path / "p.yaml"
    f.write_text(borrado, encoding="utf-8")
    assert load_people(OWNER, f).lookup("111") is None  # operador conservado pero sin user_id no entra


def test_link_con_otro_user_id_va_a_revision_manual_y_no_sobrescribe():
    # maria ya tiene "111"; una fila con su person_key y otro Telegram no la cambia de dueño.
    nuevo, ok, man = aplicar([fila("c7", "link", person_key="maria", telegram_user_id="999")])
    assert ok == [] and nuevo == YAML
    assert len(man) == 1 and "c7" in man[0] and "111" in man[0] and "999" in man[0] and "no se sobrescribe" in man[0]


def test_link_con_el_mismo_user_id_o_sin_user_id_previo_si_se_aplica():
    _, ok, man = aplicar([fila("c8", "link", person_key="maria", telegram_user_id="111")])
    assert ok == ["c8"] and not man
    _, ok2, man2 = aplicar([fila("c9", "link")])  # mauredy tiene user_id: null
    assert ok2 == ["c9"] and not man2


def test_quitar_acceso_a_quien_no_esta_en_el_yaml_no_se_da_por_aplicado():
    for accion in ("suspend", "unlink", "delete"):
        nuevo, ok, man = aplicar([fila(f"x-{accion}", accion, person_key="fantasma", telegram_user_id="555")])
        assert ok == [] and nuevo == YAML
        assert len(man) == 1 and f"x-{accion}" in man[0] and "no figura en people.yaml" in man[0]
        assert "fantasma" in man[0] and "Telegram ID" in man[0]


def test_planificar_deja_las_no_aplicadas_fuera_de_las_filas_resolubles():
    plan = su.planificar([fila("c10", "suspend", person_key="fantasma", telegram_user_id="555"),
                          fila("c11", "suspend", person_key="maria", telegram_user_id="111")], YAML)
    assert plan.filas["people_yaml"] == ["c11"]
    assert any("c10" in m for m in plan.manual)


def test_link_de_persona_nueva_exige_rol_y_si_no_va_a_manual(tmp_path):
    f = tmp_path / "p.yaml"
    nueva = fila("c6", "link", person_key="Lucía Gómez", name="Lucía Gómez", telegram_user_id="333", role="cmo",
                 brands=["Píldora"])
    nuevo, ok, man = aplicar([nueva])
    f.write_text(nuevo, encoding="utf-8")
    assert ok == ["c6"] and not man and load_people(OWNER, f).lookup("333").role == "cmo"
    _, ok2, man2 = aplicar([dict(nueva, id="c7", role="")])
    assert ok2 == [] and "c7" in man2[0]


def test_conserva_crlf():
    nuevo, _, _ = aplicar([fila("c8", "link")], YAML.replace("\n", "\r\n"))
    assert "\r\n" in nuevo and "\n" not in nuevo.replace("\r\n", "")


def test_env_anade_y_quita_sin_duplicar():
    valor, ok, man = su.aplicar_env("1,2", [fila("e1", "link", "carriles_env", telegram_user_id="3"),
                                            fila("e2", "link", "carriles_env", telegram_user_id="2"),
                                            fila("e3", "suspend", "carriles_env", telegram_user_id="1"),
                                            fila("e4", "delete", "carriles_env", telegram_user_id="")])
    assert valor == "2,3" and ok == ["e1", "e2", "e3"] and "e4" in man[0]


def test_planificar_separa_destinos_y_rechaza_filas_raras():
    filas = [fila("p1", "link"), fila("v1", "link", "carriles_env"), fila("h1", "suspend", "hermes", name="Mauredy"),
             {"id": "x1", "action": "explota", "destination": "people_yaml", "person_key": "a"},
             {"id": "x2", "action": "link", "destination": "hermes"}, "basura"]
    plan = su.planificar(filas, YAML, f"A=1\n{su.ENV_VAR}=9\n")
    assert plan.filas == {"people_yaml": ["p1"], "carriles_env": ["v1"], "hermes": ["h1"]}
    assert plan.env_line == f"{su.ENV_VAR}=9,222" and "QUITAR" in plan.hermes[0]
    assert plan.ids_manual == ["x1", "x2"] and len(plan.manual) == 3
    txt = su.informe(plan)
    assert "NO edita .env" in txt and "nunca toca ~/.hermes" in txt and "Revisión manual" in txt


class FakeHttp:
    def __init__(self, filas, falla=()):
        self.filas, self.falla, self.llamadas, self.cuerpos = filas, set(falla), [], []

    def __call__(self, metodo, url, clave, cuerpo):
        self.llamadas.append((metodo, url, clave))
        self.cuerpos.append(cuerpo)
        if metodo == "POST" and any(url.split("/")[-1] == i for i in self.falla):
            raise OSError("red")
        return {"cambios": self.filas} if metodo == "GET" else {}


def test_main_plan_escribe_solo_people_y_resolver_marca_filas(tmp_path):
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    http = FakeHttp([fila("c1", "link"), fila("h1", "link", "hermes")], falla=["r2"])
    api = su.ApiOhq("https://ohq.example/", "k", http)
    out = io.StringIO()
    assert su.main(["plan", "--escribir"], api, p, out) == 0
    assert 'user_id: "222"' in p.read_text(encoding="utf-8") and "AÑADIR" in out.getvalue()
    assert http.llamadas[0][:2] == ("GET", "https://ohq.example/api/admin/users/cambios-externos")
    out = io.StringIO()
    assert su.main(["resolver", "r1", "r2"], api, p, out) == 1
    assert "resueltas: r1" in out.getvalue() and "fallidas: r2" in out.getvalue()
    assert http.llamadas[1] == ("POST", "https://ohq.example/api/admin/users/cambios-externos/r1", "k")
    assert http.cuerpos[1] == {"estado": "aplicado"}


def test_plan_desde_fichero_sin_escribir_no_toca_people(tmp_path):
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    j = tmp_path / "filas.json"
    j.write_text(json.dumps([fila("c1", "link")]), encoding="utf-8")
    assert su.main(["plan", "--desde-fichero", str(j)], None, p, io.StringIO()) == 0
    assert p.read_text(encoding="utf-8") == YAML


def test_api_sin_entorno_falla_claro():
    try:
        su.ApiOhq.desde_entorno({})
    except SystemExit as e:
        assert "OHQ_API_URL" in str(e)
    else:
        raise AssertionError("debía fallar")


def test_normaliza_filas_reales_de_oscar_hq():
    real = {"id": "u1", "user_id": "x", "accion": "suspender", "destinos": ["hermes", "people_yaml", "carriles_env"],
            "detalle": {"telegram_id": 222, "nombre": "Mauredy", "rol": "dev"}, "estado": "pendiente"}
    rara = {"id": "u2", "accion": "teletransportar", "destinos": ["hermes"], "detalle": {"telegram_id": 5}}
    filas = su.normalizar([real, rara, fila("c1", "link")])
    assert [(f["id"], f["destination"], f["action"]) for f in filas] == [
        ("u1", "hermes", "suspend"), ("u1", "people_yaml", "suspend"), ("u1", "carriles_env", "suspend"),
        ("u2", "hermes", None), ("c1", "people_yaml", "link")]
    assert filas[0]["telegram_user_id"] == 222 and filas[0]["name"] == "Mauredy"
    plan = su.planificar(filas, YAML, f"{su.ENV_VAR}=1,222\n")
    assert plan.env_line == f"{su.ENV_VAR}=1" and "QUITAR" in plan.hermes[0] and "u2" in plan.ids_manual


def test_api_real_devuelve_filas_normalizadas():
    real = {"id": "u1", "accion": "reactivar", "destinos": ["carriles_env"], "detalle": {"telegram_id": 9}}
    api = su.ApiOhq("https://ohq.example", "k", FakeHttp([real]))
    assert api.pendientes() == [{"id": "u1", "action": "reactivate", "name": None, "telegram_user_id": 9,
                                 "role": None, "destination": "carriles_env"}]
