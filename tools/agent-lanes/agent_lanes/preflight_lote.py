"""Comprobación previa de un lote (t_a6a8de60): choques que antes se encontraban a mano, vistos ANTES de fusionar.

Solo lectura: lee git (`diff`, `ls-tree`, `rev-parse`) y un fichero de datos; no escribe nada, no abre PR ni habla con
Telegram. Dada la lista de ramas (o PR) de un lote informa de:

  a) NUMERACIÓN de ficheros de esquema SQL nuevos (los de `supabase/migrations/`, el prefijo se pasa o sale de la
     política del carril): versión repetida entre ramas del lote (o ya existente en la base), o menor que la última ya
     integrada en la base (fuera de secuencia). BLOQUEA la fusión de esa rama. Si dos ramas traen la misma versión,
     conserva la primera de la lista (orden de aprobación) y bloquea las siguientes.
  b) SOLAPES de ficheros: tocados por más de una rama del lote o también por otros PR abiertos (`otros`). Solo AVISA.
  c) FALLOS PREVIOS: dado un conjunto de firmas de fallo (de `batch.failure_signatures`), separa los ya conocidos de
     la lista curada `docs/fallos_previos.json` de los nuevos. Solo informa.

Uso suelto (informe en texto; salida 1 si hay choque de numeración, 0 si no):
    py -3.12 -m agent_lanes.preflight_lote --repo <clon> --base origin/main \\
        --ramas origin/lane/t_a,origin/lane/t_b --otros origin/feat/x --prefijos supabase/migrations/
Una rama puede escribirse `etiqueta=ref`; un número (`12` o `#12`) se lee de `refs/integrator/pr-12` o
`refs/pull/12/head` (hay que haberlos traído antes con `git fetch`: este script no escribe nada, ni refs).

Lo usa batch.py como paso informativo del lote diario. Patrón de uso con la sesión de integración:
docs/lote-en-paralelo.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger("agent_lanes")

FALLOS_PREVIOS = Path(__file__).resolve().parents[3] / "docs" / "fallos_previos.json"
_VERSION_RE = re.compile(r"^(\d{8,14})(?:[_\-.]|$)")
MAX_FILES_LINEA = 8  # ramas por fichero que se listan en el informe


@dataclass
class Rama:
    """Una rama del lote (o de otro PR): etiqueta legible, referencia git y lo que añade/toca respecto a la base."""
    etiqueta: str
    ref: str
    ficheros: list[str] = field(default_factory=list)  # todo lo que toca (diff base...ref)
    nuevos: list[str] = field(default_factory=list)    # lo que añade (diff-filter=A)


@dataclass
class Hallazgo:
    tipo: str          # duplicada | fuera_de_secuencia
    rama: str
    fichero: str
    version: int
    detalle: str
    bloquea: bool


@dataclass
class Solape:
    fichero: str
    ramas: list[str]
    en_lote: list[str]


@dataclass
class Informe:
    numeracion: list[Hallazgo] = field(default_factory=list)
    solapes: list[Solape] = field(default_factory=list)
    nuevos: list[str] = field(default_factory=list)      # fallos no reconocidos
    conocidos: list[tuple[str, str]] = field(default_factory=list)  # (firma, id/nota del fallo previo)
    ramas: list[str] = field(default_factory=list)
    ultima_base: int | None = None
    error: str = ""

    @property
    def bloqueadas(self) -> dict[str, str]:
        """etiqueta de rama -> motivo (llano) para dejarla fuera del lote."""
        out: dict[str, list[str]] = {}
        for h in self.numeracion:
            if h.bloquea:
                out.setdefault(h.rama, []).append(h.detalle)
        return {k: "; ".join(v) for k, v in out.items()}

    @property
    def hay_choques(self) -> bool:
        return bool(self.numeracion or self.solapes)

    def render(self) -> str:
        """Informe en texto llano (sirve para consola, comentario o ficha)."""
        if self.error:
            return f"Comprobación previa no disponible: {self.error}"
        out = [f"Comprobación previa del lote ({len(self.ramas)} rama(s))"]
        if not self.hay_choques and not self.nuevos and not self.conocidos:
            out.append("Sin choques: numeración limpia y ningún fichero tocado por más de una rama.")
            return "\n".join(out)
        if self.numeracion:
            out.append("Numeración de esquema " + ("(última en la base: %s)" % self.ultima_base
                                                    if self.ultima_base else "") + ":")
            for h in self.numeracion:
                out.append(f"  {'⛔ BLOQUEA' if h.bloquea else '⚠'} {h.rama}: {h.detalle}")
        else:
            out.append("Numeración de esquema: sin choques.")
        if self.solapes:
            out.append("Ficheros tocados por más de una rama (solo aviso):")
            for s in self.solapes:
                out.append(f"  • {s.fichero}: {', '.join(s.ramas)}")
        else:
            out.append("Solapes de ficheros: ninguno.")
        if self.nuevos or self.conocidos:
            out.append(f"Fallos: {len(self.nuevos)} nuevo(s), {len(self.conocidos)} ya conocido(s) (docs/fallos_previos.json)")
            out += [f"  🆕 {f}" for f in self.nuevos[:10]]
            out += [f"  · {f} — {why}" for f, why in self.conocidos[:10]]
        return "\n".join(out)


# --- git (solo lectura) -------------------------------------------------------------------------------------

def _git(repo: str, *args: str, run: Callable = subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
               timeout=120, stdin=subprocess.DEVNULL)


def _lineas(cp) -> list[str]:
    return [l.strip() for l in (cp.stdout or "").splitlines() if l.strip()] if cp.returncode == 0 else []


def resolver_ref(repo: str, texto: str, run: Callable = subprocess.run) -> tuple[str, str] | None:
    """`etiqueta=ref`, ref o número de PR -> (etiqueta, ref que existe). None = no existe."""
    etiqueta, _, ref = texto.partition("=") if "=" in texto else ("", "", texto)
    ref = ref.strip()
    num = re.fullmatch(r"#?(\d+)", ref)
    candidatas = [f"refs/integrator/pr-{num.group(1)}", f"refs/pull/{num.group(1)}/head"] if num else [ref]
    for c in candidatas:
        if _git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}", run=run).returncode == 0:
            return (etiqueta.strip() or (f"PR #{num.group(1)}" if num else ref)), c
    return None


def leer_rama(repo: str, base: str, etiqueta: str, ref: str, run: Callable = subprocess.run) -> Rama:
    rng = f"{base}...{ref}"
    return Rama(etiqueta, ref,
                _lineas(_git(repo, "diff", "--no-renames", "--name-only", rng, run=run)),
                _lineas(_git(repo, "diff", "--no-renames", "--diff-filter=A", "--name-only", rng, run=run)))


def esquemas_en_base(repo: str, base: str, prefijos: Iterable[str], run: Callable = subprocess.run) -> dict[int, list[str]]:
    """versión -> ficheros de esquema que ya hay en la base."""
    out: dict[int, list[str]] = {}
    for pre in prefijos:
        for f in _lineas(_git(repo, "ls-tree", "-r", "--name-only", base, "--", pre, run=run)):
            v = version_de(f, (pre,))
            if v is not None:
                out.setdefault(v, []).append(f)
    return out


# --- comprobaciones puras -----------------------------------------------------------------------------------

def version_de(ruta: str, prefijos: Iterable[str]) -> int | None:
    """Versión (número inicial del nombre) de un .sql dentro de alguno de los prefijos; None si no es un esquema."""
    ruta = ruta.replace("\\", "/")
    for pre in prefijos:
        pre = pre.replace("\\", "/")
        if ruta.startswith(pre) and ruta.lower().endswith(".sql"):
            m = _VERSION_RE.match(ruta[len(pre):].split("/")[-1])
            return int(m.group(1)) if m else None
    return None


def comprobar_numeracion(ramas: list[Rama], base: dict[int, list[str]], prefijos: Iterable[str]) -> tuple[list[Hallazgo], int | None]:
    """Hallazgos de numeración de los esquemas NUEVOS de las ramas (en orden de la lista) frente a la base y entre sí."""
    prefijos = tuple(prefijos)
    ultima = max(base) if base else None
    visto: dict[int, tuple[str, str]] = {}  # versión -> (rama, fichero) de la primera que la usa
    out: list[Hallazgo] = []
    for r in ramas:
        for f in sorted(r.nuevos):
            v = version_de(f, prefijos)
            if v is None:
                continue
            if v in base:
                out.append(Hallazgo("duplicada", r.etiqueta, f, v, f"{f}: la versión {v} ya existe en la base "
                                    f"({base[v][0]})", True))
            elif v in visto:
                otra, _ = visto[v]
                out.append(Hallazgo("duplicada", r.etiqueta, f, v, f"{f}: la versión {v} la trae también {otra}"
                                    + (" (va antes: esta queda fuera)" if otra != r.etiqueta else " (dos ficheros iguales)"),
                                    True))
            else:
                visto[v] = (r.etiqueta, f)
            if ultima is not None and v < ultima and v not in base:
                out.append(Hallazgo("fuera_de_secuencia", r.etiqueta, f, v,
                                    f"{f}: versión {v} fuera de secuencia (la última en la base es {ultima})", True))
    # La primera rama de un duplicado entre ramas se conserva; se avisa de ella sin bloquearla.
    for v, (dueña, f) in visto.items():
        otras = sorted({h.rama for h in out if h.tipo == "duplicada" and h.version == v and h.rama != dueña})
        if otras:
            out.append(Hallazgo("duplicada", dueña, f, v, f"{f}: la versión {v} también la traen {', '.join(otras)} "
                                "(esta se conserva, las otras quedan fuera)", False))
    return out, ultima


def comprobar_solapes(ramas: list[Rama], otros: list[Rama] = ()) -> list[Solape]:
    """Ficheros tocados por ≥2 ramas con al menos una del lote."""
    dueños: dict[str, list[str]] = {}
    en_lote = {r.etiqueta for r in ramas}
    for r in [*ramas, *otros]:
        for f in set(r.ficheros):
            dueños.setdefault(f, []).append(r.etiqueta)
    return [Solape(f, rs, [x for x in rs if x in en_lote]) for f, rs in sorted(dueños.items())
            if len(rs) > 1 and any(x in en_lote for x in rs)]


def cargar_fallos_previos(path: Path | str | None = None, repo: str = "") -> list[dict]:
    """Lista curada de fallos ya conocidos. Ausente o inválida = vacía (nunca lanza). `repo` filtra por nombre de
    repo (la entrada sin `repo` vale para todos)."""
    p = Path(path) if path else FALLOS_PREVIOS
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("fallos") if isinstance(data, dict) else None
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("patron"), str) and it["patron"].strip():
            if not it.get("repo") or not repo or str(it["repo"]).lower() == repo.lower():
                out.append(it)
    return out


def clasificar_fallos(firmas: Iterable[str], conocidos: list[dict]) -> tuple[list[str], list[tuple[str, str]]]:
    """(nuevos, [(firma, motivo)]) según si algún `patron` de la lista curada está contenido en la firma."""
    nuevos: list[str] = []
    viejos: list[tuple[str, str]] = []
    for s in sorted(set(firmas)):
        hit = next((c for c in conocidos if c["patron"] in s), None)
        if hit:
            viejos.append((s, " · ".join(str(x) for x in (hit.get("id"), hit.get("nota")) if x) or "conocido"))
        else:
            nuevos.append(s)
    return nuevos, viejos


def analizar(repo: str, base: str, ramas: Iterable[str], *, prefijos: Iterable[str] = ("supabase/migrations/",),
             otros: Iterable[str] = (), firmas: Iterable[str] = (), fallos_path: Path | str | None = None,
             nombre_repo: str = "", run: Callable = subprocess.run) -> Informe:
    """Informe completo. Nunca lanza: un fallo de git deja `error` (y el lote sigue como siempre)."""
    inf = Informe()
    try:
        prefijos = tuple(prefijos)
        lote, aparte = [], []
        for texto, destino in [(t, lote) for t in ramas] + [(t, aparte) for t in otros]:
            res = resolver_ref(repo, texto, run=run)
            if res is None:
                if destino is lote:
                    inf.error = f"no encuentro la rama o PR «{texto}» en el clon"
                    return inf
                continue  # un «otro PR» que no está traído no impide comprobar el lote
            destino.append(leer_rama(repo, base, res[0], res[1], run=run))
        inf.ramas = [r.etiqueta for r in lote]
        if prefijos:
            inf.numeracion, inf.ultima_base = comprobar_numeracion(lote, esquemas_en_base(repo, base, prefijos, run=run), prefijos)
        inf.solapes = comprobar_solapes(lote, aparte)
        inf.nuevos, inf.conocidos = clasificar_fallos(firmas, cargar_fallos_previos(fallos_path, nombre_repo))
    except Exception as exc:  # solo informa: jamás debe tumbar el lote
        log.warning("preflight_lote: %s", exc)
        inf.error = f"{type(exc).__name__}: {exc}"[:200]
    return inf


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Comprobación previa de un lote (solo lectura).")
    ap.add_argument("--repo", required=True, help="clon donde están las ramas (ya traídas con git fetch)")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--ramas", required=True, help="lista separada por comas, en orden de aprobación")
    ap.add_argument("--otros", default="", help="otros PR/ramas abiertos con los que comparar ficheros")
    ap.add_argument("--prefijos", default="supabase/migrations/", help="carpetas de esquema SQL, separadas por comas")
    ap.add_argument("--salida-tests", default="", help="fichero con la salida de vitest/tsc para separar fallos nuevos de viejos")
    ap.add_argument("--fallos", default="", help="lista curada (por defecto docs/fallos_previos.json)")
    ap.add_argument("--repo-nombre", default="", help="nombre del repo para filtrar la lista curada")
    a = ap.parse_args(argv)
    firmas: set[str] = set()
    if a.salida_tests:
        from .batch import failure_signatures  # import tardío: batch importa este módulo
        firmas = failure_signatures(Path(a.salida_tests).read_text(encoding="utf-8", errors="replace"))
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    inf = analizar(a.repo, a.base, split(a.ramas), prefijos=split(a.prefijos), otros=split(a.otros), firmas=firmas,
                   fallos_path=a.fallos or None, nombre_repo=a.repo_nombre)
    print(inf.render())
    return 1 if inf.error or inf.bloqueadas else 0


if __name__ == "__main__":
    sys.exit(main())
