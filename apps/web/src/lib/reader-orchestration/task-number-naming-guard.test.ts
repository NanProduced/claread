// task-history: TEST-GOVERNANCE-FOUNDATION-LONG-R1
/**
 * Naming governance guard — task numbers are historical tracking
 * metadata, not business identity. This guard prevents their *backflow*
 * into Web tests on two fronts:
 *
 * 1. File names: vitest `*.test.ts(x)` under `src/` and Playwright
 *    `*.spec.ts(x)` under `tests/`. The ratchet allowlist is empty and
 *    its ceiling is 0: new task-numbered file names are forbidden.
 *
 * 2. Source lines (fail-closed): every line of every test file is
 *    scanned for UPPERCASE task-history code shapes: batch/round/phase/
 *    stage/task letters followed by a digit (B/R/P/S/T+n), uppercase
 *    forms of the audited single-digit codes d5/d6 and a3/a4/a5 (a
 *    trailing digit as in d50/a30 is a pseudo-prefix, not a code),
 *    ROUND plus digits, LP-R plus digits, and `ASK-` epic prefixes.
 *    Lowercase fixture ids and regular variables are deliberately not
 *    task codes. Exemption is strip-then-scan: the exact identities in
 *    CODE_IDENTITY_ALLOWLIST are removed from the line FIRST, and the
 *    remainder is scanned — a listed identity never shields a real task
 *    code on the same line. Allowlist staleness is judged against the
 *    scanned files EXCLUDING this guard itself: the guard's own
 *    constants and regression samples are not consumers.
 *
 * Exempt by shape (never matched): product versions (v2), article
 * grades (g5), domain terms such as l1-heading, and representation-
 * event contract names such as G1/G2/G3.
 */

import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve, relative } from "node:path";

// Task-number signature in Web test file names. The boundary accepts the
// start of the name as well as `-` / `_` / `.` separators, so no
// separator style can bypass the guard.
const TASK_NUMBER_NAME_RE =
  /(?:^|[-_.])(?:d[56][-_.]|a[345][-_.]|t[0-9](?:\.[0-9]+)*[a-z]?[-_.]|r[0-9]|p[0-9][a-z]?[-_.]|s[0-9][a-z]?[-_.]|round[0-9]|lp-r[0-9])/;

// RATCHET: only shrink; the stock is empty and the ceiling is 0.
const TASK_NUMBER_TEST_FILE_ALLOWLIST = [] as const;
const TASK_NUMBER_TEST_FILE_ALLOWLIST_CEILING = 0;

// Uppercase task-history code shapes only; lowercase fixture ids and
// regular variables are not task codes. The d5/d6 and a3-a5 families
// are single-digit codes: a trailing digit (d50, a30) is a pseudo-
// prefix, not a code, hence the negative lookahead.
const TASK_CODE_LINE_RE =
  /\b[BRPST][0-9]|\bD[56](?![0-9])|\bA[345](?![0-9])|\bROUND[0-9]+|\bLP-R[0-9]/;

// Epic/project prefixes are matched case-sensitively: lowercase `ask-*`
// model keys and testids are product identities, not task codes.
const TASK_CODE_EPIC_RE = /ASK-[A-Z0-9]/;

// This guard's own path: excluded from allowlist-staleness usage, so
// the guard's constants and regression samples never count as
// consumers of an identity.
const GUARD_SELF =
  "src/lib/reader-orchestration/task-number-naming-guard.test.ts";

// Formal identities that legitimately carry an uppercase task-code shape
// inside a test source line. Stripped from the line before scanning.
// RATCHET: only shrink; every entry must still appear in a scanned line
// of some test file other than this guard.
const CODE_IDENTITY_ALLOWLIST = [
  // Library record stub display titles.
  '"P1"',
  '"P2"',
  '"P3"',
  '"P4"',
  '"A3"',
  // Article difficulty grades (product identity, same class as g5).
  '"B1"',
  '"B2"',
  // Fixture record titles.
  '"R1 submit"',
  '"R1 Inline Marks Fixture"',
] as const;

const CODE_IDENTITY_ALLOWLIST_CEILING = 9;

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

function stripIdentities(text: string): string {
  let rest = text;
  for (const identity of CODE_IDENTITY_ALLOWLIST) {
    rest = rest.split(identity).join("");
  }
  return rest;
}

