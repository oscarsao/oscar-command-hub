// Pruebas del hook destino.mjs ejecutando el ARCHIVO REAL con JSON por stdin (como lo hace Claude Code).
//   node --test tools/agent-lanes/hooks-usuario/destino.test.mjs
// Crea un remoto bare y clones desechables en el directorio temporal para que `@{push}` se resuelva de verdad.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HOOK = join(dirname(fileURLToPath(import.meta.url)), "destino.mjs");
let raiz, remoto, repo, repoEspacios, otro;

const sh = (cwd, ...a) => {
  const r = spawnSync("git", a, { cwd, encoding: "utf8" });
  assert.equal(r.status, 0, `git ${a.join(" ")} -> ${r.stderr}`);
  return r.stdout.trim();
};

function clon(destino) {
  spawnSync("git", ["clone", "-q", remoto, destino], { encoding: "utf8" });
  sh(destino, "config", "user.name", "t");
  sh(destino, "config", "user.email", "t@example.invalid");
  sh(destino, "config", "commit.gpgsign", "false");
}

before(() => {
  raiz = mkdtempSync(join(tmpdir(), "hook-destino-"));
  remoto = join(raiz, "remoto.git");
  sh(raiz, "init", "-q", "--bare", "-b", "master", remoto);
  repo = join(raiz, "repo");
  mkdirSync(repo);
  sh(repo, "init", "-q", "-b", "master");
  sh(repo, "config", "user.name", "t");
  sh(repo, "config", "user.email", "t@example.invalid");
  sh(repo, "config", "commit.gpgsign", "false");
  writeFileSync(join(repo, "a.txt"), "x");
  sh(repo, "add", "a.txt");
  sh(repo, "commit", "-q", "-m", "init");
  sh(repo, "remote", "add", "origin", remoto);
  sh(repo, "push", "-q", "-u", "origin", "master");
  sh(repo, "branch", "develop");
  sh(repo, "push", "-q", "origin", "develop");
  repoEspacios = join(raiz, "mi repo con espacios");
  clon(repoEspacios);
  otro = join(raiz, "otro-sitio");
  mkdirSync(otro);
});
after(() => rmSync(raiz, { recursive: true, force: true }));

function corre(toolName, toolInput, cwd, env = {}) {
  const t0 = Date.now();
  const r = spawnSync("node", [HOOK], {
    input: JSON.stringify({ tool_name: toolName, tool_input: toolInput, cwd, session_id: "t", hook_event_name: "PreToolUse" }),
    encoding: "utf8", env: { ...process.env, ...env },
  });
  const ms = Date.now() - t0;
  assert.equal(r.status, 0, `el hook no debe fallar nunca: ${r.stderr}`);
  const o = r.stdout.trim() ? JSON.parse(r.stdout).hookSpecificOutput : null;
  return { d: o ? o.permissionDecision : "nada", razon: o && o.permissionDecisionReason, ms };
}
const bash = (command, cwd, env) => corre("Bash", { command }, cwd, env);

function enRama(dir, nombre, crear = true) {
  if (crear) sh(dir, "checkout", "-q", "-B", nombre);
  else sh(dir, "checkout", "-q", nombre);
}

// ---------- git push ----------
test("REGRESIÓN 1: git push a secas en master -> ask (antes solo avisaba)", () => {
  enRama(repo, "master", false);
  assert.equal(bash("git push", repo).d, "ask");
});
test("git -C <repo> push (como se despliega MigraTeam) desde otro directorio -> ask", () => {
  enRama(repo, "master", false);
  assert.equal(bash(`git -C ${repo} push`, otro).d, "ask");
});
test("push a secas en lane/x con upstream -> pasa sin avisar", () => {
  enRama(repo, "lane/x");
  sh(repo, "push", "-q", "-u", "origin", "lane/x");
  assert.equal(bash("git push", repo).d, "nada");
});
test("push a secas en rama sin upstream: usa el nombre de la rama (lane/y pasa, master pregunta)", () => {
  enRama(repo, "lane/y");
  assert.equal(bash("git push", repo).d, "nada");
  enRama(repo, "master", false);
});
test("git push origin HEAD: master pregunta, lane/x pasa", () => {
  enRama(repo, "master", false);
  assert.equal(bash("git push origin HEAD", repo).d, "ask");
  enRama(repo, "lane/x", false);
  assert.equal(bash("git push origin HEAD", repo).d, "nada");
});
test("HEAD:master desde lane/x -> ask; feature:main -> ask; +force a master -> ask", () => {
  enRama(repo, "lane/x", false);
  assert.equal(bash("git push origin HEAD:master", repo).d, "ask");
  assert.equal(bash("git push origin feature:main", repo).d, "ask");
  assert.equal(bash("git push --force-with-lease origin +lane/x:refs/heads/master", repo).d, "ask");
});
test("push -u origin lane/t_1 y push origin develop -> pasan", () => {
  enRama(repo, "master", false);
  assert.equal(bash("git push -u origin lane/t_1", repo).d, "nada");
  assert.equal(bash("git push origin develop", repo).d, "nada");
});
test("borrar la rama master remota (:master) -> ask", () => {
  assert.equal(bash("git push origin :master", repo).d, "ask");
  assert.equal(bash("git push origin --delete master", repo).d, "ask");
});
test("-C con comillas y espacios en la ruta", () => {
  enRama(repoEspacios, "master", false);
  assert.equal(bash(`git -C "${repoEspacios}" push`, otro).d, "ask");
});
test("cd X && git push -> ask; compuesto git add && commit && push -> ask", () => {
  enRama(repo, "master", false);
  assert.equal(bash(`cd ${repo} && git push`, otro).d, "ask");
  assert.equal(bash("git add . && git commit -q -m x && git push", repo).d, "ask");
});
test("--all y --mirror -> ask (no resolubles)", () => {
  assert.equal(bash("git push --all origin", repo).d, "ask");
  assert.equal(bash("git push --mirror", repo).d, "ask");
});
test("envoltorios: bash -c y powershell -Command", () => {
  enRama(repo, "master", false);
  assert.equal(bash(`bash -c "git push"`, repo).d, "ask");
  assert.equal(bash(`powershell -NoProfile -Command "git push origin master"`, repo).d, "ask");
});
test("la herramienta PowerShell se analiza igual", () => {
  enRama(repo, "master", false);
  assert.equal(corre("PowerShell", { command: "git push" }, repo).d, "ask");
});
test("FALSO POSITIVO: el texto «git push origin master» dentro de un echo no pregunta", () => {
  assert.equal(bash(`echo "git push origin master"`, repo).d, "nada");
  assert.equal(bash(`git log --oneline -5 && grep -r "git push" docs`, repo).d, "nada");
});
test("sustitución de comando con push -> ask (no analizable)", () => {
  assert.equal(bash("$(echo git) push origin lane/x", repo).d, "ask");
});

