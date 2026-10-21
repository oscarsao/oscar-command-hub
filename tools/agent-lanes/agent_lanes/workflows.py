"""Workflows desde Telegram (entrega C del pitch seguimiento-peticiones; spec en specs/workflows-telegram-cola-integrador.md).

Ciclo: preguntas previas -> plan -> [✅ Aprobar plan y gasto] -> tarjetas enlazadas a una `req_<n>` -> entrega única.
Nada se crea en el kanban antes de aprobar. Tope de gasto con aviso al 80 % y pausa al 100 % ([➕ Ampliar] [⛔ Parar]).
Todos los números viven en workflows.yaml (editable con `set_ajuste`), no en constantes. Este módulo no habla con Telegram
ni con el kanban: recibe `create_card` y devuelve textos y botones. Estado en .state/workflows.json (JSON propio del hub).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable

import yaml

from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "workflows.json"
SETTINGS_PATH = ROOT / "workflows.yaml"
TIPO = "workflow"
ESTADOS = ("plan", "aprobado", "en curso", "pausado", "entregado", "parado")
ACTIVOS = ("aprobado", "en curso")


@dataclass(frozen=True)
class Ajustes:
    tope_eur: float = 8
    paso_ampliar_eur: float = 8
    aviso_pct: int = 80
    max_preguntas: int = 5
    max_tarjetas_activas: int = 5
    max_workstreams_codigo: int = 3
    escalar_horas: int = 2
    recordar_horas: int = 24


def _valido(key: str, v: object) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return False
    return v <= 100 if key == "aviso_pct" else True


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde workflows.yaml; lo que falte o no sea válido queda en su valor por defecto."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    base = Ajustes()
    vals = {f.name: raw[f.name] if f.name in raw and _valido(f.name, raw[f.name]) else getattr(base, f.name)
            for f in fields(Ajustes)}
    for k in vals:  # los contadores son enteros
        if k not in ("tope_eur", "paso_ampliar_eur"):
            vals[k] = int(vals[k])
    return Ajustes(**vals)


def set_ajuste(key: str, value: object, path: Path | str | None = None) -> Ajustes:
    """Cambia un ajuste desde un botón/panel: valida, conserva el resto del fichero y devuelve los ajustes nuevos."""
    p = Path(path or SETTINGS_PATH)
    if key not in {f.name for f in fields(Ajustes)}:
        raise ValueError(f"ajuste desconocido: {key}")
    if not _valido(key, value):
        raise ValueError(f"valor no válido para {key}: {value!r}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw[key] = value
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return load_ajustes(p)


class Store:
    """JSON {workflows: {id: {...}}}; sin red. Una escritura rota no tira el servicio: se parte de vacío."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or STATE_PATH)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, wid: str) -> dict | None:
        return self.load().get(wid)

    def put(self, wf: dict) -> dict:
        data = self.load()
        data[wf["id"]] = wf
        self.save(data)
        return wf

    def code_active(self) -> int:
        """Tarjetas de código activas en TODOS los workflows (límite global de workstreams)."""
        return sum(1 for w in self.load().values() for c in w.get("tarjetas", [])
                   if c.get("estado") == "activa" and c.get("codigo", True))


def propose(store: Store, *, wid: str, titulo: str, req: str, preguntas: list[str], tarjetas: list[dict],
            riesgos: list[str], prueba: str, coste_estimado_eur: float, ajustes: Ajustes | None = None) -> dict:
    """C1: guarda el plan en estado `plan`. No crea ninguna tarjeta. `tarjetas`: [{titulo, carril, depende, codigo}]."""
    aj = ajustes or load_ajustes()
    if len(preguntas) > aj.max_preguntas:
        raise ValueError(f"máximo {aj.max_preguntas} preguntas previas (hay {len(preguntas)})")
    if not tarjetas:
        raise ValueError("un plan necesita al menos una tarjeta")
    wf = {"id": wid, "tipo": TIPO, "titulo": titulo, "req": req, "estado": "plan", "preguntas": preguntas,
          "plan": [{"titulo": t["titulo"], "carril": t["carril"], "depende": t.get("depende", []),
                    "codigo": t.get("codigo", True)} for t in tarjetas],
          "riesgos": riesgos, "prueba": prueba, "coste_estimado_eur": coste_estimado_eur,
          "tope_eur": aj.tope_eur, "gastado_eur": 0.0, "avisado": False, "tarjetas": []}
    return store.put(wf)


def render_plan(wf: dict) -> tuple[str, list[list[tuple[str, str]]]]:
    lines = [f"🧩 Plan · {wf['titulo']} ({wf['req']})", "", "Tarjetas previstas (en este orden):"]
    for i, t in enumerate(wf["plan"], 1):
        dep = f" · tras {', '.join(map(str, t['depende']))}" if t["depende"] else ""
        lines.append(f"{i}. {t['titulo']} — carril {t['carril']}{dep}")
    lines += ["", "Riesgos: " + ("; ".join(wf["riesgos"]) or "ninguno conocido"), f"Cómo se probará: {wf['prueba']}",
              f"Coste estimado: ~{wf['coste_estimado_eur']:g} € · tope {wf['tope_eur']:g} € (ampliable con botón)",
              "No se crea nada hasta que apruebes."]
    wid = wf["id"]
    return "\n".join(lines), [[("✅ Aprobar plan y gasto", f"wf:ok:{wid}"), ("✍️ Cambiar", f"wf:edit:{wid}"),
                               ("🗄 Aparcar", f"wf:park:{wid}")]]


