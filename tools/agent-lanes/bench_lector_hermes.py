"""Mide y compara el lector de solo lectura de `show` (hermes.leer_show_ro) contra `hermes kanban show --json`.

Uso: py -3.12 tools/agent-lanes/bench_lector_hermes.py <copia.db> [<copia2.db> ...] [--n 200]

Trabaja SIEMPRE sobre una copia en un directorio temporal (nunca sobre la base viva ni sobre la copia original): el CLI
de Hermes puede escribir al abrir (recompute_ready en `list`). Para cada base:
  1. toma una muestra de N tarjetas repartida por toda la base;
  2. compara campo a campo el dict del lector con el de `show --json` (el CLI se apunta a la copia con HERMES_KANBAN_DB);
  3. mide el tiempo de `show` por subproceso frente al lector (una conexión por consulta) y el de `list` frente a un
     SELECT directo (informativo: `list` sigue yendo por el CLI porque recalcula `ready`, que es una escritura).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_lanes import hermes


def muestra(db: Path, n: int) -> list[str]:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM tasks ORDER BY created_at, id")]
    finally:
        conn.close()
    if len(ids) <= n:
        return ids
    paso = len(ids) / n
    return [ids[int(i * paso)] for i in range(n)]


def diferencias(a, b, ruta: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{ruta}.{k}: falta en uno" for k in set(a) ^ set(b)]
        for k in set(a) & set(b):
            out += diferencias(a[k], b[k], f"{ruta}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{ruta}: {len(a)} elementos frente a {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diferencias(x, y, f"{ruta}[{i}]")]
    return [] if a == b and type(a) is type(b) else [f"{ruta}: {a!r} != {b!r}"]


def main(argv: list[str]) -> int:
    n = 200
    if "--n" in argv:
        i = argv.index("--n")
        n = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__)
        return 2
    rc = 0
    for original in map(Path, argv):
        with tempfile.TemporaryDirectory(prefix="lector-bench-") as tmp:
            db = Path(tmp) / "kanban.db"
            shutil.copy2(original, db)
            env = {**os.environ, "HERMES_KANBAN_DB": str(db)}
            ids = muestra(db, n)
            cli, ro, malas = [], [], []
            for tid in ids:
                t0 = time.perf_counter()
                cp = subprocess.run([str(hermes.HERMES_EXE), "kanban", "--board", "default", "show", tid, "--json"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
                cli.append(time.perf_counter() - t0)
                t0 = time.perf_counter()
                nuevo = hermes.leer_show_ro(db, tid)
                ro.append(time.perf_counter() - t0)
                if cp.returncode != 0 or nuevo is None:
                    malas.append(f"{tid}: CLI rc={cp.returncode} lector={'None' if nuevo is None else 'ok'}")
                    continue
                malas += [f"{tid}{d}" for d in diferencias(json.loads(cp.stdout), nuevo)]
            t0 = time.perf_counter()
            cp = subprocess.run([str(hermes.HERMES_EXE), "kanban", "--board", "default", "list", "--json"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            t_list_cli = time.perf_counter() - t0
            t0 = time.perf_counter()
            conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            filas = conn.execute("SELECT * FROM tasks WHERE status != 'archived'").fetchall()
            conn.close()
            t_list_ro = time.perf_counter() - t0
        print(f"== {original.name}: {len(ids)} tarjetas de muestra")
        print(f"  show por subproceso : media {statistics.mean(cli) * 1000:8.1f} ms  mediana {statistics.median(cli) * 1000:8.1f} ms"
              f"  total {sum(cli):6.1f} s")
        print(f"  show lector ro      : media {statistics.mean(ro) * 1000:8.2f} ms  mediana {statistics.median(ro) * 1000:8.2f} ms"
              f"  total {sum(ro):6.2f} s  (x{sum(cli) / max(sum(ro), 1e-9):.0f} más rápido)")
        print(f"  list --json (CLI)   : {t_list_cli * 1000:8.1f} ms   SELECT directo: {t_list_ro * 1000:6.2f} ms ({len(filas)} filas)")
        print(f"  diferencias campo a campo: {len(malas)}")
        for m in malas[:15]:
            print("   ", m[:200])
        rc = rc or (1 if malas else 0)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
