// task-history: TEST-GOVERNANCE-FOUNDATION-LONG-R1
/**
 * Naming governance guard — task numbers are historical tracking
 * metadata, not business identity. This guard prevents their *backflow*
 * into new Web test file names (vitest `*.test.ts(x)` under `src/` and
 * Playwright `*.spec.ts(x)` under `tests/`).
 *
 * Existing stock lives in TASK_NUMBER_TEST_FILE_ALLOWLIST below. The
 * allowlist is a ratchet: entries may only be REMOVED (when a file is
 * renamed to a business name or deleted); adding new entries fails this
 * suite. Reuses the node:fs scan pattern of
 * reader-orchestration-source-guard.test.ts.
 *
 * Exempt by design (product/persisted identity, not task numbers):
 * `-v2` product versions (reader-ask-v2), article grades (`-g5-`), and
 * domain terms such as `l1-heading`.
 */

import { describe, expect, it } from "vitest";
import { readdirSync, statSync } from "node:fs";
import { join, resolve, relative } from "node:path";

// Task-number signature in hyphen/underscore-separated Web names
// (audit 2026-07-23 §1.1-10). `r[0-9]` covers `-r0`, `-r2-1d`, `-r3-r1`.
const TASK_NUMBER_NAME_RE =
  /[-_](?:d6[-_]|a[345][-_]|t5[0-9]|t6[0-9]|r[0-9][0-9a-z._-]*|p[0-9][a-z]?|s[0-9][a-z]?|round[0-9]+|lp-r[0-9])/;

// Existing stock of task-numbered test files (relative to apps/web).
// RATCHET: only shrink this list. Renamed/deleted files must have their
// entry removed in the same change; new task-numbered file names are
// forbidden and must be renamed to business names instead.
const TASK_NUMBER_TEST_FILE_ALLOWLIST = [
  "src/lib/reader-plate-snapshot/incremental-projection-merger-p2c.test.ts",
] as const;

// Ratchet ceiling (GOVERNANCE-CLOSEOUT-R1): the allowlist may shrink but
// must never grow beyond this size.
const TASK_NUMBER_TEST_FILE_ALLOWLIST_CEILING = 1;

const SCAN_ROOTS = ["src", "tests"] as const;
const TEST_FILE_RE = /\.(test|spec)\.(ts|tsx)$/;

function listTestFiles(rootDir: string): string[] {
  // vitest runs from the package root (`apps/web`); `rootDir` is relative
  // to that root. Mirrors listSourceFiles in reader-orchestration-source-guard.
  const absoluteRoot = resolve(process.cwd(), rootDir);
  const results: string[] = [];

  let stats;
  try {
    stats = statSync(absoluteRoot);
  } catch {
    return results;
  }
  if (!stats.isDirectory()) {
    return results;
  }

  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      const entryStats = statSync(full);
      if (entryStats.isDirectory()) {
        walk(full);
      } else if (TEST_FILE_RE.test(entry)) {
        results.push(relative(process.cwd(), full).replaceAll("\\", "/"));
      }
    }
  };

  walk(absoluteRoot);
  return results;
}

describe("Naming governance guard: task-numbered Web test file names", () => {
  const scannedFiles: string[] = [];
  for (const root of SCAN_ROOTS) {
    scannedFiles.push(...listTestFiles(root));
  }

  const actual = scannedFiles.filter((file) =>
    TASK_NUMBER_NAME_RE.test(file.split("/").at(-1) ?? ""),
  );

  it("scanned at least one Web test file (guard is not vacuous)", () => {
    expect(scannedFiles.length).toBeGreaterThan(0);
  });

  it("no new task-numbered test file names outside the ratchet allowlist", () => {
    const allowlist = new Set<string>(TASK_NUMBER_TEST_FILE_ALLOWLIST);
    const unlisted = actual.filter((file) => !allowlist.has(file));
    expect(
      unlisted,
      "new task-numbered test file names are forbidden; rename to a " +
        "business name instead of allowlisting",
    ).toEqual([]);
  });

  it("allowlist stays under its ratchet ceiling", () => {
    expect(TASK_NUMBER_TEST_FILE_ALLOWLIST.length).toBeLessThanOrEqual(
      TASK_NUMBER_TEST_FILE_ALLOWLIST_CEILING,
    );
  });

  it.each(TASK_NUMBER_TEST_FILE_ALLOWLIST)(
    "allowlist entry %s still matches an existing task-numbered file (ratchet only shrinks)",
    (relativePath) => {
      expect(
        actual,
        `remove stale allowlist entry ${relativePath}`,
      ).toContain(relativePath);
    },
  );
});
