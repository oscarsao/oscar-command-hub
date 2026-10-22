"""/al_dia, tarjeta b (specs/al-dia-prod.md): `prod`, «🛠 Resolver» y la oferta proactiva «🔄 Poner al día».

- `prod` NO despliega por su cuenta: propone la ficha de `/desplegar` y el despliegue lo hace el integrador
  (`INT_DEPLOY`, Dokploy/Railway según la política) tras «¿Seguro?». Solo Oscar o quien tenga "*" en people.yaml.
  Migraciones SQL, infraestructura y variables de entorno nunca se aplican: se listan («pedir al integrador»).
- Resolver: tarjeta de arreglo en el carril, idempotente por (fallo, SHA).
- Ofertas: la clave es el hash de HECHOS ESTABLES (conjuntos de PR/tarjetas; nunca cuentas ni edades, que crecen
  solas), guardada en `.state/al_dia/ofertas.json`."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .titulos import proyecto_de_carril

log = logging.getLogger("agent-lanes.al_dia")

AD_PROD, AD_PROD_OK = "ad_prod", "ad_prod_ok"           # «🚀 Desplegar producción» → «¿Seguro?» → «✅ Sí, desplegar»
AD_RESOLVER, AD_PONER = "ad_resolver", "ad_poner"       # «🛠 Resolver» / «🔄 Poner al día» (oferta proactiva)
PRIORIDAD_ALTA = 10                                      # kanban: más alto = antes (igual que /hazlo)


def edad_horas(creado: str, ahora: float) -> float:
    try:
        dt = datetime.fromisoformat(str(creado).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (ahora - (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()) / 3600)


class ProdMixin:
    # --- estado en .state/al_dia/ ------------------------------------------------------------------------------------

    @property
    def estado_dir(self) -> Path:
        return self._estado_dir or Path(self.i.state_dir).parent / "al_dia"

    def _leer(self, nombre: str) -> dict:
        try:
            data = json.loads((self.estado_dir / nombre).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _guardar(self, nombre: str, data: dict) -> None:
        self.estado_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.estado_dir / f"{nombre}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.estado_dir / nombre)

    # --- prod: solo propone --------------------------------------------------------------------------------------------

    def pendientes_manuales(self, st) -> list[str]:
        from .integrator import pending_migration
        out = []
        for tid in st.sin_desplegar:
            mig = pending_migration(self.i._state(tid))
            if mig:
                out.append(f"⏸ {tid}: {mig} pendiente → pedir al integrador (no se aplica desde aquí)")
        return out

    def prod_plan(self, key: str, promotores=()) -> dict:
        """{"texto"} si no hay despliegue que lanzar desde aquí, o {"rec"} con la ficha de /desplegar (la punta de la
        base: el integrador marca como desplegadas las demás fusiones)."""
        from .al_dia import ProjectState
        from .integrator import DEPLOY_BUTTON
        lane, policy = self.proyectos()[key]
        if key in promotores:
            return {"texto": f"{key}: producción va por /promover {key} (develop → master, con su doble confirmación)."}
        if policy.deploy not in DEPLOY_BUTTON:
            return {"texto": f"{key}: se despliega solo al fusionar; no hay nada que lanzar desde aquí."}
        merged = self.i._merged_states(lane, "")
        if not merged:
            return {"texto": f"{key}: nada fusionado sin desplegar."}
        bloqueo = self.pendientes_manuales(ProjectState(key, lane, policy, sin_desplegar=[t for t, _ in merged]))
        if bloqueo:
            return {"texto": f"⛔ No despliego {key}:\n" + "\n".join(bloqueo)}
        tid, ficha = merged[-1]
        return {"rec": {"kind": "integrate", "task_id": tid, "board": lane.board, "lane": lane.name,
                        "title": ficha.get("title") or "", "pr_number": ficha.get("pr"),
                        "pr_url": ficha.get("pr_url") or "", "merge_sha": ficha.get("merge_sha"), "migration": None}}

    # --- «🛠 Resolver» -------------------------------------------------------------------------------------------------

    def crear_arreglo(self, lane, fallo: str, sha: str, titulo: str, detalle: str, rama: str = "") -> str:
        """Tarjeta de arreglo (formato tarjeta-carril). Idempotente por (fallo, SHA), también tras reiniciar."""
        clave = f"resolver-{fallo}-{(sha or 'sin-sha')[:12]}"
        hechas = self._leer("resolver.json")
        if hechas.get(clave):
            return f"🛠 Ya existe la tarjeta {hechas[clave]} para este fallo ({fallo} · {(sha or '?')[:7]})."
        cuerpo = "\n".join([
            f"- **Origen y decisión de Oscar:** pulsó «🛠 Resolver» en /al_dia ante un fallo ({fallo}) en {(sha or '?')[:12]}.",
            f"- **Repo / base:** el del carril {lane.name}, `{lane.base}`." + (f" Rama-origen: {rama}" if rama else ""),
            "- **Alcance:** diagnosticar y arreglar el fallo de abajo con el cambio mínimo; sin refactors.",
            "- **Hecho cuando:** el fallo desaparece (CI/gate/health en verde) y la suite del carril pasa.",
            "- **Límites:** sin tocar hooks ni contrato; sin desplegar ni cambiar variables de entorno.",
            "", f"## Fallo ({fallo})", (detalle or "")[:3000] or "(sin log disponible)"])
        card = self.i.hermes_for(lane.board).create(
            f"[{proyecto_de_carril(lane.name)}] {titulo} · arreglar", cuerpo, lane.name, priority=PRIORIDAD_ALTA,
            key=clave, created_by="lane-integrator")
        hechas[clave] = card
        self._guardar("resolver.json", hechas)
        return f"🛠 Tarjeta de arreglo {card} creada en {lane.name} (prioridad alta, con el log adjunto)."

    def resolver_ci(self, st, p) -> str:
        detalle = f"Checks en rojo del PR #{p.number} ({p.url}):\n" + "\n".join(f"- {r}" for r in p.rojos)
        return self.crear_arreglo(st.lane, "ci_rojo", p.head, f"CI rojo en #{p.number} {p.title[:40]}", detalle,
                                  rama=f"lane/{p.tid}")

    # --- oferta proactiva ----------------------------------------------------------------------------------------------

    def deriva(self, st, ahora: float) -> dict:
        aj = self._aj()
        vistos = self._leer("visto.json")
        for tid in st.sin_desplegar:  # la edad de lo fusionado se mide desde que se vio por primera vez
            vistos.setdefault(f"{st.key}:{tid}", ahora)
        self._guardar("visto.json", {k: v for k, v in vistos.items()
                                     if not k.startswith(st.key + ":") or k.split(":", 1)[1] in st.sin_desplegar})
        hecho = {
            "listos_viejos": sorted(p.number for p in st.prs if p.estado == "listo"
                                    and edad_horas(p.creado, ahora) >= aj["pr_listo_horas"]),
            "atrasadas": sorted(p.number for p in st.prs if p.atras >= aj["rama_atras_commits"]),
            "sin_desplegar": sorted(t for t in st.sin_desplegar
                                    if ahora - vistos[f"{st.key}:{t}"] >= aj["sin_desplegar_horas"] * 3600)}
        return {k: v for k, v in hecho.items() if v}

    def ofertas(self, ahora: float | None = None) -> list[tuple[str, str, dict]]:
        """[(clave, texto, rec)] de proyectos con deriva NUEVA: una oferta por estado; al volver a estar al día se olvida."""
        ahora = time.time() if ahora is None else ahora
        hechas, nuevas = self._leer("ofertas.json"), []
        for key, (lane, _) in self.proyectos().items():
            try:
                st = self.diagnose(key)
                hecho = self.deriva(st, ahora)
            except Exception as exc:  # un proyecto roto no tumba el resto
                log.warning("al_dia: oferta de %s: %s", key, exc)
                continue
            if not hecho:
                hechas.pop(key, None)
                continue
            h = hashlib.sha1(json.dumps(hecho, sort_keys=True).encode()).hexdigest()[:16]
            if hechas.get(key) == h:
                continue
            hechas[key] = h
            partes = []
            if hecho.get("listos_viejos"):
                partes.append(f"PR listos desde hace más de {self._aj()['pr_listo_horas']} h: "
                              + ", ".join(f"#{n}" for n in hecho["listos_viejos"]))
            if hecho.get("atrasadas"):
                partes.append("ramas atrasadas: " + ", ".join(f"#{n}" for n in hecho["atrasadas"]))
            if hecho.get("sin_desplegar"):
                partes.append("fusionado sin desplegar: " + ", ".join(hecho["sin_desplegar"]))
            nuevas.append((key, f"🔄 {key} se ha quedado atrás: " + "; ".join(partes) + ".",
                           {"kind": "al_dia", "lane": lane.name, "board": lane.board, "key": key,
                            "task_id": (st.prs[0].tid if st.prs else (st.sin_desplegar or [""])[0])}))
        self._guardar("ofertas.json", hechas)
        return nuevas
