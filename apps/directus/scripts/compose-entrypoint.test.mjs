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

// Regression for the Directus "silent exit 0" boot bug: a YAML folded (`>`)
// entrypoint scalar collapses the `mkdir -p` lines and `exec npx directus start`
// into a single malformed `mkdir` command, so Directus never starts yet the
// container exits 0. The entrypoint must stay an exec-form list whose shell
// command is a literal block with each statement on its own line.

test("directus compose entrypoint is not a folded scalar", () => {
  const src = body();
  assert.doesNotMatch(
    src,
    /entrypoint:\s*>/,
    "entrypoint must not be a folded `>` scalar (it would fold the boot commands into one malformed mkdir)",
  );
  // The `entrypoint:` key must introduce a block (exec-form list), not an inline
  // scalar value on the same line.
  assert.match(
    src,
    /^\s*entrypoint:\s*(#.*)?$/m,
    "entrypoint must be a block key introducing an exec-form list, not an inline scalar",
  );
});

test("directus compose entrypoint is exec-form /bin/sh -c with a literal command block", () => {
  const src = body();
  assert.match(src, /^\s*-\s*\/bin\/sh\s*$/m, "entrypoint list must include /bin/sh");
  assert.match(src, /^\s*-\s*-c\s*$/m, "entrypoint list must include -c");
  assert.match(
    src,
    /^\s*-\s*\|/m,
    "entrypoint shell command must be a literal block scalar (| or |-) so newlines are preserved",
  );
});

test("directus compose entrypoint runs each mkdir separately then exec npx directus start", () => {
  const src = body();
  const mkdirLines = src
    .split(/\r?\n/)
    .filter((line) => /mkdir -p \/directus\/runtime-evals/.test(line));
  assert.ok(
    mkdirLines.length >= 3,
    `expected >=3 separate 'mkdir -p /directus/runtime-evals' lines, found ${mkdirLines.length}`,
  );
  // `exec npx directus start` must be its own shell statement on its own line,
  // not folded into the tail of the last mkdir.
  assert.match(
    src,
    /^\s*exec npx directus start\s*$/m,
    "'exec npx directus start' must be on its own line as the final command",
  );
});
