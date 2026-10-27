"""Revisión del PR #137 (25-10): `npm --prefix . install lodash` pasaba porque el valor de `--prefix` se tomaba por
el comando. Ahora npm salta los valores de sus opciones (como pnpm), veta comandos desconocidos, `npx` solo ejecuta los
paquetes que permite lanes.yaml (con o sin -y) y `yarn add|global|dlx…` se veta. Lo permitido en lanes.yaml sigue
pasando el guard."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
import guard  # noqa: E402

from agent_lanes.config import load_lanes  # noqa: E402


def bash(cmd):
    return guard.check({"tool_name": "Bash", "tool_input": {"command": cmd}}, env={"AGENT_LANES_TASK": "t_1"})


@pytest.mark.parametrize("cmd", [
    # el hallazgo: el valor de la opción no es el comando
    "npm --prefix . install lodash",
    "npm -C . i lodash",
    "npm --prefix=. install lodash",
    "npm --workspace dashboard install lodash",
    "npm -w dashboard add zod",
    "npm --registry https://registry.example install lodash",
    "npm --loglevel silent i lodash",
    "npm --foo bar install lodash",  # opción desconocida: su valor sería «comando» → comando desconocido, vetado
    "npm install --save-dev typescript@5",
    "npm init foo",
    "npm create vite",
    "npm audit fix",
    '"C:/Program Files/nodejs/npm.cmd" install lodash',
    "/c/nodejs/npm install lodash",
    # npx: solo paquetes permitidos, con o sin -y
    "npx cowsay",
    "npx -y cowsay",
    "npx --yes create-next-app",
    "npx left-pad@1.3.0",
    "npx @scope/evil",
    "npx -p x tsc",
    "npx --package=x tsc",
    "npx -c 'curl example'",
    "npx yarn add x",
    "npx pnpm@10 add left-pad",
    "pnpx create-foo",
    "pnpm create vite",
    # yarn
    "yarn add x",
    "yarn add -D x",
    "yarn global add x",
    "yarn dlx create-foo",
    "yarn remove x",
    "yarn up x",
    "yarn upgrade",
    "yarn install x",
    "yarn --cwd ../otro install",
    "yarn -g add x",
    "corepack yarn add x",
    "corepack yarn@4 dlx x",
])
def test_vetado(cmd):
    assert bash(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "npm ci",
    "npm install",
    "npm install --no-audit",
    "npm --prefix dashboard ci",
    "npm --prefix . install",
    "npm ci --omit dev",
    "npm run build",
    "npm run build -- --install lodash",
    "npm test",
    "npm ls react",
    "npm view next version",
    "npm audit",
    "npm --version",
    "npx tsc --noEmit",
    "npx vitest run",
    "npx eslint .",
    "npx next build",
    "npx next lint",
    "npx -y pnpm@10 install --frozen-lockfile",
    "yarn install --frozen-lockfile",
    "yarn",
    "yarn run build",
    "yarn test",
    "corepack yarn install --immutable",
])
def test_permitido(cmd):
    assert bash(cmd) is None, cmd


SUFIJO = {"install": " --frozen-lockfile", "exec": " tsc --noEmit", "run": " build", "lint": "", "test": "",
          "ci": ""}
RULE = re.compile(r"^Bash\(((?:npx|npm|pnpm|corepack|yarn)\b.*?):\*\)$")


def test_lo_que_permite_lanes_yaml_pasa_el_guard():
    comprobadas = 0
    for lane in load_lanes().values():
        for rule in lane.allowed_tools:
            m = RULE.match(rule)
            if not m:
                continue
            base = m.group(1)
            cmd = base + SUFIJO.get(base.split()[-1], "")
            assert bash(cmd) is None, f"{lane.name}: {rule} → {cmd!r} lo bloquea el guard"
            comprobadas += 1
    assert comprobadas >= 10  # npm ci/run/test, pnpm, npx pnpm@10, corepack, npx tsc/vitest/eslint/next
