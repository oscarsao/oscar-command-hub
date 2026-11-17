"""PR en borrador automático al terminar una tarjeta verificada (decisión de Oscar, 17-11-2026).

Cuando el runner da por buena la verificación mecánica y la rama `lane/<id>` está empujada, abre con
`gh pr create --draft` un PR hacia la base del repo de la tarjeta. Solo abre: nunca fusiona ni lo marca como listo.

- Interruptor `pr_borrador_automatico` en lanes.yaml (por carril y en `defaults`); apagado por defecto.
- Idempotente: si ya hay un PR abierto para la rama, no crea otro (y comenta el existente).
- Tarjetas de rebase/arreglo (línea `Rama-push:`): no abre PR nuevo, el PR ya existe.
- Un fallo de gh (sin red, permisos, repo sin remoto) NUNCA tumba la tarjeta: devuelve el aviso para el log y el
  comentario, y el runner sigue.
- El cuerpo no lleva rutas locales ni nada que parezca un secreto.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from . import proc as _proc

log = logging.getLogger("agent_lanes")

GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
NOTA = "Borrador abierto por el runner; fusionar solo con el botón de Oscar."
MAX_RESUMEN = 1500
# Rutas locales de Windows (C:\..., C:/...) y de Git Bash (/c/Users/...), y homes de Unix.
RUTA_LOCAL = re.compile(r"(?:[A-Za-z]:[\\/][^\s`'\"<>|)\]]*|/[a-z]/(?:Users|tmp|dev)[^\s`'\"<>|)\]]*"
                        r"|/(?:home|Users)/[^\s`'\"<>|)\]]+)")


def _gh_exe() -> str:
    return GH_EXE if Path(GH_EXE).exists() else "gh"


def limpiar(texto: str) -> str:
    """Quita rutas locales del texto y, si parece llevar un secreto, descarta la línea entera (sin citar el valor)."""
    from .integrator import scan_secrets  # import tardío: integrator importa módulos que importan este
    salida = []
    for linea in (texto or "").splitlines():
        linea = RUTA_LOCAL.sub("<ruta local>", linea)
        if scan_secrets(f"diff --git a/x b/x\n+++ b/x\n@@ -0,0 +1 @@\n+{linea}"):
            linea = "(línea omitida: parecía contener un secreto)"
        salida.append(linea)
    return "\n".join(salida).strip()


def construir_cuerpo(tid: str, summary: str, sha: str, test_cmd: str, test_exit: int | None) -> str:
    resultado = "sin comando de pruebas en el carril" if test_exit is None else \
        ("OK (exit 0)" if test_exit == 0 else f"exit {test_exit}")
    cuerpo = "\n\n".join((
        f"Tarjeta: {tid}",
        limpiar(summary or "")[:MAX_RESUMEN] or "(sin resumen)",
        f"SHA: {sha}",
        f"Pruebas: `{limpiar(test_cmd or '(ninguno)')}` → {resultado}",
        NOTA,
    ))
    return cuerpo


def _gh(args: list[str], gh_run) -> object:
    # cwd fuera de cualquier clon: gh no debe tocar el worktree del worker.
    return gh_run([_gh_exe(), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=90, cwd=tempfile.gettempdir())


def abrir(lane, tid: str, task: dict, result: dict, check, *, gh_run=_proc.run) -> str | None:
    """Abre (o reconoce) el PR en borrador. Devuelve el texto para el comentario de la tarjeta, o None si no hay
    nada que decir (interruptor apagado, rebase). No lanza nunca."""
    try:
        if not getattr(lane, "pr_borrador_automatico", False):
            return None
        from .worker import push_refs  # import tardío: worker importa este paquete
        if push_refs(task):
            return None  # tarjeta de rebase/arreglo: el PR de la rama original ya existe
        slug = lane.github
        if not slug:
            return _fallo(tid, "el carril no tiene `github` configurado")
        branch = f"lane/{tid}"
        ls = _gh(["pr", "list", "--repo", slug, "--head", branch, "--state", "open", "--json", "number,url"], gh_run)
        if ls.returncode != 0:
            return _fallo(tid, f"no se pudo comprobar si ya hay PR: {_cola(ls)}")
        try:
            abiertos = json.loads(ls.stdout or "[]")
        except json.JSONDecodeError:
            return _fallo(tid, "respuesta de gh pr list ilegible")
        if abiertos:
            return f"PR de la rama ya existente (no se abre otro): {abiertos[0].get('url')}"
        sha = (getattr(check, "remote_sha", None) or result.get("head_sha") or "").strip()
        cuerpo = construir_cuerpo(tid, result.get("summary") or "", sha, getattr(lane, "test_cmd", ""),
                                  getattr(check, "test_exit", None))
        cp = _gh(["pr", "create", "--draft", "--repo", slug, "--base", lane.base, "--head", branch,
                  "--title", (task.get("title") or tid), "--body", cuerpo], gh_run)
        if cp.returncode != 0:
            return _fallo(tid, f"gh pr create falló: {_cola(cp)}")
        m = re.search(r"https://github\.com/\S+/pull/\d+", cp.stdout or "")
        return f"PR en borrador abierto por el runner: {m.group(0)}" if m else \
            "PR en borrador abierto por el runner (sin URL en la salida de gh)"
    except Exception as exc:  # nunca tumba la tarjeta
        return _fallo(tid, f"{type(exc).__name__}")


def _cola(cp) -> str:
    return limpiar(((cp.stderr or "") + (cp.stdout or "")).strip()[-200:]) or f"exit {cp.returncode}"


def _fallo(tid: str, motivo: str) -> str:
    log.warning("%s: PR en borrador no abierto: %s", tid, motivo)
    return f"PR en borrador NO abierto ({motivo}); la tarjeta sigue su curso, el PR se abre a mano."
