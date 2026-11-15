#!/usr/bin/env node
// Genera (NO escribe) el contenido de pr-preautorizados.json para preautorizar fusiones a master/main por lote.
//   node preautoriza.mjs --repo oscarsao/oscar-hq [--horas 24] [--nota "lote 14-11"] 482 483 ...
// Imprime el JSON en stdout, conservando las entradas vigentes del fichero actual. Quien lo escribe es la herramienta
// Write/Edit, y ESE aviso de permisos (que el hook destino.mjs fuerza) es el «sí» de Oscar: ve repo, números, SHA y caducidad.
// Cada entrada fija el SHA de cabeza del PR: un push posterior al OK invalida la preautorización.
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FICHERO = process.env.HOOK_PREAUTH_FILE || join(AQUI, "pr-preautorizados.json");
const MAX_HORAS = 72;
const fallo = (m) => { process.stderr.write(`preautoriza: ${m}\n`); process.exit(2); };

const a = process.argv.slice(2);
let repo = null, horas = 24, nota = ""; const numeros = [];
for (let i = 0; i < a.length; i++) {
  if (a[i] === "--repo") repo = a[++i];
  else if (a[i] === "--horas") horas = Number(a[++i]);
  else if (a[i] === "--nota") nota = a[++i] || "";
  else if (/^\d+$/.test(a[i])) numeros.push(Number(a[i]));
  else fallo(`argumento no reconocido: ${a[i]}`);
}
if (!repo || !/^[\w.-]+\/[\w.-]+$/.test(repo)) fallo("falta --repo owner/nombre");
if (!numeros.length) fallo("indica al menos un número de PR");
if (!(horas > 0 && horas <= MAX_HORAS)) fallo(`--horas debe estar entre 0 y ${MAX_HORAS}`);

let pol = {};
try { pol = JSON.parse(readFileSync(process.env.HOOK_POLICY || join(AQUI, "politica-permisos.json"), "utf8")); } catch { /* usa el mínimo */ }
const protegidas = [...(pol.protegidas || ["master", "main"])];

function info(n) {
  if (process.env.HOOK_GH_FAKE_PR !== undefined) {
    if (process.env.HOOK_GH_FAKE_PR === "__error__") return null;
    try { return { ...JSON.parse(process.env.HOOK_GH_FAKE_PR), numero: n }; } catch { return null; }
  }
  const r = spawnSync(process.env.HOOK_GH_BIN || "gh", ["pr", "view", String(n), "-R", repo, "--json", "baseRefName,headRefOid,state"],
    { encoding: "utf8", timeout: 15000, windowsHide: true });
  if (r.status !== 0) return null;
  try { const j = JSON.parse(r.stdout); return j.state === "OPEN" ? { base: j.baseRefName, sha: j.headRefOid, numero: n } : null; } catch { return null; }
}

let actual = [];
try { const f = JSON.parse(readFileSync(FICHERO, "utf8")); if (Array.isArray(f.prs)) actual = f.prs; } catch { /* sin fichero previo */ }
const ahora = Date.now();
const nuevos = new Set(numeros.map((n) => `${repo.toLowerCase()}#${n}`));
const prs = actual.filter((p) => p && Date.parse(p.expira) > ahora && !nuevos.has(`${String(p.repo).toLowerCase()}#${p.numero}`));
const expira = new Date(ahora + horas * 3600e3).toISOString();
for (const n of numeros) {
  const i = info(n);
  if (!i || !i.sha) fallo(`PR #${n}: no se pudo leer (¿abierto y en ${repo}?)`);
  if (!protegidas.includes(i.base)) fallo(`PR #${n} apunta a «${i.base}», que no es rama protegida: no necesita preautorización`);
  prs.push({ repo, numero: n, sha: i.sha, expira, ...(nota ? { nota } : {}) });
}
process.stdout.write(JSON.stringify({ version: 1, prs }, null, 2) + "\n");
