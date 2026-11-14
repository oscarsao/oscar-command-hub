#!/usr/bin/env node
/**
 * Hook PreToolUse "por destino" (Bash, PowerShell y herramientas MCP).
 *
 * Por qué existe (14-11-2026): las reglas «SIEMPRE confirma» (push/merge a master/main, despliegues, migraciones,
 * envíos) estaban solo en CLAUDE.md y los permisos las contradecían (`gh pr merge *` y `railway up *` en `allow`,
 * `mcp__claude_ai_Supabase__*` sin filtro). El hook anterior miraba el TEXTO del comando: un `git push` a secas
 * (cómo se despliega MigraTeam) no preguntaba. Este mira el DESTINO real.
 *
 * Decisión por llamada (se juntan las de todos los segmentos del comando; gana ask > allow):
 *   - ask:   destino protegido, destino no resoluble, o herramienta/comando de la política.
 *   - allow: solo `gh pr merge` cuyo PR apunta a una rama NO protegida (así se puede quitar el allow ancho).
 *   - nada:  el resto sigue el flujo normal de permisos (exit 0 sin salida).
 *
 * Reglas de diseño:
 *   - Falla cerrado hacia master/main: lo que no se puede resolver, se pregunta.
 *   - En `claude -p` (carriles) un ask equivale a denegar: los workers solo empujan a `lane/*`, que pasa sin avisar.
 *   - Nunca debe romper: cualquier excepción interna -> pregunta solo si el texto parece riesgoso; si no, no opina.
 *   - Sin dependencias: Node ≥ 18. La configuración está en politica-permisos.json (mismo directorio, o HOOK_POLICY).
 *   - Solo para pruebas: HOOK_GH_FAKE_BASE sustituye la llamada a `gh pr view` ('__error__' simula fallo).
 */
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve, basename } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));

function cargaPolitica() {
  const ruta = process.env.HOOK_POLICY || join(AQUI, "politica-permisos.json");
  try {
    return { ...JSON.parse(readFileSync(ruta, "utf8")), _cargada: true };
  } catch {
    // Sin política legible: mínimo seguro (solo protege master/main y no lista herramientas).
    return { protegidas: ["master", "main"], protegidas_por_repo: {}, gh_timeout_ms: 8000,
      comandos_ask: [], herramientas_ask: [], sql: { herramienta: "^$" }, _cargada: false };
  }
}

// ---------- utilidades ----------
const nombreCmd = (p) => String(p).replace(/\\/g, "/").split("/").pop().toLowerCase().replace(/\.(exe|cmd|bat|ps1)$/, "");

function rutaNativa(p) {
  // Git Bash escribe /c/Users/...; el git nativo de Windows no lo entiende.
  if (process.platform === "win32") {
    const m = /^\/([a-zA-Z])(\/.*)?$/.exec(p);
    if (m) return `${m[1]}:${m[2] || "/"}`;
  }
  return p;
}
const resuelve = (dir, base) => resolve(base, rutaNativa(dir));

function git(cwd, ...args) {
  const r = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8", timeout: 5000, windowsHide: true });
  return r.status === 0 ? r.stdout.trim() : null;
}

function globARegex(g) {
  return new RegExp("^" + g.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$");
}
function esProtegida(rama, cwd, pol) {
  const top = git(cwd, "rev-parse", "--show-toplevel");
  const extra = (top && pol.protegidas_por_repo && pol.protegidas_por_repo[basename(top)]) || [];
  return [...(pol.protegidas || []), ...extra].some((g) => globARegex(g).test(rama));
}

// ---------- tokenizador de shell (comillas, operadores, saltos de línea) ----------
export function tokeniza(s) {
  const out = []; let cur = ""; let q = null; let has = false;
  const push = () => { if (has) out.push({ t: cur }); cur = ""; has = false; };
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) {
      if (c === q) q = null;
      else if (q === '"' && c === "\\" && s[i + 1] === '"') { cur += '"'; i++; }
      else cur += c;
      continue;
    }
    if (c === '"' || c === "'") { q = c; has = true; continue; }
    if (c === " " || c === "\t" || c === "\r") { push(); continue; }
    if (c === "\n" || c === ";") { push(); out.push({ op: ";" }); continue; }
    if (c === "&" || c === "|") {
      push();
      if (s[i + 1] === c) { out.push({ op: c + c }); i++; } else out.push({ op: c });
      continue;
    }
    cur += c; has = true;
  }
  push();
  return out;
}

