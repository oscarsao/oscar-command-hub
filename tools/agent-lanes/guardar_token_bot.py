"""Guarda CARRILES_BOT_TOKEN en tools/agent-lanes/.env (28-09). Pide el token sin mostrarlo y lo valida con getMe.

Uso (terminal normal, no con '!' en Claude Code, porque pide el token):
    py -3.12 C:\\Users\\oscar\\oscar-command-hub\\tools\\agent-lanes\\guardar_token_bot.py
"""
import getpass
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"
tok = getpass.getpass("Pega el token del bot de carriles (no se verá): ").strip().strip('"').strip("'")
if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", tok):
    sys.exit("FAIL: eso no parece un token de BotFather (formato 123456:ABC...). No se ha guardado nada.")
try:
    me = json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=20))
except urllib.error.HTTPError as e:
    sys.exit(f"FAIL: Telegram rechaza el token ({e.code}). No se ha guardado nada.")
user = me["result"]["username"]
if user == "oscarsaoBot":
    sys.exit("FAIL: ese es el bot de Hermes; los botones necesitan OTRO bot. No se ha guardado nada.")
lines = [l for l in ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("CARRILES_BOT_TOKEN=")]
lines.append(f"CARRILES_BOT_TOKEN={tok}")
ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"OK: guardado el token de @{user} en {ENV}. Díselo a Claude para reiniciar el runner y probar los botones.")
