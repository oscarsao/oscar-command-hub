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

from agent_lanes import ciego, drain, notices
from agent_lanes.bandeja import load as load_bandeja
from agent_lanes.board_guard import BoardGuardTicker
from agent_lanes.choques import ChoquesTicker
from agent_lanes.solapes import SolapeTicker
from agent_lanes.config import (ROOT, load_env, load_lanes, load_notice_limits, load_runner_settings,
                                load_telegram_settings)
from agent_lanes.commands import CommandCenter, register_commands
from agent_lanes.collab_intake import CollabIntake
from agent_lanes.people import load_people
from agent_lanes.decisions import CALLBACKS_DIR, OWNER_TELEGRAM_ID, CallbackStore, DecisionDesk, UpdatePoller
from agent_lanes import explain, telegram_lane, triaje
from agent_lanes.deps import ShowCache, TreeReader
from agent_lanes.hermes_answers import HermesAnswers
from agent_lanes.reminders import Reminders
from agent_lanes.limpieza import LimpiezaRunner, LimpiezaTicker
from agent_lanes.paradas import ParadasRunner, ParadasTicker
from agent_lanes.reconcile import ReconcileTicker, Reconciler, gh_prs
from agent_lanes.aviso import REMIND_HOURS, StuckWatcher
from agent_lanes.git_ops import GitOps, github_slug_problem
from agent_lanes.hermes import HermesCLI
from agent_lanes.integration import IntegrationRoute
from agent_lanes.integrator import build_integrator, load_integrator_settings
from agent_lanes.promote import Promoter, load_promote_configs
from agent_lanes.review import ClaudeReviewer, ReviewRunner, sweep_done
from agent_lanes.notices import LinkBuilder, MessageStore
from agent_lanes.ops import OpsWorkspace, verify_ops
from agent_lanes.runner import MESSAGES_DIR, STATE_DIR, LaneRunner
from agent_lanes.service import Service, acquire_single_instance
from agent_lanes.telegram import notifier_from_env
from agent_lanes.verify import verify
from agent_lanes.worker import ClaudeWorker

LOCK = ROOT / ".state" / "runner.lock"
TG_OFFSET = ROOT / ".state" / "tg_offset"      # offset de getUpdates del bot de carriles
REMINDERS_STATE = ROOT / ".state" / "reminders.json"  # franjas 13:00/18:00 ya enviadas
STUCK_STATE = ROOT / ".state" / "stuck_avisos.json"  # avisos de 🧊 atascadas ya enviados (tarjeta:estado:ronda)
COLLAB_STATE = ROOT / ".state" / "collab_reject.json"  # DM de desconocidos ya rechazados (1 cada 6 h)


