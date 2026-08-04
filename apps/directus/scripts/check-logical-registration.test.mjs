import assert from "node:assert/strict";
import { existsSync, writeFileSync, unlinkSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../..");
const GATE = resolve(HERE, "check-logical-registration.mjs");
const INIT = resolve(REPO_ROOT, "infra/scripts/init-eval-center-dev.ps1");

function runGate() {
  return spawnSync(process.execPath, [GATE], {
    encoding: "utf8",
    timeout: 30000,
  });
}

test("registration gate script exits 0 on current tree", () => {
  const result = runGate();
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.hooks, "example-lab-validation-keep");
  assert.equal(payload.init_eval_center, "physically-deleted");
  assert.deepEqual(payload.endpoints, ["reader-orch"]);
  assert.equal(payload.panels.length, 0);
  assert.equal(payload.retired_sync, "physically-deleted");
  assert.equal(payload.physical_deletion, "enforced");
  assert.equal(payload.eval_example_lab, "protected");
});

test("init-eval-center-dev.ps1 stays physically deleted", () => {
  assert.equal(existsSync(INIT), false);
});

test("gate rejects executable destructive SQL against eval_example_lab_entries", () => {
  const scratchDir = resolve(REPO_ROOT, "infra/scripts");
  mkdirSync(scratchDir, { recursive: true });
  const scratch = resolve(scratchDir, "zz-d2-negative-test.sql");
  writeFileSync(
    scratch,
    "TRUNCATE TABLE eval_example_lab_entries;\n",
    "utf8",
  );
  try {
    const result = runGate();
    assert.notEqual(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stderr, /must not TRUNCATE\/DROP\/DELETE protected eval_example_lab_entries/);
  } finally {
    unlinkSync(scratch);
  }
  // Gate returns to green once the destructive scratch file is gone.
  assert.equal(runGate().status, 0);
});

test("gate allows comment-only mentions of eval_example_lab_entries", () => {
  const scratch = resolve(REPO_ROOT, "infra/scripts/zz-d2-negative-comment.sql");
  writeFileSync(
    scratch,
    "-- TRUNCATE TABLE eval_example_lab_entries; (comment only)\nSELECT 1;\n",
    "utf8",
  );
  try {
    const result = runGate();
    assert.equal(result.status, 0, result.stdout + result.stderr);
  } finally {
    unlinkSync(scratch);
  }
});