def approve(store: Store, wid: str) -> dict:
    wf = _need(store, wid)
    if wf["estado"] == "plan":
        wf["estado"] = "aprobado"
        store.put(wf)
    return wf


def launch(store: Store, wid: str, create_card: Callable[[dict, str], str], ajustes: Ajustes | None = None) -> list[str]:
    """C2: crea las tarjetas pendientes cuyo orden lo permite, enlazadas a `req`. Sin aprobar -> no crea ninguna.
    `create_card(item, req) -> id` lo aporta el cableado (kanban real) y debe escribir `req: <req>` en la tarjeta."""
    aj = ajustes or load_ajustes()
    wf = _need(store, wid)
    if wf["estado"] not in ACTIVOS:
        return []
    hechas = {c["titulo"] for c in wf["tarjetas"] if c["estado"] == "hecha"}
    creadas = {c["titulo"] for c in wf["tarjetas"]}
    activas = sum(1 for c in wf["tarjetas"] if c["estado"] == "activa")
    code_global = store.code_active()
    nuevos: list[str] = []
    for item in wf["plan"]:
        if item["titulo"] in creadas or not all(d in hechas for d in item["depende"]):
            continue
        if activas >= aj.max_tarjetas_activas or (item["codigo"] and code_global >= aj.max_workstreams_codigo):
            continue
        tid = create_card(item, wf["req"])
        wf["tarjetas"].append({"id": tid, "titulo": item["titulo"], "estado": "activa", "codigo": item["codigo"]})
        activas += 1
        code_global += 1 if item["codigo"] else 0
        nuevos.append(tid)
    wf["estado"] = "en curso"
    store.put(wf)
    return nuevos


def card_done(store: Store, wid: str, card_id: str) -> dict:
    wf = _need(store, wid)
    for c in wf["tarjetas"]:
        if c["id"] == card_id:
            c["estado"] = "hecha"
    store.put(wf)
    return wf


def progreso(wf: dict) -> str:
    """Línea para /estado: «3/5 tarjetas»."""
    return f"{sum(1 for c in wf['tarjetas'] if c['estado'] == 'hecha')}/{len(wf['plan'])} tarjetas"


def record_spend(store: Store, wid: str, eur: float) -> tuple[dict, str | None]:
    """C3: suma gasto. Devuelve (wf, aviso|None): 'aviso' al cruzar el % (una vez) o 'pausa' al llegar al 100 %."""
    aj = load_ajustes()
    wf = _need(store, wid)
    wf["gastado_eur"] = round(wf["gastado_eur"] + eur, 4)
    evento = None
    if wf["gastado_eur"] >= wf["tope_eur"] and wf["estado"] in ACTIVOS:
        wf["estado"], evento = "pausado", "pausa"
    elif wf["gastado_eur"] >= wf["tope_eur"] * aj.aviso_pct / 100 and not wf["avisado"]:
        wf["avisado"], evento = True, "aviso"
    store.put(wf)
    return wf, evento


def render_gasto(wf: dict, evento: str) -> tuple[str, list[list[tuple[str, str]]]]:
    wid = wf["id"]
    resumen = f"{wf['gastado_eur']:g} € de {wf['tope_eur']:g} €"
    if evento == "pausa":
        return (f"⏸ {wf['titulo']} ({wf['req']}) pausado: has llegado al tope ({resumen}). ¿Qué hago?",
                [[("➕ Ampliar", f"wf:more:{wid}"), ("⛔ Parar", f"wf:stop:{wid}")]])
    return f"💶 {wf['titulo']} ({wf['req']}): llevo {resumen}, cerca del tope.", []


def ampliar(store: Store, wid: str, ajustes: Ajustes | None = None) -> dict:
    aj = ajustes or load_ajustes()
    wf = _need(store, wid)
    wf["tope_eur"] = round(wf["tope_eur"] + aj.paso_ampliar_eur, 4)
    wf["avisado"] = wf["gastado_eur"] >= wf["tope_eur"] * aj.aviso_pct / 100
    if wf["estado"] == "pausado":
        wf["estado"] = "en curso"
    return store.put(wf)


def parar(store: Store, wid: str) -> dict:
    wf = _need(store, wid)
    wf["estado"] = "parado"
    return store.put(wf)


def render_entrega(wf: dict, hecho: str, enlaces: list[str], como_probar: str) -> str:
    """El único mensaje final: qué se hizo, enlaces y cómo probarlo."""
    links = "\n".join(f"• {e}" for e in enlaces) or "• (sin enlaces)"
    return (f"✅ Entregado · {wf['titulo']} ({wf['req']})\n\nQué se hizo: {hecho}\n\nEnlaces:\n{links}\n\n"
            f"Cómo probarlo: {como_probar}\nGastado: {wf['gastado_eur']:g} € de {wf['tope_eur']:g} €")


def deliver(store: Store, wid: str) -> dict:
    wf = _need(store, wid)
    wf["estado"] = "entregado"
    return store.put(wf)


def _need(store: Store, wid: str) -> dict:
    wf = store.get(wid)
    if wf is None:
        raise KeyError(f"workflow desconocido: {wid}")
    return wf
