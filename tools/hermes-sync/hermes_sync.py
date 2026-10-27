"""Hermes toma proveedor y modelo de Ajustes › IA › Hermes de Oscar HQ (t_a38103b2).

Lee `GET <OHQ_API_URL><ruta>` (clave de servicio OHQ_API_KEY del .env local, nunca en el repo) con la forma
`{"provider": "...", "model": "..."}` y, si difiere de `model.provider` / `model.default` de la config de Hermes:
copia `.bak-<fecha>`, edita solo esas dos líneas (los comentarios del YAML se conservan), valida que el YAML resultante
es el esperado y reinicia el gateway. Si el reinicio falla, restaura la copia para que la próxima pasada reintente.

Por defecto es un ENSAYO (no escribe nada); `--apply` aplica. Cada ejecución deja una línea en el registro (JSONL, sin
secretos). Pensado para programarse cada 10 min con `--apply` (lo registra el integrador con OK de Oscar).

Uso:   python hermes_sync.py [--apply] [--env-file RUTA] [--config RUTA] [--ruta /api/...] [--registro RUTA]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
ENV_POR_DEFECTO = Path(__file__).resolve().parents[1] / "agent-lanes" / ".env"
RUTA_POR_DEFECTO = "/api/ajustes/hermes"  # configurable: --ruta u OHQ_HERMES_AJUSTE_RUTA en el .env
VALOR_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,99}$")  # ids de proveedor/modelo; nada de YAML ni espacios


class Abortado(Exception):
    """Algo no cuadra: no se ha tocado la config (o se ha restaurado)."""


def leer_env(ruta: Path | None) -> dict:
    env: dict = {}
    if ruta and ruta.is_file():
        for l in ruta.read_text(encoding="utf-8").splitlines():
            if "=" in l and not l.lstrip().startswith("#"):
                k, v = l.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return {**env, **{k: v for k, v in os.environ.items() if k.startswith("OHQ_")}}


def http_real(url: str, clave: str):
    req = urllib.request.Request(url, headers={"X-API-Key": clave})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (URL de configuración, no de usuario)
        return json.loads(r.read() or b"{}")


def deseado(env: dict, ruta: str | None, http=http_real) -> dict:
    base, clave = env.get("OHQ_API_URL", "").rstrip("/"), env.get("OHQ_API_KEY", "")
    if not base or not clave:
        raise Abortado("faltan OHQ_API_URL y/o OHQ_API_KEY en el .env")
    data = http(base + (ruta or env.get("OHQ_HERMES_AJUSTE_RUTA") or RUTA_POR_DEFECTO), clave)
    if isinstance(data, dict) and isinstance(data.get("hermes"), dict):
        data = data["hermes"]
    out = {}
    for campo in ("provider", "model"):
        v = data.get(campo) if isinstance(data, dict) else None
        if v in (None, ""):
            continue  # ajuste no definido: Hermes conserva el suyo
        if not isinstance(v, str) or not VALOR_OK.match(v):
            raise Abortado(f"valor no válido para «{campo}» en el ajuste de Oscar HQ")
        out[campo] = v
    return out


def actual(texto: str) -> dict:
    m = (yaml.safe_load(texto) or {}).get("model")
    if not isinstance(m, dict):
        raise Abortado("la config no tiene `model:` como bloque (provider/default); no la toco")
    return {"provider": m.get("provider"), "model": m.get("default")}


def editar(texto: str, cambios: dict) -> str:
    """Reescribe solo `provider:`/`default:` dentro del bloque `model:` (clave → línea del YAML)."""
    lineas = texto.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in texto else "\n"
    ini = next((i for i, l in enumerate(lineas) if re.match(r"^model:\s*(#.*)?$", l.rstrip("\r\n"))), None)
    if ini is None:
        raise Abortado("no encuentro el bloque `model:` en la config")
    fin = next((i for i in range(ini + 1, len(lineas)) if re.match(r"^\S", lineas[i]) and not lineas[i].startswith("#")),
               len(lineas))
    for campo, clave in (("provider", "provider"), ("model", "default")):
        if campo not in cambios:
            continue
        idx = next((i for i in range(ini + 1, fin) if re.match(rf"^\s+{clave}:", lineas[i])), None)
        if idx is None:
            lineas.insert(ini + 1, f"  {clave}: {cambios[campo]}{eol}")
            fin += 1
        else:
            sangria = re.match(r"^\s+", lineas[idx]).group(0)
            lineas[idx] = f"{sangria}{clave}: {cambios[campo]}{eol}"
    return "".join(lineas)


def reiniciar_real() -> None:
    subprocess.run(["hermes", "gateway", "restart"], check=True, timeout=120)  # noqa: S603,S607


def registrar(registro: Path | None, **campos) -> None:
    if registro is None:
        return
    registro.parent.mkdir(parents=True, exist_ok=True)
    linea = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **campos}
    with registro.open("a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")


def sincronizar(config: Path, env: dict, aplicar: bool = False, ruta: str | None = None, registro: Path | None = None,
                http=http_real, reiniciar=reiniciar_real, ahora=lambda: datetime.now()) -> tuple[int, str]:
    """Devuelve (código de salida, mensaje). Nunca incluye claves en el mensaje ni en el registro."""
    try:
        quiero = deseado(env, ruta, http)
        with config.open(encoding="utf-8", newline="") as f:  # sin traducir CRLF
            texto = f.read()
        tengo = actual(texto)
        cambios = {k: v for k, v in quiero.items() if tengo.get(k) != v}
        if not cambios:
            registrar(registro, resultado="sin_cambios")
            return 0, "Sin cambios: Hermes ya usa el proveedor/modelo de Oscar HQ."
        resumen = ", ".join(f"{k}: {tengo.get(k)} → {v}" for k, v in cambios.items())
        nuevo = editar(texto, cambios)
        esperado = yaml.safe_load(texto)
        esperado["model"].update(**({"provider": cambios["provider"]} if "provider" in cambios else {}),
                                 **({"default": cambios["model"]} if "model" in cambios else {}))
        try:
            ok = yaml.safe_load(nuevo) == esperado
        except yaml.YAMLError:
            ok = False
        if not ok:
            raise Abortado("el YAML resultante no es el esperado; no se escribe nada")
        if not aplicar:
            registrar(registro, resultado="ensayo", cambios=resumen)
            return 0, f"ENSAYO (no se ha escrito nada; usa --apply): {resumen}"
        bak = config.with_name(f"{config.name}.bak-{ahora():%Y%m%d-%H%M%S}")
        shutil.copy2(config, bak)
        tmp = config.with_name(config.name + ".tmp-sync")
        tmp.write_text(nuevo, encoding="utf-8", newline="")
        os.replace(tmp, config)
        try:
            reiniciar()
        except Exception as exc:  # el gateway sigue con la config vieja: se restaura para reintentar en la próxima pasada
            shutil.copy2(bak, config)
            registrar(registro, resultado="reinicio_fallido", cambios=resumen, error=type(exc).__name__)
            return 1, f"El reinicio del gateway falló ({type(exc).__name__}); config restaurada desde {bak.name}."
        registrar(registro, resultado="aplicado", cambios=resumen, bak=bak.name)
        return 0, f"APLICADO ({resumen}); copia {bak.name}; gateway reiniciado."
    except Abortado as exc:
        registrar(registro, resultado="abortado", motivo=str(exc))
        return 1, f"ABORTADO: {exc}"
    except Exception as exc:  # red/HTTP/lectura: no se toca nada y se reintenta en 10 min
        registrar(registro, resultado="error", error=type(exc).__name__)
        return 1, f"ERROR ({type(exc).__name__}): no se ha tocado la config."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="aplica (por defecto, ensayo)")
    ap.add_argument("--config", type=Path, default=HOME / "config.yaml")
    ap.add_argument("--env-file", type=Path, default=ENV_POR_DEFECTO)
    ap.add_argument("--ruta", help=f"ruta del ajuste en la API (por defecto {RUTA_POR_DEFECTO})")
    ap.add_argument("--registro", type=Path, default=HOME / "hermes-sync.log.jsonl")
    a = ap.parse_args(argv)
    codigo, msg = sincronizar(a.config, leer_env(a.env_file), a.apply, a.ruta, a.registro)
    print(msg)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
