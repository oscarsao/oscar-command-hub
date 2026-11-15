// Pruebas de las fusiones preautorizadas por lote (destino.mjs + preautoriza.mjs), ejecutando los archivos reales.
//   node --test tools/agent-lanes/hooks-usuario/lote.test.mjs
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const HOOK = join(AQUI, "destino.mjs");
const GEN = join(AQUI, "preautoriza.mjs");
let raiz, repo;

const sh = (cwd, ...a) => {
  const r = spawnSync("git", a, { cwd, encoding: "utf8" });
  assert.equal(r.status, 0, `git ${a.join(" ")} -> ${r.stderr}`);
  return r.stdout.trim();
};
before(() => {
  raiz = mkdtempSync(join(tmpdir(), "hook-lote-"));
  repo = join(raiz, "repo");
  mkdirSync(repo);
  sh(repo, "init", "-q", "-b", "master");
  sh(repo, "config", "user.name", "t");
  sh(repo, "config", "user.email", "t@example.invalid");
  sh(repo, "config", "commit.gpgsign", "false");
  writeFileSync(join(repo, "a.txt"), "x");
  sh(repo, "add", "a.txt");
  sh(repo, "commit", "-q", "-m", "init");
});
after(() => rmSync(raiz, { recursive: true, force: true }));

function corre(toolName, toolInput, env = {}) {
  const r = spawnSync("node", [HOOK], {
    input: JSON.stringify({ tool_name: toolName, tool_input: toolInput, cwd: repo, session_id: "t", hook_event_name: "PreToolUse" }),
    encoding: "utf8", env: { ...process.env, ...env },
  });
  assert.equal(r.status, 0, `el hook no debe fallar nunca: ${r.stderr}`);
  const o = r.stdout.trim() ? JSON.parse(r.stdout).hookSpecificOutput : null;
  return { d: o ? o.permissionDecision : "nada", razon: o && o.permissionDecisionReason };
}
const bash = (command, env) => corre("Bash", { command }, env);

const PR = (o = {}) => JSON.stringify({ base: "master", repo: "oscarsao/oscar-hq", numero: 482, sha: "abc123", ...o });
const futuro = () => new Date(Date.now() + 3600e3).toISOString();
const pasado = () => new Date(Date.now() - 3600e3).toISOString();
function preauth(prs, raw) {
  const f = join(raiz, `pre-${Math.random().toString(36).slice(2)}.json`);
  writeFileSync(f, raw !== undefined ? raw : JSON.stringify({ version: 1, prs }));
  return f;
}
const merge = (env) => bash("gh pr merge 482 --squash", env);
const ent = (o = {}) => ({ repo: "oscarsao/oscar-hq", numero: 482, sha: "abc123", expira: futuro(), nota: "lote 14-11", ...o });

