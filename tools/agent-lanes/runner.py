"""agent-lanes runner.

    py -3.12 runner.py --all                                 # servicio: todos los carriles + review (tarea programada)
    py -3.12 runner.py --lane claude-oscarhq --once          # una pasada de un carril (E2E)
    py -3.12 runner.py --check                               # solo test de contrato de la CLI de hermes
    py -3.12 lanes.py status                                 # carriles libres/ocupados
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from agent_lanes.config import ROOT, load_env, load_lanes, load_runner_settings, load_telegram_settings
from agent_lanes.decisions import OWNER_TELEGRAM_ID, CallbackStore, DecisionDesk, UpdatePoller
from agent_lanes.git_ops import GitOps
from agent_lanes.hermes import HermesCLI
from agent_lanes.review import ClaudeReviewer, ReviewRunner, sweep_done
from agent_lanes.notices import LinkBuilder, MessageStore
from agent_lanes.runner import MESSAGES_DIR, LaneRunner
from agent_lanes.service import Service, acquire_single_instance
from agent_lanes.telegram import TelegramNotifier
from agent_lanes.verify import verify
from agent_lanes.worker import ClaudeWorker

LOCK = ROOT / ".state" / "runner.lock"
CALLBACKS_DIR = ROOT / ".state" / "callbacks"  # un JSON por teclado enviado (callback_data corto -> tarea/acción)
TG_OFFSET = ROOT / ".state" / "tg_offset"      # offset de getUpdates del bot de carriles


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="claude-oscarhq", help="un solo carril (ignorado con --all)")
    ap.add_argument("--all", action="store_true", help="todos los carriles de lanes.yaml, review incluido")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=0, help="segundos entre pasadas (0 = runner.interval_seconds)")
    ap.add_argument("--max-workers", type=int, default=0, help="tope global de claude simultáneos (0 = lanes.yaml)")
    ap.add_argument("--check", action="store_true", help="solo test de contrato de la CLI de hermes")
    ap.add_argument("--exclude", nargs="*", default=[], help="ids de tarea que este runner no debe reclamar")
    ap.add_argument("--until-one", action="store_true", help="salir tras procesar una tarea")
    ap.add_argument("--max-minutes", type=float, default=0, help="salir tras N minutos (0 = sin límite)")
    ap.add_argument("--log-file", default="", help="log rotativo (el servicio usa .state/runner.log)")
    args = ap.parse_args(argv)
    handlers = [logging.StreamHandler()]
    if args.log_file:
        handlers = [RotatingFileHandler(args.log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
                        handlers=handlers)
    log = logging.getLogger("agent_lanes")

    all_lanes = load_lanes()
    settings = load_runner_settings()
    selected = all_lanes if args.all else {args.lane: all_lanes[args.lane]}
    hermes_cache: dict[str, HermesCLI] = {}

    def hermes_for(board: str) -> HermesCLI:
        return hermes_cache.setdefault(board, HermesCLI(board))

    problems = hermes_for(next(l.board for l in all_lanes.values() if l.board)).check_contract()
    if problems:
        log.error("Contrato de la CLI de hermes ROTO; el runner no arranca:\n  - %s", "\n  - ".join(problems))
        return 3
    log.info("contrato hermes OK")
    if args.check:
        return 0
    if not acquire_single_instance(LOCK):
        log.warning("ya hay un runner vivo (%s); salgo", LOCK)
        return 0

    env = load_env()
    allowed = {c.strip() for c in env.get("TELEGRAM_ALLOWED_CHATS", "").split(",") if c.strip()}
    tg_settings = load_telegram_settings()
    # CARRILES_BOT_TOKEN = bot propio de los carriles: avisos con botones + escucha de pulsaciones. Sin él, el bot de
    # Hermes y sin botones ni getUpdates (Hermes ya lo consume; dos consumidores del mismo bot chocan).
    lanes_bot = env.get("CARRILES_BOT_TOKEN") or None
    notify = TelegramNotifier(lanes_bot or env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"),
                              env.get("TELEGRAM_THREAD_ID"), allowed_chats=allowed,
                              generic_origins=tg_settings["generic_origins"])
    if not notify.enabled:
        log.warning("Telegram desactivado (faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID en agent-lanes/.env)")
    # Un solo almacén de message_id para todos los carriles: la tarea pasa del implementador a review y vuelve.
    messages = MessageStore(MESSAGES_DIR)
    links = LinkBuilder(env.get("KANBAN_BASE_URL") or tg_settings["kanban_base_url"])
    decisions = None
    if lanes_bot and notify.enabled and not args.once:
        decisions = DecisionDesk(notify, CallbackStore(CALLBACKS_DIR), lanes=all_lanes, hermes_for=hermes_for,
                                 links=links, owner_id=env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID)
        UpdatePoller(notify, decisions, TG_OFFSET).start()
        log.info("bot de carriles activo (id %s): avisos con botones y escucha de decisiones", notify.bot_id)
    else:
        log.info("sin bot de carriles: avisos con el bot de Hermes, sin botones")
    git = GitOps()
    impl = {n: l for n, l in selected.items() if l.kind == "implement"}
    runners: list = [LaneRunner(l, hermes=hermes_for(l.board), git=git, worker=ClaudeWorker(), verifier=verify,
                                notify=notify, exclude=set(args.exclude), messages=messages, links=links,
                                decisions=decisions)
                     for l in impl.values()]
    for name, lane in selected.items():
        if lane.kind == "review":
            runners.append(ReviewRunner(lane, all_lanes, hermes_for=hermes_for, git=git, reviewer=ClaudeReviewer(),
                                        verifier=verify, notify=notify, messages=messages, links=links,
                                        decisions=decisions))
    service = Service(runners, max_workers=args.max_workers or settings["max_workers"])
    interval = args.interval or settings["interval_seconds"]
    log.info("runner: carriles=%s max_workers=%s interval=%ss", list(selected), service.max_workers, interval)

    started = time.monotonic()
    for r in runners:
        if isinstance(r, LaneRunner) and (orphans := r.reconcile()):
            log.warning("reconciliación %s: bloqueadas por runner reiniciado: %s", r.lane.name, orphans)
    while True:
        for lane in impl.values():
            try:
                if cleaned := sweep_done(lane, hermes_for(lane.board), git):
                    log.info("%s: worktrees limpiados tras done: %s", lane.name, cleaned)
            except Exception as exc:
                log.warning("%s: limpieza falló: %s", lane.name, exc)
        results = service.run_pass()
        if results:
            log.info("pasada: %s", results)
        if args.once or (args.until_one and results):
            return 0
        if args.max_minutes and time.monotonic() - started > args.max_minutes * 60:
            log.info("max-minutes alcanzado sin más trabajo")
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
