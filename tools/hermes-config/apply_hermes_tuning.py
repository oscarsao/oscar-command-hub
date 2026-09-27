"""Ajuste de Hermes como Coordinador (27-09). Por defecto hace un ensayo sin escribir; --apply escribe.

Nunca imprime valores de config. Aborta sin tocar nada si algún ancla no coincide.
Uso:   python apply_hermes_tuning.py [--apply] [--keep-browser]
Después de --apply: `hermes gateway restart`, `hermes kanban diagnostics` y /new en el DM y en t5.
"""
import argparse
import copy
import os
import shutil
import sys
from pathlib import Path

import yaml

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
CFG = HOME / "config.yaml"
BAK = HOME / "config.yaml.bak-20260927-tuning"
TG_OLD = ("  telegram: [a2a, browser, clarify, connections, delegation, image_gen, kanban, memory, "
          "session_search, skills, todo, tts, vision, web]\n")
CRON_TS = ["kanban", "session_search", "todo", "no_mcp"]


def build(keep_browser: bool):
    tg = ["clarify", "kanban", "memory", "session_search", "skills", "todo", "tts", "vision", "web", "no_mcp"]
    if keep_browser:
        tg.insert(0, "browser")
    subs = [
        ("kanban:\n  review_dispatch: true\n",
         "kanban:\n  # tuning 27-09: Hermes coordina, no ejecuta (incidente t_ecde279e)\n  review_dispatch: false\n"
         "  auto_decompose: false\n  auto_promote_children: false\n  dispatch_profiles: []\n"),
        (TG_OLD, f"  telegram: [{', '.join(tg)}]\n  cron: [{', '.join(CRON_TS)}]\n"),
        ("  threshold_tokens: 256000\n", "  threshold_tokens: 120000\n"),
        ("  creation_nudge_interval: 15\n", "  creation_nudge_interval: 15\n  write_approval: true\n"),
        ("  nudge_interval: 10        # Nudge every 10 user turns (0 = disabled)\n",
         "  nudge_interval: 10        # Nudge every 10 user turns (0 = disabled)\n  write_approval: true\n"),
    ]
    return tg, subs


fails = []


def check(ok, label):
    print(("OK  " if ok else "FAIL") + ": " + label)
    if not ok:
        fails.append(label)
    return ok


def expected(d, tg):
    e = copy.deepcopy(d)
    k = e.setdefault("kanban", {})
    k.update(review_dispatch=False, auto_decompose=False, auto_promote_children=False, dispatch_profiles=[])
    e["platform_toolsets"]["telegram"] = tg
    e["platform_toolsets"]["cron"] = CRON_TS
    e["compression"]["threshold_tokens"] = 120000
    e["skills"]["write_approval"] = True
    e["memory"]["write_approval"] = True
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep-browser", action="store_true", help="conserva 'browser' en Telegram")
    a = ap.parse_args()
    tg, subs = build(a.keep_browser)
    text = CFG.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    check("\r" not in text, "finales LF")
    check("cron" not in data.get("platform_toolsets", {}), "no existe platform_toolsets.cron")
    for key in ("auto_decompose", "dispatch_profiles", "auto_promote_children"):
        check(key not in (data.get("kanban") or {}), f"kanban.{key} ausente")
    for old, _ in subs:
        check(text.count(old) == 1, f"ancla única: {old.strip()[:40]}")
    if fails:
        print("ABORTADO: no se ha tocado nada.")
        return 1
    new = text
    for old, rep in subs:
        new = new.replace(old, rep, 1)
    nd = yaml.safe_load(new)
    check(nd == expected(data, tg), "diff semántico = solo las claves previstas")
    check(nd.get("platforms") == data.get("platforms"), "platforms.telegram (W3b) intacto")
    if fails:
        print("ABORTADO: no se ha tocado nada.")
        return 1
    if not a.apply:
        print("DRY-RUN OK (no se ha escrito nada). Repite con --apply.")
        return 0
    if not check(not BAK.exists(), "no existe un backup previo con este nombre"):
        return 1
    shutil.copy2(CFG, BAK)
    tmp = CFG.with_suffix(".yaml.tmp-tuning")
    tmp.write_text(new, encoding="utf-8", newline="\n")
    os.replace(tmp, CFG)
    check(yaml.safe_load(CFG.read_text(encoding="utf-8")) == nd, "escrito y releído")
    print("HECHO. Ahora: hermes gateway restart; hermes kanban diagnostics; /new en el DM y en t5.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
