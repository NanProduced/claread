import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../..");
const COMPOSE = resolve(REPO_ROOT, "infra/docker/docker-compose.directus.yml");

function body() {
  assert.equal(existsSync(COMPOSE), true, "docker-compose.directus.yml missing");
  return readFileSync(COMPOSE, "utf8");
}

// Directus boot contract + retired-surface prohibition for
// infra/docker/docker-compose.directus.yml.
//
// Boot contract: the entrypoint must be an exec-form list (`/bin/sh -c` with a
// literal command block) whose final/only statement is `exec npx directus start`.
// A YAML folded (`>`) scalar would collapse the command into a malformed string
// and the container could silently exit 0 without starting Directus.
//
// Retired-surface prohibition: after CUTOVER-CONTROL-EVAL the compose file must
// not carry the retired Eval Center / Workflow Lab / Node Lab runtime contract —
// no runtime-evals mkdir, no legacy CLAREAD_* env, no Node Lab judge worker
// command, and no consumer-less /directus/evals or /directus/runtime-evals mounts.

test("directus compose entrypoint is not a folded scalar", () => {
  const src = body();
  assert.doesNotMatch(
    src,
    /entrypoint:\s*>/,
    "entrypoint must not be a folded `>` scalar (it would fold the boot command into a malformed string)",
  );
  assert.match(
    src,
    /^\s*entrypoint:\s*(#.*)?$/m,
    "entrypoint must be a block key introducing an exec-form list, not an inline scalar",
  );
});

test("directus compose entrypoint is exec-form /bin/sh -c ending in exec npx directus start", () => {
  const src = body();
  assert.match(src, /^\s*-\s*\/bin\/sh\s*$/m, "entrypoint list must include /bin/sh");
  assert.match(src, /^\s*-\s*-c\s*$/m, "entrypoint list must include -c");
  assert.match(
    src,
    /^\s*exec npx directus start\s*$/m,
    "'exec npx directus start' must be its own shell statement (the boot command)",
  );
});

test("directus compose no longer creates or mounts the retired runtime-evals dirs", () => {
  const src = body();
  assert.doesNotMatch(
    src,
    /mkdir -p \/directus\/runtime-evals/,
    "entrypoint must not create the retired /directus/runtime-evals directories",
  );
  assert.doesNotMatch(
    src,
    /\/directus\/runtime-evals/,
    "compose must not reference the retired /directus/runtime-evals mount/path",
  );
  assert.doesNotMatch(
    src,
    /\/directus\/evals\b/,
    "compose must not reference the retired /directus/evals mount",
  );
});

test("directus compose carries no retired Eval/Workflow/Node Lab env or worker command", () => {
  const src = body();
  const forbiddenTokens = [
    "CLAREAD_EVAL_PROXY_TIMEOUT_MS",
    "CLAREAD_EVAL_RUNS_ROOT",
    "CLAREAD_NODE_LAB_ARTIFACTS_ROOT",
    "CLAREAD_WORKFLOW_RUNTIME_RUNS_ROOT",
    "CLAREAD_WORKFLOW_COMPARE_RUNTIME_ROOT",
    "CLAREAD_NODE_LAB_JUDGE_DISPATCH_MODE",
    "CLAREAD_NODE_LAB_JUDGE_WORKER_COMMAND",
    "CLAREAD_API_BASE_URL",
    "CLAREAD_API_ADMIN_KEY",
    // Node Lab judge worker command referenced a module deleted in the physical cutover.
    "claread_eval.node_lab_judge.worker",
  ];
  for (const token of forbiddenTokens) {
    assert.equal(src.includes(token), false, `compose must not carry retired token: ${token}`);
  }
});
