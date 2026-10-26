"""F0-01: copia en caliente del kanban (tools/backup_kanban.py) sobre un HERMES_HOME de prueba; nunca el real."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "backup_kanban.py"


@pytest.fixture(scope="module")
def bk():
    spec = importlib.util.spec_from_file_location("backup_kanban", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bd(ruta: Path, filas: int, wal: bool = True) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?, ?)", [(f"t_{i}", "todo") for i in range(filas)])
    con.commit()
    return con


@pytest.fixture
def entorno(tmp_path):
    home = tmp_path / "hermes"
    conexiones = [_bd(home / "kanban.db", 5), _bd(home / "kanban" / "boards" / "oscarhq" / "kanban.db", 3),
                  _bd(home / "state.db", 2, wal=False)]
    (home / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    (home / "SOUL.md").write_text("soul\n", encoding="utf-8")
    hub = tmp_path / "hub"
    lanes = hub / "tools" / "agent-lanes"
    lanes.mkdir(parents=True)
    (lanes / "lanes.yaml").write_text("lanes: []\n", encoding="utf-8")
    (lanes / ".env").write_text("SECRETO=x\n", encoding="utf-8")
    yield home, hub, tmp_path / "copias"
    for c in conexiones:  # abiertas durante la copia: es «en caliente»
        c.close()


def test_copia_en_caliente_integridad_y_manifiesto(bk, entorno):
    home, hub, dest = entorno
    m = bk.hacer_copia(dest, home, "2026-10-25", hub=hub, oscarhq=hub / "no-existe")
    assert m["errores"] == []
    nombres = {b["nombre"]: b for b in m["bases"]}
    assert set(nombres) == {"kanban.db", "state.db", "boards/oscarhq/kanban.db"}
    assert all(b["integrity_check"] == "ok" for b in m["bases"])
    con = sqlite3.connect(dest / "2026-10-25" / "hermes" / "kanban.db")
    assert con.execute("SELECT count(*) FROM tasks").fetchone()[0] == 5
    con.close()
    assert {c["nombre"] for c in m["config"]} == {"config/lanes.yaml", "config/agent-lanes.env",
                                                   "config/hermes-config.yaml", "config/SOUL.md"}
    guardado = json.loads((dest / "2026-10-25" / "manifest.json").read_text(encoding="utf-8"))
    assert set(guardado["sha"]) == {"hub/main", "oscar-hq/master"}
    # sin --bak-junto no se escribe nada junto a los originales
    assert not list(home.glob("*.bak-*"))


def test_integridad_detecta_bd_corrupta(bk, tmp_path):
    ruta = tmp_path / "rota.db"
    _bd(ruta, 200, wal=False).close()
    datos = bytearray(ruta.read_bytes())
    datos[4096 + 100:4096 + 400] = b"\xff" * 300  # machaca la segunda página
    ruta.write_bytes(bytes(datos))
    try:
        assert bk.integridad(ruta) != "ok"
    except sqlite3.DatabaseError:
        pass  # también vale: la BD ni se abre


def test_retencion_borra_la_15a(bk, tmp_path):
    dest = tmp_path / "copias"
    dias = [f"2026-10-{d:02d}" for d in range(1, 16)]
    for d in dias:
        (dest / d).mkdir(parents=True)
    (dest / "otra-cosa").mkdir()
    borradas = bk.aplicar_retencion(dest, 14)
    assert borradas == ["2026-10-01"]
    assert sorted(p.name for p in dest.iterdir() if p.name != "otra-cosa") == dias[1:]
    assert (dest / "otra-cosa").is_dir()


def test_bak_junto(bk, entorno):
    home, hub, dest = entorno
    bk.hacer_copia(dest, home, "2026-10-25", hub=hub, oscarhq=hub, bak_junto=True)
    assert (home / "config.yaml.bak-20261025-workflow").read_text(encoding="utf-8") == "a: 1\n"
    assert (hub / "tools" / "agent-lanes" / "lanes.yaml.bak-20261025-workflow").is_file()


class _StorageFalso:
    def __init__(self):
        self.objetos = {f"2026-10-{d:02d}/manifest.json": b"" for d in range(1, 15)}

    def subir(self, nombre, ruta):
        self.objetos[nombre] = ruta.read_bytes()

    def listar(self, prefijo=""):
        hijos = {}
        for k in self.objetos:
            if k.startswith(prefijo):
                resto = k[len(prefijo):]
                cab, _, cola = resto.partition("/")
                hijos[cab] = {"name": cab, "id": None if cola else "x"}
        return list(hijos.values())

    def borrar_dia(self, dia):
        for k in [k for k in self.objetos if k.startswith(dia + "/")]:
            del self.objetos[k]


def test_subida_sin_env_y_con_retencion_remota(bk, entorno):
    home, hub, dest = entorno
    bk.hacer_copia(dest, home, "2026-10-25", hub=hub, oscarhq=hub)
    st = _StorageFalso()
    n, sobran = bk.subir_copia(st, dest / "2026-10-25", 14)
    subidos = [k for k in st.objetos if k.startswith("2026-10-25/")]
    assert n == len(subidos) and n > 0
    assert not any(k.endswith(".env") for k in st.objetos)  # el .env no sale del PC
    assert not any(k.startswith("2026-10-25/config/") for k in st.objetos)  # ni ninguna config
    assert "2026-10-25/manifest.json" in st.objetos and "2026-10-25/hermes/kanban.db" in st.objetos
    assert sobran == ["2026-10-01"]
    assert not any(k.startswith("2026-10-01/") for k in st.objetos)


def test_main_sin_credenciales_no_sube(bk, entorno, monkeypatch, capsys):
    home, hub, dest = entorno
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    codigo = bk.main(["--dest", str(dest), "--hermes-home", str(home), "--oscarhq-repo", str(hub),
                      "--subir", "--fecha", "2026-10-25"])
    assert codigo == 3
    assert "no se sube" in capsys.readouterr().err
    assert (dest / "2026-10-25" / "manifest.json").is_file()


def test_env_file_lee_solo_las_dos_claves(bk, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    lineas = ["# x", 'SUPABASE_URL="https://ejemplo.supabase.co"', "SUPABASE_SERVICE_ROLE_KEY=clave",
              "OTRA=no", "export SUPABASE_ANON_KEY=tampoco"]
    env.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    assert bk.leer_env_file(env) == {"SUPABASE_URL": "https://ejemplo.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "clave"}
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    st = bk.Storage.desde_entorno("hub-backups", bk.leer_env_file(env))
    assert st.base == "https://ejemplo.supabase.co/storage/v1" and st.clave == "clave"


def test_bak_junto_retiene_14_y_no_toca_otros_bak(bk, entorno):
    home, hub, dest = entorno
    for d in range(1, 16):
        (home / f"config.yaml.bak-202610{d:02d}-workflow").write_text("v", encoding="utf-8")
    (home / "config.yaml.bak-20260927-dashboard").write_text("manual", encoding="utf-8")
    (home / "SOUL.md.bak-20261001-workflow").write_text("s", encoding="utf-8")
    bk.hacer_copia(dest, home, "2026-10-25", hub=hub, oscarhq=hub, bak_junto=True, retencion=14)
    wf = sorted(p.name for p in home.glob("config.yaml.bak-*-workflow"))
    assert len(wf) == 14 and wf[-1] == "config.yaml.bak-20261025-workflow"
    assert "config.yaml.bak-20261002-workflow" not in wf  # las dos más antiguas fuera (16 - 14)
    assert (home / "config.yaml.bak-20260927-dashboard").is_file()
    assert (home / "SOUL.md.bak-20261001-workflow").is_file()  # retención por fichero, no global
