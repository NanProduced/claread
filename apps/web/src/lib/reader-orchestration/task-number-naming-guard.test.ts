// task-history: TEST-GOVERNANCE-FOUNDATION-LONG-R1
/**
 * Naming governance guard — task numbers are historical tracking
 * metadata, not business identity. This guard prevents their *backflow*
 * into Web tests on two fronts:
 *
 * 1. File names: vitest `*.test.ts(x)` under `src/` and Playwright
 *    `*.spec.ts(x)` under `tests/`. Existing stock lives in
 *    TASK_NUMBER_TEST_FILE_ALLOWLIST below. The allowlist is a ratchet:
 *    entries may only be REMOVED (when a file is renamed to a business
 *    name or deleted); adding new entries fails this suite. Reuses the
 *    node:fs scan pattern of reader-orchestration-source-guard.test.ts.
 *
 * 2. Source lines (fail-closed): every line of every test file is
 *    scanned. A task code may only appear on an explicit `task-history:`
 *    line or on a line carrying one of the formal protocol/fixture
 *    identities listed in CODE_IDENTITY_ALLOWLIST (equality ratchet).
 *
 * Exempt by design (product/persisted identity, not task numbers):
 * `-v2` product versions (reader-ask-v2), article grades (`-g5-`),
 * domain terms such as `l1-heading`, and representation-event contract
 * names such as G1/G2/G3 — none of these match the task-code regexes.
 */

import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve, relative } from "node:path";

// Task-number signature in Web test file names. Hyphen, underscore and
// dot separators are all recognized so no separator style can bypass
// the guard.
const TASK_NUMBER_NAME_RE =
  /[-_.](?:d6[-_.]|a[345][-_.]|t5[0-9]|t6[0-9]|r[0-9][0-9a-z._-]*|p[0-9][a-z]?|s[0-9][a-z]?|round[0-9]+|lp-r[0-9])/;

// Existing stock of task-numbered test files (relative to apps/web).
// RATCHET: only shrink this list. Renamed/deleted files must have their
// entry removed in the same change; new task-numbered file names are
// forbidden and must be renamed to business names instead. The stock is
// empty: every stock file has been renamed to a business name and the
// ceiling is 0.
const TASK_NUMBER_TEST_FILE_ALLOWLIST = [] as const;

// Ratchet ceiling: equality ratchet — the allowlist size must match
// exactly, so a shrunk list can never grow back. Every governance
// rename lowers the ceiling in the same change.
const TASK_NUMBER_TEST_FILE_ALLOWLIST_CEILING = 0;

// Fail-closed source scan: task-code token shapes (round/phase/stage/
// batch/task letters followed by a digit, plus epic prefixes).
// Deliberately excludes formal identity shapes that never carry
// tracking meaning: product versions (v2), article grades (g5),
// navigation levels (l1-heading), representation-event contract groups
// (G1/G2/G3).
const TASK_CODE_LINE_RE =
  /\b(?:b|p|r|s|t)[0-9]|\bd6[-_.]|\ba[345][-_.]|\bround[0-9]|\blp-r[0-9]/i;

// Epic/project prefixes match case-sensitively: lowercase `ask-*` model
// keys, testids and `data-ask-*` markers are product identities, not
// task codes.
const TASK_CODE_EPIC_RE = /ASK-[A-Z0-9]/;