function segmentos(tokens) {
  const segs = []; let cur = [];
  for (const k of tokens) {
    if (k.op) { if (cur.length) segs.push(cur); cur = []; } else cur.push(k.t);
  }
  if (cur.length) segs.push(cur);
  return segs;
}

const ENVASS = /^[A-Za-z_][A-Za-z0-9_]*=/;
const ENVOLTORIOS = new Set(["env", "command", "time", "nohup", "exec", "sudo"]);
function limpia(w) {
  let i = 0;
  while (i < w.length && ENVASS.test(w[i])) i++;
  while (i < w.length && ENVOLTORIOS.has(nombreCmd(w[i]))) {
    i++;
    while (i < w.length && (ENVASS.test(w[i]) || w[i].startsWith("-"))) i++;
  }
  return w.slice(i);
}

// ---------- git push ----------
function rama(ref) { return ref.replace(/^refs\/heads\//, ""); }

function destinosPush(args, cwd) {
  // devuelve { destinos: string[] } o { sinResolver: motivo }
  const pos = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--all" || a === "--mirror") return { sinResolver: `git push ${a} empuja varias ramas` };
    if (a === "--repo" || a === "-o" || a === "--push-option" || a === "--receive-pack" || a === "--exec") { i++; continue; }
    if (a.startsWith("-")) continue;
    pos.push(a);
  }
  const actual = git(cwd, "symbolic-ref", "--short", "-q", "HEAD");
  const refspecs = pos.slice(1);
  if (refspecs.length === 0) {
    const t = git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}");
    if (t) return { destinos: [t.includes("/") ? t.slice(t.indexOf("/") + 1) : t] };
    if (actual) return { destinos: [actual] };  // sin upstream: push.default simple/current empuja a la misma rama
    return { sinResolver: "no se puede determinar la rama de destino (HEAD suelto o fuera de un repo)" };
  }
  const destinos = [];
  for (let rs of refspecs) {
    rs = rs.replace(/^\+/, "");
    let dst;
    if (rs.includes(":")) {
      dst = rs.slice(rs.indexOf(":") + 1);
      if (dst === "") return { sinResolver: `refspec «${rs}» no resoluble` };
    } else dst = rs;
    dst = rama(dst);
    if (dst === "HEAD") {
      if (!actual) return { sinResolver: "HEAD suelto" };
      dst = actual;
    }
    if (dst.startsWith("refs/tags/") || dst.startsWith("tags/")) continue;
    destinos.push(dst);
  }
  return { destinos };
}

// ---------- gh ----------
function baseDelPR(sel, repo, cwd, pol) {
  if (process.env.HOOK_GH_FAKE_BASE !== undefined) {
    return process.env.HOOK_GH_FAKE_BASE === "__error__" ? null : process.env.HOOK_GH_FAKE_BASE;
  }
  const args = ["pr", "view", ...(sel ? [sel] : []), ...(repo ? ["-R", repo] : []), "--json", "baseRefName", "-q", ".baseRefName"];
  const r = spawnSync(process.env.HOOK_GH_BIN || "gh", args, { cwd, encoding: "utf8", timeout: pol.gh_timeout_ms || 8000, windowsHide: true });
  const b = r.status === 0 ? r.stdout.trim() : "";
  return b || null;
}

