"""Botones del aviso de solape/choque (t_ee6ad56c): Oscar decide qué hacer con dos tarjetas que tocan lo mismo.

[🔗 Fusionar en una] una tarjeta nueva con el alcance de ambas (cuerpo combinado; `Rama-origen` si una ya empezó) y las
                     originales se archivan con un comentario que enlaza a la nueva.
[⛓ Encadenar]        la segunda pasa a depender de la primera (`kanban link` + `ESPERA-A`, como «Esperar a…»).
[✅ Se complementan] la pareja queda revisada (`solapes_descartados.json`) y no se vuelve a avisar; ambas siguen.
[🗂 Duplicada]       archiva la elegida con enlace a la otra.
[⏸ Pausar una]       bloquea la elegida (no la que está en curso).
«🔗 Fusionar con…» en la ficha (/tarea): lista tarjetas abiertas del mismo repo y fusiona la elegida con esta.

Fusionar y archivar una tarjeta en curso (running) pide un segundo toque. Todo es idempotente (un token se gasta una vez,
una tarjeta ya archivada/hecha se rechaza y la tarjeta fusionada lleva `idempotency-key`) y se firma con quien pulsa
(`desk._who()`). Los permisos por marca los aplica el despachador antes de llegar aquí (`puede_con`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from . import deps, solapes
from .hermes import OSCAR_AUTHOR

log = logging.getLogger("agent_lanes")

AUTHOR = "oscar-telegram"
FUS_MERGE, FUS_CHAIN, FUS_OK = "fus_merge", "fus_chain", "fus_ok"
FUS_DUP, FUS_PAUSE = "fus_dup", "fus_pause"                       # piden elegir tarjeta
FUS_DUP_PICK, FUS_PAUSE_PICK = "fus_dup_pick", "fus_pause_pick"   # la elegida (button index)
FUS_MERGE_YES, FUS_DUP_YES = "fus_merge_yes", "fus_dup_yes"       # 2.º toque con tarjeta en curso
FUS_WITH, FUS_WITH_PICK, FUS_NO = "fus_with", "fus_with_pick", "fus_no"
FUS_ACTIONS = (FUS_MERGE, FUS_CHAIN, FUS_OK, FUS_DUP, FUS_PAUSE, FUS_DUP_PICK, FUS_PAUSE_PICK, FUS_MERGE_YES,
               FUS_DUP_YES, FUS_WITH, FUS_WITH_PICK, FUS_NO)
ABIERTAS = ("ready", "todo", "running", "blocked")
CERRADAS = ("done", "archived")
MAX_CANDIDATAS = 8
_TID_RE = re.compile(r"^t_[0-9a-f]{8}$")


def teclado_solape() -> list[list[dict]]:
    """Botones del aviso de solape/choque. Las tarjetas van en el registro (`cards`)."""
    return [[{"text": "🔗 Fusionar en una", "action": FUS_MERGE}, {"text": "⛓ Encadenar", "action": FUS_CHAIN}],
            [{"text": "✅ Se complementan, seguir", "action": FUS_OK}, {"text": "🗂 Duplicada", "action": FUS_DUP}],
            [{"text": "⏸ Pausar una", "action": FUS_PAUSE}]]


def registro_tarjetas(cards: list[dict]) -> dict | None:
    """Registro del teclado para varias tarjetas [{tid, board, lane?, title?}]; None si faltan menos de 2."""
    cards = [{"tid": c["tid"], "board": c.get("board") or "", "lane": c.get("lane") or "", "title": c.get("title") or ""}
             for c in cards if _TID_RE.match(str(c.get("tid") or ""))]
    if len(cards) < 2:
        return None
    first = cards[0]
    return {"task_id": first["tid"], "board": first["board"], "lane": first["lane"], "title": first["title"],
            "solape": True, "cards": cards}


def _short(text: str, n: int = 40) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


class Fusion:
    def __init__(self, lanes: dict, hermes_for, *, descartes_path: Path | None = None):
        self.lanes, self.hermes_for = lanes, hermes_for
        self.descartes_path = descartes_path or solapes.DESCARTES_PATH

    # --- utilidades ---------------------------------------------------------------------------------------

    def _load(self, c: dict) -> dict | None:
        """{tid, board, lane, h, status, title, body, assignee, show} o None si Hermes no la devuelve."""
        try:
            h = self.hermes_for(c["board"])
            show = h.show(c["tid"])
            task = show.get("task") or {}
        except Exception as exc:
            log.info("fusion: %s no leída: %s", c.get("tid"), exc)
            return None
        if not task:
            return None
        return {"tid": c["tid"], "board": c["board"], "lane": c.get("lane") or task.get("assignee") or "", "h": h,
                "status": task.get("status") or "", "title": task.get("title") or c.get("title") or "",
                "body": task.get("body") or "", "assignee": task.get("assignee") or c.get("lane") or "", "show": show}

    def _cargar(self, rec: dict, desk, where) -> list[dict] | None:
        """Tarjetas del registro, frescas. None (y mensaje) si alguna no se lee o ya no está abierta."""
        cards = [c for c in rec.get("cards") or () if c.get("tid")]
        loaded = [self._load(c) for c in cards]
        if len(loaded) < 2 or any(x is None for x in loaded):
            desk._edit_text(where, "No pude leer las tarjetas ahora; vuelve a intentarlo desde su ficha (/tarea)")
            return None
        cerradas = [x["tid"] for x in loaded if x["status"] in CERRADAS]
        if cerradas:
            desk._edit_text(where, f"Nada que hacer: {', '.join(cerradas)} ya no está(n) abierta(s) (ya resuelto)")
            return None
        return loaded

    def _sub(self, desk, rec: dict, spec: list[list[dict]], where, text: str) -> bool:
        """Edita el mensaje con una pregunta y teclado nuevo (sub-paso o confirmación)."""
        markup = desk.store.issue(rec, spec)[1]
        if not where:
            return False
        desk.notifier.edit(where["chat_id"], where["message_id"], text, html=False, reply_markup=markup)
        return True

    @staticmethod
    def _enlace(loaded: list[dict], c: dict) -> str:
        return ", ".join(x["tid"] for x in loaded if x["tid"] != c["tid"])

    # --- botones del aviso ----------------------------------------------------------------------------------

    def on_button(self, action: str, rec: dict, where: dict | None, desk, index=None) -> bool:
        who = desk._who()
        if action == FUS_NO:
            desk._edit_text(where, "✖ Cancelado: no se ha tocado ninguna tarjeta")
            return True
        if action == FUS_WITH:
            return self._with(rec, where, desk)
        if action == FUS_OK:
            return self._ok(rec, where, desk, who)
        if action == FUS_WITH_PICK:  # ficha: esta tarjeta (rec.task_id) + la elegida
            return self._with_pick(rec, where, desk, who, index)
        loaded = self._cargar(rec, desk, where)
        if loaded is None:
            return True
        if action in (FUS_MERGE, FUS_MERGE_YES):
            return self._fusionar(loaded, rec, where, desk, who, confirmado=action == FUS_MERGE_YES)
        if action == FUS_CHAIN:
            return self._encadenar(loaded, where, desk, who)
        if action in (FUS_DUP, FUS_PAUSE):
            verbo = "archivar como duplicada" if action == FUS_DUP else "pausar"
            pick = FUS_DUP_PICK if action == FUS_DUP else FUS_PAUSE_PICK
            spec = [[{"text": f"{x['tid']} «{_short(x['title'], 28)}»", "action": pick, "index": i}]
                    for i, x in enumerate(loaded)] + [[{"text": "✖ Cancelar", "action": FUS_NO}]]
            return self._sub(desk, rec, spec, where, f"¿Cuál quieres {verbo}?")
        if action in (FUS_DUP_PICK, FUS_DUP_YES):
            return self._duplicada(loaded, rec, where, desk, who, index, confirmado=action == FUS_DUP_YES)
        if action == FUS_PAUSE_PICK:
            return self._pausar(loaded, where, desk, who, index)
        return False

    # --- acciones -------------------------------------------------------------------------------------------

    def _ok(self, rec: dict, where, desk, who: str) -> bool:
        tids = [c["tid"] for c in rec.get("cards") or () if c.get("tid")]
        descartes = solapes.load_descartes(self.descartes_path)
        descartes.update(solapes.par(a, b) for i, a in enumerate(tids) for b in tids[i + 1:])
        try:
            self.descartes_path.parent.mkdir(parents=True, exist_ok=True)
            self.descartes_path.write_text(json.dumps(sorted(descartes)), encoding="utf-8")
        except OSError as exc:
            log.warning("fusion: no se pudo guardar el descarte: %s", exc)
            desk._edit_text(where, "No se pudo guardar la revisión; detalle en el log")
            return False
        desk._edit_text(where, f"✅ {', '.join(tids)}: se complementan ({who}); siguen las dos y no se vuelve a avisar")
        return True

    def _fusionar(self, loaded: list[dict], rec: dict, where, desk, who: str, *, confirmado: bool) -> bool:
        if len({x["board"] for x in loaded}) > 1:
            desk._edit_text(where, "No se pueden fusionar tarjetas de tableros distintos; hazlo a mano")
            return True
        enmarcha = [x["tid"] for x in loaded if x["status"] == "running"]
        if enmarcha and not confirmado:
            spec = [[{"text": "✅ Sí, fusionar", "action": FUS_MERGE_YES}, {"text": "✖ No", "action": FUS_NO}]]
            return self._sub(desk, rec, spec, where,
                             f"⚠️ {', '.join(enmarcha)} está en curso. Fusionar la archiva y el trabajo seguirá en la "
                             f"tarjeta nueva (misma rama). ¿Seguro?")
        base = next((x for x in loaded if x["status"] == "running"), loaded[0])
        tids = sorted(x["tid"] for x in loaded)
        titulo = _short(f"{base['title']} (+ fusión con {self._enlace(loaded, base)})", 140)
        partes = [f"## Fusión de {', '.join(tids)} (decidida por {who} desde un aviso de solape)\n"]
        for x in [base] + [y for y in loaded if y is not base]:
            partes.append(f"### {x['tid']} — {x['title']}\n\n{x['body'].strip()}\n")
        if base["status"] == "running":
            partes.append(f"Rama-origen: lane/{base['tid']}\nRama-push: lane/{base['tid']}\n")
        key = "fusion-" + hashlib.sha1("|".join(tids).encode()).hexdigest()[:12]
        try:
            nueva = base["h"].create(titulo, "\n".join(partes), base["assignee"], key=key)
        except Exception as exc:
            log.warning("fusion: no se pudo crear la tarjeta fusionada: %s", exc)
            desk._edit_text(where, f"No pude crear la tarjeta fusionada ({_short(str(exc), 120)}); no se ha tocado nada")
            return False
        fallos = []
        for x in loaded:
            x["h"].comment(x["tid"], f"{who} fusionó esta tarjeta en {nueva} (con {self._enlace(loaded, x)}). "
                                     "Se archiva; el trabajo sigue en la nueva.", author=AUTHOR)
            if not x["h"].archive(x["tid"]):
                fallos.append(x["tid"])
        base["h"].comment(nueva, f"Nace de fusionar {', '.join(tids)} ({who}).", author=AUTHOR)
        desk._edit_text(where, f"🔗 {', '.join(tids)} fusionadas en {nueva} ({who})"
                        + (f" · no pude archivar {', '.join(fallos)}: archívalas a mano" if fallos else ""))
        return True

    def _encadenar(self, loaded: list[dict], where, desk, who: str) -> bool:
        orden = sorted(loaded, key=lambda x: {"running": 0, "ready": 1}.get(x["status"], 2))  # estable
        lineas = []
        for padre, hija in zip(orden, orden[1:]):
            if hija["status"] == "running":
                lineas.append(f"{hija['tid']} está en curso: no puede esperar a {padre['tid']}")
            elif padre["tid"] in deps.declared_waits(hija["show"]):
                lineas.append(f"{hija['tid']} ya esperaba a {padre['tid']}")
            elif hija["tid"] in deps.declared_waits(padre["show"]):
                lineas.append(f"{padre['tid']} ya espera a {hija['tid']}: crearía un ciclo")
            elif not hija["h"].link(padre["tid"], hija["tid"]):
                lineas.append(f"Hermes no aceptó el enlace {padre['tid']} → {hija['tid']}")
            else:
                hija["h"].comment(hija["tid"], deps.wait_comment(padre["tid"]) + f" ({who}, aviso de solape)",
                                  author=OSCAR_AUTHOR)
                lineas.append(f"{hija['tid']} espera a {padre['tid']} (empieza cuando se fusione)")
        desk._edit_text(where, "⛓ " + "; ".join(lineas) + f" · {who}")
        return True

    def _elegida(self, loaded: list[dict], index, where, desk):
        try:
            return loaded[int(index)]
        except (TypeError, ValueError, IndexError):
            desk._edit_text(where, "No reconozco la tarjeta elegida; vuelve a pulsar el botón del aviso")
            return None

    def _duplicada(self, loaded, rec, where, desk, who: str, index, *, confirmado: bool) -> bool:
        if index is None and confirmado:
            index = rec.get("pick")
        x = self._elegida(loaded, index, where, desk)
        if x is None:
            return True
        if x["status"] == "running" and not confirmado:
            spec = [[{"text": "✅ Sí, archivar", "action": FUS_DUP_YES}, {"text": "✖ No", "action": FUS_NO}]]
            return self._sub(desk, {**rec, "pick": int(index)}, spec, where,
                             f"⚠️ {x['tid']} está en curso. Archivarla detiene su trabajo. ¿Seguro?")
        otras = self._enlace(loaded, x)
        x["h"].comment(x["tid"], f"{who} la marcó como duplicada de {otras} (aviso de solape) y la archivó.", author=AUTHOR)
        if not x["h"].archive(x["tid"]):
            desk._edit_text(where, f"No pude archivar {x['tid']}; archívala a mano")
            return False
        for y in loaded:
            if y is not x:
                y["h"].comment(y["tid"], f"{who} archivó {x['tid']} como duplicada de esta (aviso de solape).", author=AUTHOR)
        desk._edit_text(where, f"🗂 {x['tid']} archivada como duplicada de {otras} ({who})")
        return True

    def _pausar(self, loaded, where, desk, who: str, index) -> bool:
        x = self._elegida(loaded, index, where, desk)
        if x is None:
            return True
        if x["status"] == "running":
            desk._edit_text(where, f"{x['tid']} está en curso: no se pausa desde aquí")
            return True
        if x["status"] == "blocked":
            desk._edit_text(where, f"{x['tid']} ya estaba bloqueada/pausada")
            return True
        otras = self._enlace(loaded, x)
        if not x["h"].block(x["tid"], "needs_input", f"⏸ Pausada por {who} desde un aviso de solape con {otras}. "
                            "Reanúdala cuando decidas cómo repartir el trabajo."):
            desk._edit_text(where, f"Hermes no dejó pausar {x['tid']}")
            return False
        x["h"].comment(x["tid"], f"{who} la pausó por solape con {otras}.", author=AUTHOR)
        desk._edit_text(where, f"⏸ {x['tid']} pausada ({who}); {otras} sigue")
        return True

    # --- «🔗 Fusionar con…» en la ficha ---------------------------------------------------------------------

    def candidatas(self, tid: str, board: str, lane_name: str) -> list[dict]:
        """Tarjetas abiertas del mismo repo (cualquier carril con ese repo) distintas de `tid`."""
        lane = self.lanes.get(lane_name)
        repo = solapes._norm_repo(getattr(lane, "repo", "")) if lane is not None else ""
        if not repo:
            return []
        out, vistos = [], {tid}
        boards = list(dict.fromkeys([board] + [l.board for l in self.lanes.values() if getattr(l, "board", "")]))
        for b in boards:
            for status in ABIERTAS:
                try:
                    tasks = self.hermes_for(b).list_status_all(status)
                except Exception:
                    continue
                for t in tasks:
                    other = self.lanes.get(t.get("assignee"))
                    if t.get("id") in vistos or other is None or solapes._norm_repo(getattr(other, "repo", "")) != repo:
                        continue
                    vistos.add(t["id"])
                    out.append({"tid": t["id"], "board": b, "lane": t["assignee"], "title": t.get("title") or ""})
        return out[:MAX_CANDIDATAS]

    def boton_ficha(self, task: dict, board: str, issue) -> list[dict] | None:
        """Fila con «🔗 Fusionar con…» para la ficha de una tarjeta abierta de un carril con repo."""
        lane = self.lanes.get(task.get("assignee"))
        if task.get("status") not in ABIERTAS or lane is None or not getattr(lane, "repo", ""):
            return None
        rec = {"task_id": task["id"], "board": board, "lane": lane.name, "title": task.get("title") or "",
               "cards": [{"tid": task["id"], "board": board, "lane": lane.name}]}
        return issue(rec, [[{"text": "🔗 Fusionar con…", "action": FUS_WITH}]])[1]["inline_keyboard"][0]

    def _with(self, rec: dict, where, desk) -> bool:
        tid = rec["task_id"]
        cands = self.candidatas(tid, rec["board"], rec["lane"])
        if not cands:
            desk._edit_text(where, f"{tid}: no hay otras tarjetas abiertas del mismo repo para fusionar")
            return True
        spec = [[{"text": f"{c['tid']} «{_short(c['title'], 30)}»", "action": FUS_WITH_PICK, "index": i}]
                for i, c in enumerate(cands)] + [[{"text": "✖ Cancelar", "action": FUS_NO}]]
        self_rec = {**rec, "candidates": cands, "cards": [rec["cards"][0]] if rec.get("cards") else []}
        return self._sub(desk, self_rec, spec, where, f"¿Con cuál fusionas {tid}? (mismo repo, abiertas)")

    def _with_pick(self, rec: dict, where, desk, who: str, index) -> bool:
        cands = rec.get("candidates") or []
        try:
            otra = cands[int(index)]
        except (TypeError, ValueError, IndexError):
            desk._edit_text(where, "No reconozco la tarjeta elegida; abre de nuevo «Fusionar con…»")
            return True
        pareja = {**rec, "cards": [{"tid": rec["task_id"], "board": rec["board"], "lane": rec["lane"]}, otra]}
        loaded = self._cargar(pareja, desk, where)
        if loaded is None:
            return True
        return self._fusionar(loaded, pareja, where, desk, who, confirmado=False)
