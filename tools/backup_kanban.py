"""Copia en caliente del kanban de Hermes y de la config del hub (plan del hub, tarea F0-01).

Qué hace en cada ejecución (una carpeta por día, ``<dest>/<AAAA-MM-DD>/``):

1. ``VACUUM INTO`` de ``kanban*.db`` y ``state.db`` de ``HERMES_HOME`` y de cada ``kanban/boards/<tablero>/kanban.db``.
   La BD de origen se abre en solo lectura: no se toca ni se bloquea más que una lectura normal.
2. ``PRAGMA integrity_check`` de cada copia; si alguna no da ``ok`` el script sale con código 1.
3. Copia de los ficheros de config (``lanes.yaml``, ``choques.yaml``, ``agent-lanes/.env``, ``hermes/config.yaml``,
   ``SOUL.md``) dentro de la carpeta del día; con ``--bak-junto`` además deja ``<fichero>.bak-<AAAAMMDD>-workflow``
   al lado del original (lo que pide el plan antes de cada cambio de config).
4. ``manifest.json`` con tamaño, sha256 e integridad de cada fichero y el SHA de ``main`` del hub y ``master`` de oscar-hq.
5. Retención: conserva las 14 carpetas de día más recientes y borra el resto (solo carpetas con nombre de fecha).
6. Con ``--subir``: sube la carpeta a Supabase Storage (bucket ``hub-backups``, privado) con ``SUPABASE_URL`` y
   ``SUPABASE_SERVICE_ROLE_KEY`` del entorno o de ``--env-file`` (un .env ya existente, p. ej. el de oscar-hq; solo se
   leen esas dos claves; no crea credenciales nuevas) y aplica la misma retención en el bucket. Solo se suben las BD
   (``hermes/``) y ``manifest.json``: la config (``config/``, con tokens y secretos) NUNCA sale del PC.

Uso::

    py -3.12 tools/backup_kanban.py                       # copia local en ~/hub-backups
    py -3.12 tools/backup_kanban.py --subir --env-file C:/Users/oscar/dev/oscar-hq/.env
    py -3.12 tools/backup_kanban.py --dest D:/copias --retencion 14 --bak-junto

Restaurar: copiar ``kanban.db`` / ``state.db`` de la carpeta del día a un ``HERMES_HOME`` temporal y comprobar
``hermes kanban --board oscarhq list``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
RETENCION = 14
BUCKET = "hub-backups"
_DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Lo único que sale del PC: las BD y el manifiesto. config/ lleva tokens y secretos.
SUBIR_CARPETAS = ("hermes",)
SUBIR_FICHEROS = ("manifest.json",)
_BAK = re.compile(r"\.bak-\d{8}-workflow$")
CLAVES_STORAGE = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "hermes"


def bases_sqlite(home: Path) -> list[tuple[str, Path]]:
    """(nombre relativo en la copia, ruta de origen) de cada BD a copiar."""
    out: list[tuple[str, Path]] = []
    for p in sorted(home.glob("kanban*.db")):
        out.append((p.name, p))
    st = home / "state.db"
    if st.is_file():
        out.append(("state.db", st))
    boards = home / "kanban" / "boards"
    if boards.is_dir():
        for p in sorted(boards.glob("*/kanban*.db")):
            out.append((f"boards/{p.parent.name}/{p.name}", p))
    return out


def ficheros_config(home: Path, hub: Path = HUB) -> list[tuple[str, Path]]:
    lanes = hub / "tools" / "agent-lanes"
    cands = [
        ("config/lanes.yaml", lanes / "lanes.yaml"),
        ("config/choques.yaml", lanes / "choques.yaml"),
        ("config/agent-lanes.env", lanes / ".env"),
        ("config/hermes-config.yaml", home / "config.yaml"),
        ("config/SOUL.md", home / "SOUL.md"),
    ]
    return [(n, p) for n, p in cands if p.is_file()]


def vacuum_into(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()
    uri = "file:" + urllib.parse.quote(origen.resolve().as_posix()) + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        con.execute("VACUUM INTO ?", (str(destino),))
    finally:
        con.close()


def integridad(ruta: Path) -> str:
    con = sqlite3.connect(f"file:{urllib.parse.quote(ruta.resolve().as_posix())}?mode=ro", uri=True)
    try:
        filas = con.execute("PRAGMA integrity_check").fetchall()
    finally:
        con.close()
    return "ok" if filas == [("ok",)] else "; ".join(str(f[0]) for f in filas)[:500]


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def sha_rama(repo: Path, rama: str) -> str | None:
    for ref in (f"origin/{rama}", rama):
        try:
            r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def leer_env_file(ruta: Path, claves: tuple[str, ...] = CLAVES_STORAGE) -> dict[str, str]:
    """Lee SOLO ``claves`` de un fichero dotenv (no ejecuta nada ni toca el resto)."""
    out: dict[str, str] = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, _, v = linea.removeprefix("export ").partition("=")
        k, v = k.strip(), v.strip()
        if k in claves and v:
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k] = v
    return out


def retener_bak_junto(origen: Path, conservar: int = RETENCION) -> list[str]:
    """Borra los ``<fichero>.bak-AAAAMMDD-workflow`` más antiguos de ``origen`` (nunca otros .bak)."""
    baks = sorted((p for p in origen.parent.glob(origen.name + ".bak-*-workflow")
                   if (m := _BAK.search(p.name)) and p.name[:m.start()] == origen.name), key=lambda p: p.name)
    sobran = baks[:-conservar] if conservar > 0 else baks
    for p in sobran:
        p.unlink()
    return [p.name for p in sobran]


def aplicar_retencion(dest: Path, conservar: int = RETENCION) -> list[str]:
    """Borra las carpetas de día más antiguas; devuelve las borradas."""
    dias = sorted((p for p in dest.iterdir() if p.is_dir() and _DIA.match(p.name)), key=lambda p: p.name)
    sobran = dias[:-conservar] if conservar > 0 else dias
    for p in sobran:
        shutil.rmtree(p)
    return [p.name for p in sobran]


def hacer_copia(dest: Path, home: Path, hoy: str, *, hub: Path = HUB, oscarhq: Path | None = None,
                bak_junto: bool = False, retencion: int = RETENCION) -> dict:
    carpeta = dest / hoy
    carpeta.mkdir(parents=True, exist_ok=True)
    manifiesto: dict = {"fecha": hoy, "creado": dt.datetime.now().isoformat(timespec="seconds"),
                        "hermes_home": str(home), "bases": [], "config": [], "errores": []}
    for nombre, origen in bases_sqlite(home):
        destino = carpeta / "hermes" / nombre
        try:
            vacuum_into(origen, destino)
            estado = integridad(destino)
        except sqlite3.Error as e:
            manifiesto["errores"].append(f"{nombre}: {e}")
            continue
        manifiesto["bases"].append({"nombre": nombre, "bytes": destino.stat().st_size,
                                    "sha256": sha256(destino), "integrity_check": estado})
        if estado != "ok":
            manifiesto["errores"].append(f"{nombre}: integrity_check={estado}")
    sufijo = f".bak-{hoy.replace('-', '')}-workflow"
    for nombre, origen in ficheros_config(home, hub):
        destino = carpeta / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        manifiesto["config"].append({"nombre": nombre, "bytes": destino.stat().st_size, "sha256": sha256(destino)})
        if bak_junto:
            junto = origen.with_name(origen.name + sufijo)
            if not junto.exists():
                shutil.copy2(origen, junto)
            retener_bak_junto(origen, retencion)
    oscarhq = oscarhq or (Path.home() / "dev" / "oscar-hq")
    manifiesto["sha"] = {"hub/main": sha_rama(hub, "main"), "oscar-hq/master": sha_rama(oscarhq, "master")}
    (carpeta / "manifest.json").write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8", newline="\n")
    return manifiesto


# --- Supabase Storage (opcional) -------------------------------------------------------------------------------

class Storage:
    def __init__(self, url: str, clave: str, bucket: str = BUCKET):
        self.base = url.rstrip("/") + "/storage/v1"
        self.clave = clave
        self.bucket = bucket

    @classmethod
    def desde_entorno(cls, bucket: str = BUCKET, extra: dict[str, str] | None = None) -> "Storage | None":
        fuente = {**(extra or {}), **{k: os.environ[k] for k in CLAVES_STORAGE if os.environ.get(k)}}
        url, clave = fuente.get("SUPABASE_URL"), fuente.get("SUPABASE_SERVICE_ROLE_KEY")
        return cls(url, clave, bucket) if url and clave else None

    def _peticion(self, metodo: str, ruta: str, datos: bytes | None = None, tipo: str = "application/json",
                  extra: dict | None = None):
        cab = {"Authorization": f"Bearer {self.clave}", "apikey": self.clave, "Content-Type": tipo}
        cab.update(extra or {})
        req = urllib.request.Request(self.base + ruta, data=datos, method=metodo, headers=cab)
        with urllib.request.urlopen(req, timeout=120) as r:
            cuerpo = r.read()
        return json.loads(cuerpo) if cuerpo else None

    def subir(self, nombre: str, ruta: Path) -> None:
        self._peticion("POST", f"/object/{self.bucket}/{urllib.parse.quote(nombre)}", ruta.read_bytes(),
                       "application/octet-stream", {"x-upsert": "true"})

    def listar(self, prefijo: str = "") -> list[dict]:
        cuerpo = json.dumps({"prefix": prefijo, "limit": 1000, "offset": 0}).encode()
        return self._peticion("POST", f"/object/list/{self.bucket}", cuerpo) or []

    def borrar(self, nombres: list[str]) -> None:
        if nombres:
            self._peticion("DELETE", f"/object/{self.bucket}", json.dumps({"prefixes": nombres}).encode())

    def borrar_dia(self, dia: str) -> None:
        """Borra todos los objetos bajo ``<dia>/`` (la API no borra carpetas: hay que listar hasta el fondo)."""
        pendientes, objetos = [dia], []
        while pendientes:
            pref = pendientes.pop()
            for e in self.listar(pref + "/"):
                ruta = f"{pref}/{e['name']}"
                (objetos if e.get("id") else pendientes).append(ruta)
        self.borrar(objetos)


def subir_copia(st: Storage, carpeta: Path, conservar: int = RETENCION) -> tuple[int, list[str]]:
    n = 0
    candidatos = [carpeta / f for f in SUBIR_FICHEROS]
    for sub in SUBIR_CARPETAS:
        candidatos += sorted((carpeta / sub).rglob("*"))
    for p in candidatos:
        if p.is_file():
            st.subir(f"{carpeta.name}/{p.relative_to(carpeta).as_posix()}", p)
            n += 1
    dias = sorted(e["name"] for e in st.listar("") if not e.get("id") and _DIA.match(e["name"]))
    sobran = dias[:-conservar] if conservar > 0 else dias
    for d in sobran:
        st.borrar_dia(d)
    return n, sobran


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dest", type=Path, default=Path.home() / "hub-backups")
    ap.add_argument("--hermes-home", type=Path, default=None)
    ap.add_argument("--oscarhq-repo", type=Path, default=None)
    ap.add_argument("--retencion", type=int, default=RETENCION)
    ap.add_argument("--subir", action="store_true", help="subir a Supabase Storage (bucket hub-backups)")
    ap.add_argument("--bucket", default=BUCKET)
    ap.add_argument("--env-file", type=Path, default=None,
                    help="dotenv existente del que leer SOLO SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY")
    ap.add_argument("--bak-junto", action="store_true", help="dejar <fichero>.bak-<fecha>-workflow junto a cada config")
    ap.add_argument("--fecha", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    home = a.hermes_home or hermes_home()
    if not home.is_dir():
        print(f"ERROR: no existe HERMES_HOME {home}", file=sys.stderr)
        return 2
    hoy = a.fecha or dt.date.today().isoformat()
    a.dest.mkdir(parents=True, exist_ok=True)
    m = hacer_copia(a.dest, home, hoy, oscarhq=a.oscarhq_repo, bak_junto=a.bak_junto, retencion=a.retencion)
    borradas = aplicar_retencion(a.dest, a.retencion)
    print(f"copia {a.dest / hoy}: {len(m['bases'])} BD, {len(m['config'])} config, "
          f"hub/main={m['sha']['hub/main']}, oscar-hq/master={m['sha']['oscar-hq/master']}")
    if borradas:
        print("retención local: borradas " + ", ".join(borradas))
    codigo = 1 if m["errores"] or not m["bases"] else 0
    for e in m["errores"]:
        print(f"ERROR: {e}", file=sys.stderr)
    if a.subir:
        extra: dict[str, str] = {}
        if a.env_file:
            try:
                extra = leer_env_file(a.env_file)
            except OSError as e:
                print(f"AVISO: no se pudo leer --env-file: {e}", file=sys.stderr)
        st = Storage.desde_entorno(a.bucket, extra)
        if st is None:
            print("AVISO: sin SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY (entorno ni --env-file); no se sube", file=sys.stderr)
            return codigo or 3
        try:
            n, sobran = subir_copia(st, a.dest / hoy, a.retencion)
        except (urllib.error.URLError, OSError) as e:
            print(f"ERROR subiendo a Storage: {e}", file=sys.stderr)
            return codigo or 4
        print(f"subidos {n} ficheros a {a.bucket}/{hoy}" + (f"; retención remota: {', '.join(sobran)}" if sobran else ""))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
