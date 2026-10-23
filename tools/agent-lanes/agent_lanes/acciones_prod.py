"""Acciones de producción del carril integrador desde Telegram (fases 1-2 de specs/carril-integrador-telegram.md).

El worker NO ejecuta shell libre: propone una acción tipada `{tipo, objetivo, parámetros}` y este módulo la valida
contra una lista blanca, construye un argv FIJO (sin `shell=True`) y la ejecuta con límites de tiempo y tamaño. La
salida se redacta (tokens/JWT/`KEY=`) antes de volver al worker o a Telegram y cada acción queda en el registro de
auditoría (append-only; sin valores ni SQL, solo hash y longitud).

Solo el nivel A (leer/abrir PR) se ejecuta aquí. Las acciones de nivel B están registradas pero `ejecutar` las
rechaza: requieren el botón con doble toque (fase 3, con security-auditor). Nivel C (DROP/borrados) no existe.
Los tokens los lee la herramienta envuelta (ssh/gh/curl con ficheros ya existentes), nunca este módulo ni el modelo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import proc

NIVEL_A, NIVEL_B = "A", "B"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "integrador_telegram.yaml"
AUDITORIA_PATH = Path(__file__).resolve().parents[1] / ".state" / "integrador_telegram" / "auditoria.jsonl"
MAX_SALIDA = 8000       # caracteres que vuelven al worker/Telegram
TIMEOUT_S = 60
MAX_LINEAS_LOGS = 500


class AccionRechazada(ValueError):
    """La acción no está en la lista blanca o sus parámetros no validan. El mensaje es apto para avisar."""


# ---------- redacción de secretos ----------
_REDACCIONES = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),                      # JWT
    re.compile(r"\b(?:sk|pk|sbp|ghp|gho|ghs|github_pat|xox[abp]|glpat|vercel|rk)[-_][A-Za-z0-9_-]{10,}"),  # prefijos de token
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),                                                 # token de bot de Telegram
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
]
_ASIGNACION = re.compile(r"(?im)\b([A-Z0-9_.-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL)[A-Z0-9_.-]*)\s*[=:]\s*\S+")


def redactar(texto: str) -> str:
    """Quita de un texto todo lo que parezca un secreto. Mejor pasarse que quedarse corto."""
    texto = str(texto or "")
    for rx in _REDACCIONES:
        texto = rx.sub("[REDACTADO]", texto)
    return _ASIGNACION.sub(lambda m: f"{m.group(1)}=[REDACTADO]", texto)


# ---------- validadores ----------
_NOMBRE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RAMA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_VERBOS_ESCRITURA = re.compile(
    r"(?i)\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|copy|merge|call|do|execute|vacuum|reindex|"
    r"comment|lock|set|reset|listen|notify|into|pg_sleep|pg_read_file|pg_write|lo_import|lo_export|dblink|"
    r"set_config|nextval|setval|pg_terminate_backend|pg_cancel_backend)\b"
)


def validar_select(consulta: str) -> str:
    """Una sola sentencia `SELECT`/`WITH`, sin `;` ni comentarios, sin verbos ni funciones de escritura."""
    q = (consulta or "").strip() if isinstance(consulta, str) else ""
    if not q or len(q) > 4000:
        raise AccionRechazada("consulta vacía o demasiado larga")
    if ";" in q or "--" in q or "/*" in q or "\x00" in q:
        raise AccionRechazada("la consulta no puede llevar ';' ni comentarios")
    if not re.match(r"(?i)^(select|with)\b", q):
        raise AccionRechazada("solo se admite SELECT o WITH")
    if _VERBOS_ESCRITURA.search(q):
        raise AccionRechazada("la consulta contiene un verbo o función de escritura")
    return q


def _en_lista(valor, permitidos, etiqueta: str) -> str:
    v = valor if isinstance(valor, str) else ""
    if not _NOMBRE.match(v):
        raise AccionRechazada(f"{etiqueta} no válido")
    if v not in permitidos:
        raise AccionRechazada(f"{etiqueta} «{v}» no está en la lista blanca")
    return v


# ---------- configuración (falla cerrado) ----------
_CLAVES = ("servicios_logs", "repos_pr", "proyectos_sql", "hosts_health", "bots_webhook")


def cargar_config(path: Path | None = None) -> dict[str, frozenset]:
    """Lista blanca de nombres. Cualquier error ⇒ listas vacías ⇒ todo rechazado."""
    vacio = {k: frozenset() for k in _CLAVES}
    try:
        import yaml
        datos = yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    except Exception:
        return vacio
    if not isinstance(datos, dict):
        return vacio
    out = dict(vacio)
    for k in _CLAVES:
        v = datos.get(k)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = frozenset(v)
    return out


# ---------- registro de acciones ----------
@dataclass(frozen=True)
class Accion:
    tipo: str
    nivel: str
    construir: object        # (cfg, objetivo, params) -> argv (lista) o None si no es ejecutable aquí
    descripcion: str


def _logs(cfg, objetivo, p):
    servicio = _en_lista(objetivo, cfg["servicios_logs"], "servicio")
    try:
        n = int(p.get("lineas", 100))
    except (TypeError, ValueError):
        raise AccionRechazada("líneas no válidas")
    if not 1 <= n <= MAX_LINEAS_LOGS:
        raise AccionRechazada(f"líneas fuera de rango (1-{MAX_LINEAS_LOGS})")
    # Alias `pildora-01` de ~/.ssh/config: la clave la resuelve ssh, nunca este módulo. Comando remoto fijo.
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "pildora-01", f"docker logs --tail {n} {servicio}"]


def _sql_select(cfg, objetivo, p):
    proyecto = _en_lista(objetivo, cfg["proyectos_sql"], "proyecto")
    q = validar_select(p.get("consulta"))
    # Envoltorio de solo lectura sobre la Management API: añade el token desde su fichero habitual (no va por argv).
    return ["curl-supabase", "--read-only", proyecto, json.dumps({"query": q})]


def _health(cfg, objetivo, p):
    host = _en_lista(objetivo, cfg["hosts_health"], "host")
    return ["curl", "-sS", "-m", "10", "-o", "/dev/null", "-w", "%{http_code} %{time_total}s", f"https://{host}/"]


def _webhook_info(cfg, objetivo, p):
    bot = _en_lista(objetivo, cfg["bots_webhook"], "bot")
    return ["curl-telegram", "getWebhookInfo", bot]


def _abrir_pr(cfg, objetivo, p):
    repo = _en_lista(objetivo, cfg["repos_pr"], "repo")
    rama, base = p.get("rama"), p.get("base") or "main"
    for r in (rama, base):
        if not isinstance(r, str) or not _RAMA.match(r) or ".." in r or r.endswith(("/", ".lock")):
            raise AccionRechazada("rama no válida")
    if base not in ("main", "master"):
        raise AccionRechazada("un PR solo puede apuntar a main o master")
    titulo = re.sub(r"[\r\n\x00]", " ", str(p.get("titulo") or rama))[:120]
    return ["gh", "pr", "create", "--repo", f"oscarsao/{repo}", "--head", rama, "--base", base,
            "--title", titulo, "--body", "Abierto por el carril integrador (ver tarjeta de Hermes)."]


def _solo_b(*_):
    return None   # se ejecuta únicamente tras el botón con doble toque (fase 3)


ACCIONES: dict[str, Accion] = {a.tipo: a for a in (
    Accion("logs", NIVEL_A, _logs, "logs de un contenedor de pildora-01 (SSH, solo lectura)"),
    Accion("sql_select", NIVEL_A, _sql_select, "SELECT en Supabase por la Management API, rol de solo lectura"),
    Accion("health", NIVEL_A, _health, "health HTTP de un host de la lista blanca"),
    Accion("webhook_info", NIVEL_A, _webhook_info, "getWebhookInfo de un bot"),
    Accion("abrir_pr", NIVEL_A, _abrir_pr, "abrir PR hacia main en un repo de la lista blanca"),
    Accion("fusionar", NIVEL_B, _solo_b, "fusionar un PR a main/master"),
    Accion("desplegar", NIVEL_B, _solo_b, "desplegar una app (Dokploy/Vercel)"),
    Accion("migracion", NIVEL_B, _solo_b, "aplicar una migración SQL revisada"),
    Accion("variable", NIVEL_B, _solo_b, "cambiar una variable de entorno (el valor lo da el aprobador)"),
)}


def hash_params(objetivo: str, params: dict) -> str:
    """Huella estable de la acción: es lo que liga un botón de aprobación a lo que se aprobó."""
    return hashlib.sha256(json.dumps([objetivo, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def preparar(tipo: str, objetivo: str, params: dict | None = None, cfg: dict | None = None) -> tuple[Accion, list[str] | None]:
    """Valida la acción y devuelve (acción, argv). Lanza AccionRechazada si no encaja. argv=None en nivel B."""
    accion = ACCIONES.get(tipo) if isinstance(tipo, str) else None
    if accion is None:
        raise AccionRechazada(f"acción «{str(tipo)[:40]}» no existe en la lista blanca")
    if not isinstance(params, (dict, type(None))):
        raise AccionRechazada("parámetros mal formados")
    cfg = cfg if cfg is not None else cargar_config()
    return accion, accion.construir(cfg, objetivo, params or {})


# ---------- auditoría ----------
def auditar(registro: dict, path: Path | None = None) -> None:
    """Una línea JSON por acción. Append-only y 0600; si no se puede escribir, la acción NO debe seguir."""
    p = Path(path or AUDITORIA_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    linea = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **registro},
                       ensure_ascii=False, sort_keys=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def _huella_params(tipo: str, params: dict) -> dict:
    """Solo hash y longitud de lo sensible (SQL, valores); nunca el contenido."""
    if tipo == "sql_select":
        q = str(params.get("consulta") or "")
        return {"consulta_len": len(q), "consulta_sha": hashlib.sha256(q.encode()).hexdigest()[:12]}
    return {k: v for k, v in params.items() if k in ("lineas", "rama", "base")}


# ---------- ejecución (nivel A) ----------
def ejecutar(tipo: str, objetivo: str, params: dict | None = None, *, tarjeta: str = "", carril: str = "",
             cfg: dict | None = None, runner=None, audit_path: Path | None = None) -> dict:
    """Ejecuta una acción de NIVEL A. Devuelve {ok, salida (redactada y recortada), hash}. `runner(argv, timeout)`
    se inyecta en los tests; por defecto `proc.run`. Rechazos y nivel B también quedan auditados."""
    params = params if isinstance(params, dict) else {}
    base = {"tarjeta": tarjeta, "accion": str(tipo)[:40], "objetivo": str(objetivo)[:80], "quien_pidio": carril}
    try:
        accion, argv = preparar(tipo, objetivo, params, cfg)
    except AccionRechazada as e:
        auditar({**base, "resultado": "rechazada", "motivo": str(e)[:200]}, audit_path)
        raise
    h = hash_params(str(objetivo), params)
    base |= {"nivel": accion.nivel, "hash_params": h, **_huella_params(tipo, params)}
    if accion.nivel != NIVEL_A or argv is None:
        auditar({**base, "resultado": "rechazada", "motivo": "nivel B: requiere botón con doble toque"}, audit_path)
        raise AccionRechazada("esta acción necesita la aprobación con botón (nivel B)")
    auditar({**base, "resultado": "iniciada"}, audit_path)   # si la auditoría falla, no se ejecuta
    t0 = time.monotonic()
    try:
        if runner is None:
            cp = proc.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, shell=False)
            salida, rc = (cp.stdout or "") + (cp.stderr or ""), cp.returncode
        else:
            salida, rc = runner(argv, TIMEOUT_S)
    except Exception as e:  # incluye timeout o herramienta ausente
        salida, rc = f"{type(e).__name__}: {e}", -1
    texto = redactar(salida)
    if len(texto) > MAX_SALIDA:
        texto = texto[:MAX_SALIDA] + f"\n… (recortado, {len(salida)} caracteres)"
    auditar({**base, "resultado": "ok" if rc == 0 else "error", "rc": rc, "segundos": round(time.monotonic() - t0, 1)},
            audit_path)
    return {"ok": rc == 0, "salida": texto, "hash": h}
