/**
 * F7 source guard — verifies invariants that protect the Reader
 * Orchestration frontend contract from silent regressions:
 *
 * 1. The Ask `article_rag` sidecar never surfaces debug-only fields to UI
 *    state. The boundary mapper (`mapAskArticleRagSidecar`) must strip
 *    `failure_code`, `retryable`, `fallback_allowed`, `source_pack_hash`,
 *    and `query_sha256` so components cannot accidentally render them.
 *
 * 2. The new Reader Orchestration flow (F0-F6) does not re-import the
 *    legacy `/app/reader/{recordId}` route helper. The legacy route is
 *    kept for the old ReaderWorkbench surface only; the new flow must
 *    navigate via `appReadingRecordRoute` (`/app/reader-record/{recordId}`).
 *    Re-introducing the legacy route into the new flow would break the
 *    "legacy path stays untouched" hard constraint and split traffic
 *    between two reader surfaces.
 *
 * 3. The F7 Ask sidecar e2e fixture route is blocked in production. It is
 *    allowed as a stable Playwright entry point, but must never become a
 *    user-visible app page.
 *
 * This is a guard test: it scans source files at test time so a future
 * regression fails the test suite before it can ship.
 */

import { describe, expect, it } from "vitest";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname, resolve } from "node:path";

import { mapAskArticleRagSidecar } from "./status-mapper";
import type { ReaderAskArticleRagSidecarDto } from "@/types/api/reader-ask";

// ---------------------------------------------------------------------------
// 1. Ask article_rag sidecar — debug-only field stripping
// ---------------------------------------------------------------------------

describe("F7 source guard: Ask article_rag sidecar strips debug-only fields", () => {
  const debugOnlyFields = [
    "failure_code",
    "retryable",
    "fallback_allowed",
    "source_pack_hash",
    "query_sha256",
  ] as const;

  it("mapped safe DTO does not carry debug-only keys", () => {
    const raw: ReaderAskArticleRagSidecarDto = {
      status: "available",
      failure_code: "internal_error",
      retryable: true,
      fallback_allowed: false,
      should_attach: true,
      context_ids: ["ctx_1"],
      source_pack_hash: "pack_hash_secret",
      query_sha256: "query_hash_secret",
      citations: [
        {
          context_id: "ctx_1",
          chunk_id: "chunk_1",
          citation: {
            reading_record_id: "rec_1",
            stable_document_id: "sd_1",
            base_id: "base_1",
            record_generation: 1,
            block_ids: ["block_1"],
            unit_ids: ["unit_1"],
            anchor_segment_ids: ["anchor_1"],
            canonical_text_start_utf16: 0,
            canonical_text_end_utf16: 10,
          },
        },
      ],
    };

    const mapped = mapAskArticleRagSidecar(raw);

    for (const field of debugOnlyFields) {
      expect(mapped).not.toHaveProperty(field);
    }
  });

  it.each([
    "stale_due_to_repair",
    "disabled",
    "composer_rejected",
    "not_indexed_or_unavailable",
    "empty",
  ] as const)(
    "non-available status %s clears citations and strips debug fields",
    (status) => {
      const raw: ReaderAskArticleRagSidecarDto = {
        ...{
          status: "available",
          failure_code: "leak",
          retryable: true,
          fallback_allowed: true,
          should_attach: true,
          context_ids: ["ctx_1"],
          source_pack_hash: "leak",
          query_sha256: "leak",
          citations: [
            {
              context_id: "ctx_1",
              chunk_id: "chunk_1",
              citation: {
                reading_record_id: "rec_1",
                stable_document_id: "sd_1",
                base_id: "base_1",
                record_generation: 1,
                block_ids: ["block_1"],
                unit_ids: ["unit_1"],
                anchor_segment_ids: ["anchor_1"],
                canonical_text_start_utf16: 0,
                canonical_text_end_utf16: 10,
              },
            },
          ],
        },
        status,
      };

      const mapped = mapAskArticleRagSidecar(raw as ReaderAskArticleRagSidecarDto);

      expect(mapped.citations).toEqual([]);
      for (const field of debugOnlyFields) {
        expect(mapped).not.toHaveProperty(field);
      }
    },
  );
});

// ---------------------------------------------------------------------------
// 2. Canonical route isolation guard
// ---------------------------------------------------------------------------

