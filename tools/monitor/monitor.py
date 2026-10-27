"""Ventana de monitoreo en vivo: PC + sistema de agentes + chats (28-09, pedido por Oscar).

Uso:  py -3.12 monitor.py            (ventana en vivo; Ctrl+C para salir)
      py -3.12 monitor.py --once     (una sola foto, para diagnóstico)

Solo LEE: psutil, lanes.py status, state.db / logs de Hermes (modo solo lectura) y health públicos.
Las alertas nuevas se añaden a .state/alerts.log (Claude las vigila desde su sesión). No imprime secretos.
"""
from __future__ import annotations

import argparse
import json
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbwatch  # noqa: E402  (sonda de la base de Supabase; mismo directorio)
import disponibilidad as disp  # noqa: E402  (F1-06: latido, batería, tareas diarias perdidas; mismo directorio)
import prodwatch  # noqa: E402  (despliegues de producción en Vercel y salud del servidor; mismo directorio)

HOME = Path.home()
HERMES = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "hermes"
LANES = HOME / "oscar-command-hub" / "tools" / "agent-lanes"
STATE = Path(__file__).resolve().parent / ".state"
ALERTS_LOG = STATE / "alerts.log"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CHATS = {"6744452215": "DM Oscar","-1003530490339": "Gestión", "-1004277259008": "Marketing"}
TOPICS = {
    "-1003530490339": {None: "Dirección", "1": "Dirección", "5": "Ops·General", "230": "Ops·MigraTeam",
                       "231": "Ops·Píldora", "6": "Ventas", "7": "Mkt·Decisiones", "355": "Finanzas", "148": "Finanzas(arch)"},
    "-1004277259008": {None: "General", "1": "General", "74": "Planificación", "77": "MigraTeam", "80": "Píldora",
                       "83": "Recursos"},
}
HEALTH = {"Oscar HQ": "https://api.pildoradigital.com/api/health",  # 22-10: fuera de Railway (Dokploy, pildora-01)
          "MigraTeam": "https://ocr-pdf-and-images-production.up.railway.app/health",
          "Panel kanban": "https://kanban.pildoradigital.com/kanban",
          "Dashboard local": "http://127.0.0.1:9119/"}
WATCH_PROCS = ("claude", "python", "pythonw", "node", "chrome", "hermes", "cloudflared", "msedge", "code")
LOG_LEVEL_RE = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b")
# Silencio del DM de 23:00 a 08:00 (30-09): solo pasan las caídas reales (runner, Hermes, servicios, listado de tareas).
QUIET_START, QUIET_END = 23, 8
OUTAGE_KEYS = ("runner-dead", "gw-dead", "health-", "list-fail", "db-down", "db-slow", "vercel-prod", "battery-low")
PROD_EVERY = 300      # s entre comprobaciones de Vercel (producción) y del servidor (health + Dokploy)
DB_EVERY = 300        # s entre sondas de la base de Supabase (01-10: saturada 30-09 y 01-10)
DB_MAX_CONNS = 45     # AVISO por encima (el plan Micro admite 60)
DB_MIN_MEM_PCT = 15   # AVISO si la memoria disponible baja de esto
LIST_FAIL = "no se pudieron listar tareas"
LIST_FAIL_AFTER = 600  # s de fallos seguidos listando tareas (hermes/WinError) antes de abrir ALTA
RETRY_TASKS = ("Resumen diario Hermes", "Auditor diario carriles")  # tareas programadas a relanzar si fallaron
LIVE_LAG_AFTER = 30 * 60  # s con el checkout vivo por detrás de origin/main antes de avisar
RUNNER_AUTO_SECS = 900  # el «parado» que dura menos se da por transitorio: sin «Qué hacer» y un solo «resuelto»
RAM_ALTA_PCT = 85  # a partir de aquí se dice que la causa es la RAM
RUNNER_DEAD_SECS = 300 # s seguidos con el runner sin aparecer «VIVO» antes de avisar (Oscar, 23-10)
LIST_FAIL_GAP = 240  # s sin fallos nuevos = recuperado (el runner lista cada ~60 s)