// ---------- análisis de un comando ----------
export function analizaComando(cmd, cwd0, pol, prof = 0) {
  const asks = []; const allows = [];
  let cwd = cwd0;
  for (const seg0 of segmentos(tokeniza(cmd))) {
    const w = limpia(seg0);
    if (!w.length) continue;
    const c = nombreCmd(w[0]);

    if (c === "cd" || c === "pushd") { if (w[1]) cwd = resuelve(w[1], cwd); continue; }

    if (["bash", "sh", "zsh", "pwsh", "powershell", "cmd"].includes(c) && prof < 2) {
      const i = w.findIndex((x) => /^(-c|-command|\/c)$/i.test(x));
      if (i > 0 && w[i + 1]) { const r = analizaComando(w.slice(i + 1).join(" "), cwd, pol, prof + 1); asks.push(...r.asks); allows.push(...r.allows); }
      continue;
    }

    if (c === "git") {
      let dir = cwd; let i = 1;
      for (; i < w.length; i++) {
        if (w[i] === "-C") { dir = resuelve(w[++i] || ".", dir); continue; }
        if (w[i] === "-c") { i++; continue; }
        if (w[i].startsWith("-")) continue;
        break;
      }
      if (w[i] === "push") {
        const r = destinosPush(w.slice(i + 1), dir);
        if (r.sinResolver) asks.push(`${r.sinResolver}; confirma el destino`);
        else for (const d of r.destinos) {
          if (esProtegida(d, dir, pol)) asks.push(`Push a «${d}» (rama de producción): requiere tu confirmación`);
        }
      }
      continue;
    }

    if (c === "gh") {
      const j = w.findIndex((x) => x === "pr");
      if (w.includes("api") && /pulls\/[^\s/]+\/merge|\/merges?\b/.test(w.join(" "))) {
        asks.push("Merge por API de GitHub (gh api): no se puede comprobar la rama de destino");
        continue;
      }
      if (j > -1 && w[j + 1] === "merge") {
        let sel = null; let repo = null; const rest = w.slice(j + 2);
        for (let k = 0; k < rest.length; k++) {
          const a = rest[k];
          if (a === "-R" || a === "--repo") { repo = rest[++k]; continue; }
          if (/^(-t|-b|-F|--subject|--body|--body-file|--match-head-commit|-A|--author-email)$/.test(a)) { k++; continue; }
          if (a.startsWith("-")) continue;
          if (!sel) sel = a;
        }
        const base = baseDelPR(sel, repo, cwd, pol);
        if (!base) asks.push("gh pr merge: no se pudo comprobar a qué rama apunta el PR; confirma");
        else if (esProtegida(base, cwd, pol)) asks.push(`Merge de PR hacia «${base}» (producción): requiere tu confirmación`);
        else allows.push(`PR hacia «${base}» (no protegida)`);
      }
      continue;
    }

    const norm = `${c} ${w.slice(1).join(" ")}`;
    if (pol.dokploy && ["curl", "wget", "invoke-restmethod", "irm", "invoke-webrequest", "iwr"].includes(c)
        && new RegExp(pol.dokploy.endpoint, "i").test(norm)
        && !(pol.dokploy.libres || []).some((id) => norm.includes(id))) {
      asks.push("Dokploy: cambio en una app de producción o ID no identificado (despliegue, variables, dominios…)");
      continue;
    }
    for (const r of pol.comandos_ask || []) {
      if (new RegExp(r.patron, "i").test(norm)) { asks.push(r.motivo); break; }
    }
  }
  // Último recurso: sustituciones de comando que el tokenizador no ve.
  if (!asks.length && /\$\(|`|\beval\b/.test(cmd) && /\bgit\b[^|;&]*\bpush\b|\bgh\b[^|;&]*\bmerge\b/.test(cmd)) {
    asks.push("Comando compuesto no analizable que contiene un push o merge: confirma");
  }
  return { asks, allows };
}

// ---------- SQL de solo lectura ----------
export function esSoloLectura(sql) {
  if (typeof sql !== "string" || !sql.trim()) return false;
  if (/\$[A-Za-z_]*\$/.test(sql)) return false;               // $$...$$ puede esconder DO o funciones
  let limpio = ""; let i = 0; const n = sql.length;
  while (i < n) {
    const c = sql[i];
    if (c === "-" && sql[i + 1] === "-") { while (i < n && sql[i] !== "\n") i++; limpio += " "; continue; }
    if (c === "/" && sql[i + 1] === "*") {
      let d = 1; i += 2;
      while (i < n && d > 0) {
        if (sql[i] === "/" && sql[i + 1] === "*") { d++; i += 2; }
        else if (sql[i] === "*" && sql[i + 1] === "/") { d--; i += 2; }
        else i++;
      }
      if (d > 0) return false;                                  // comentario sin cerrar
      limpio += " "; continue;
    }
    if (c === "'" || c === '"') {
      const esc = c === "'" && /[eE]/.test(sql[i - 1] || "");   // E'..\'..'
      i++; let cerrada = false;
      while (i < n) {
        if (esc && sql[i] === "\\") { i += 2; continue; }
        if (sql[i] === c) { if (sql[i + 1] === c) { i += 2; continue; } i++; cerrada = true; break; }
        i++;
      }
      if (!cerrada) return false;
      limpio += c === "'" ? "''" : '""'; continue;
    }
    limpio += c; i++;
  }
  const sentencias = limpio.split(";").map((s) => s.trim()).filter(Boolean);
  if (!sentencias.length) return false;
  for (const s of sentencias) {
    if (!/^\(?\s*(select|with|values|show|table|explain)\b/i.test(s)) return false;
    if (/^explain\b/i.test(s) && /\banalyze\b/i.test(s)) return false;
  }
  const PROHIBIDO = /\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge|call|do|vacuum|reindex|refresh|comment|set|reset|lock|listen|notify|unlisten|execute|prepare|deallocate|analyze|cluster|discard|security|import|load|begin|commit|rollback|savepoint|release|declare|fetch|move|close|into)\b|\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b|\b(pg_terminate_backend|pg_cancel_backend|set_config|nextval|setval|pg_sleep|dblink\w*|pg_read_file|pg_read_binary_file|lo_import|lo_export|pg_advisory_lock\w*)\b/i;
  return !PROHIBIDO.test(limpio);
}

// ---------- herramientas MCP ----------
export function analizaMcp(nombre, entrada, pol) {
  const asks = [];
  for (const r of pol.herramientas_ask || []) {
    if (new RegExp(r.patron).test(nombre)) { asks.push(r.motivo); break; }
  }
  if (!asks.length && pol.sql && new RegExp(pol.sql.herramienta).test(nombre)) {
    if (!esSoloLectura(entrada && entrada.query)) asks.push("SQL que no es claramente de solo lectura: confirma antes de ejecutarlo");
  }
  return { asks, allows: [] };
}

function salida(decision, motivo) {
  process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: decision, permissionDecisionReason: motivo } }));
}

export function decide(input, pol) {
  const nombre = input.tool_name || "";
  const e = input.tool_input || {};
  let r;
  if (/^(Bash|PowerShell)$/.test(nombre)) r = analizaComando(e.command || "", input.cwd || process.cwd(), pol);
  else if (nombre.startsWith("mcp__")) r = analizaMcp(nombre, e, pol);
  else return null;
  if (r.asks.length) return { d: "ask", m: [...new Set(r.asks)].join(" · ") };
  if (r.allows.length) return { d: "allow", m: r.allows.join(" · ") };
  return null;
}

function main() {
  let input;
  try { input = JSON.parse(readFileSync(0, "utf8")); } catch { process.exit(0); }
  const pol = cargaPolitica();
  try {
    const r = decide(input, pol);
    if (r) salida(r.d, r.m);
  } catch (err) {
    const txt = JSON.stringify(input && input.tool_input || "");
    if (/git\b.*\bpush|gh\b.*\bmerge|railway|vercel/.test(txt)) salida("ask", `El hook de destino falló (${String(err).slice(0, 80)}); confirma a mano`);
  }
  process.exit(0);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
