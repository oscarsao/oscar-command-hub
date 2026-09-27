"""agent-lanes runner.

    py -3.12 runner.py --lane claude-oscarhq --once        # one pass (E2E / scheduled task)
    py -3.12 runner.py --lane claude-oscarhq --interval 60 # loop
    py -3.12 runner.py --check                             # hermes CLI contract only
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from agent_lanes.config import load_env, load_lanes
from agent_lanes.git_ops import GitOps
from agent_lanes.hermes import HermesCLI
from agent_lanes.runner import LaneRunner
from agent_lanes.telegram import TelegramNotifier
from agent_lanes.verify import verify
from agent_lanes.worker import ClaudeWorker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="claude-oscarhq")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--check", action="store_true", help="solo test de contrato de la CLI de hermes")
    ap.add_argument("--exclude", nargs="*", default=[], help="ids de tarea que este runner no debe reclamar")
    ap.add_argument("--until-one", action="store_true", help="salir tras procesar una tarea")
    ap.add_argument("--max-minutes", type=float, default=0, help="salir tras N minutos (0 = sin límite)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("agent_lanes")

    lane = load_lanes()[args.lane]
    hermes = HermesCLI(lane.board)
    problems = hermes.check_contract()
    if problems:
        log.error("Contrato de la CLI de hermes ROTO; el runner no arranca:\n  - %s", "\n  - ".join(problems))
        return 3
    log.info("contrato hermes OK")
    if args.check:
        return 0

    env = load_env()
    notify = TelegramNotifier(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"), env.get("TELEGRAM_THREAD_ID"))
    if not notify.enabled:
        log.warning("Telegram desactivado (faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID en agent-lanes/.env)")
    runner = LaneRunner(lane, hermes=hermes, git=GitOps(), worker=ClaudeWorker(), verifier=verify, notify=notify,
                         exclude=set(args.exclude))
    started = time.monotonic()
    orphans = runner.reconcile()
    if orphans:
        log.warning("reconciliación: bloqueadas por runner reiniciado: %s", orphans)
    while True:
        results = runner.run_once()
        if results:
            log.info("pasada: %s", results)
        if args.once or (args.until_one and results):
            return 0
        if args.max_minutes and time.monotonic() - started > args.max_minutes * 60:
            log.info("max-minutes alcanzado sin más trabajo")
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