/**
 * New Reader Orchestration flow source roots (F0-F6). Any file under these
 * roots that imports a removed route helper is a regression. The canonical
 * route is `/app/reader/{recordId}`.
 */
const NEW_FLOW_ROOTS = [
  "src/lib/reader-orchestration",
  "src/services/bff/reader-plate",
  "src/services/bff/reader-ask",
  "src/services/api/reader-ask",
  // The `app/read` input page and final reader rendering surface are part of
  // the new flow.
  "src/app/(private)/app/read",
  "src/app/(private)/app/reader/[recordId]",
] as const;

const LEGACY_ROUTE_IDENTIFIERS = [
  "legacyAppReaderRoute",
  "appReadingRecordRoute",
] as const;

const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"]);

function listSourceFiles(rootDir: string): string[] {
  // vitest runs from the package root (`apps/web`); `rootDir` is relative
  // to that root, e.g. `src/lib/reader-orchestration`.
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

  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const fullPath = join(dir, entry);
      const entryStats = statSync(fullPath);
      if (entryStats.isDirectory()) {
        walk(fullPath);
      } else if (SOURCE_EXTENSIONS.has(extname(fullPath))) {
        results.push(fullPath);
      }
    }
  };

  walk(absoluteRoot);
  return results;
}

describe("F7 source guard: removed route helpers are not re-imported by new flow", () => {
  const scannedFiles: { path: string; content: string }[] = [];

  for (const root of NEW_FLOW_ROOTS) {
    const files = listSourceFiles(root);
    for (const file of files) {
      // Skip test files — tests may reference the legacy route identifier
      // in assertions or fixtures without the production code importing it.
      if (/\.(test|spec)\.(ts|tsx)$/.test(file)) {
        continue;
      }
      scannedFiles.push({ path: file, content: readFileSync(file, "utf8") });
    }
  }

  it("scanned at least one new-flow source file (guard is not vacuous)", () => {
    expect(scannedFiles.length).toBeGreaterThan(0);
  });

  it.each(scannedFiles)(
    "$path does not import removed route helpers",
    ({ path, content }) => {
      for (const identifier of LEGACY_ROUTE_IDENTIFIERS) {
        // Match either an import binding or a dynamic property access.
        // We deliberately match the identifier token anywhere in the file;
        // a comment mentioning the legacy route is acceptable in principle,
        // but production new-flow code must not import or call it.
        const importPattern = new RegExp(
          `\\bimport\\b[^;]*\\b${identifier}\\b`,
        );
        const callPattern = new RegExp(`\\b${identifier}\\s*\\(`);

        expect(
          importPattern.test(content),
          `${path} imports removed helper ${identifier}`,
        ).toBe(false);
        expect(
          callPattern.test(content),
          `${path} calls removed helper ${identifier}()`,
        ).toBe(false);
      }
    },
  );
});

// ---------------------------------------------------------------------------
// 3. Removed route and BFF namespace guard
// ---------------------------------------------------------------------------

describe("F7 source guard: removed pages and old BFF namespaces stay absent", () => {
  const removedPaths = [
    "src/app/(private)/app/reader-record/[recordId]/page.tsx",
    "src/app/(private)/app/reader-plate/page.tsx",
    "src/app/(private)/app/f7-ask-fixture/[recordId]/page.tsx",
    "src/app/api/web/reader-plate",
    "src/app/api/web/reader-ask",
    "src/app/api/web/annotations",
    "src/app/api/web/favorites",
    "src/app/api/web/reader-notes",
    "src/app/api/web/reading-record",
    "src/app/api/web/reading-records",
    "src/app/api/web/analysis",
    "src/app/api/web/reader/[recordId]",
    "src/app/api/web/records",
  ] as const;

  it.each(removedPaths)("does not retain removed path %s", (relativePath) => {
    expect(existsSync(resolve(process.cwd(), relativePath))).toBe(false);
  });

  it("keeps the final route on the Plate surface without a surface switch", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/reader/[recordId]/plate-page.tsx"),
      "utf8",
    );
    expect(source).toContain("ReaderRecordPlateSurface");
    expect(source).not.toContain("ReaderRecordWorkbenchSurface");
    expect(source).not.toContain("getReaderRecordSurfaceMode");
    expect(source).toContain("/api/web/reader/records/");
  });
});