def effective_max_workers(arg: int, settings: dict) -> int:
    """`--max-workers N` (>0) pisa a lanes.yaml `runner.max_workers`; 0 = lo que diga lanes.yaml."""
    return arg or settings["max_workers"]


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
    notices.configure(load_notice_limits())
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
    tg_settings = load_telegram_settings()
    # CARRILES_BOT_TOKEN = bot propio de los carriles: avisos con botones + escucha de pulsaciones. Sin él, el bot de
    # Hermes y sin botones ni getUpdates (Hermes ya lo consume; dos consumidores del mismo bot chocan).
    notify, lanes_bot = notifier_from_env(env, tg_settings)
    if not notify.enabled:
        log.warning("Telegram desactivado (faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID en agent-lanes/.env)")
    # Un solo almacén de message_id para todos los carriles: la tarea pasa del implementador a review y vuelve.
    messages = MessageStore(MESSAGES_DIR)
    links = LinkBuilder(env.get("KANBAN_BASE_URL") or tg_settings["kanban_base_url"])
    decisions = None
    if lanes_bot and notify.enabled and not args.once:
        owner = env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID
        decisions = DecisionDesk(notify, CallbackStore(CALLBACKS_DIR), lanes=all_lanes, hermes_for=hermes_for,
                                 links=links, owner_id=owner, messages=messages, free_text=tg_settings["free_text"])
        username = None
        try:
            username = notify.get_me().get("username")
        except Exception as exc:
            log.warning("getMe falló (los comandos con @sufijo no se reconocerán): %s", exc)
        commands = CommandCenter(notify, decisions, lanes=all_lanes, hermes_for=hermes_for, messages=messages,
                                 links=links, generic_origins=tg_settings["generic_origins"], bot_username=username,
                                 owner_id=owner, bandeja=load_bandeja(), asker=explain.answer,
                                 classifier=triaje.clasificar)
        decisions.commands = commands
        decisions.team_notice = tg_settings.get("team_notice") or None
        decisions.collab = CollabIntake(notify, load_people(owner), hermes_for=hermes_for, state_path=COLLAB_STATE)
        log.info("comandos del bot registrados en: %s", register_commands(notify))
        UpdatePoller(notify, decisions, TG_OFFSET).start()
        watch = None
        if tg_settings.get("attention"):  # tema Atención: aviso accionable de cada 🧊 y recordatorio al DM a las 24 h
            att = tg_settings["attention"]
            ren = commands.renotifier()
            ren.attention = att
            watcher = StuckWatcher(commands.stuck_tasks, lambda p: ren._publish(p, ren.messages.get(p.tid)),
                                   ren.remind_dm, STUCK_STATE, hours=float(att.get("remind_hours") or REMIND_HOURS))
            watch = watcher.tick
        Reminders(notify, owner, commands.pending_decisions, REMINDERS_STATE, cards=commands.decision_cards,
                  stuck=commands.stuck_tasks, base_url=links.kanban_base_url, veto=commands.veto_tick,
                  watch=watch).start()
        log.info("bot de carriles activo (id %s, @%s): avisos con botones, comandos, escucha y recordatorios",
                 notify.bot_id, username)
    else:
        log.info("sin bot de carriles: avisos con el bot de Hermes, sin botones")
    # Árbol padre/hijas en los avisos: lecturas `show` cacheadas (60 s y vaciadas en cada pasada del bucle).
    shows = ShowCache(ttl=60)
    tree = TreeReader(hermes_for, shows)
    if decisions:
        decisions.tree = tree
    if not args.once:  # RESPUESTA-OSCAR vía Hermes: hilo propio (el bucle puede esperar horas a un worker)
        HermesAnswers(all_lanes, hermes_for=hermes_for, desk=decisions, alert=drain.dm_alert(env)).start()
    git = GitOps()
    impl = {n: l for n, l in selected.items() if l.kind == "implement"}
    # Candado de repos: un carril de código solo reclama si el remote de su repo es el `github:` de lanes.yaml.
    runners: list = [LaneRunner(l, hermes=hermes_for(l.board), git=git, worker=ClaudeWorker(pid_dir=STATE_DIR),
                                verifier=verify, notify=notify, exclude=set(args.exclude), messages=messages, links=links,
                                decisions=decisions, tree=tree, repo_guard=github_slug_problem, lanes=all_lanes,
                                final_only=tg_settings["final_only"])
                     for l in impl.values()]
    # Carril ops: sin repo. Carpeta de trabajo por tarea + verificación por evidencias; siempre acaba en review para
    # Oscar (no está en `review.reviews`, y sweep_done no lo toca: el workspace se conserva).
    runners += [LaneRunner(l, hermes=hermes_for(l.board), git=OpsWorkspace(), worker=ClaudeWorker(pid_dir=STATE_DIR), verifier=verify_ops,
                           notify=notify, exclude=set(args.exclude), messages=messages, links=links, decisions=decisions,
                           tree=tree, final_only=tg_settings["final_only"])
                for l in selected.values() if l.kind == "ops"]
    # Tema de Integración (integration_telegram): la tarjeta done con ✅ Aprobar va allí aunque el integrador esté apagado.
    integration = (IntegrationRoute(tg_settings["integration"], load_integrator_settings(env=env, lane_filter=False).policies)
                   if tg_settings.get("integration") else None)
    for name, lane in selected.items():
        if lane.kind == "review":
            runners.append(ReviewRunner(lane, all_lanes, hermes_for=hermes_for, git=git, reviewer=ClaudeReviewer(),
                                        verifier=verify, notify=notify, messages=messages, links=links,
                                        decisions=decisions, integration=integration, tree=tree,
                                        state_dir=ROOT / ".state" / "review-blocked"))
    # Carril Integrador: solo con INTEGRATOR_ENABLED. Sin bot de carriles corre los gates y avisa sin botones.
    integrator = build_integrator(env, all_lanes, hermes_for=hermes_for, links=links, notifier=notify,
                                  messages=messages, desk=decisions)
    if integrator:
        integrator.tree = tree
        if decisions:
            decisions.integrator = integrator
        log.info("integrador activo: %s", list(integrator.settings.policies))
        drain.mark_integrator_active()  # lo comprueba `lanes.py restart --drain` tras reiniciar
        if decisions and notify:  # /promover y [🚀 Promover a producción]: sección `promote:` de lanes.yaml
            configs = load_promote_configs()
            if configs:
                decisions.promoter = Promoter(configs, notifier=notify, desk=decisions)
                log.info("promoción a producción activa: %s", list(configs))
    service = Service(runners, max_workers=effective_max_workers(args.max_workers, settings), busy_path=drain.BUSY_FILE)
    interval = args.interval or settings["interval_seconds"]
    log.info("runner: carriles=%s max_workers=%s interval=%ss", list(selected), service.max_workers, interval)

    if not args.once:  # plaza reservada: hilo propio, para que arranque aunque la pasada general espere a sus workers
        service.start_reserved_ticker(interval, paused=drain.active)
    started = time.monotonic()
    drain.discard_stale()  # un drain.json de más de 2 h (restart muerto a medias) no deja los carriles parados
    for r in runners:
        if isinstance(r, LaneRunner) and (orphans := r.reconcile()):
            log.warning("reconciliación %s: bloqueadas por runner reiniciado: %s", r.lane.name, orphans)
    draining_logged = False
    # Runner ciego: mismo fallo al listar tareas durante `ciego_minutes` -> aviso ALTA al DM + restart --drain (1/hora).
    watch = None if args.once else ciego.Vigilante(minutes=float(settings["ciego_minutes"]), alert=drain.dm_alert(env))
    guard = None
    reconcile = None
    limpieza = None
    paradas = None
    solapes = None
    choques = None
    if decisions and not args.once:  # PR resueltos -> tarjetas cerradas; propuesta diaria de obsoletas (con botón)
        owner_dm = env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID
        boards = sorted({l.board for l in all_lanes.values() if l.board})
        slugs = sorted({l.github for l in all_lanes.values() if l.github})
        recon = Reconciler(hermes_for, boards, lambda: gh_prs(slugs),
                           lambda text, markup=None: notify.send_to(owner_dm, None, text, html=False,
                                                                    reply_markup=markup),
                           issue=decisions.store.issue, state_path=ROOT / ".state" / "reconcile.json")
        decisions.reconciler = recon
        reconcile = ReconcileTicker(recon, minutes=float(env.get("RECONCILE_MINUTES") or 10))
        limpieza_runner = LimpiezaRunner(hermes_for, boards, lambda: gh_prs(slugs),  # propone cierres; nunca cierra sola
                                         lambda text, markup=None: notify.send_to(owner_dm, None, text, html=False,
                                                                                  reply_markup=markup),
                                         issue=decisions.store.issue, state_path=ROOT / ".state" / "limpieza.json")
        recon.peer, limpieza_runner.peer = limpieza_runner.silenciada, recon.silenciada  # «Mantener» en uno silencia el otro
        decisions.limpieza = limpieza_runner
        limpieza = LimpiezaTicker(limpieza_runner, minutes=float(env.get("RECONCILE_MINUTES") or 10))
        # Aviso de las 9:00 de tarjetas paradas > 24 h: al tema de Integración (lanes.yaml) o, sin él, al DM de Oscar.
        p_chat, p_thread = integration.target if integration else (owner_dm, None)
        paradas_runner = ParadasRunner(all_lanes, hermes_for,
                                       lambda text, markup=None: notify.send_to(p_chat, p_thread, text, html=False,
                                                                                reply_markup=markup),
                                       issue=decisions.store.issue, state_path=ROOT / ".state" / "paradas.json")
        decisions.paradas = paradas_runner
        paradas = ParadasTicker(paradas_runner, minutes=float(env.get("RECONCILE_MINUTES") or 10))
    if not args.once:  # guarda de tablero (ADR-036): tarjetas en un tablero que su carril no lee
        owner_dm = env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID
        guard = BoardGuardTicker(all_lanes, hermes_for, apply=env.get("BOARD_GUARD_APPLY", "").lower() == "true",
                                 send=lambda text: notify.send_to(owner_dm, None, text, html=False),
                                 state_path=ROOT / ".state" / "board_guard.json",
                                 minutes=float(env.get("BOARD_GUARD_MINUTES") or 10))
        # Solape de alcance entre tarjetas activas del mismo repo: avisa a Oscar, no fusiona nada (solapes.yaml).
        solapes = SolapeTicker(all_lanes, hermes_for,
                               send=lambda text, markup=None: notify.send_to(owner_dm, None, text, html=False,
                                                                             reply_markup=markup),
                               state_path=ROOT / ".state" / "solapes.json",
                               issue=decisions.store.issue if decisions else None)
        if decisions:
            decisions.solapes = solapes  # los botones del aviso los atiende el despachador de decisiones
        # Choques reales en git (ramas lane/*, PR, ramas de personas): solo avisa a Oscar con propuesta (choques.yaml).
        choques = ChoquesTicker(all_lanes, hermes_for, send=lambda text: notify.send_to(owner_dm, None, text, html=False),
                                state_path=ROOT / ".state" / "choques.json")
    while True:
        shows.clear()
        # Reinicio ordenado (lanes.py restart --drain): entre pasadas, sin reclamar nada; el acuse dice si queda
        # algo en marcha fuera del pool (deploy/gates lanzados por un botón de Oscar).
        if drain.active():
            busy = integrator.busy() if integrator else []
            drain.write_ack(busy=busy)
            if not draining_logged:
                log.info("drenando: no se reclaman tareas; esperando el reinicio%s",
                         f" (en marcha: {', '.join(busy)})" if busy else "")
                draining_logged = True
            if args.once:
                return 0
            time.sleep(min(interval, 5))
            continue
        draining_logged = False
        for lane in impl.values():
            try:
                if cleaned := sweep_done(lane, hermes_for(lane.board), git):
                    log.info("%s: worktrees limpiados tras done: %s", lane.name, cleaned)
            except Exception as exc:
                log.warning("%s: limpieza falló: %s", lane.name, exc)
        if integrator:
            try:
                if integrated := integrator.run_pass():
                    log.info("integrador: %s", integrated)
            except Exception as exc:
                log.warning("integrador falló: %s", exc)
        if guard:
            guard.tick()
        if solapes:
            solapes.tick()
        if choques:
            choques.tick()
        if reconcile:
            reconcile.tick()
        if limpieza:
            limpieza.tick()
        if paradas:
            paradas.tick()
        try:  # tarjetas de Telegram que ya estaban en hub/oscarhq (ready) -> claude-telegram; las running no se tocan
            if moved := telegram_lane.reasignar_abiertas(hermes_for, all_lanes):
                log.info("claude-telegram: tarjetas reasignadas: %s", moved)
        except Exception as exc:
            log.warning("reasignación a claude-telegram falló: %s", exc)
        results = service.run_pass()
        if args.once:  # sin hilo de fondo: la plaza reservada también se atiende y se espera
            service.reserved_pass()
            results.update(service.wait_reserved())
        if watch:
            watch.observe(service.list_errors)
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