test("PR a master con entrada vigente (mismo repo, número y SHA) -> allow", () => {
  const r = merge({ HOOK_GH_FAKE_PR: PR(), HOOK_PREAUTH_FILE: preauth([ent()]) });
  assert.equal(r.d, "allow");
  assert.match(r.razon, /preautorizado/);
});
test("sin fichero, o PR que no está en él -> ask", () => {
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR(), HOOK_PREAUTH_FILE: join(raiz, "no-existe.json") }).d, "ask");
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR({ numero: 483 }), HOOK_PREAUTH_FILE: preauth([ent()]) }).d, "ask");
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR({ repo: "oscarsao/otro" }), HOOK_PREAUTH_FILE: preauth([ent()]) }).d, "ask");
});
test("SHA distinto (hubo un push después del OK) -> ask", () => {
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR({ sha: "zzz999" }), HOOK_PREAUTH_FILE: preauth([ent()]) }).d, "ask");
});
test("entrada caducada, sin fecha o con fecha inválida -> ask", () => {
  for (const e of [ent({ expira: pasado() }), ent({ expira: undefined }), ent({ expira: "mañana" })]) {
    assert.equal(merge({ HOOK_GH_FAKE_PR: PR(), HOOK_PREAUTH_FILE: preauth([e]) }).d, "ask");
  }
});
test("fichero corrupto o con otra forma -> ask (falla cerrado)", () => {
  for (const raw of ["{no json", "[]", "null", JSON.stringify({ prs: "x" })]) {
    assert.equal(merge({ HOOK_GH_FAKE_PR: PR(), HOOK_PREAUTH_FILE: preauth(null, raw) }).d, "ask", raw);
  }
});
test("la preautorización no cubre git push a master ni un comando que lo mezcle", () => {
  assert.equal(bash("git push", { HOOK_PREAUTH_FILE: preauth([ent()]) }).d, "ask");
  assert.equal(bash("gh pr merge 482 && git push", { HOOK_GH_FAKE_PR: PR(), HOOK_PREAUTH_FILE: preauth([ent()]) }).d, "ask");
});
test("PR no resoluble con fichero vigente -> ask; PR a develop sigue en allow", () => {
  assert.equal(merge({ HOOK_GH_FAKE_PR: "__error__", HOOK_PREAUTH_FILE: preauth([ent()]) }).d, "ask");
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR({ base: "develop" }), HOOK_PREAUTH_FILE: preauth([]) }).d, "allow");
});
test("escribir o editar el fichero de preautorizados -> ask; otros ficheros no", () => {
  for (const t of ["Write", "Edit", "MultiEdit"]) {
    assert.equal(corre(t, { file_path: "C:\\Users\\oscar\\.claude\\hooks\\pr-preautorizados.json" }).d, "ask", t);
    assert.equal(corre(t, { file_path: "/c/Users/oscar/.claude/hooks/pr-preautorizados.json" }).d, "ask", t);
  }
  assert.equal(corre("Write", { file_path: "C:\\Users\\oscar\\.claude\\hooks\\otra-cosa.json" }).d, "nada");
});
test("Bash que escribe el fichero -> ask; leerlo no", () => {
  const escribe = [
    "echo '{}' > ~/.claude/hooks/pr-preautorizados.json",
    "cp x.json ~/.claude/hooks/pr-preautorizados.json",
    "python -c \"open('pr-preautorizados.json','w')\"",
    "rm pr-preautorizados.json",
    "sed -i s/a/b/ pr-preautorizados.json",
  ];
  for (const c of escribe) assert.equal(bash(c).d, "ask", c);
  for (const c of ["cat ~/.claude/hooks/pr-preautorizados.json", "grep numero pr-preautorizados.json"]) assert.equal(bash(c).d, "nada", c);
});

// ---------- generador ----------
const gen = (args, env) => spawnSync("node", [GEN, ...args], { encoding: "utf8", env: { ...process.env, ...env } });

test("preautoriza.mjs genera el JSON con SHA y caducidad, conserva vigentes y descarta caducadas", () => {
  const previo = preauth([ent({ numero: 1, sha: "s1" }), ent({ numero: 2, sha: "s2", expira: pasado() })]);
  const r = gen(["--repo", "oscarsao/oscar-hq", "--horas", "2", "--nota", "lote prueba", "482", "483"],
    { HOOK_PREAUTH_FILE: previo, HOOK_GH_FAKE_PR: JSON.stringify({ base: "master", sha: "sha-fake" }) });
  assert.equal(r.status, 0, r.stderr);
  const j = JSON.parse(r.stdout);
  assert.deepEqual(j.prs.map((p) => p.numero).sort((a, b) => a - b), [1, 482, 483]);
  const nuevo = j.prs.find((p) => p.numero === 482);
  assert.equal(nuevo.sha, "sha-fake");
  assert.equal(nuevo.nota, "lote prueba");
  const horas = (Date.parse(nuevo.expira) - Date.now()) / 3600e3;
  assert.ok(horas > 1.9 && horas <= 2.01, `caducidad ${horas}`);
});
test("el fichero generado, una vez escrito, hace pasar el merge (circuito completo)", () => {
  const f = join(raiz, "circuito.json");
  const r = gen(["--repo", "oscarsao/oscar-hq", "482"], { HOOK_PREAUTH_FILE: f, HOOK_GH_FAKE_PR: JSON.stringify({ base: "master", sha: "abc123" }) });
  assert.equal(r.status, 0, r.stderr);
  writeFileSync(f, r.stdout);
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR(), HOOK_PREAUTH_FILE: f }).d, "allow");
  assert.equal(merge({ HOOK_GH_FAKE_PR: PR({ sha: "otro" }), HOOK_PREAUTH_FILE: f }).d, "ask");
});
test("preautoriza.mjs rechaza rama no protegida, PR ilegible, caducidad absurda o sin PR", () => {
  assert.notEqual(gen(["--repo", "o/r", "5"], { HOOK_GH_FAKE_PR: PR({ base: "develop" }) }).status, 0);
  assert.notEqual(gen(["--repo", "o/r", "5"], { HOOK_GH_FAKE_PR: "__error__" }).status, 0);
  assert.notEqual(gen(["--repo", "o/r", "--horas", "999", "5"], { HOOK_GH_FAKE_PR: PR() }).status, 0);
  assert.notEqual(gen(["--repo", "o/r"], { HOOK_GH_FAKE_PR: PR() }).status, 0);
});