// ---------- gh ----------
test("REGRESIÓN 2: gh pr merge hacia master -> ask; hacia develop -> allow; no resoluble -> ask", () => {
  assert.equal(bash("gh pr merge 482 --squash", repo, { HOOK_GH_FAKE_BASE: "master" }).d, "ask");
  assert.equal(bash("gh pr merge 482 --squash", repo, { HOOK_GH_FAKE_BASE: "develop" }).d, "allow");
  assert.equal(bash("gh pr merge 482", repo, { HOOK_GH_FAKE_BASE: "__error__" }).d, "ask");
});
test("gh con ruta completa y con comillas", () => {
  const cmd = `"/c/Program Files/GitHub CLI/gh.exe" pr merge 12 --admin`;
  assert.equal(bash(cmd, repo, { HOOK_GH_FAKE_BASE: "main" }).d, "ask");
});
test("gh pr merge sin gh instalado ni red: falla cerrado (ask)", () => {
  assert.equal(bash("gh pr merge 99", repo, { HOOK_GH_BIN: "gh-no-existe-xyz" }).d, "ask");
});
test("gh api .../pulls/N/merge -> ask", () => {
  assert.equal(bash("gh api -X PUT repos/o/r/pulls/3/merge", repo).d, "ask");
});
test("gh pr merge y push a master en el mismo comando: gana ask sobre allow", () => {
  enRama(repo, "master", false);
  assert.equal(bash("gh pr merge 5 && git push", repo, { HOOK_GH_FAKE_BASE: "develop" }).d, "ask");
});
test("gh pr view / list / create no opinan", () => {
  for (const c of ["gh pr view 5", "gh pr list", "gh pr create --fill"]) assert.equal(bash(c, repo).d, "nada", c);
});

// ---------- despliegues ----------
const PROD = "2ZFE7_x-2MURejAG3Ey7y", STAGING = "Mfim9xPHZe8OKSKAUFxqb";
const dk = (ep, id) => `curl -s -X POST -H "x-api-key: K" -H "Content-Type: application/json" http://localhost:3300/api/${ep} -d '{"applicationId":"${id}","title":"x"}'`;
test("Dokploy (sustituye a Railway): desplegar o cambiar variables de PRODUCCIÓN -> ask", () => {
  assert.equal(bash(dk("application.deploy", PROD), repo).d, "ask");
  assert.equal(bash(dk("application.redeploy", PROD), repo).d, "ask");
  assert.equal(bash(dk("application.saveEnvironment", PROD), repo).d, "ask");
  assert.equal(bash(dk("compose.deploy", "Fho00pG-1pcD4LvkpBl-f"), repo).d, "ask");
});
test("Dokploy: staging pasa; lecturas pasan; ID no identificado o cuerpo en fichero -> ask", () => {
  assert.equal(bash(dk("application.deploy", STAGING), repo).d, "nada");
  assert.equal(bash(`curl -s http://localhost:3300/api/project.all -H "x-api-key: K"`, repo).d, "nada");
  assert.equal(bash(`curl -s "http://localhost:3300/api/application.one?applicationId=${PROD}" -H "x-api-key: K"`, repo).d, "nada");
  assert.equal(bash(`curl -s -X POST http://localhost:3300/api/application.deploy -d @cuerpo.json`, repo).d, "ask");
  assert.equal(bash(`curl -s -X POST https://panel.pildoradigital.com/api/application.deploy -d '{"applicationId":"APP"}'`, repo).d, "ask");
});
test("Railway ya no existe: railway up no opina; vercel env ls tampoco", () => {
  assert.equal(bash("railway up", repo).d, "nada");
  assert.equal(bash("vercel env ls", repo).d, "nada");
});
test("vercel --prod, vercel promote y alembic downgrade -> ask", () => {
  assert.equal(bash("vercel --prod --yes", repo).d, "ask");
  assert.equal(bash("vercel promote https://x.vercel.app --yes", repo).d, "ask");
  assert.equal(bash("alembic downgrade -1", repo).d, "ask");
  assert.equal(bash("alembic upgrade head", repo).d, "nada");
});
test("comandos normales no opinan", () => {
  for (const c of ["ls -la", "python -m pytest -q", "git status", "git diff --stat", "ruff check ."]) assert.equal(bash(c, repo).d, "nada", c);
});

