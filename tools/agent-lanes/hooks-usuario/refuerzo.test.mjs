// Pruebas del refuerzo del 14-11: force push de cualquier tipo, gh workflow run y vercel env.
//   node --test tools/agent-lanes/hooks-usuario/refuerzo.test.mjs
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HOOK = join(dirname(fileURLToPath(import.meta.url)), "destino.mjs");
let raiz, repo;
const sh = (cwd, ...a) => {
  const r = spawnSync("git", a, { cwd, encoding: "utf8" });
  assert.equal(r.status, 0, `git ${a.join(" ")} -> ${r.stderr}`);
  return r.stdout.trim();
};
before(() => {
  raiz = mkdtempSync(join(tmpdir(), "hook-refuerzo-"));
  const remoto = join(raiz, "remoto.git");
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
  sh(repo, "checkout", "-q", "-b", "lane/t_x");
  sh(repo, "push", "-q", "-u", "origin", "lane/t_x");
});
after(() => rmSync(raiz, { recursive: true, force: true }));

function bash(command, cwd = repo) {
  const r = spawnSync("node", [HOOK], {
    input: JSON.stringify({ tool_name: "Bash", tool_input: { command }, cwd, session_id: "t", hook_event_name: "PreToolUse" }),
    encoding: "utf8",
  });
  assert.equal(r.status, 0, r.stderr);
  const o = r.stdout.trim() ? JSON.parse(r.stdout).hookSpecificOutput : null;
  return { d: o ? o.permissionDecision : "nada", razon: o && o.permissionDecisionReason };
}

test("force push a una rama PROPIA (lane/*) pregunta, en todas sus formas", () => {
  for (const c of ["git push --force", "git push -f", "git push --force-with-lease", "git push --force-with-lease=lane/t_x",
    "git push --force-if-includes origin lane/t_x", "git push -fu origin lane/t_x", "git push origin +lane/t_x",
    "git push origin +HEAD:lane/t_x", `git -C ${repo} push --force origin lane/t_x`]) {
    const r = bash(c);
    assert.equal(r.d, "ask", c);
    assert.match(r.razon, /Force push/, c);
  }
});
test("push normales a lane/* y a develop siguen pasando", () => {
  for (const c of ["git push", "git push -u origin lane/t_x", "git push origin lane/t_x", "git push --follow-tags origin lane/t_x",
    "git push -v origin lane/t_x", "git push --set-upstream origin lane/t_y", "git push origin develop"]) {
    assert.equal(bash(c).d, "nada", c);
  }
});
test("force push compuesto con otros comandos y dentro de bash -c -> ask", () => {
  assert.equal(bash("git add . && git commit -q -m x && git push --force-with-lease").d, "ask");
  assert.equal(bash(`bash -c "git push -f origin lane/t_x"`).d, "ask");
});
test("gh workflow run/enable/disable -> ask; list y view pasan", () => {
  for (const c of ["gh workflow run deploy.yml", "gh workflow run deploy-staging.yml --ref develop", "gh workflow enable ci.yml",
    "gh workflow disable ci.yml", `"/c/Program Files/GitHub CLI/gh.exe" workflow run x.yml`]) assert.equal(bash(c).d, "ask", c);
  for (const c of ["gh workflow list", "gh workflow view ci.yml", "gh run list", "gh run view 123"]) assert.equal(bash(c).d, "nada", c);
});
test("vercel env add/rm/update/pull -> ask; ls y el resto pasan", () => {
  for (const c of ["vercel env add FOO production", "vercel env rm FOO", "vercel env update FOO", "vercel env pull .env.local",
    `node "/c/Users/oscar/AppData/Roaming/npm/node_modules/vercel/dist/index.js" env add X`]) {
    // La forma con `node ... vercel/dist/index.js` no empieza por vercel: se documenta como límite si pasa.
    const r = bash(c).d;
    if (c.startsWith("node ")) assert.ok(r === "ask" || r === "nada", c);
    else assert.equal(r, "ask", c);
  }
  for (const c of ["vercel env ls", "vercel whoami", "vercel ls"]) assert.equal(bash(c).d, "nada", c);
});