function hasTaskCode(text: string): boolean {
  return TASK_CODE_LINE_RE.test(text) || TASK_CODE_EPIC_RE.test(text);
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

  it("matchers reject task-numbered samples and accept business identities", () => {
    const nameMustMatch = [
      "r1-example.test.ts",
      "feature.p2c.test.ts",
      "feature_t4.2a.test.ts",
    ];
    const nameMustPass = ["reader-ask-v2", "article-g5", "l1-heading"];
    for (const name of nameMustMatch) {
      expect(TASK_NUMBER_NAME_RE.test(name), `${name} must be rejected`).toBe(true);
    }
    for (const name of nameMustPass) {
      expect(TASK_NUMBER_NAME_RE.test(name), `${name} must pass`).toBe(false);
    }

    // Source-line samples. Positive samples are built by concatenation
    // so the guard's own source stays clean under its own scan.
    const lineMustMatch = [
      "B" + "7 batch",
      "D" + "5 fence",
      "D" + "6 migration",
      "A" + "3 audit",
      "A" + "4 audit",
      "A" + "5 audit",
      "ROUND" + "20 rerun",
      "LP-R" + "3 gate",
    ];
    const lineMustPass = [
      "D50 pseudo-prefix",
      "A30 pseudo-prefix",
      "d5 lowercase fixture",
      "round20 lowercase",
      "lp-r2 lowercase",
      "reader-ask-v2",
      "article-g5",
      "l1-heading",
      "G1 representation event",
    ];
    for (const sample of lineMustMatch) {
      expect(hasTaskCode(sample), `${sample} must be rejected`).toBe(true);
    }
    for (const sample of lineMustPass) {
      expect(hasTaskCode(sample), `${sample} must pass`).toBe(false);
    }
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

  it("test-file allowlist size equals its ratchet ceiling", () => {
    expect(TASK_NUMBER_TEST_FILE_ALLOWLIST.length).toBe(
      TASK_NUMBER_TEST_FILE_ALLOWLIST_CEILING,
    );
  });

  // --- Fail-closed source line scan (strip-then-scan) ---
  const scannedLines: { file: string; line: number; text: string }[] = [];
  for (const file of scannedFiles) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((text, index) => {
      scannedLines.push({ file, line: index + 1, text });
    });
  }

  const codeLines = scannedLines.filter(
    ({ text }) =>
      !text.includes("task-history:") && hasTaskCode(stripIdentities(text)),
  );

  it("no task codes in test sources outside task-history lines and listed identities", () => {
    expect(
      codeLines.map((hit) => `${hit.file}:${hit.line}: ${hit.text.trim()}`),
      "uppercase task codes may only survive on task-history lines; a " +
        "listed identity is stripped before scanning and never shields a " +
        "real task code on the same line",
    ).toEqual([]);
  });

  it("code identity allowlist equals its ratchet ceiling and has no stale entries", () => {
    // Consumers are the other scanned test files only; this guard's own
    // constants and regression samples do not count.
    const externalText = scannedLines
      .filter(({ file }) => file !== GUARD_SELF)
      .map(({ text }) => text)
      .join("\n");
    const stale = CODE_IDENTITY_ALLOWLIST.filter(
      (identity) => !externalText.includes(identity),
    );
    expect(stale, "remove stale code identity allowlist entries").toEqual([]);
    expect(CODE_IDENTITY_ALLOWLIST.length).toBe(CODE_IDENTITY_ALLOWLIST_CEILING);
  });

  it("identity exemption strips exact identities instead of passing the whole line", () => {
    // Regression: a listed identity on a line must NOT shield a real task
    // code on the same line. The code token is built by concatenation so
    // the guard's own source stays clean under its own scan.
    const realCode = "R" + "2";
    const identityOnly = 'record({ readingRecordId: "p1", title: "P1" });';
    const mixedLine = `record({ title: "P1" }); // ${realCode} follow-up`;
    const specCase = `const fixture = "s1"; // ${realCode} follow-up`;
    expect(hasTaskCode(stripIdentities(identityOnly))).toBe(false);
    expect(hasTaskCode(stripIdentities(mixedLine))).toBe(true);
    expect(hasTaskCode(stripIdentities(specCase))).toBe(true);
  });
});
