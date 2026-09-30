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
# Silencio del DM de 23:00 a 08:00 (30-09): solo pasan las caídas reales (runner, Hermes, servicios, listado de tareas).
QUIET_START, QUIET_END = 23, 8
OUTAGE_KEYS = ("runner-dead", "gw-dead", "health-", "list-fail")
LIST_FAIL = "no se pudieron listar tareas"
LIST_FAIL_AFTER = 600  # s de fallos seguidos listando tareas (hermes/WinError) antes de abrir ALTA
RETRY_TASKS = ("Resumen diario Hermes", "Auditor diario carriles")  # tareas programadas a relanzar si fallaron
LIST_FAIL_GAP = 240   # s sin fallos nuevos = recuperado (el runner lista cada ~60 s)


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


OWNER_DM = "6744452215"


def dm_pusher():
    """Envía alertas ALTA al DM de Oscar con el bot de Trabajos (token de agent-lanes/.env, nunca se imprime)."""
    token = ""
    try:
        for line in (LANES / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("CARRILES_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
    except OSError:
        pass
    if not token:
        return None

    def push(text: str) -> None:
        import json
        body = json.dumps({"chat_id": OWNER_DM, "text": "🚨 Monitor: " + text[:900],
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception:
            pass  # sin red: queda en alerts.log

    return push


class Monitor:
    def __init__(self, *, write_log: bool = False, push=None):
        STATE.mkdir(exist_ok=True)
        self.write_log, self.push = write_log, push
        self.alerts: deque[tuple[str, str, str]] = deque(maxlen=14)  # (hora, nivel, texto)
        self._seen: dict[str, float] = {}
        self.now = datetime.now  # inyectable en tests
        self._quiet_pending: list[str] = []
        self._list_fail_first = self._list_fail_last = 0.0
        self._list_fail_was_active = False
        self.run = lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30,
                                               creationflags=NO_WINDOW)  # inyectable en tests
        self.cpu_hist: deque[float] = deque(maxlen=5)
        self.lanes_text, self.lanes_at = "(cargando…)", 0.0
        self.health: dict[str, str] = {}
        self.health_at = 0.0
        self.runner_events: deque[str] = deque(maxlen=9)
        self.hermes_tail = Tail(HERMES / "logs" / "errors.log")
        self.runner_tail = Tail(LANES / ".state" / "runner.log")
        psutil.cpu_percent(None)

    # --- alertas --------------------------------------------------------------------------------------
    def condition(self, key: str, active: bool, text: str, fix: str, *, min_hits: int = 3, every: float = 1800) -> None:
        """Estado persistente (runner/Hermes/servicio caído): alerta solo si dura `min_hits` lecturas seguidas, con
        qué hacer, y avisa cuando se resuelve. 28-09: un reinicio de 20 s del runner mandó "NO está vivo" sin más."""
        conds = self.__dict__.setdefault("_conds", {})
        c = conds.setdefault(key, {"hits": 0, "since": 0.0, "alerted": False})
        if active:
            c["hits"] += 1
            c["since"] = c["since"] or time.time()
            if c["hits"] >= min_hits:
                self.alert("ALTA", f"{text}\n👉 Qué hacer: {fix}", key, every=every)
                c["alerted"] = True
            return
        if c["alerted"]:
            mins = max(1, round((time.time() - c["since"]) / 60))
            self._seen.pop(key, None)
            self.alert("RESUELTO", f"✅ Resuelto: {text.split(chr(10))[0]} (duró ~{mins} min)", key + "-ok", every=0)
        conds[key] = {"hits": 0, "since": 0.0, "alerted": False}

    def alert(self, level: str, text: str, key: str | None = None, every: float = 600) -> None:
        key = key or text
        t = time.time()
        if t - self._seen.get(key, 0) < every:
            return
        self._seen[key] = t
        self.alerts.appendleft((now_s(), level, text))
        if not self.write_log:  # la ventana solo muestra; el servicio headless registra y avisa
            return
        with ALERTS_LOG.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {level} {text}\n")
        if level in ("ALTA", "RESUELTO") and self.push:
            h = self.now().hour
            if (h >= QUIET_START or h < QUIET_END) and not key.startswith(OUTAGE_KEYS):
                self._quiet_pending.append(text.split("\n")[0][:160])  # se resume a las 08:00
                return
            self.push(text)

    def flush_quiet(self) -> None:
        """Pasadas las 08:00, un solo DM con lo que se calló por la noche."""
        if self._quiet_pending and QUIET_END <= self.now().hour < QUIET_START and self.push:
            lines, self._quiet_pending = self._quiet_pending[-10:], []
            self.push("🌙 Durante la noche (sin avisar):\n" + "\n".join(f"• {ln}" for ln in lines))

    def note_runner_line(self, line: str) -> None:
        """Fallos de `hermes` al listar tareas (WinError o cualquier otro) en runner.log: si duran más de 10 min con el
        runner vivo, condición ALTA. 29/30-09: 24,5 h en MEDIA sin que nadie lo viese."""
        if LIST_FAIL not in line:
            return
        try:
            ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            ts = time.time()
        if not self._list_fail_first or ts - self._list_fail_last > LIST_FAIL_GAP:
            self._list_fail_first = ts
        self._list_fail_last = ts

    def check_list_failures(self) -> None:
        streak = self._list_fail_last - self._list_fail_first
        active = bool(self._list_fail_first) and time.time() - self._list_fail_last <= LIST_FAIL_GAP \
            and streak >= LIST_FAIL_AFTER
        if self._list_fail_was_active and not active:
            self.rerun_failed_jobs()  # recuperado: el brief/auditor de esas horas pudieron fallar por lo mismo
        self._list_fail_was_active = active
        self.condition("list-fail", active,
                       "El runner está vivo pero lleva más de 10 min sin poder listar tareas (falla `hermes`): no "
                       "arranca nada.",
                       "mira las últimas líneas de agent-lanes/.state/runner.log; suele ser `hermes` caído o sin "
                       "acceso a su base de datos. /salud te dice el estado.", min_hits=1, every=3600)

    def rerun_failed_jobs(self) -> list[str]:
        """Al cerrarse list-fail: relanza (tarea programada ya existente) el brief y el auditor cuyo último resultado
        fue distinto de 0. Devuelve los relanzados. `self.run` es inyectable en tests."""
        rerun = []
        for task in RETRY_TASKS:
            try:
                cp = self.run(["schtasks", "/Query", "/TN", task, "/V", "/FO", "LIST"])
                m = re.search(r"^\s*(?:Last Result|Último resultado|Resultado de la última ejecución)\s*:\s*(-?\d+)",
                              cp.stdout or "", re.M | re.I)
                if not m or int(m.group(1)) == 0:
                    continue
                if self.run(["schtasks", "/Run", "/TN", task]).returncode == 0:
                    rerun.append(task)
            except Exception:
                continue
        if rerun:
            self.alert("MEDIA", "Listado de tareas recuperado: relanzado " + " y ".join(f"«{t}»" for t in rerun)
                       + " porque su última ejecución había fallado.", "rerun-jobs", every=0)
        return rerun

    # --- PC -------------------------------------------------------------------------------------------
    def pc(self) -> Panel:
        cpu = psutil.cpu_percent(None)
        self.cpu_hist.append(cpu)
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        free_gb = disk.free / 2**30
        if len(self.cpu_hist) == self.cpu_hist.maxlen and min(self.cpu_hist) > 90:
            self.alert("ALTA", f"CPU sostenida >90% ({cpu:.0f}%)", "cpu")
        self.condition("ram", vm.percent > 88,
                       f"RAM al {vm.percent:.0f}% ({vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB)",
                       "cierra ventanas de Chrome/Edge o sesiones de Claude que no uses; si sigue, revisa el top de "
                       "RAM del monitor.", min_hits=4, every=3600)
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
        # Un reinicio (ordenado o no) deja el runner ~20 s sin proceso: 3 lecturas (~45 s) y nunca durante un drenaje.
        draining = (LANES / ".state" / "drain.json").exists()
        self.condition("runner-dead", "VIVO" not in self.lanes_text and not draining,
                       "El runner de carriles lleva más de un minuto parado: no se reclaman tareas (quedan en cola, "
                       "no se pierde nada).",
                       "espera 2-3 min por si es un reinicio; si sigue, en el PC: Programador de tareas → "
                       "'agent-lanes runner' → Ejecutar. /salud (bot de Trabajos) te dice el estado.")
        self.condition("orphan", "huérfano" in self.lanes_text,
                       "Hay un worker funcionando sin runner que lo vigile.",
                       "normalmente se resuelve solo al terminar la tarea; si pasa de 30 min, avísame en la "
                       "sesión de Claude Code o mira /salud.", min_hits=4)

    def refresh_health(self) -> None:
        """Solo el servicio headless consulta (cada 5 min) y deja el resultado en .state/health.json; la ventana lo
        lee. 28-09: dos monitores cada 60 s contra producción dispararon el rate limit de MigraTeam (429)."""
        import json
        shared = STATE / "health.json"
        if not self.write_log:
            try:
                self.health = json.loads(shared.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.health = {"(servicio de alertas)": "sin datos todavía"}
            return
        if time.time() - self.health_at < 300:
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
            limited = code == 429  # vivo pero limitando peticiones: no es una caída
            self.health[name] = f"{'✅' if ok else ('🟡' if limited else '❌')} {code or 'sin respuesta'}"
            fixes = {"Oscar HQ": "mira el último deploy en railway.app → Oscar HQ → Deployments (si falló, "
                                 "pulsa Redeploy del anterior) y dime qué ves.",
                     "MigraTeam": "¡producción! mira railway.app → MigraTeam → Deployments y los logs; si es un "
                                  "deploy reciente, avísame para revertirlo.",
                     "Panel kanban": "el túnel de Cloudflare o el dashboard de Hermes: en el PC, servicios → "
                                     "'cloudflared' → Reiniciar, o tarea 'Hermes Dashboard' → Ejecutar.",
                     "Dashboard local": "Programador de tareas → 'Hermes Dashboard' → Ejecutar."}
            # Consulta cada 5 min: 2 lecturas seguidas (~5-10 min) antes de avisar.
            self.condition(f"health-{name}", not ok and not limited,
                           f"{name} no responde bien ({code or 'sin respuesta'}).",
                           fixes.get(name, "revisa /salud."), min_hits=2)
        try:
            shared.write_text(json.dumps(self.health, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def refresh_branch_drift(self) -> str:
        """MigraTeam: develop debe contener master. Tras un hotfix directo a master hay que devolverlo a develop
        (28-09: develop iba 20 commits por detrás y los carriles no sabían qué base usar)."""
        if time.time() - getattr(self, "_drift_at", 0) < 900:
            return getattr(self, "_drift_text", "")
        self._drift_at = time.time()
        repo = HOME / "dev" / "migrateam"
        try:
            subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin", "master", "develop"], capture_output=True,
                           timeout=60, creationflags=NO_WINDOW)
            cp = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "origin/develop..origin/master"],
                                capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            behind = int((cp.stdout or "0").strip() or 0)
        except Exception:
            self._drift_text = "MigraTeam develop↔master: ?"
            return self._drift_text
        if behind:
            self.alert("MEDIA", f"MigraTeam: develop va {behind} commit(s) por detrás de master (hotfix sin devolver a develop)",
                       "drift-migrateam", every=3600)
        self._drift_text = f"MigraTeam develop↔master: {'✅ al día' if not behind else f'❌ develop -{behind}'}"
        return self._drift_text

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
        self.condition("gw-dead", not alive,
                       f"Hermes está caído (estado {st.get('gateway_state', '?')}): no responderá en Telegram.",
                       "en el PC abre una terminal y ejecuta `hermes gateway restart`; los botones del bot de "
                       "Trabajos siguen funcionando mientras tanto.", min_hits=3)
        if alive and age is not None and age > 300:
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
        t.append("  ".join(f"{k} {v}" for k, v in self.health.items()) + "\n")
        t.append(self.refresh_branch_drift() + "\n\n")
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
            self.note_runner_line(line)
            self.runner_events.appendleft(line[11:19] + " " + line[24:].strip())
            m = LOG_LEVEL_RE.search(line[:40])
            if m:
                self.alert("MEDIA" if m.group(1) == "WARNING" else "ALTA", "carriles: " + line[24:160].strip(),
                           every=10 ** 9)
        self.check_list_failures()
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
    ap.add_argument("--headless", action="store_true",
                    help="servicio sin ventana: registra alertas en .state/alerts.log y manda las ALTA al DM")
    a = ap.parse_args()
    if a.headless:
        return run_headless()
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


def run_headless(interval: float = 15.0) -> int:
    """Una sola instancia (pid en .state/headless.pid). Las comprobaciones son las mismas que pinta la ventana."""
    STATE.mkdir(exist_ok=True)
    pidf = STATE / "headless.pid"
    try:
        old = int(pidf.read_text().strip())
        if old != os.getpid() and psutil.pid_exists(old) and "python" in psutil.Process(old).name().lower():
            return 0  # ya hay uno vivo
    except (OSError, ValueError, psutil.Error):
        pass
    pidf.write_text(str(os.getpid()))
    mon = Monitor(write_log=True, push=dm_pusher())
    mon.alert("INFO", "Monitor headless arrancado", every=0)
    while True:
        for step in (mon.pc, mon.system, mon.chats, mon.alerts_panel):
            try:
                step()
            except Exception as exc:  # una comprobación rota no para las demás
                mon.alert("MEDIA", f"monitor: fallo en {step.__name__}: {exc}", f"self-{step.__name__}", every=3600)
        mon.flush_quiet()
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
