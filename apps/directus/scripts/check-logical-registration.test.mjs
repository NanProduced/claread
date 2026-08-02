import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../..");
const INIT = resolve(REPO_ROOT, "infra/scripts/init-eval-center-dev.ps1");
const GATE = resolve(HERE, "check-logical-registration.mjs");

test("init-eval-center-dev.ps1 is retired tombstone before any Docker/DDL", () => {
  assert.equal(existsSync(INIT), true);
  const body = readFileSync(INIT, "utf8");
  assert.match(body, /\[retired\]/i);
  assert.match(body, /\bexit\s+1\b/i);
  // No executable docker/psql/pnpm sync lines
  const executable = body
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && !l.startsWith("Write-Error") && !l.startsWith("Write-Host") && !l.startsWith("param") && !l.startsWith("$ErrorActionPreference") && !l.startsWith("exit"));
  for (const line of executable) {
    assert.equal(
      /^(docker|pnpm|psql)\b/i.test(line),
      false,
      `unexpected executable side-effect line: ${line}`,
    );
  }
  assert.equal(/\bdocker\s+(cp|exec)\b/i.test(body) && !/Write-Error|retired/i.test(body), false);
});

test("init-eval-center-dev.ps1 exits non-zero without Docker side effects", () => {
  // Run via pwsh if available; otherwise parse-only guarantee already covered.
  const shell = process.platform === "win32" ? "pwsh" : "pwsh";
  const result = spawnSync(shell, ["-NoProfile", "-File", INIT], {
    encoding: "utf8",
    timeout: 15000,
  });
  // On environments without pwsh, spawn may fail to launch; treat launch failure separately.
  if (result.error && result.error.code === "ENOENT") {
    // Fallback: windows powershell
    const result2 = spawnSync("powershell", ["-NoProfile", "-File", INIT], {
      encoding: "utf8",
      timeout: 15000,
    });
    if (result2.error && result2.error.code === "ENOENT") {
      assert.ok(true, "shell unavailable; static tombstone already asserted");
      return;
    }
    assert.notEqual(result2.status, 0, result2.stdout + result2.stderr);
    const combined = `${result2.stdout || ""}\n${result2.stderr || ""}`;
    assert.match(combined, /retired/i);
    return;
  }
  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  const combined = `${result.stdout || ""}\n${result.stderr || ""}`;
  assert.match(combined, /retired/i);
});

test("registration gate script exits 0 on current tree", () => {
  const result = spawnSync(process.execPath, [GATE], {
    encoding: "utf8",
    timeout: 15000,
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.hooks, "example-lab-validation-keep");
  assert.equal(payload.init_eval_center, "fail-closed");
  assert.deepEqual(payload.endpoints, ["reader-orch"]);
  assert.equal(payload.panels.length, 0);
  assert.equal(payload.retired_sync, "physically-deleted");
  assert.equal(payload.physical_deletion, "enforced");
});
