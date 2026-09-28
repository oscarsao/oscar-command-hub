"""Ventana de monitoreo en vivo: PC + sistema de agentes + chats (28-09, pedido por Oscar).

Uso:  py -3.12 monitor.py            (ventana en vivo; Ctrl+C para salir)
      py -3.12 monitor.py --once     (una sola foto, para diagnóstico)

Solo LEE: psutil, lanes.py status, state.db / logs de Hermes (modo solo lectura) y health públicos.
Las alertas nuevas se añaden a .state/alerts.log (Claude las vigila desde su sesión). No imprime secretos.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

import psutil
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

HOME = Path.home()
HERMES = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "hermes"
LANES = HOME / "oscar-command-hub" / "tools" / "agent-lanes"
STATE = Path(__file__).resolve().parent / ".state"
ALERTS_LOG = STATE / "alerts.log"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CHATS = {"6744452215": "DM Oscar", "5516823836": "DM María", "-1003530490339": "Gestión", "-1004277259008": "Marketing"}
TOPICS = {
    "-1003530490339": {None: "Dirección", "1": "Dirección", "5": "Ops·General", "230": "Ops·MigraTeam",
                       "231": "Ops·Píldora", "6": "Ventas", "7": "Mkt·Decisiones", "355": "Finanzas", "148": "Finanzas(arch)"},
    "-1004277259008": {None: "General", "1": "General", "74": "Planificación", "77": "MigraTeam", "80": "Píldora",
                       "83": "Recursos"},
}
HEALTH = {"Oscar HQ": "https://oscar-hq-production.up.railway.app/api/health",
          "MigraTeam": "https://ocr-pdf-and-images-production.up.railway.app/health",
          "Panel kanban": "https://kanban.pildoradigital.com/kanban",
          "Dashboard local": "http://127.0.0.1:9119/"}
WATCH_PROCS = ("claude", "python", "pythonw", "node", "chrome", "hermes", "cloudflared", "msedge", "code")
LOG_LEVEL_RE = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b")


def now_s() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Tail:
    """Lee solo las líneas NUEVAS de un log desde que arrancó el monitor."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = path.stat().st_size if path.exists() else 0

    def new_lines(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.pos:  # rotado
            self.pos = 0
        if size == self.pos:
            return []
        with self.path.open("rb") as f:
            f.seek(self.pos)
            data = f.read(size - self.pos)
        self.pos = size
        return data.decode("utf-8", "replace").splitlines()


class Monitor:
    def __init__(self):
        STATE.mkdir(exist_ok=True)
        self.alerts: deque[tuple[str, str, str]] = deque(maxlen=14)  # (hora, nivel, texto)
        self._seen: dict[str, float] = {}
        self.cpu_hist: deque[float] = deque(maxlen=5)
        self.lanes_text, self.lanes_at = "(cargando…)", 0.0
        self.health: dict[str, str] = {}
        self.health_at = 0.0
        self.runner_events: deque[str] = deque(maxlen=9)
        self.hermes_tail = Tail(HERMES / "logs" / "errors.log")
        self.runner_tail = Tail(LANES / ".state" / "runner.log")
        psutil.cpu_percent(None)

    # --- alertas --------------------------------------------------------------------------------------
    def alert(self, level: str, text: str, key: str | None = None, every: float = 600) -> None:
        key = key or text
        t = time.time()
        if t - self._seen.get(key, 0) < every:
            return
        self._seen[key] = t
        self.alerts.appendleft((now_s(), level, text))
        with ALERTS_LOG.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {level} {text}\n")

    # --- PC -------------------------------------------------------------------------------------------
    def pc(self) -> Panel:
        cpu = psutil.cpu_percent(None)
        self.cpu_hist.append(cpu)
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        free_gb = disk.free / 2**30
        if len(self.cpu_hist) == self.cpu_hist.maxlen and min(self.cpu_hist) > 90:
            self.alert("ALTA", f"CPU sostenida >90% ({cpu:.0f}%)", "cpu")
        if vm.percent > 88:
            self.alert("ALTA", f"RAM al {vm.percent:.0f}% ({vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB)", "ram")
        if free_gb < 10:
            self.alert("MEDIA", f"Disco C: solo {free_gb:.1f} GB libres", "disk", every=3600)

        groups: dict[str, list] = {}
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
            try:
                name = (p.info["name"] or "").lower().removesuffix(".exe")
                rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
                procs.append((rss, p.info["cpu_percent"] or 0.0, name, p.info["pid"]))
                for w in WATCH_PROCS:
                    if name == w or name.startswith(w):
                        groups.setdefault(w, []).append(rss)
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        n_claude = len(groups.get("claude", []))
        if n_claude > 6:
            self.alert("MEDIA", f"{n_claude} procesos claude abiertos (¿workers colgados?)", "claude-count", every=1800)

        head = Text()
        head.append(f"CPU {cpu:4.0f}%  ", style="red" if cpu > 85 else "green")
        head.append(f"RAM {vm.percent:4.0f}% ({vm.used / 2**30:.1f}/{vm.total / 2**30:.0f} GB)  ",
                    style="red" if vm.percent > 85 else "green")
        head.append(f"Disco C libre {free_gb:.0f} GB", style="red" if free_gb < 10 else "green")
        gt = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
        gt.add_column("grupo")
        gt.add_column("nº", justify="right")
        gt.add_column("RAM", justify="right")
        for w in WATCH_PROCS:
            if w in groups:
                gt.add_row(w, str(len(groups[w])), f"{sum(groups[w]) / 2**20:,.0f} MB")
        top = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
        top.add_column("top RAM")
        top.add_column("pid", justify="right")
        top.add_column("MB", justify="right")
        for rss, _, name, pid in sorted(procs, reverse=True)[:6]:
            top.add_row(name[:18], str(pid), f"{rss / 2**20:,.0f}")
        grid = Table.grid(expand=True)
        grid.add_row(gt, top)
        return Panel(Group(head, grid), title="🖥  PC", border_style="cyan")

    # --- sistema --------------------------------------------------------------------------------------
    def refresh_lanes(self) -> None:
        if time.time() - self.lanes_at < 15:
            return
        self.lanes_at = time.time()
        try:
            cp = subprocess.run([sys.executable, str(LANES / "lanes.py"), "status"], cwd=LANES, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WINDOW)
            self.lanes_text = (cp.stdout or cp.stderr).strip()
        except Exception as exc:
            self.lanes_text = f"lanes.py status falló: {exc}"
        if "VIVO" not in self.lanes_text:
            self.alert("ALTA", "El runner de carriles NO está vivo", "runner-dead", every=900)
        if "huérfano" in self.lanes_text:
            self.alert("ALTA", "Hay un worker huérfano en los carriles", "orphan", every=900)

    def refresh_health(self) -> None:
        if time.time() - self.health_at < 60:
            return
        self.health_at = time.time()
        for name, url in HEALTH.items():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "oscar-monitor"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception:
                code = 0
            ok = 200 <= code < 400 or (name == "Panel kanban" and code in (302, 401, 403))
            self.health[name] = f"{'✅' if ok else '❌'} {code or 'sin respuesta'}"
            if not ok:
                self.alert("ALTA", f"{name} no responde bien ({code or 'sin respuesta'})", f"health-{name}", every=600)

    def hermes_gateway(self) -> str:
        """gateway_state.json (JSON: pid, gateway_state, active_agents, updated_at)."""
        import json
        try:
            st = json.loads((HERMES / "gateway_state.json").read_text(encoding="utf-8"))
        except Exception:
            st = {}
        pid = st.get("pid")
        alive = bool(pid and psutil.pid_exists(int(pid)) and st.get("gateway_state") == "running")
        age = None
        try:
            age = time.time() - datetime.fromisoformat(st["updated_at"]).timestamp()
        except Exception:
            pass
        if not alive:
            self.alert("ALTA", f"Gateway de Hermes caído (estado {st.get('gateway_state', '?')})", "gw-dead", every=600)
        elif age is not None and age > 300:
            self.alert("MEDIA", f"Estado de Hermes sin actualizar desde hace {age / 60:.0f} min", "gw-hb", every=900)
        agents = st.get("active_agents")
        return (f"{'✅ vivo' if alive else '❌ caído'} pid {pid or '?'} · agentes activos {agents if agents is not None else '?'}"
                + (f" · estado hace {age:.0f}s" if age is not None else ""))

    def system(self) -> Panel:
        self.refresh_lanes()
        self.refresh_health()
        t = Text()
        t.append("Hermes gateway: ", style="bold")
        t.append(self.hermes_gateway() + "\n")
        t.append("Servicios: ", style="bold")
        t.append("  ".join(f"{k} {v}" for k, v in self.health.items()) + "\n\n")
        lines = [ln for ln in self.lanes_text.splitlines() if not ln.startswith("---")]
        for ln in lines[:8]:
            style = "yellow" if "ocupado" in ln else ("red" if "huérfano" in ln else None)
            t.append(ln[:120] + "\n", style=style)
        return Panel(t, title="⚙️  Sistema (carriles · Hermes · servicios)", border_style="magenta")

    # --- chats ----------------------------------------------------------------------------------------
    @staticmethod
    def label(chat: str | None, thread: str | None) -> str:
        chat = str(chat or "")
        name = CHATS.get(chat, chat[-6:] or "?")
        topics = TOPICS.get(chat)
        if topics is not None:
            name += "›" + topics.get(str(thread) if thread else None, str(thread))
        return name

    def chats(self) -> Panel:
        tb = Table(box=None, padding=(0, 1), expand=True, show_header=True, header_style="bold")
        tb.add_column("hora", width=8)
        tb.add_column("dónde", width=20)
        tb.add_column("quién", width=6)
        tb.add_column("mensaje", ratio=1, overflow="ellipsis", no_wrap=True)
        busy = ""
        try:
            c = sqlite3.connect(f"file:{HERMES / 'state.db'}?mode=ro", uri=True, timeout=2)
            rows = c.execute(
                "select m.timestamp, s.chat_id, s.thread_id, m.role, m.content, m.tool_name from messages m "
                "join sessions s on s.id = m.session_id where s.source = 'telegram' "
                "order by m.id desc limit 40").fetchall()
            leases = c.execute("select count(*) from session_turn_leases where expires_at > ?",
                               (time.time(),)).fetchone()[0]
            c.close()
            busy = f"Hermes trabajando en {leases} conversación(es)" if leases else "Hermes libre"
        except Exception as exc:
            rows, busy = [], f"state.db no legible: {exc}"
        shown = 0
        collapsed: list = []  # filas de herramienta iguales y seguidas -> una sola "×N"
        for r in rows:
            if collapsed and r[3] == "tool" and collapsed[-1][0][3] == "tool" and collapsed[-1][0][5] == r[5] \
                    and ("error" in (r[4] or "")[:80].lower()) == ("error" in (collapsed[-1][0][4] or "")[:80].lower()):
                collapsed[-1][1] += 1
            else:
                collapsed.append([r, 1])
        for (ts, chat, thread, role, content, tool), times in collapsed:
            if shown >= 14:
                break
            if role == "tool":
                ok = "error" not in (content or "")[:80].lower()
                if not ok:
                    self.alert("MEDIA", f"Hermes: herramienta {tool} falló en {self.label(chat, thread)}",
                               f"tool-{tool}-{int(ts)}", every=10 ** 9)
                text, who, style = (f"🔧 {tool} {'ok' if ok else 'ERROR'}" + (f" ×{times}" if times > 1 else ""),
                                    "tool", ("dim" if ok else "red"))
            elif role in ("user", "assistant") and (content or "").strip():
                text = " ".join((content or "").split())
                who, style = ("tú" if role == "user" else "Hermes"), ("bold" if role == "user" else None)
            else:
                continue
            tb.add_row(datetime.fromtimestamp(ts).strftime("%H:%M:%S"), self.label(chat, thread), who,
                       Text(text[:200], style=style))
            shown += 1
        for line in self.runner_tail.new_lines():
            if not line.strip() or line.startswith((" ", "-")):
                continue
            self.runner_events.appendleft(line[11:19] + " " + line[24:].strip())
            m = LOG_LEVEL_RE.search(line[:40])
            if m:
                self.alert("MEDIA" if m.group(1) == "WARNING" else "ALTA", "carriles: " + line[24:160].strip(),
                           every=10 ** 9)
        ev = Text("\n".join(e[:150] for e in list(self.runner_events)[:6]) or "(sin eventos nuevos desde que abriste el monitor)",
                  style="dim")
        return Panel(Group(Text(busy, style="bold green"), tb, Text("\nCarriles (bot Trabajos) — últimos eventos:",
                                                                    style="bold"), ev),
                     title="💬 Chats (Hermes) y avisos de carriles", border_style="green")

    # --- alertas ----------------------------------------------------------------------------------------
    def alerts_panel(self) -> Panel:
        for line in self.hermes_tail.new_lines():
            m = LOG_LEVEL_RE.search(line[:40])
            if m:
                msg = re.sub(r"^\S+ \S+ \w+ ", "", line)
                self.alert("MEDIA" if m.group(1) == "WARNING" else "ALTA", "Hermes: " + msg[:170], every=10 ** 9)
        t = Text()
        if not self.alerts:
            t.append("Sin alertas desde que abriste el monitor ✅", style="green")
        for hora, level, text in self.alerts:
            t.append(f"{hora} ", style="dim")
            t.append(f"[{level}] ", style="bold red" if level == "ALTA" else "bold yellow")
            t.append(text[:180] + "\n")
        return Panel(t, title=f"🚨 Alertas (también en {ALERTS_LOG.name})", border_style="red")

    def render(self) -> Layout:
        root = Layout()
        root.split_column(Layout(name="top", size=13), Layout(name="mid"), Layout(name="bottom", size=12))
        root["top"].split_row(Layout(self.pc(), name="pc", ratio=2), Layout(self.system(), name="sys", ratio=3))
        root["mid"].update(self.chats())
        root["bottom"].update(self.alerts_panel())
        return root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0)
    a = ap.parse_args()
    mon = Monitor()
    mon.alert("INFO", "Monitor arrancado", every=0)
    if a.once:
        from rich.console import Console
        Console(width=160, height=60).print(mon.render())
        return 0
    with Live(mon.render(), refresh_per_second=1, screen=True) as live:
        try:
            while True:
                time.sleep(a.interval)
                live.update(mon.render())
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
