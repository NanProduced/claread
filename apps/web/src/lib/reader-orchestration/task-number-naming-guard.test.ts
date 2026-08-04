// task-history: TEST-GOVERNANCE-FOUNDATION-LONG-R1
/**
 * Naming governance guard — task numbers are historical tracking
 * metadata, not business identity. This guard prevents their *backflow*
 * into new Web test file names (vitest `*.test.ts(x)` under `src/` and
 * Playwright `*.spec.ts(x)` under `tests/`) and into describe/it/test
 * titles (TEST-GOVERNANCE-WEB-P2).
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
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve, relative } from "node:path";

// Task-number signature in hyphen/underscore-separated Web names
// (audit 2026-07-23 §1.1-10). `r[0-9]` covers `-r0`, `-r2-1d`, `-r3-r1`.
const TASK_NUMBER_NAME_RE =
  /[-_](?:d6[-_]|a[345][-_]|t5[0-9]|t6[0-9]|r[0-9][0-9a-z._-]*|p[0-9][a-z]?|s[0-9][a-z]?|round[0-9]+|lp-r[0-9])/;

// Existing stock of task-numbered test files (relative to apps/web).
// RATCHET: only shrink this list. Renamed/deleted files must have their
// entry removed in the same change; new task-numbered file names are
// forbidden and must be renamed to business names instead. The stock is
// now empty: the last file was renamed to a business name in
// TEST-GOVERNANCE-WEB-P2, and the ceiling was lowered to 0 in the same
// change.
const TASK_NUMBER_TEST_FILE_ALLOWLIST = [] as const;

// Ratchet ceiling (GOVERNANCE-CLOSEOUT-R1): equality ratchet — the
// allowlist size must match exactly, so a shrunk list can never grow
// back. Every governance rename lowers the ceiling in the same change.
const TASK_NUMBER_TEST_FILE_ALLOWLIST_CEILING = 0;

// Task-number scan for describe/it/test titles (TEST-GOVERNANCE-WEB-P2).
// Same code family as file names; titles keep the same exemptions
// (product versions, article grades, domain terms), plus a small identity
// allowlist for persisted fixture names referenced from titles.
// Known gap: titles assembled from it.each tables or string concatenation
// are not scanned — direct string-literal titles are the contract.
const TASK_NUMBER_TITLE_RE =
  /\b(?:d6[-_]|a[345][-_]|t5[0-9]|t6[0-9]|r[0-9][0-9a-z._-]*|p[0-9][a-z]?(?![a-z0-9])|s[0-9][a-z]?(?![a-z0-9])|round[0-9]+|lp-r[0-9])/i;

// Business identities that legitimately carry a task-number-shaped token
// inside a title. RATCHET: only shrink.
const TITLE_IDENTITY_ALLOWLIST = ["r14_complex"] as const;
const TITLE_IDENTITY_ALLOWLIST_CEILING = 1;

const TITLE_CALL_RE = /\b(?:describe|it|test)(?:\.[a-zA-Z]+)*\s*\(\s*(["'`])/;

function extractTitle(line: string): string | null {
  const match = TITLE_CALL_RE.exec(line);
  if (!match) return null;
  const quote = match[1];
  const start = match.index + match[0].length;
  let end = start;
  while (end < line.length) {
    const ch = line[end];
    if (ch === "\\") {
      end += 2;
      continue;
    }
    if (quote === "`" && ch === "$" && line[end + 1] === "{") break;
    if (ch === quote) break;
    end += 1;
  }
  return line.slice(start, end);
}

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

  // --- Title scan (TEST-GOVERNANCE-WEB-P2) ---
  const scannedTitles: { file: string; title: string }[] = [];
  for (const file of scannedFiles) {
    const content = readFileSync(file, "utf8");
    for (const line of content.split("\n")) {
      const title = extractTitle(line);
      if (title !== null) {
        scannedTitles.push({ file, title });
      }
    }
  }

  const titleViolations = scannedTitles.filter(
    ({ title }) =>
      TASK_NUMBER_TITLE_RE.test(title) &&
      !TITLE_IDENTITY_ALLOWLIST.some((identity) => title.includes(identity)),
  );

  it("scanned at least one Web test title (title guard is not vacuous)", () => {
    expect(scannedTitles.length).toBeGreaterThan(0);
  });

  it("no new task-numbered test titles outside the allowed identities", () => {
    expect(
      titleViolations.map(
        (violation) => `${violation.file}: ${violation.title}`,
      ),
      "new task-numbered test titles are forbidden; rename the title to a " +
        "business description instead of allowlisting",
    ).toEqual([]);
  });

  it("title identity allowlist size equals its ratchet ceiling", () => {
    expect(TITLE_IDENTITY_ALLOWLIST.length).toBe(
      TITLE_IDENTITY_ALLOWLIST_CEILING,
    );
  });

  it.each(TITLE_IDENTITY_ALLOWLIST)(
    "allowed title identity %s still appears in a scanned title (ratchet only shrinks)",
    (identity) => {
      expect(
        scannedTitles.some(({ title }) => title.includes(identity)),
        `remove stale title identity allowlist entry ${identity}`,
      ).toBe(true);
    },
  );
});
