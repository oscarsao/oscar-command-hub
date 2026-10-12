"""Opciones de una tarjeta de decisión de Oscar: formato del bloque, parser tolerante y mensaje de Telegram.

Una tarjeta de decisión ([DECISIÓN]/[SEMANA]/[IDEA], assignee oscar) puede llevar en su cuerpo (o en un comentario
`PREPARADO-POR-CLAUDE:`) un bloque así; cualquier línea puede faltar:

    Contexto: Por qué hay que decidir esto (una o varias líneas).
    Opciones:
    A) Primera opción
    B) Segunda opción
    C) Tercera opción
    Recomendada: A

Reglas del parser (nunca lanza): claves sin distinguir mayúsculas ni tildes ("Contexto"/"Opciones"/"Recomendada" o
"Recomendación"); opciones `A)`/`A.`/`A:`/`A -` (también `1)`), con viñeta opcional, y las líneas con sangría siguen a
la opción anterior. Hacen falta al menos 2 opciones; más de 3 se recortan a 3 (los botones son solo A/B/C) y se cuenta
cuántas sobran. Una recomendada que no existe se ignora. Sin opciones válidas, la tarjeta se decide con los botones
genéricos y /preparar t_xxx la completa.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

from .notices import card_url, hours_ago, truncate

LETTERS = "ABC"
MAX_OPTIONS = len(LETTERS)
MIN_OPTIONS = 2
CONTEXT_MAX = 400      # contexto resumido en el mensaje
OPTION_TEXT_MAX = 220  # cada opción va escrita entera en el mensaje; los botones solo dicen la letra
TITLE_MAX = 80
PREPARED_PREFIX = "PREPARADO-POR-CLAUDE:"
PREPARED_AUTHOR = "carriles-preparador"  # solo los comentarios de este autor cuentan como borrador preparado

_KEY_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(contexto|opciones|opcion|recomendada|recomendacion)(?:\*\*)?\s*:\s*(.*)$",
                     re.I)
_OPT_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?([A-Ca-c1-3])(?:\*\*)?\s*[).:\-]\s*(.+?)\s*$")
_REC_RE = re.compile(r"([A-Ca-c1-3])\b")
_HEAD_RE = re.compile(r"^\s{0,3}#{1,6}\s*")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").lower()


def _letter_index(token: str) -> int | None:
    t = token.strip().upper()
    if t in LETTERS:
        return LETTERS.index(t)
    if t in ("1", "2", "3"):
        return int(t) - 1
    return None


@dataclass
class CardSpec:
    context: str = ""
    options: list[str] = field(default_factory=list)
    recommended: int | None = None
    dropped: int = 0         # opciones de más (>3) que no se pueden pulsar
    prepared: bool = False   # vienen de un comentario PREPARADO-POR-CLAUDE (no del cuerpo)
    reversible: bool = False  # el cuerpo dice `Reversible: sí`: candidata al plazo de veto (bandeja.py)

    @property
    def has_options(self) -> bool:
        return len(self.options) >= MIN_OPTIONS

    def answer_for(self, idx: int) -> str | None:
        """"B) texto" de la opción idx (None si no existe)."""
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(self.options):
            return f"{LETTERS[idx]}) {self.options[idx]}"
        return None


def _parse_block(text: str | None) -> tuple[str, list[str], int | None, int, bool]:
    """(contexto, opciones, recomendada, sobrantes, hubo_clave). Contexto = texto de `Contexto:` o, sin esa clave, lo
    que precede a `Opciones:`/a la primera opción."""
    context_lines: list[str] = []
    pre: list[str] = []
    options: list[str] = []
    rec_raw: str | None = None
    section = "pre"
    keyed = False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        folded = _fold(line)
        m = _KEY_RE.match(folded)
        if m:
            key = m.group(1)
            rest = (line[m.start(2):] if len(folded) == len(line) else m.group(2)).strip().lstrip("*").strip()
            keyed = True
            if key == "contexto":
                section = "context"
                if rest:
                    context_lines.append(rest)
            elif key.startswith("opcion"):
                section = "options"
                om = _OPT_RE.match(rest) if rest else None
                if om:
                    options.append(om.group(2))
            else:
                section = "rec"
                rec_raw = rest
            continue
        om = _OPT_RE.match(line)
        if om and section != "rec":
            section = "options"
            options.append(om.group(2))
            continue
        if not line.strip():
            continue
        if section == "options" and options and raw[:1].isspace():
            options[-1] = f"{options[-1]} {line.strip()}"
        elif section == "context":
            context_lines.append(line.strip())
        elif section == "rec":
            rec_raw = f"{rec_raw or ''} {line.strip()}".strip()
        elif section == "pre":
            pre.append(_HEAD_RE.sub("", line).strip())
    context = " ".join(context_lines) if context_lines else " ".join(p for p in pre if p)
    dropped = max(0, len(options) - MAX_OPTIONS)
    options = [o.strip() for o in options[:MAX_OPTIONS]]
    rec = None
    if rec_raw:
        rm = _REC_RE.match(rec_raw.strip().strip("*").strip())
        rec = _letter_index(rm.group(1)) if rm else None
    return context, options, rec, dropped, keyed


def parse_card(body: str | None, comments=()) -> CardSpec:
    """Opciones de una tarjeta: primero las del cuerpo; si no hay (≥2), el comentario PREPARADO-POR-CLAUDE más reciente
    del autor del preparador. Nunca lanza."""
    try:
        from .bandeja import is_reversible  # import tardío: bandeja importa notices, que este módulo ya usa
        reversible = is_reversible(body)
        context, options, rec, dropped, _ = _parse_block(body)
        if len(options) >= MIN_OPTIONS:
            return CardSpec(context, options, rec if rec is not None and rec < len(options) else None, dropped,
                            reversible=reversible)
        spec = CardSpec(context=context, reversible=reversible)
        for c in reversed(list(comments or ())):
            text = (c or {}).get("body")
            if (c or {}).get("author") != PREPARED_AUTHOR or not isinstance(text, str) \
                    or not text.startswith(PREPARED_PREFIX):
                continue
            pctx, popts, prec, pdrop, _ = _parse_block(text[len(PREPARED_PREFIX):])
            if len(popts) >= MIN_OPTIONS:
                return CardSpec(pctx or context, popts, prec if prec is not None and prec < len(popts) else None,
                                pdrop, prepared=True, reversible=reversible)
        return spec
    except Exception:  # un cuerpo raro nunca tumba /decisiones
        return CardSpec()


def format_block(spec: CardSpec) -> str:
    """Bloque canónico (el que escribe /preparar y entiende el parser)."""
    lines = [f"Contexto: {spec.context}".rstrip(), "Opciones:"]
    lines += [f"{LETTERS[i]}) {o}" for i, o in enumerate(spec.options[:MAX_OPTIONS])]
    if spec.recommended is not None and spec.recommended < len(spec.options):
        lines.append(f"Recomendada: {LETTERS[spec.recommended]}")
    return "\n".join(lines)


def card_message(card: dict, spec: CardSpec, base_url: str | None, now: float) -> str:
    """HTML de una tarjeta de decisión: título, antigüedad, enlace, contexto y opciones escritas. Siempre < 4000."""
    e = html.escape
    t = card["task"]
    url = card_url(base_url, card["board"], t["id"])
    ref = f'<a href="{e(url, quote=True)}">{e(t["id"])}</a>' if url else f"<code>{e(t['id'])}</code>"
    lines = [f"📌 {ref} · <b>{e(truncate(t.get('title'), TITLE_MAX))}</b> · hace {hours_ago(card['since'], now)} h"]
    if spec.context:
        lines.append(f"<i>Contexto:</i> {e(truncate(spec.context, CONTEXT_MAX))}")
    if spec.has_options:
        lines += [f"<b>{LETTERS[i]})</b> {e(truncate(o, OPTION_TEXT_MAX))}" for i, o in enumerate(spec.options)]
        if spec.recommended is not None:
            lines.append(f"⭐ Recomendada: {LETTERS[spec.recommended]}")
        if spec.dropped:
            lines.append(f"(+{spec.dropped} opción{'es' if spec.dropped != 1 else ''} más en la tarjeta)")
        if spec.prepared:
            lines.append("<i>Borrador preparado por Claude</i>")
    else:
        lines.append(f"sin opciones preparadas: pide /preparar {e(t['id'])}")
    return "\n".join(lines)
