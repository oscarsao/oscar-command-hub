"""Tipo de petición (entrega B del pitch seguimiento-peticiones): `/pedir <texto>` -> bug o feature -> preguntas por tipo
-> (feature) modo OASP -> tarjeta con el tipo y las respuestas en el cuerpo, y el registro de la petición.

Reglas (pitch §4.5): el bot PROPONE el tipo, nunca lo fija solo; lo fija el botón de una persona. Máximo 3-4 preguntas por
tipo, en el formato de telegram-decisiones-v2 (A/B, ⭐ Recomendada, ✍️ Otra, «Acepto todas las recomendadas»), sin repetir
lo que ya dice el mensaje original. Sin LLM: reglas y listas editables en `tipo_peticion.yaml` (lo que falte vale el
valor por defecto de DEFAULTS, así que el fichero puede no existir).

Registro: B solo lee/escribe `tipo`, `tipo_confirmado_por`, `urgencia` y `modo_oasp` mediante la interfaz mínima
`RegistroPeticiones` (la implementa A1; hasta entonces es opcional y el flujo solo escribe en la tarjeta).

Sin estado propio en disco: el estado de la conversación viaja en el registro del teclado (`CallbackStore`), un
teclado por paso, de un solo uso.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Callable, Protocol

import yaml

from .config import ROOT
from .notices import OPTION_LETTERS, truncate

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "tipo_peticion.yaml"
KIND = "pedir"  # `kind` del registro de teclado: decisions.py lo reenvía a Commands.pedir_step
A_TIPO, A_OPT, A_REC, A_ASK, A_ALL, A_SKIP, A_MODO, A_NO = (
    "pedir_tipo", "pedir_opt", "pedir_rec", "pedir_ask", "pedir_all", "pedir_skip", "pedir_modo", "pedir_no")
REGISTRO_CAMPOS = ("tipo", "tipo_confirmado_por", "urgencia", "modo_oasp")
ANSWER_MAX = 600

DEFAULTS: dict = {
    "tipos": {"bug": "🐞 Arreglar algo que falla", "feature": "✨ Crear algo nuevo",
              "consulta": "❓ Solo una pregunta"},
    "etiquetas": {"bug": "Bug", "feature": "Feature", "consulta": "Consulta"},
    "pistas_tipo": {
        "bug": ["falla", "fallo", "error", "no funciona", "roto", "rompe", "bug", "se cae", "no carga", "no deja",
                "arregla", "corrige", "no sale", "no aparece"],
        "feature": ["añade", "añadir", "crea", "crear", "nuevo", "nueva", "quiero que", "implementa", "soporte para",
                    "feature", "me gustaría", "permite"],
        "consulta": ["consulta", "pregunta", "explícame", "explica", "dime", "cómo funciona", "por qué", "qué hace",
                     "dónde está", "cuántos", "cuántas"],
    },
    # P0-P3: `prioridad` = prioridad de la tarjeta en el kanban (más alto = antes). /hazlo usa 10.
    "urgencias": [
        {"id": "P0", "texto": "Producción rota o datos en riesgo", "prioridad": 10},
        {"id": "P1", "texto": "Bloquea el trabajo de alguien", "prioridad": 8},
        {"id": "P2", "texto": "Molesta pero hay rodeo", "prioridad": 5},
        {"id": "P3", "texto": "Cuando haya hueco", "prioridad": 2},
    ],
    "urgencia_recomendada": "P2",
    "modos": [
        {"id": "fast-track", "texto": "Fast-Track (menos de 1 h)"},
        {"id": "spec-lite", "texto": "Spec-Lite (1-3 días)"},
        {"id": "pitch", "texto": "Pitch (1-3 semanas)"},
    ],
    "pistas_modo": {
        "pitch": ["semanas", "módulo", "integración", "plataforma", "rediseño", "sistema nuevo", "migrar"],
        "fast-track": ["texto", "color", "typo", "copy", "renombrar", "cambiar el", "botón"],
    },
    "modo_por_defecto": "spec-lite",
    "palabras_modo_largo": 120,  # un encargo con más palabras que esto sube de Fast-Track a Spec-Lite
    "max_preguntas": {"bug": 4, "feature": 3, "consulta": 0},  # una consulta se contesta tal cual, sin cuestionario
    "preguntas": {
        "bug": [
            {"id": "repro", "texto": "¿Cómo se reproduce? (pasos o qué estabas haciendo)", "opciones": [],
             "pistas": [r"al (hacer|pulsar|entrar|abrir|enviar)", r"\bcuando\b", r"\bpasos\b", r"reproduc"]},
            {"id": "donde", "texto": "¿Dónde falla? (pantalla, comando, proyecto)", "opciones": [],
             "pistas": [r"https?://", r"pantalla", r"página", r"endpoint", r"pestaña", r"botón", r"comando", r"/\w+"]},
            {"id": "esperado", "texto": "¿Qué esperabas que pasara?", "opciones": [],
             "pistas": [r"deber[ií]a", r"esperaba", r"espero", r"tendr[ií]a que"]},
            {"id": "urgencia", "texto": "¿Cuánto urge?", "opciones": "@urgencias", "recomendada": "@urgencia",
             "pistas": [r"\bp[0-3]\b", r"urgente", r"no urge", r"producci[oó]n"]},
        ],
        "feature": [
            {"id": "objetivo", "texto": "¿Qué quieres conseguir con esto? (el objetivo, no la solución)", "opciones": [],
             "pistas": [r"para que", r"objetivo", r"quiero conseguir", r"con el fin"]},
            {"id": "usuarios", "texto": "¿Quién lo va a usar?", "recomendada": 0,
             "opciones": ["Solo Oscar", "El equipo", "Clientes o usuarios finales"],
             "pistas": [r"lo (usa|usan|usar[aá])", r"usuarios", r"clientes", r"el equipo"]},
            {"id": "hecho", "texto": "¿Cómo sabremos que está hecho? (criterio de «hecho»)", "opciones": [],
             "pistas": [r"criterio", r"hecho cuando", r"estar[aá] listo", r"aceptaci[oó]n"]},
        ],
    },
    "textos": {
        "propone_bug": "Parece algo que falla. ¿Lo confirmas?",
        "propone_feature": "Parece algo nuevo. ¿Lo confirmas?",
        "propone_consulta": "Parece una pregunta (se responde sin crear rama). ¿Lo confirmas?",
        "pregunta_tipo": "¿Qué es esto?",
        "modo": "¿Con qué tamaño lo hacemos? Te propongo {modo}:",
    },
    "proyecto_por_carril": {"claude-hub": "Hub", "claude-migrateam": "MigraTeam", "claude-oscarhq": "OHQ",
                            "claude-scraper": "Scraper", "claude-nextjobs": "NextJobs"},
}


class RegistroPeticiones(Protocol):
    """Interfaz mínima del registro de A1 (`req_<n>`). B no inventa otro: solo crea la petición y escribe 4 campos."""

    def crear(self, proyecto: str | None, titulo: str, origen: str) -> str: ...

    def actualizar(self, req_id: str, **campos) -> None: ...


def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base[k], v) if k in base else v
        return out
    return over if type(over) is type(base) or base is None else base


def load_config(path: Path | str | None = None) -> dict:
    """DEFAULTS + lo que diga el YAML (un valor de otro tipo que el del defecto se ignora). Nunca lanza."""
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else SETTINGS_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return cfg
    return _merge(cfg, data) if isinstance(data, dict) else cfg


# --- reglas puras ------------------------------------------------------------------------------------------------

def detect_tipo(text: str, cfg: dict) -> str | None:
    """Tipo que el texto sugiere ('bug' | 'feature'), o None si no hay pista o empata. Solo PROPONE."""
    low = (text or "").casefold()
    score = {t: sum(1 for w in words if re.search(rf"(?<!\w){re.escape(w.casefold())}(?!\w)", low))
             for t, words in cfg["pistas_tipo"].items()}
    bug, feat, ask = score.get("bug", 0), score.get("feature", 0), score.get("consulta", 0)
    if ask > bug and ask > feat:
        return "consulta"
    return "bug" if bug > feat else "feature" if feat > bug else None


def resolve(value, cfg: dict):
    """Una pregunta puede decir '@urgencias' / '@urgencia' (se toman de la config) o dar la lista/valor directo."""
    if value == "@urgencias":
        return [f"{u['id']} · {u['texto']}" for u in cfg["urgencias"]]
    if value == "@urgencia":
        ids = [u["id"] for u in cfg["urgencias"]]
        return ids.index(cfg["urgencia_recomendada"]) if cfg["urgencia_recomendada"] in ids else None
    return value


def pending_questions(tipo: str, text: str, cfg: dict) -> list[dict]:
    """Preguntas del tipo que el mensaje original aún no contesta, con tope `max_preguntas`."""
    out = []
    for q in cfg["preguntas"].get(tipo, []):
        if any(re.search(p, text or "", re.I) for p in q.get("pistas") or ()):
            continue
        out.append({"id": q["id"], "texto": q["texto"], "opciones": resolve(q.get("opciones", []), cfg) or [],
                    "recomendada": resolve(q.get("recomendada"), cfg)})
    return out[:int(cfg["max_preguntas"].get(tipo, 4))]


def urgencia_id(answer: str, cfg: dict) -> str | None:
    m = re.search(r"\bP([0-3])\b", answer or "", re.I)
    ids = {u["id"] for u in cfg["urgencias"]}
    return f"P{m.group(1)}" if m and f"P{m.group(1)}" in ids else None


def propose_modo(text: str, answers: dict, cfg: dict) -> str:
    """Modo OASP que se PROPONE para una feature (la persona confirma con botón)."""
    blob = " ".join([text or ""] + [str(v) for v in answers.values()]).casefold()
    hits = lambda m: any(re.search(rf"(?<!\w){re.escape(w.casefold())}(?!\w)", blob)
                         for w in cfg["pistas_modo"].get(m, ()))
    ids = [m["id"] for m in cfg["modos"]]
    if hits("pitch") and "pitch" in ids:
        return "pitch"
    if hits("fast-track") and len(blob.split()) <= int(cfg["palabras_modo_largo"]) and "fast-track" in ids:
        return "fast-track"
    return cfg["modo_por_defecto"] if cfg["modo_por_defecto"] in ids else ids[0]


def prioridad(urgencia: str | None, cfg: dict) -> int | None:
    return next((u["prioridad"] for u in cfg["urgencias"] if u["id"] == urgencia), None)


def card_body(st: dict, cfg: dict, req_id: str | None, origin: str) -> str:
    """Cuerpo de la tarjeta: vínculo `req:`, línea `Tipo: …` y las respuestas. El texto es dato, no instrucciones."""
    head = f"Tipo: {st['tipo']}" + (f" · Urgencia: {st['urgencia']}" if st.get("urgencia") else "") + (
        f" · Modo OASP: {st['modo']}" if st.get("modo") else "")
    lines = ([f"req: {req_id}"] if req_id else []) + [head, ""]
    lines += ["## Petición", *("> " + l for l in (st["texto"] or "").splitlines()), ""]
    qs = {q["id"]: q["texto"] for t in cfg["preguntas"].values() for q in t}
    if st["answers"]:
        lines += ["## Respuestas"] + [f"- {qs.get(k, k)} {v}" for k, v in st["answers"].items()] + [""]
    lines += [f"Tipo confirmado por: {st['confirmado_por']} (botón de Telegram)", origin]
    return "\n".join(lines) + "\n"


# --- teclados ----------------------------------------------------------------------------------------------------

def tipo_spec(cfg: dict) -> list[list[dict]]:
    return [[{"text": cfg["tipos"]["bug"], "action": A_TIPO, "index": 0},
             {"text": cfg["tipos"]["feature"], "action": A_TIPO, "index": 1}],
            [{"text": cfg["tipos"]["consulta"], "action": A_TIPO, "index": 2},
             {"text": "🚫 Cancelar", "action": A_NO}]]


def question_spec(q: dict, accept_all: bool) -> list[list[dict]]:
    opts, rec = q["opciones"], q["recomendada"]
    skip = {"text": "⏭ Sin dato", "action": A_SKIP}
    if not opts:
        return [[{"text": "✍️ Responder", "action": A_ASK}, skip], [{"text": "🚫 Cancelar", "action": A_NO}]]
    rows = [[{"text": OPTION_LETTERS[i], "action": A_OPT, "index": i} for i in range(min(len(opts), len(OPTION_LETTERS)))]]
    other = {"text": "✍️ Otra", "action": A_ASK}
    rows.append([{"text": "⭐ Recomendada", "action": A_REC, "index": rec}, other] if rec is not None else [other])
    if accept_all:
        rows.append([{"text": "✅ Acepto todas las recomendadas", "action": A_ALL}])
    rows.append([skip, {"text": "🚫 Cancelar", "action": A_NO}])
    return rows


def question_text(q: dict, n: int, total: int) -> str:
    lines = [f"Pregunta {n}/{total}: {q['texto']}"]
    for i, o in enumerate(q["opciones"][:len(OPTION_LETTERS)]):
        lines.append(f"{OPTION_LETTERS[i]}) {o}" + (" ⭐" if q["recomendada"] == i else ""))
    return "\n".join(lines)


# --- flujo -------------------------------------------------------------------------------------------------------

class TipoPeticion:
    """Motor del flujo. `crear_tarjeta(st, titulo, cuerpo, prioridad) -> texto de confirmación (HTML)` lo pone Commands
    (carril, kanban); `registro` es opcional (A1)."""

    def __init__(self, notifier, store, *, crear_tarjeta: Callable[..., str], cfg: dict | None = None,
                 registro: RegistroPeticiones | None = None, now: Callable[[], float] = time.time):
        self.notifier, self.store, self.crear_tarjeta = notifier, store, crear_tarjeta
        self.cfg = cfg or load_config()
        self.registro = registro
        self._now = now

    # envío -----------------------------------------------------------------------------------------------------
    def _issue(self, st: dict, spec, extra: dict | None = None) -> dict:
        rec = {"task_id": KIND, "board": "", "lane": "", "kind": KIND, "st": st, **(extra or {})}
        return self.store.issue(rec, spec)[1]

    def _send(self, where, text: str, st: dict, spec, extra: dict | None = None) -> None:
        self.notifier.send_to(where[0], where[1], text, html=False, reply_markup=self._issue(st, spec, extra))

    def _close(self, where: dict, text: str) -> None:
        """Deja el mensaje pulsado sin botones y con lo elegido."""
        self.notifier.edit(where["chat_id"], where["message_id"], text, html=False)

    # entrada ---------------------------------------------------------------------------------------------------
    def start(self, where: tuple, text: str, *, origen: str = "telegram", lane: str | None = None,
              brand: str | None = None) -> None:
        """B1: propone el tipo (si hay pista) y espera el botón. No crea nada todavía."""
        st = {"texto": text.strip(), "tipo": None, "confirmado_por": None, "answers": {}, "queue": [], "urgencia": None,
              "modo": None, "origen": origen, "lane": lane, "brand": brand, "where": list(where), "req": None}
        guess = detect_tipo(st["texto"], self.cfg)
        t = self.cfg["textos"]
        head = t["propone_" + guess] if guess else t["pregunta_tipo"]
        self._send(where, f"{head}\n{truncate(st['texto'], 300)}", st, tipo_spec(self.cfg))

    def step(self, rec: dict, button: dict, where: dict, actor: str, text: str | None = None) -> bool:
        """Un toque de botón (o la respuesta escrita de ✍️). True = aplicado; False = devuelve los botones."""
        st, action, idx = rec["st"], button["action"], button.get("index")
        try:
            if action == A_NO:
                self._close(where, "🚫 Cancelado, no he creado nada")
                return True
            if action == A_TIPO:
                return self._set_tipo(st, ("bug", "feature", "consulta")[idx], actor, where)
            if action == A_MODO:
                modes = [m["id"] for m in self.cfg["modos"]]
                if not isinstance(idx, int) or not 0 <= idx < len(modes):
                    return False
                st["modo"] = modes[idx]
                self._close(where, f"Modo: {self._modo_texto(modes[idx])}")
                return self._finish(st, where)
            if not st["queue"]:
                return False
            q = st["queue"][0]
            if action == A_ALL:
                for p in st["queue"]:
                    self._answer(st, p, p["opciones"][p["recomendada"]])
                st["queue"] = []
                self._close(where, "✅ Acepto todas las recomendadas")
            else:
                value = self._value(q, action, idx, text)
                if value is None:
                    return False
                self._answer(st, q, value)
                st["queue"] = st["queue"][1:]
                self._close(where, f"{q['texto']}\n→ {truncate(value, 200)}")
            return self._next(st, where)
        except (IndexError, KeyError, TypeError):
            return False

    # pasos -----------------------------------------------------------------------------------------------------
    def _set_tipo(self, st: dict, tipo: str, actor: str, where: dict) -> bool:
        st.update(tipo=tipo, confirmado_por=actor, queue=pending_questions(tipo, st["texto"], self.cfg))
        if tipo == "bug":  # la urgencia ya dicha en el mensaje original no se vuelve a preguntar
            st["urgencia"] = urgencia_id(st["texto"], self.cfg)
        self._close(where, f"{self.cfg['tipos'][tipo]} · confirmado por {actor}")
        if self.registro:
            st["req"] = self.registro.crear(self._proyecto(st), truncate(st["texto"].splitlines()[0], 80), st["origen"])
            self._write_registro(st, tipo=tipo, tipo_confirmado_por=actor)
        return self._next(st, where)

    def _next(self, st: dict, where: dict) -> bool:
        w = (where["chat_id"], where["thread_id"])
        if st["queue"]:
            total = len(st["answers"]) + len(st["queue"])
            q = st["queue"][0]
            every = all(p["recomendada"] is not None and p["opciones"] for p in st["queue"]) and len(st["queue"]) > 1
            self._send(w, question_text(q, len(st["answers"]) + 1, total), st, question_spec(q, every),
                       {"prompt": f"{q['texto']}"})
            return True
        if st["tipo"] == "feature" and not st["modo"]:
            modo = propose_modo(st["texto"], st["answers"], self.cfg)
            modes = [m["id"] for m in self.cfg["modos"]]
            spec = [[{"text": ("⭐ " if m["id"] == modo else "") + m["texto"], "action": A_MODO, "index": i}]
                    for i, m in enumerate(self.cfg["modos"])] + [[{"text": "🚫 Cancelar", "action": A_NO}]]
            self._send(w, self.cfg["textos"]["modo"].format(modo=self._modo_texto(modo)), st, spec)
            return modo in modes
        return self._finish(st, where)

    def _finish(self, st: dict, where: dict) -> bool:
        if st["tipo"] == "bug" and not st["urgencia"]:
            st["urgencia"] = self.cfg["urgencia_recomendada"]  # «Sin dato» en la urgencia: la recomendada, dicho en la tarjeta
        self._write_registro(st, urgencia=st["urgencia"], modo_oasp=st["modo"])
        label = self.cfg["etiquetas"][st["tipo"]] + (f" {st['urgencia']}" if st["urgencia"] else "")
        titulo = f"[{label}] {truncate(st['texto'].splitlines()[0], 70)}"
        origin = f"Origen-Telegram: chat={where['chat_id']} thread={where['thread_id']}"
        confirm = self.crear_tarjeta(st, titulo, card_body(st, self.cfg, st.get("req"), origin),
                                     prioridad(st["urgencia"], self.cfg))
        self.notifier.send_to(where["chat_id"], where["thread_id"], confirm)
        return True

    # ayudas ----------------------------------------------------------------------------------------------------
    def _value(self, q: dict, action: str, idx, text: str | None) -> str | None:
        if action == A_SKIP:
            return "sin dato"
        if action == A_ASK:
            clean = " ".join((text or "").split())[:ANSWER_MAX]
            return clean or None
        if action in (A_OPT, A_REC) and isinstance(idx, int) and 0 <= idx < len(q["opciones"]):
            return q["opciones"][idx]
        return None

    def _answer(self, st: dict, q: dict, value: str) -> None:
        st["answers"][q["id"]] = value
        if q["id"] == "urgencia":
            st["urgencia"] = urgencia_id(value, self.cfg)

    def _modo_texto(self, modo: str) -> str:
        return next((m["texto"] for m in self.cfg["modos"] if m["id"] == modo), modo)

    def _proyecto(self, st: dict) -> str | None:
        return self.cfg["proyecto_por_carril"].get(st.get("lane") or "")

    def _write_registro(self, st: dict, **campos) -> None:
        if self.registro and st.get("req"):
            self.registro.actualizar(st["req"], **{k: v for k, v in campos.items() if k in REGISTRO_CAMPOS})


def card_key(user: str, where: tuple, text: str, now: float) -> str:
    """Clave idempotente: el reenvío del mismo update no duplica la tarjeta."""
    return "pedir-" + hashlib.sha1(f"{user}|{where}|{int(now // 600)}|{text}".encode()).hexdigest()[:16]