RAM_REPORT_EVERY = 4 * 3600  # s entre informes de RAM al DM (antes: aviso repetido cada hora mientras RAM > 88 %)
ALERT_CAP_HOURS = 24  # como mucho un aviso por clave y día (retro 22-10); editable: ajustes_hub.json avisos.alerta_cada_horas
CAP_EXEMPT = ("INFO", "RESUELTO")
WATCHED_CODE = ("monitor.py", "dbwatch.py", "prodwatch.py", "disponibilidad.py")  # si cambian, se reinicia solo
RESUMEN_INFO = LANES / ".state" / "resumen_info.json"  # resumen diario INFO del runner (F1-04)
RUNNER_LOCK = LANES / ".state" / "runner.lock"
HERMES_CHECK = LANES.parent / "hermes-config" / "apply_hermes_config.py"  # --check: solo avisa (F1-07, CF-08)
# Tareas diarias laborables que se relanzan si a la hora + margen no han dejado línea de hoy en su log (DISP-09).
DAILY_TASKS = (disp.TareaDiaria("Auditor diario carriles", LANES / ".state" / "auditor.log", (7, 40), 50),
               disp.TareaDiaria("Resumen diario Hermes", LANES / ".state" / "brief.log", (8, 0), 90))


def alert_cap_seconds() -> float:
    """Horas mínimas entre avisos de una misma clave: ajuste del hub (agent-lanes/ajustes_hub.json) o 24 h."""
    import json
    try:
        h = float(json.loads((LANES / "ajustes_hub.json").read_text(encoding="utf-8"))["valores"]["avisos.alerta_cada_horas"])
    except (OSError, ValueError, KeyError, TypeError):
        h = ALERT_CAP_HOURS
    return max(h, 0.0) * 3600


def code_fingerprint(base: Path | None = None) -> tuple:
    """(nombre, mtime_ns, tamaño) de los .py del monitor: cambia cuando un merge aplicado los reescribe."""
    base = base or Path(__file__).resolve().parent
    out = []
    for name in WATCHED_CODE:
        try:
            st = (base / name).stat()
            out.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((name, 0, 0))
    return tuple(out)


def restart_if_code_changed(started: tuple, *, base: Path | None = None, state: Path | None = None, run=None,
                            spawn=None, alert=None) -> bool:
    """Reinicio ordenado tras un merge que toca tools/monitor (como `restart --drain` del runner): solo entre ciclos
    (no hay nada a medias), tras comprobar que el código nuevo compila y lanzando el relevo ANTES de salir. True =
    relevo lanzado y este proceso debe terminar. Si el código nuevo no compila, avisa y sigue con el actual."""
    base = base or Path(__file__).resolve().parent
    state = state or STATE
    if code_fingerprint(base) == started:
        return False
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=60,
                                              creationflags=NO_WINDOW))
    for name in WATCHED_CODE:
        cp = run([sys.executable, "-m", "py_compile", str(base / name)])
        if cp.returncode:
            if alert:
                alert("ALTA", f"El monitor no se reinicia: {name} nuevo no compila ({(cp.stderr or '')[-160:].strip()}).",
                      "monitor-restart-bad", 86400)
            return False
    spawn = spawn or (lambda args: subprocess.Popen(
        args, creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True))
    (state / "headless.pid").unlink(missing_ok=True)  # si no, el relevo vería este pid vivo y saldría
    spawn([sys.executable, str(base / "monitor.py"), "--headless"])
    return True


