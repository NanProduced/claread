/**
 * Web hygiene deletion guard — keeps the proven-dead modules removed in
 * the hygiene round from being resurrected. Three closed paths:
 *
 * 1. The deleted files/directories must stay absent.
 * 2. The reader-plate root barrel must not regain the retired selection
 *    re-export.
 * 3. No source under src/ may import the deleted module paths again
 *    (this guard file itself is excluded from the scan).
 */

import { describe, expect, it } from "vitest";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const ROOT = process.cwd();
const GUARD_FILE = "src/lib/web-hygiene-source-guard.test.ts";

const DELETED_PATHS = [
  "src/lib/reader-plate/bridges/selection",
  "src/lib/reader-plate/bridges/selection/index.ts",
  "src/lib/reader-plate/bridges/selection/read-plate-reader-selection.ts",
  "src/lib/reader-plate/bridges/selection/selection-toolbar-rect.ts",
  "src/components/reader/dictionary/index.ts",
];

// Import specifiers that would revive the deleted barrels.
const REVIVAL_IMPORT_PATTERNS = [
  /from\s+["'][^"']*reader-plate\/bridges\/selection["']/,
  /from\s+["']@\/components\/reader\/dictionary["']/,
];

// Inside src/components/reader/**, a bare `"./dictionary"` /
// `"../dictionary"` import would resolve to the deleted components
// barrel. Elsewhere the same specifier legitimately targets other live
// modules (e.g. reader-plate bridges/dictionary), so it is only checked
// there.
const READER_DIR_DICTIONARY_PATTERN = /from\s+["']\.{1,2}\/dictionary["']/;

function collectSourceFiles(dir: string, found: string[]): void {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      collectSourceFiles(full, found);
      continue;
    }
    if (/\.(?:ts|tsx)$/.test(entry)) {
      found.push(full);
    }
  }
}

describe("web hygiene deletion guard", () => {
  it("keeps the deleted modules absent", () => {
    for (const rel of DELETED_PATHS) {
      expect(existsSync(resolve(ROOT, rel)), `${rel} must stay deleted`).toBe(false);
    }
  });

  it("keeps the reader-plate root barrel free of the retired selection re-export", () => {
    const barrel = readFileSync(resolve(ROOT, "src/lib/reader-plate/index.ts"), "utf8");
    expect(barrel).not.toMatch(/bridges\/selection/);
  });

  it("keeps the deleted module paths out of every import site", () => {
    const files: string[] = [];
    collectSourceFiles(resolve(ROOT, "src"), files);

    const offenders: string[] = [];
    for (const file of files) {
      const rel = relative(ROOT, file).replaceAll("\\", "/");
      if (rel === GUARD_FILE) {
        continue;
      }
      const source = readFileSync(file, "utf8");
      const inReaderComponents = rel.startsWith("src/components/reader/");
      const revived =
        REVIVAL_IMPORT_PATTERNS.some((pattern) => pattern.test(source)) ||
        (inReaderComponents && READER_DIR_DICTIONARY_PATTERN.test(source));
      if (revived) {
        offenders.push(rel);
      }
    }

    expect(offenders).toEqual([]);
  });
});
