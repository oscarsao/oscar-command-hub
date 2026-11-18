import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claves  # noqa: E402

SECRETO = "VALOR-SECRETO-12345"


def montar(tmp_path):
    env = tmp_path / "x.env"
    env.write_text(f"MI_CLAVE={SECRETO}\n# c\nOTRA = {SECRETO}\n", encoding="utf-8")
    e = env.as_posix()
    inv = tmp_path / "inv.yaml"
    inv.write_text(
        f"""
env_conocidos: ["{e}"]
claves:
  - nombre: MI_CLAVE
    proveedor: Prov
    url: "https://ej.test/k"
    pasos: [uno, dos]
    cada_dias: 30
    ultima_rotacion: 2026-01-01
    quien_rota: Oscar
    depende: algo
    destinos:
      - tipo: fichero_env
        fichero: "{e}"
        variable: MI_CLAVE
      - tipo: fichero_env
        fichero: "{e}"
        variable: AUSENTE
  - nombre: SIN_FECHA
    destinos: []
""",
        encoding="utf-8",
    )
    return inv, tmp_path / "reg.jsonl"


def correr(args, inv, reg):
    sal = []
    rc = claves.main(args, out=sal.append, inv=inv, reg=reg)
    return rc, "\n".join(sal)


def test_ninguna_salida_contiene_valores(tmp_path):
    inv, reg = montar(tmp_path)
    for args in (["lista"], ["guia", "MI_CLAVE"], ["estado"], ["rotada", "MI_CLAVE", "--persona", "Oscar"]):
        rc, s = correr(args, inv, reg)
        assert rc == 0 and SECRETO not in s
    assert SECRETO not in reg.read_text(encoding="utf-8")


def test_estado_presente_falta_y_antiguedad(tmp_path):
    inv, reg = montar(tmp_path)
    _, s = correr(["estado"], inv, reg)
    assert "presente" in s and "FALTA" in s
    assert "caducada" in s and "sin fecha" in s


def test_guia_orden_y_desconocida(tmp_path):
    inv, reg = montar(tmp_path)
    _, s = correr(["guia", "mi_clave"], inv, reg)
    assert s.index("https://ej.test/k") < s.index("Pegarla") < s.index("Comprobar")
    assert correr(["guia", "NOPE"], inv, reg)[0] == 2


def test_rotada_registra_y_rejuvenece(tmp_path):
    inv, reg = montar(tmp_path)
    correr(["rotada", "MI_CLAVE", "--persona", "Ana"], inv, reg)
    assert '"persona": "Ana"' in reg.read_text(encoding="utf-8")
    _, s = correr(["estado"], inv, reg)
    assert "MI_CLAVE: 0 d · ok" in s


def test_inventario_real_valido():
    datos = claves.cargar(claves.AQUI / "inventario.yaml")
    assert datos["claves"]
    for c in datos["claves"]:
        assert c["nombre"] and isinstance(c.get("destinos"), list)