def ram_text(top: int = 5) -> str:
    """RAM total y los `top` procesos que más usan (agrupados por nombre). Mismo formato que /ram del bot."""
    vm = psutil.virtual_memory()
    by_name: dict[str, int] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (p.info["name"] or "?").lower().removesuffix(".exe")
            by_name[name] = by_name.get(name, 0) + (p.info["memory_info"].rss if p.info["memory_info"] else 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    flag = "⚠️ " if vm.percent >= 90 else ""
    lines = [f"{flag}🧠 RAM {vm.percent:.0f} % ({vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB)".replace(".", ",")]
    lines += [f"• {n}: {r / 2**30:.1f} GB".replace(".", ",")
              for n, r in sorted(by_name.items(), key=lambda kv: -kv[1])[:top]]
    return "\n".join(lines)


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
    def __init__(self, *, write_log: bool = False, push=None, db=None, prod=None, latido=None, tareas=None):
        STATE.mkdir(exist_ok=True)
        self.write_log, self.push = write_log, push
        self.latido = latido  # disponibilidad.Latido o None (F1-06: Oscar HQ + Kuma cada 5 min)
        self.tareas = tareas  # disponibilidad.TareasDiarias o None
        self.read_battery = None  # inyectable en tests (psutil.sensors_battery)
        self.resumen_path = RESUMEN_INFO
        self.prod, self.prod_at = prod, 0.0  # prodwatch.ProdWatch o None (sin avisos de producción/servidor)
        self.db, self.db_at = db, 0.0  # dbwatch.DbWatch o None (vigilancia de Supabase desactivada)
        self.alerts: deque[tuple[str, str, str]] = deque(maxlen=14)  # (hora, nivel, texto)
        self._daily = self._load_json(STATE / "alert_daily.json") if write_log else {}  # {clave: último aviso}
        self._seen: dict[str, float] = {k: v for k, v in self._daily.items() if isinstance(v, (int, float))}
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
    def condition(self, key: str, active: bool, text: str, fix: str, *, min_hits: int = 3, every: float = 1800,
                  level: str = "ALTA", min_secs: float = 0, auto_secs: float = 0, cause=None) -> None:
        """Estado persistente (runner/Hermes/servicio caído): alerta solo si dura `min_hits` lecturas seguidas y, si se
        da, al menos `min_secs` segundos, con qué hacer, y avisa cuando se resuelve. 28-09: un reinicio de 20 s del
        runner mandó "NO está vivo" sin más.
        `auto_secs` (F1-14): condición que suele resolverse sola (hermes lento por RAM). Antes de ese tiempo el aviso
        no manda al Programador de tareas; si se resuelve antes, el «resuelto» sale como mucho una vez por `every`
        (parado y resuelto en bucle cada pocos minutos eran ruido). `cause`: texto o función con la causa probable."""
        conds = self.__dict__.setdefault("_conds", {})
        c = conds.setdefault(key, {"hits": 0, "since": 0.0, "alerted": False})
        causa = (cause() if callable(cause) else cause) if cause else ""
        if active:
            c["hits"] += 1
            c["since"] = c["since"] or time.time()
            if c["hits"] >= min_hits and time.time() - c["since"] >= min_secs:
                pronto = auto_secs and time.time() - c["since"] < auto_secs
                cuerpo = f"{text}\n🔎 Causa probable: {causa}." if causa else text
                cuerpo += (f"\nSuele resolverse solo; si pasa de {round(auto_secs / 60)} min te digo qué hacer."
                           if pronto else f"\n👉 Qué hacer: {fix}")
                self.alert(level, cuerpo, key, every=every)
                c["alerted"] = True
            return
        if c["alerted"]:
            mins = max(1, round((time.time() - c["since"]) / 60))
            rapido = auto_secs and time.time() - c["since"] < auto_secs
            if not rapido:
                self._seen.pop(key, None)
            fin = f"✅ Resuelto: {text.split(chr(10))[0]} (duró ~{mins} min" + (f"; {causa}" if causa else "") + ")"
            self.alert("RESUELTO", fin, key + "-ok", every=every if rapido else 0)
        conds[key] = {"hits": 0, "since": 0.0, "alerted": False}

    @staticmethod
    def _load_json(path: Path) -> dict:
        import json
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def silenced(self, key: str) -> bool:
        """/silenciar <clave> <horas> (agent-lanes): .state/silenced.json {clave: epoch de fin}; la clave cubre las que
        empiezan por ella."""
        now = time.time()
        for k, until in self._load_json(STATE / "silenced.json").items():
            try:
                if key.startswith(k) and float(until) > now:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def alert(self, level: str, text: str, key: str | None = None, every: float = 600) -> None:
        key = key or text
        t = time.time()
        capped = level not in CAP_EXEMPT and every > 0  # RESUELTO/INFO y los de `every=0` salen siempre
        if capped:
            if self.silenced(key):
                return
            every = max(every, alert_cap_seconds())  # como mucho 1 al día por clave
        if t - self._seen.get(key, 0) < every:
            return
        self._seen[key] = t
        if capped and self.write_log:  # sobrevive a reinicios del monitor (si no, cada reinicio repetiría todo)
            import json
            self._daily = {k: v for k, v in {**self._daily, key: t}.items() if t - v < 7 * 86400}
            try:
                (STATE / "alert_daily.json").write_text(json.dumps(self._daily), encoding="utf-8")
            except OSError:
                pass
        self.alerts.appendleft((now_s(), level, text))
        if not self.write_log:  # la ventana solo muestra; el servicio headless registra y avisa
            return
        with ALERTS_LOG.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {level} {text}\n")
        if level in ("ALTA", "AVISO", "RESUELTO") and self.push:
            h = self.now().hour
            if (h >= QUIET_START or h < QUIET_END) and not key.startswith(OUTAGE_KEYS):
                self._quiet_pending.append(text.split("\n")[0][:160])  # se resume a las 08:00
                return
            self.push(text)

    def ram_report(self) -> None:
        """Informe de RAM al DM cada RAM_REPORT_EVERY (4 h), con el top de procesos; es lo mismo que /ram. De noche
        (silencio) se aplaza al primer ciclo de las 08:00. La hora del último envío se guarda para sobrevivir a reinicios."""
        if not self.push:
            return
        h = self.now().hour
        if h >= QUIET_START or h < QUIET_END:
            return
        stamp = STATE / "ram_report.ts"
        try:
            last = float(stamp.read_text().strip())
        except (OSError, ValueError):
            last = 0.0
        if time.time() - last < RAM_REPORT_EVERY:
            return
        self.push(ram_text())
        try:
            stamp.write_text(str(time.time()))
        except OSError:
            pass

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

    # --- disponibilidad (F1-06) -----------------------------------------------------------------------
    def info_resumen(self, tipo: str, texto: str, clave: str) -> None:
        """INFO al resumen diario del runner (una vez al día por clave); siempre queda también en alerts.log."""
        self.alert("INFO", texto, f"{tipo}-{clave}", every=0)
        if self.write_log:
            disp.anotar_resumen(self.resumen_path, tipo, texto, clave, now=self.now)

    def check_battery(self) -> tuple[float, bool] | None:
        """DISP-05: sin corriente 10 min = AVISO al DM; por debajo del 20 % sin corriente = ALTA (también de noche:
        si se agota, se para el hub). Al enchufar, «resuelto». Cada desenchufe va también al resumen diario."""
        b = disp.bateria(self.read_battery)
        unplugged = b is not None and not b[1]
        pct = b[0] if b else 100.0
        if unplugged and not self.__dict__.get("_bat_unplugged"):  # una línea por desenchufe, no por vuelta
            self.info_resumen("bateria", f"PC sin corriente ({pct:.0f} %) a las {self.now():%H:%M}",
                              f"{self.now():%Y-%m-%d}")
        self._bat_unplugged = unplugged
        self.condition("battery-on", unplugged, f"El PC funciona con batería ({pct:.0f} %).",
                       "enchúfalo: con la tapa cerrada o 10 min inactivo se suspende y el hub se para.",
                       min_hits=1, min_secs=disp.BATERIA_AVISO_SEG, level="AVISO", every=3600)
        self.condition("battery-low", unplugged and pct < disp.BATERIA_ALTA_PCT,
                       f"Batería del PC al {pct:.0f} % y sin corriente: si se agota, se para todo el hub.",
                       "enchúfalo ya.", min_hits=1, level="ALTA", every=1800)
        return b

    def heartbeat(self) -> dict | None:
        """Latido cada 5 min (Oscar HQ y Kuma). Un fallo seguido 30 min avisa una vez; la vuelta, «resuelto»."""
        if not (self.write_log and self.latido):
            return None
        b = disp.bateria(self.read_battery)
        detalle = {"runner": "VIVO" in self.lanes_text or disp.runner_pid_vivo(RUNNER_LOCK),
                   "ram_pct": round(psutil.virtual_memory().percent), "bateria_pct": round(b[0]) if b else None,
                   "enchufado": b[1] if b else None}
        res = self.latido.tick(detalle)
        if res is None:
            return None
        for destino, ok in res.items():
            motivo = self.latido.fallos.get(destino) or "sin respuesta"
            self.condition(f"latido-{destino}", not ok,
                           f"El latido del PC a {'Oscar HQ' if destino == 'ohq' else 'Uptime Kuma'} falla ({motivo}).",
                           "mira la red del PC y la clave/URL del latido en agent-lanes/.env (sin pegarla en el chat).",
                           min_hits=6, every=3600, level="MEDIA")
        return res

    def check_hermes_config(self) -> list[str] | None:
        """F1-07 (CF-08): una vez al día (desde las 9:00) compara la config de Hermes con la plantilla del hub. Solo
        avisa en el resumen diario; nunca cambia nada. None = hoy ya se miró o aún no toca."""
        if not self.write_log:
            return None
        now = self.now()
        hoy = f"{now:%Y-%m-%d}"
        if now.hour < 9 or self.__dict__.get("_hermes_cfg_day") == hoy:
            return None
        self._hermes_cfg_day = hoy
        cmd = [sys.executable, str(HERMES_CHECK), "--check", "--json"]
        try:
            cp = self.run(cmd)
            data = json.loads((cp.stdout or "").strip().splitlines()[-1])
        except Exception as exc:
            self.alert("MEDIA", f"monitor: no pude comprobar la config de Hermes ({type(exc).__name__})",
                       "self-hermes-config", every=86400)
            return None
        lines = [str(x) for x in data.get("deriva") or []]
        if lines:
            self.info_resumen("hermes_config", "Config de Hermes: " + "; ".join(lines)[:260], hoy)
        return lines

    def check_daily_tasks(self) -> list[str]:
        if not (self.write_log and self.tareas):
            return []
        out = self.tareas.tick()
        for name in out:
            self.info_resumen("relanzada", f"«{name}» no se había ejecutado hoy: relanzada a las {self.now():%H:%M}",
                              name)
        return out

    # --- PC -------------------------------------------------------------------------------------------
    def pc(self) -> Panel:
        cpu = psutil.cpu_percent(None)
        self.cpu_hist.append(cpu)
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        free_gb = disk.free / 2**30
        if len(self.cpu_hist) == self.cpu_hist.maxlen and min(self.cpu_hist) > 90:
            self.alert("ALTA", f"CPU sostenida >90% ({cpu:.0f}%)", "cpu")
        if vm.percent > 88:  # solo se ve en la ventana/alerts.log; al DM va el informe de cada 4 h y /ram
            self.alert("MEDIA", f"RAM al {vm.percent:.0f}% ({vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB)", "ram",
                       every=3600)
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
        # Un reinicio deja el runner ~20 s sin proceso, y con Hermes cargado `lanes.py status` tarda y el runner sale
        # «no vivo» 2-3 min y se recupera solo (162 avisos el 23-10). Oscar, 23-10: avisar a los 5 min, no al minuto.
        draining = (LANES / ".state" / "drain.json").exists()
        # DISP-10: con RAM al límite `lanes.py status` tarda o falla y el runner sigue vivo: si el PID de runner.lock
        # vive (y es Python), no es «runner parado». Es el mismo criterio que usa `status` para decir VIVO, así que
        # no se pierde nada: un runner colgado con el proceso vivo tampoco lo detectaba `status`.
        dead = "VIVO" not in self.lanes_text and not disp.runner_pid_vivo(RUNNER_LOCK)
        self.condition("runner-dead", dead and not draining,
                       "El runner de carriles lleva más de cinco minutos parado: no se reclaman tareas (quedan en cola, "
                       "no se pierde nada).",
                       "en el PC: Programador de tareas → "
                       "'agent-lanes runner' → Ejecutar. /salud (bot de Trabajos) te dice el estado.",
                       min_secs=RUNNER_DEAD_SECS, auto_secs=RUNNER_AUTO_SECS, cause=self._causa_runner)
        self.condition("orphan", "huérfano" in self.lanes_text,
                       "Hay un worker funcionando sin runner que lo vigile.",
                       "normalmente se resuelve solo al terminar la tarea; si pasa de 30 min, avísame en la "
                       "sesión de Claude Code o mira /salud.", min_hits=4)

    def _causa_runner(self) -> str:
        """Por qué `lanes.py status` no ve el runner: con RAM al límite Hermes tarda en contestar."""
        try:
            ram = round(psutil.virtual_memory().percent)
        except Exception:
            ram = 0
        if ram >= RAM_ALTA_PCT:
            return f"RAM alta ({ram} %): hermes responde lento"
        return "hermes lento al contestar (el runner puede seguir vivo)"

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
            fixes = {"Oscar HQ": "mira el último deploy en Dokploy (servidor pildora-01) → Oscar HQ → Deployments "
                                 "(si falló, redespliega el anterior) y dime qué ves.",
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

    def refresh_db(self, force: bool = False) -> dict | None:
        """Solo el servicio headless sonda la base de Supabase (cada 5 min), guarda la foto y evalúa los avisos."""
        if not (self.write_log and self.db) or (not force and time.time() - self.db_at < DB_EVERY):
            return None
        self.db_at = time.time()
        snap = self.db.probe()
        self.db.save(snap)
        self.check_db(snap)
        return snap

    def check_db(self, snap: dict) -> None:
        """AVISO: memoria <15 %, load > cores 10 min, >45 conexiones. ALTA: sonda muda o consulta trivial >5 s, 2 veces."""
        fix = self.db.fix_text()
        sql = snap.get("sql") or {}
        self.condition("db-down", not snap["ok"],
                       f"La base de Supabase de Oscar HQ no responde a la sonda ({snap.get('error', '?')}).",
                       fix, min_hits=2, every=1800)
        if not snap["ok"]:
            return  # sin datos fiables no se evalúa (ni se resuelve) el resto
        slow_s = sql["trivial_s"] if "trivial_s" in sql else snap.get("latency_s", 0)
        self.condition("db-slow", slow_s > dbwatch.SLOW_S,
                       f"La base de Supabase de Oscar HQ está lenta: una consulta trivial tarda {slow_s:.0f} s.",
                       fix, min_hits=2, every=1800)
        mem = snap.get("mem_avail_pct")
        self.condition("db-mem", mem is not None and mem < DB_MIN_MEM_PCT,
                       f"Supabase: a la base le queda solo el {mem}% de memoria disponible.",
                       fix, min_hits=2, every=3600, level="AVISO")
        load, cores = snap.get("load1"), snap.get("cores") or 2
        self.condition("db-load", load is not None and load > cores,
                       f"Supabase: carga {load} con {cores} núcleos, mantenida ~10 min.",
                       fix, min_hits=3, every=3600, level="AVISO")  # 3 sondas de 5 min = 10 min
        conns = snap.get("backends")
        self.condition("db-conns", conns is not None and conns > DB_MAX_CONNS,
                       f"Supabase: {conns} conexiones abiertas (máximo del plan: 60).",
                       fix, min_hits=2, every=3600, level="AVISO")

    def refresh_prod(self, force: bool = False) -> None:
        """Solo el servicio headless (cada 5 min): despliegues de PRODUCCIÓN fallidos en Vercel (un aviso por
        despliegue; las vistas previas de lane/* no cuentan) y salud del servidor (health_url + Dokploy)."""
        if not (self.write_log and self.prod) or (not force and time.time() - self.prod_at < PROD_EVERY):
            return
        self.prod_at = time.time()
        for f in self.prod.vercel_failures():
            self.alert("ALTA", f"Vercel: falló el despliegue de PRODUCCIÓN de {f['project']} (commit {f['sha']}"
                       + (f", «{f['message']}»" if f["message"] else "") + ").\n👉 Qué hacer: mira el log en vercel.com → "
                       "proyecto → Deployments. Si dice «Failed deployment from …», el autor del commit no es del "
                       "equipo (lanes.yaml → integrator.commit_author).", f"vercel-prod-{f['id']}", every=10 ** 9)
        for key, active, text, fix in self.prod.server_checks():
            self.condition(key, active, text, fix, min_hits=2)

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

    def refresh_live_lag(self) -> str:
        """Checkout vivo del hub (el que ejecuta el runner) frente a origin/main. Por detrás más de LIVE_LAG_AFTER s =
        el runner corre código viejo (1-5/10: PR #2, #3, #4, #7 y #10 fusionados sin ejecutarse). El botón
        [🔁 Aplicar] lo pone el integrador en el DM; aquí queda el aviso visible en el panel y en alerts.log."""
        if time.time() - getattr(self, "_lag_at", 0) < 300:
            return getattr(self, "_lag_text", "")
        self._lag_at = time.time()
        repo = LANES.parents[1]
        try:
            self.run(["git", "-C", str(repo), "fetch", "-q", "origin", "main"])
            behind = int((self.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD..origin/main"]).stdout
                          or "0").strip() or 0)
        except Exception:
            self._lag_text = "Checkout vivo del hub: ?"
            return self._lag_text
        if not behind:
            self._lag_since = None
            self._lag_text = "Checkout vivo del hub: ✅ en origin/main"
            return self._lag_text
        self._lag_since = getattr(self, "_lag_since", None) or time.time()
        mins = (time.time() - self._lag_since) / 60
        if mins * 60 > LIVE_LAG_AFTER:
            self.alert("MEDIA", f"El runner ejecuta código viejo: el checkout vivo va {behind} commit(s) por detrás de "
                        f"origin/main desde hace {mins:.0f} min. Pulsa 🔁 Aplicar (DM del bot de Trabajos).",
                        "live-lag", every=3600)
        self._lag_text = f"Checkout vivo del hub: ❌ -{behind} vs origin/main ({mins:.0f} min)"
        return self._lag_text

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
        t.append(self.refresh_branch_drift() + "\n")
        t.append(self.refresh_live_lag() + "\n\n")
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


def ohq_api_url_default() -> str:
    """`ohq.api_url` de lanes.yaml (F1-02); OHQ_API_URL del .env manda sobre esto."""
    try:
        import yaml
        cfg = (yaml.safe_load((LANES / "lanes.yaml").read_text(encoding="utf-8")) or {}).get("ohq") or {}
        return str(cfg.get("api_url") or "").strip() if isinstance(cfg, dict) else ""
    except Exception:
        return ""


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
    envs = [LANES / ".env", Path(__file__).resolve().parent / ".env"]
    prod = prodwatch.from_config(LANES / "lanes.yaml", envs, STATE / "prodwatch.json", skip_urls=HEALTH.values())
    env = dbwatch.read_env((disp.OHQ_KEY, disp.KUMA_KEY, disp.OHQ_URL_KEY), envs)
    latido = disp.Latido.desde_env(env, api_url_defecto=ohq_api_url_default())
    mon = Monitor(write_log=True, push=dm_pusher(), db=dbwatch.from_env(envs), prod=prod, latido=latido)
    mon.tareas = disp.TareasDiarias(DAILY_TASKS, estado=STATE / "tareas_diarias.json", run=mon.run, now=mon.now)
    mon.alert("INFO", "Monitor headless arrancado" + ("" if mon.db else " (vigilancia de Supabase desactivada: falta "
              + dbwatch.ENV_KEY + ")"), every=0)
    try:  # F1-02: una línea INFO con cada vigilancia y su estado (solo nombres); nunca impide arrancar
        partes = [f"Supabase: {'activa' if mon.db else 'desactivada (falta ' + dbwatch.ENV_KEY + ')'}"]
        partes += prod.estado_vigilancias() if prod else ["prodwatch: sin nada que vigilar en lanes.yaml"]
        partes += latido.estado()
        mon.alert("INFO", "Vigilancias: " + " · ".join(partes), "vigilancias-arranque", every=0)
    except Exception as exc:
        mon.alert("MEDIA", f"monitor: no pude resumir las vigilancias: {exc}", "self-vigilancias", every=3600)
    started = code_fingerprint()
    while True:
        for step in (mon.pc, mon.system, mon.chats, mon.alerts_panel, mon.refresh_db, mon.refresh_prod,
                     mon.check_battery, mon.heartbeat, mon.check_daily_tasks, mon.check_hermes_config):
            try:
                step()
            except Exception as exc:  # una comprobación rota no para las demás
                mon.alert("MEDIA", f"monitor: fallo en {step.__name__}: {exc}", f"self-{step.__name__}", every=3600)
        mon.flush_quiet()
        try:
            mon.ram_report()
        except Exception as exc:
            mon.alert("MEDIA", f"monitor: fallo en ram_report: {exc}", "self-ram_report", every=3600)
        try:
            if restart_if_code_changed(started, alert=mon.alert):  # un merge cambió tools/monitor: relevo y salida
                mon.alert("INFO", "Monitor reiniciado: código nuevo en tools/monitor", every=0)
                return 0
        except Exception as exc:
            mon.alert("MEDIA", f"monitor: fallo en el reinicio: {exc}", "self-restart", every=3600)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