// ---------- MCP ----------
const sql = (q) => corre("mcp__claude_ai_Supabase__execute_sql", { project_id: "p", query: q }, repo).d;
test("SQL de solo lectura pasa", () => {
  for (const q of ["SELECT 1", "select count(*) from ohq_people", "WITH a AS (select 1) select * from a",
    "EXPLAIN SELECT * FROM t", "select 'update' as w", "select '--' as x; select 2", "select * from t -- delete me\n where a = 1",
    "select \"update\" from t", "SELECT created_at, updated_at FROM t"]) assert.equal(sql(q), "nada", q);
});
test("SQL que escribe o no es claramente lectura -> ask", () => {
  for (const q of ["UPDATE t SET a=1", "insert into t values (1)", "select 1; drop table x", "select '--'; drop table x",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "DO $$ BEGIN DELETE FROM t; END $$", "select * from t for update",
    "select * into nueva from t", "select set_config('a','b',false)", "EXPLAIN ANALYZE select 1", "select 'sin cerrar", "/* sin cerrar select 1",
    "select pg_terminate_backend(1)", "TRUNCATE t", "", "grant all on t to x"]) assert.equal(sql(q), "ask", q);
});
test("Supabase apply_migration y deploy_edge_function -> ask; list_tables pasa", () => {
  assert.equal(corre("mcp__claude_ai_Supabase__apply_migration", { name: "x", query: "select 1" }, repo).d, "ask");
  assert.equal(corre("mcp__claude_ai_Supabase__deploy_edge_function", {}, repo).d, "ask");
  assert.equal(corre("mcp__claude_ai_Supabase__list_tables", {}, repo).d, "nada");
});
test("Gmail: envío pregunta, borrador y búsqueda pasan", () => {
  for (const t of ["send_message", "reply", "forward", "trash_thread"]) assert.equal(corre(`mcp__claude_ai_Gmail__${t}`, {}, repo).d, "ask", t);
  for (const t of ["create_draft", "search_threads", "get_thread"]) assert.equal(corre(`mcp__claude_ai_Gmail__${t}`, {}, repo).d, "nada", t);
});
test("Resend, Apollo y Vercel: envío, gasto y despliegue preguntan; lectura pasa", () => {
  assert.equal(corre("mcp__plugin_resend_resend__send-email", {}, repo).d, "ask");
  assert.equal(corre("mcp__plugin_resend_resend__list-emails", {}, repo).d, "nada");
  assert.equal(corre("mcp__claude_ai_Apollo_io__apollo_emailer_messages_send_now", {}, repo).d, "ask");
  assert.equal(corre("mcp__claude_ai_Apollo_io__apollo_mixed_companies_search", {}, repo).d, "nada");
  assert.equal(corre("mcp__claude_ai_Vercel__request_promote", {}, repo).d, "ask");
  assert.equal(corre("mcp__claude_ai_Vercel__list_deployments", {}, repo).d, "nada");
});
test("herramientas no relacionadas no opinan", () => {
  for (const t of ["Read", "Edit", "Grep", "mcp__claude-in-chrome__navigate"]) assert.equal(corre(t, { file_path: "x" }, repo).d, "nada", t);
});

// ---------- robustez ----------
test("entrada vacía, JSON roto y herramientas sin command no rompen el hook", () => {
  for (const input of ["", "{no json", "{}", JSON.stringify({ tool_name: "Bash" }), JSON.stringify({ tool_name: "Bash", tool_input: {} })]) {
    const r = spawnSync("node", [HOOK], { input, encoding: "utf8" });
    assert.equal(r.status, 0);
    assert.equal(r.stdout.trim(), "");
  }
});
test("sin política legible: sigue protegiendo master/main", () => {
  enRama(repo, "master", false);
  assert.equal(bash("git push", repo, { HOOK_POLICY: join(raiz, "no-existe.json") }).d, "ask");
});
test("rendimiento: un comando típico tarda menos de 1,5 s", () => {
  enRama(repo, "master", false);
  assert.ok(bash("git push", repo).ms < 1500);
  assert.ok(bash("ls -la", repo).ms < 1500);
});