// Formal protocol/fixture identities that legitimately carry a
// task-code-shaped token on a test source line. RATCHET: only shrink;
// every entry must still appear in a scanned test source.
const CODE_IDENTITY_ALLOWLIST = [
  // The file-name regex literal above (guard self-match).
  "t5[0-9]|t6[0-9]",
  // Structured-source renderer fixture name (fixture identity).
  "r14_complex",
  // Quoted synthetic fixture ids asserted across reader/ask suites:
  // sentence/paragraph/block ids, library stub titles, article
  // difficulty grades, and one fixture record title.
  '"s1"',
  '"s2"',
  '"s3"',
  '"s4"',
  '"s9"',
  '"p1"',
  '"p2"',
  '"p3"',
  '"p4"',
  '"t1"',
  '"r1"',
  '"P1"',
  '"P2"',
  '"P3"',
  '"P4"',
  '"b1"',
  '"B1"',
  '"B2"',
  '"R1 submit"',
  "'r1'",
  "&quot;r1&quot;",
  // Composite annotation target keys built from those sentence ids.
  "record-1:sentence:s1",
  "record-1:range:s1",
  "record-1:range:s2",
  // Dictionary request keys and outline node testids built from them.
  "context::record-1::s1",
  "reader-record-outline-node-s2",
  // Reader route URL fixtures (settings dialog history tests).
  "/app/reader/r1",
  // Selector-injection attack payload fixture (data value, not a title).
  "evilId = 's1",
  // Inline-marks projection fixture record title.
  '"R1 Inline Marks Fixture"',
  // Structured-source / projection fixture block ids (field-anchored).
  'block_id: "b',
  'parent_block_id: "b',
  'data-block-id="b',
  'stableBlockId: "b',
  // Snapshot/segment fixture ids (field-anchored) plus two exact values.
  'snapshotId: "s',
  '"s5_accepted"',
  'segmentId: "s',
  "deterministic-e2e-r0",
  // Retired e2e/config paths the source guard asserts stay deleted.
  "playwright.ask-activity-r2.config.ts",
  "playwright.ask-process-target-r0.config.ts",
  "playwright.ask-retry-r7.config.ts",
  "tests/e2e/ask-activity-r2-server-setup.ts",
  "tests/e2e/ask-retry-r7-server-setup.ts",
  "src/lib/reader-ask/ask-activity-r2-server-setup.test.ts",
  "tests/e2e/ask-retry-submission-r5.spec.ts",
  "tests/e2e/ask-retry-submission-r6.spec.ts",
  "tests/e2e/ask-retry-submission-r7.spec.ts",
  "tests/e2e/ask-ux-mobile-r3-floating-overlay.spec.ts",
  "tests/e2e/ask-ux-streaming-delta-r2.spec.ts",
  "tests/e2e/plate-grammar-callout-state-r2-1c.spec.ts",
  "tests/e2e/plate-surface-grammar-expansion-scroll-anchor-r3-r2.spec.ts",
  "tests/e2e/plate-surface-grammar-first-publish-p2c.spec.ts",
  "tests/e2e/plate-surface-grammar-group-identity-p2a.spec.ts",
  "tests/e2e/plate-surface-incremental-r2-1d.spec.ts",
  "tests/e2e/plate-surface-l1-heading-navigation-t5-1d.spec.ts",
  "tests/e2e/plate-surface-layer-revision-r2-1e.spec.ts",
  "tests/e2e/plate-surface-quick-peek-reanchor-r3-r1.spec.ts",
  "tests/e2e/plate-surface-section-translation-t5-6c.spec.ts",
  "tests/e2e/plate-surface-semantic-outline-t5-5a.spec.ts",
  "tests/e2e/plate-targeted-ops-s2.spec.ts",
  "tests/e2e/reader-record-ask-agentic-activity-r2.spec.ts",
  "tests/e2e/reader-record-ask-process-target-r0.spec.ts",
  "tests/e2e/reader-record-rail-stable-progress-quiet-t5-1e.spec.ts",
  "ask-activity-r2",
  "ask-retry-r7",
] as const;

const CODE_IDENTITY_ALLOWLIST_CEILING = 66;

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

describe("Naming governance guard: task numbers stay out of Web test names and sources", () => {
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

  it("allowlist size equals its ratchet ceiling", () => {
    expect(TASK_NUMBER_TEST_FILE_ALLOWLIST.length).toBe(
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

  // --- Fail-closed source line scan ---
  const scannedLines: { file: string; line: number; text: string }[] = [];
  for (const file of scannedFiles) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((text, index) => {
      scannedLines.push({ file, line: index + 1, text });
    });
  }

  const codeLines = scannedLines.filter(
    ({ text }) =>
      !text.includes("task-history:") &&
      (TASK_CODE_LINE_RE.test(text) || TASK_CODE_EPIC_RE.test(text)) &&
      !CODE_IDENTITY_ALLOWLIST.some((identity) => text.includes(identity)),
  );

  it("no task codes in test sources outside task-history lines and listed identities", () => {
    expect(
      codeLines.map((hit) => `${hit.file}:${hit.line}: ${hit.text.trim()}`),
      "task codes may only survive on task-history lines or on lines " +
        "carrying a listed formal protocol/fixture identity",
    ).toEqual([]);
  });

  it("code identity allowlist size equals its ratchet ceiling", () => {
    expect(CODE_IDENTITY_ALLOWLIST.length).toBe(CODE_IDENTITY_ALLOWLIST_CEILING);
  });

  it.each(CODE_IDENTITY_ALLOWLIST)(
    "listed code identity %s still appears in a scanned test source (ratchet only shrinks)",
    (identity) => {
      expect(
        scannedLines.some(({ text }) => text.includes(identity)),
        `remove stale code identity allowlist entry ${identity}`,
      ).toBe(true);
    },
  );
});
