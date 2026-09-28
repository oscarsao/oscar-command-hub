"""Aplica la reorganización del kanban de Hermes del 28-09 (plan aprobado en bloque por Oscar).

Plan:   references/2026-09-28-plan-reorganizacion-kanban.md  (explicación, tabla y preguntas)
Datos:  references/2026-09-28-plan-reorganizacion-kanban.json (acciones)

Uso:
    py -3.12 tools/agent-lanes/aplicar_reorganizacion.py                 # dry-run (por defecto): solo lecturas
    py -3.12 tools/agent-lanes/aplicar_reorganizacion.py --apply         # aplica
    py -3.12 tools/agent-lanes/aplicar_reorganizacion.py --apply --con-unlink   # + desenlaza los paraguas antiguos

Garantías:
- Dry-run por defecto: solo `list`/`show`; nunca `create` ni ninguna escritura.
- Idempotente: cada acción compara el estado actual antes de escribir; lo ya hecho se salta ("ya").
  Las épicas nuevas se crean con --idempotency-key (y antes se buscan por título).
- Deriva: si el valor actual no es ni el `desde` del plan ni el destino, se salta con DERIVA (el tablero está
  vivo); --forzar lo ignora.
- Nunca toca tarjetas running/review (asignar, archivar) y nunca asigna un carril a una tarjeta de un tablero
  que ese carril no lee (carril_tablero del JSON).
- En Hermes `link <padre> <hijo>` hace que el padre BLOQUEE al hijo: cada épica es HIJA de sus tarjetas.
- Sin --con-unlink no se toca nada de los 4 paraguas antiguos (título, dueño, enlaces): siguen como hoy.
- Log en .state/reorganizacion/<fecha>-<modo>.log (además de stdout).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUB = HERE.parent.parent
DEFAULT_PLAN = HUB / "references" / "2026-09-28-plan-reorganizacion-kanban.json"
DEFAULT_HERMES = "C:/Users/oscar/AppData/Local/hermes/bin/hermes.exe"
AUTHOR = "claude-coordinador"
BUSY = ("running", "review")
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


class Hermes:
    def __init__(self, exe: str, apply: bool, log):
        self.exe, self.apply, self.log = exe, apply, log
        self._show: dict[tuple[str, str], dict] = {}

    def _run(self, board: str, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([self.exe, "kanban", "--board", board, *args], capture_output=True, env=ENV, check=False,
                              text=True, encoding="utf-8", errors="replace")

    def read(self, board: str, args: list[str]):
        r = self._run(board, args)
        if r.returncode != 0:
            raise RuntimeError(f"hermes {' '.join(args)} (board {board}) falló: {r.stderr.strip()[:300]}")
        return json.loads(r.stdout)

    def show(self, board: str, tid: str) -> dict:
        key = (board, tid)
        if key not in self._show:
            self._show[key] = self.read(board, ["show", tid, "--json"])
        return self._show[key]

    def list_titles(self, board: str) -> dict[str, str]:
        return {t["title"]: t["id"] for t in self.read(board, ["list", "--json"])}

    def write(self, board: str, args: list[str], touched: tuple[str, ...] = ()) -> str:
        """Escritura: en dry-run solo se registra. Devuelve stdout."""
        pretty = "hermes kanban --board " + board + " " + " ".join(_q(a) for a in args)
        if not self.apply:
            self.log(f"  [DRY] {pretty}")
            return ""
        r = self._run(board, args)
        for tid in touched:
            self._show.pop((board, tid), None)
        if r.returncode != 0:
            raise RuntimeError(f"{pretty} -> exit {r.returncode}: {r.stderr.strip()[:300]}")
        self.log(f"  [OK] {pretty}")
        return r.stdout


def _q(a: str) -> str:
    return a if re.fullmatch(r"[\w@:./=+-]+", a) else '"' + a.replace('"', '\\"')[:120] + ('…"' if len(a) > 120 else '"')


def _parent_ids(show: dict) -> list[str]:
    return [p if isinstance(p, str) else (p or {}).get("id") for p in show.get("parents") or []]


class Aplicador:
    def __init__(self, plan: dict, h: Hermes, con_unlink: bool, forzar: bool, log):
        self.plan, self.h, self.con_unlink, self.forzar, self.log = plan, h, con_unlink, forzar, log
        self.lane_board: dict[str, str] = plan["carril_tablero"]
        self.epic_ids: dict[str, str] = {}
        self.stats: dict[str, int] = {}
        self._pending_unlinks: set[tuple[str, str]] = set()  # solo dry-run: unlinks "hechos" en seco

    def _count(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1

    def skip(self, why: str) -> None:
        self.log(f"  - salto: {why}")
        self._count("DERIVA" if why.startswith("DERIVA") else "saltadas")

    def done(self) -> None:
        self._count("aplicadas" if self.h.apply else "planificadas")

    # ---- épicas
    def resolve_epics(self) -> None:
        titles: dict[str, dict[str, str]] = {}
        for key, e in self.plan["epicas"].items():
            if e["reutiliza"]:
                self.epic_ids[key] = e["reutiliza"]
                continue
            b = e["board"]
            titles.setdefault(b, self.h.list_titles(b))
            if e["titulo"] in titles[b]:
                self.epic_ids[key] = titles[b][e["titulo"]]

    def epic(self, ref: str) -> str | None:
        return self.epic_ids.get(ref.split(":", 1)[1]) if ref.startswith("@epica:") else ref

    def a_crear_epica(self, a: dict) -> None:
        if a["epica"] in self.epic_ids:
            return self.skip(f"ya existe ({self.epic_ids[a['epica']]})")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="\n") as f:
            f.write(a["cuerpo"])
            body = f.name
        try:
            out = self.h.write(a["board"], ["create", a["titulo"], "--body-file", body, "--assignee", a["asignado"],
                                            "--priority", str(a["prioridad"]), "--idempotency-key", a["idempotency_key"],
                                            "--created-by", AUTHOR, "--json"])
        finally:
            os.unlink(body)
        if self.h.apply:
            m = re.search(r"t_[0-9a-f]{8}", out)
            if not m:
                raise RuntimeError(f"create sin id en la salida: {out[:200]}")
            self.epic_ids[a["epica"]] = m.group(0)
            self.log(f"  épica {a['epica']} = {m.group(0)}")
        self.done()

    # ---- campos
    def _gated(self, a: dict) -> bool:
        """Acciones sobre los paraguas antiguos que solo tienen sentido si se desenlazan (P1 = A)."""
        if a.get("requiere") == "--con-unlink" and not self.con_unlink:
            self.skip("requiere --con-unlink")
            return True
        return False

    def _field(self, a: dict, field: str, cli: list[str]) -> None:
        if self._gated(a):
            return
        t = self.h.show(a["board"], a["id"])["task"]
        cur = t.get(field)
        if t.get("status") == "archived":
            return self.skip("archivada")
        if cur == a["hacia"]:
            return self.skip("ya")
        if cur != a["desde"] and not self.forzar:
            return self.skip(f"DERIVA {field}: plan={a['desde']!r} actual={cur!r}")
        self.h.write(a["board"], cli, touched=(a["id"],))
        self.done()

    def a_titulo(self, a: dict) -> None:
        self._field(a, "title", ["edit", a["id"], "--title", a["hacia"]])

    def a_prioridad(self, a: dict) -> None:
        self._field(a, "priority", ["edit", a["id"], "--priority", str(a["hacia"])])

    def a_asignar(self, a: dict) -> None:
        if self._gated(a):
            return
        lane_b = self.lane_board.get(a["hacia"])
        if lane_b and lane_b != a["board"]:
            return self.skip(f"el carril {a['hacia']} lee el tablero {lane_b}, no {a['board']}")
        t = self.h.show(a["board"], a["id"])["task"]
        if t.get("status") in BUSY or t.get("status") == "archived":
            return self.skip(f"estado {t.get('status')}")
        if t.get("assignee") == a["hacia"]:
            return self.skip("ya")
        if t.get("assignee") != a["desde"] and not self.forzar:
            return self.skip(f"DERIVA assignee: plan={a['desde']!r} actual={t.get('assignee')!r}")
        if a.get("lanza_trabajo"):
            self.log("  ⚡ al quedar ready, el carril la reclamará en la siguiente pasada del runner")
        self.h.write(a["board"], ["assign", a["id"], a["hacia"]], touched=(a["id"],))
        self.done()

    # ---- enlaces
    def a_desenlazar(self, a: dict) -> None:
        if not self.con_unlink:
            return self.skip("requiere --con-unlink")
        if a["padre"] not in _parent_ids(self.h.show(a["board"], a["hijo"])):
            return self.skip("ya")
        self.h.write(a["board"], ["unlink", a["padre"], a["hijo"]], touched=(a["padre"], a["hijo"]))
        if not self.h.apply:
            self._pending_unlinks.add((a["padre"], a["hijo"]))
        self.done()

    def a_enlazar(self, a: dict) -> None:
        if a.get("requiere") == "--con-unlink" and not self.con_unlink:
            return self.skip("requiere --con-unlink (épica reutilizada de un paraguas antiguo)")
        hijo = self.epic(a["hijo"])
        if hijo is None:
            if self.h.apply:
                raise RuntimeError(f"épica {a['hijo']} sin id")
            self.log(f"  [DRY] hermes kanban --board {a['board']} link {a['padre']} <{a['hijo']}>")
            return self.done()
        sh = self.h.show(a["board"], hijo)
        if a["padre"] in _parent_ids(sh):
            return self.skip("ya")
        if sh["task"].get("status") == "running":
            return self.skip("el hijo está running")
        if hijo in _parent_ids(self.h.show(a["board"], a["padre"])) and (hijo, a["padre"]) not in self._pending_unlinks:
            return self.skip(f"ciclo: {hijo} aún es padre de {a['padre']} (desenlazar primero)")
        self.h.write(a["board"], ["link", a["padre"], hijo], touched=(a["padre"], hijo))
        self.done()

    # ---- recrear y archivar
    def _comment_once(self, board: str, tid: str, text: str) -> None:
        if any((c.get("body") or "") == text for c in self.h.show(board, tid).get("comments") or []):
            return
        self.h.write(board, ["comment", tid, text, "--author", AUTHOR], touched=(tid,))

    def a_recrear(self, a: dict) -> None:
        t = self.h.show(a["board"], a["id"])["task"]
        if t.get("status") == "archived":
            return self.skip("la original ya está archivada")
        if t.get("status") in BUSY:
            return self.skip(f"estado {t.get('status')}")
        dest = a["board_destino"]
        if self.lane_board.get(a["asignado"], dest) != dest:
            return self.skip(f"el carril {a['asignado']} no lee {dest}")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="\n") as f:
            f.write(a["cuerpo"])
            body = f.name
        try:
            out = self.h.write(dest, ["create", a["titulo"], "--body-file", body, "--assignee", a["asignado"],
                                      "--priority", str(a["prioridad"]), "--idempotency-key", a["idempotency_key"],
                                      "--created-by", AUTHOR, "--json"])
        finally:
            os.unlink(body)
        new = re.search(r"t_[0-9a-f]{8}", out).group(0) if self.h.apply else "<nueva>"
        if a.get("lanza_trabajo"):
            self.log(f"  ⚡ {new} nace ready en {dest} con {a['asignado']}: el carril la reclamará")
        if a.get("epica"):
            ep = self.epic_ids.get(a["epica"])
            if self.h.apply and ep:
                if new not in _parent_ids(self.h.show(dest, ep)):
                    self.h.write(dest, ["link", new, ep], touched=(new, ep))
            else:
                self.log(f"  [DRY] hermes kanban --board {dest} link {new} {ep or '<' + a['epica'] + '>'}")
        self._comment_once(a["board"], a["id"], f"Reorganización 28-09: recreada como {new} en el tablero {dest}. {a['motivo']}")
        self.h.write(a["board"], ["archive", a["id"]], touched=(a["id"],))
        self.done()

    def a_archivar(self, a: dict) -> None:
        t = self.h.show(a["board"], a["id"])["task"]
        if t.get("status") == "archived":
            return self.skip("ya")
        if t.get("status") in BUSY:
            return self.skip(f"estado {t.get('status')}")
        self._comment_once(a["board"], a["id"], f"Reorganización 28-09: archivada. Motivo: {a['motivo']}")
        self.h.write(a["board"], ["archive", a["id"]], touched=(a["id"],))
        self.done()

    def run(self) -> int:
        self.resolve_epics()
        self.log("épicas existentes: " + (", ".join(f"{k}={v}" for k, v in self.epic_ids.items()) or "ninguna"))
        errors = 0
        for n, a in enumerate(sorted(self.plan["acciones"], key=lambda x: x["fase"]), 1):
            what = a.get("id") or a.get("epica") or f"{a.get('padre')}->{a.get('hijo')}"
            self.log(f"[{n:03}] fase {a['fase']} {a['tipo']} {a['board']} {what}")
            try:
                getattr(self, "a_" + a["tipo"])(a)
            except (RuntimeError, OSError, ValueError, KeyError, AttributeError) as exc:  # se registra y se sigue
                errors += 1
                self.log(f"  ERROR: {exc}")
        self.log("RESUMEN: " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items())) + f", errores={errors}")
        return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--apply", action="store_true", help="escribe en el kanban (sin esto: dry-run)")
    ap.add_argument("--con-unlink", action="store_true",
                    help="desenlaza los paraguas antiguos (t_f90da954, t_55600b16, t_63a9b340, t_992e42ac) de sus hijas")
    ap.add_argument("--forzar", action="store_true", help="aplica aunque el valor actual no coincida con el del plan")
    ap.add_argument("--hermes", default=DEFAULT_HERMES if Path(DEFAULT_HERMES).exists() else (shutil.which("hermes") or "hermes"))
    ap.add_argument("--log-dir", default=str(HERE / ".state" / "reorganizacion"))
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    Path(args.log_dir).mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    log_path = Path(args.log_dir) / f"{stamp}-{'apply' if args.apply else 'dry'}.log"
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:

        def log(msg: str) -> None:
            fh.write(msg + "\n")
            fh.flush()
            try:
                print(msg)
            except UnicodeEncodeError:
                print(msg.encode("ascii", "replace").decode())

        log(f"reorganización kanban · {'APPLY' if args.apply else 'DRY-RUN'} · con_unlink={args.con_unlink} "
            f"forzar={args.forzar} · plan {args.plan}")
        try:
            return Aplicador(plan, Hermes(args.hermes, args.apply, log), args.con_unlink, args.forzar, log).run()
        finally:
            log(f"log: {log_path}")


if __name__ == "__main__":
    sys.exit(main())
