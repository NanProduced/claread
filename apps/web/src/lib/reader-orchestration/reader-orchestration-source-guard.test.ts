/**
 * F7 source guard — verifies invariants that protect the Reader
 * Orchestration frontend contract from silent regressions:
 *
 * 1. The Ask `article_rag` sidecar never surfaces debug-only fields to UI
 *    state. The boundary mapper (`mapAskArticleRagSidecar`) must strip
 *    `failure_code`, `retryable`, `fallback_allowed`, `source_pack_hash`,
 *    and `query_sha256` so components cannot accidentally render them.
 *
 * 2. The final Reader Orchestration flow (F0-F6) does not re-import a
 *    removed route helper or old BFF URL. The only Reader product page is
 *    `/app/reader/{recordId}`, and its reachable source closure must use
 *    the canonical `/api/web/reader/**` namespace.
 *
 * 3. Retired Reader Workbench, adapter, and E2E harness paths remain absent
 *    after the Web Physical cutover.
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

describe("Source guard: Ask article_rag sidecar strips debug-only fields", () => {
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

describe("Source guard: removed route helpers are not re-imported by new flow", () => {
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

describe("Source guard: removed pages and old BFF namespaces stay absent", () => {
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
    // The unified Web submit entry is the only supported record input route.
    "src/app/api/web/reader/records/plain-text/route.ts",
    // Keep the retired legacy submit endpoint from being recreated under the
    // old namespace while its zero-consumer clients await P-WEB cleanup.
    "src/app/api/web/reader-plate/submit/route.ts",
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

// ---------------------------------------------------------------------------
// 4. P-WEB Physical deletion and retained production chain guard
// ---------------------------------------------------------------------------

const PHYSICAL_DELETED_PATHS = [
  // Retired Workbench surface and projection
  "src/app/(private)/app/reader/[recordId]/ReaderWorkbench.tsx",
  "src/components/reader/ReaderRecordWorkbenchSurface.tsx",
  "src/components/reader/ReaderRecordWorkbenchSurface.source-navigation.test.tsx",
  "src/components/reader/ReaderNotePanel.tsx",
  "src/components/reader/plate/ReaderPlateSnapshotSurface.tsx",
  "src/components/reader/plate/ReaderPlateSnapshotSurface.test.tsx",
  "src/lib/reader-plate/projection/snapshot-to-reader-workbench.ts",
  "src/lib/reader-plate/projection/snapshot-to-reader-workbench.test.tsx",
  // Retired legacy adapters/services/types
  "src/adapters/records.adapter.ts",
  "src/adapters/records.adapter.test.ts",
  "src/services/bff/reader.ts",
  "src/services/bff/records.ts",
  "src/services/bff/reader-notes.ts",
  "src/services/bff/analysis.ts",
  "src/services/bff/analysis.test.ts",
  "src/services/bff/annotations.ts",
  "src/services/api/reader-scene.ts",
  "src/services/api/records.ts",
  "src/types/api/reader-scene.ts",
  "src/types/api/records.ts",
  "src/types/view/RecordListItemVm.ts",
  // Retired E2E harness roots, configs, setup, and fixtures
  "src/app/e2e-plate-spike",
  "src/app/e2e-plate-paste-spike",
  "playwright.ask-activity-r2.config.ts",
  "playwright.ask-process-target-r0.config.ts",
  "playwright.ask-retry-r7.config.ts",
  "playwright.e2e-spike-disabled.config.ts",
  "tests/e2e/ask-activity-r2-server-setup.ts",
  "tests/e2e/ask-retry-r7-server-setup.ts",
  "tests/e2e/gate-disabled-server-setup.ts",
  "src/lib/reader-ask/ask-activity-r2-server-setup.test.ts",
  "tests/e2e/fixtures/l1-heading-navigation-snapshot.ts",
  "tests/e2e/fixtures/semantic-outline-navigation-snapshot.ts",
  "tests/e2e/analysis-loading-state.spec.ts",
  "tests/e2e/reader-orchestration-flow.spec.ts",
  "tests/e2e/ask-chain-of-thought.spec.ts",
  "tests/e2e/ask-retry-submission-r5.spec.ts",
  "tests/e2e/ask-retry-submission-r6.spec.ts",
  "tests/e2e/ask-retry-submission-r7.spec.ts",
  "tests/e2e/ask-ux-history-cold-load.spec.ts",
  "tests/e2e/ask-ux-mobile-r3-floating-overlay.spec.ts",
  "tests/e2e/ask-ux-streaming-delta-r2.spec.ts",
  "tests/e2e/citation-ui-verify.spec.ts",
  "tests/e2e/plate-grammar-callout-state-r2-1c.spec.ts",
  "tests/e2e/plate-paste-baseline.spec.ts",
  "tests/e2e/plate-paste-spike.spec.ts",
  "tests/e2e/plate-surface-gate-disabled.spec.ts",
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
  "tests/e2e/reader-ask-web-search.spec.ts",
  "tests/e2e/reader-record-ask-agentic-activity-r2.spec.ts",
  "tests/e2e/reader-record-ask-process-target-r0.spec.ts",
  "tests/e2e/reader-record-rail-stable-progress-quiet-t5-1e.spec.ts",
  "tests/e2e/source-callout-aside.spec.ts",
] as const;

const PHYSICAL_RETAINED_PATHS = [
  "src/app/(private)/app/read/page.tsx",
  "src/app/(private)/app/reader/[recordId]/page.tsx",
  "src/app/(private)/app/reader/[recordId]/plate-page.tsx",
  "src/components/reader/AiWorkspacePanel.tsx",
  "src/components/reader/plate/ReaderRecordPlateSurface.tsx",
  "src/lib/reader-plate/projection/render-scene-to-plate-document.ts",
  "src/lib/reader-plate-snapshot/polling.ts",
  "src/lib/reader-plate-snapshot/progressive-transition.ts",
  "src/lib/reader-plate-snapshot/incremental-projection-merger.ts",
  "src/types/view/ReaderMockVm.ts",
  "src/components/product-page/hero/HeroAppStage.tsx",
  "src/services/bff/reader-plate.ts",
  "src/services/bff/reader-ask.ts",
  "src/services/bff/reading-records.ts",
  "src/services/bff/reading-record-user-assets.ts",
  "src/services/api/reader-notes.ts",
  "src/services/api/annotations.ts",
  "src/services/api/favorites.ts",
  "tests/e2e/server-setup.ts",
] as const;

describe("P-WEB Physical guard: retired clusters stay deleted", () => {
  it.each(PHYSICAL_DELETED_PATHS)("does not restore deleted path %s", (relativePath) => {
    expect(existsSync(resolve(process.cwd(), relativePath))).toBe(false);
  });

  it.each(PHYSICAL_RETAINED_PATHS)("retains production path %s", (relativePath) => {
    expect(existsSync(resolve(process.cwd(), relativePath))).toBe(true);
  });

  it("retains the current Plate/Hero projection chain", () => {
    const projectionIndex = readFileSync(
      resolve(process.cwd(), "src/lib/reader-plate/projection/index.ts"),
      "utf8",
    );
    const heroSource = readFileSync(
      resolve(process.cwd(), "src/components/product-page/hero/HeroAppStage.tsx"),
      "utf8",
    );
    expect(projectionIndex).toContain('renderSceneToPlateDocument');
    expect(projectionIndex).not.toContain('snapshot-to-reader-workbench');
    expect(heroSource).toContain('renderSceneToPlateDocument');
    expect(readFileSync(resolve(process.cwd(), "src/types/view/ReaderMockVm.ts"), "utf8")).toContain(
      "ReaderMockVm",
    );
  });

  it("canonical E2E runner has no retired spike/gate wiring", () => {
    const runnerFiles = [
      "playwright.config.ts",
      "tests/e2e/server-setup.ts",
      "next.config.ts",
      "package.json",
    ];
    const retiredMarkers = [
      "e2e-plate-spike",
      "e2e-plate-paste-spike",
      "CLAREAD_ENABLE_E2E_SPIKE",
      "CLAREAD_E2E_SPIKE_TEST",
      "CLAREAD_E2E_GATE_TEST",
      "ask-activity-r2",
      "ask-retry-r7",
      "gate-disabled",
      "chromium-spike",
    ];

    for (const relativePath of runnerFiles) {
      const source = readFileSync(resolve(process.cwd(), relativePath), "utf8");
      for (const marker of retiredMarkers) {
        expect(source, `${relativePath} contains retired marker ${marker}`).not.toContain(marker);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// 5. Reachable final Reader source closure uses canonical BFF URLs
// ---------------------------------------------------------------------------

const FINAL_READER_SOURCE_FILES = [
  "src/app/(private)/app/reader/[recordId]/page.tsx",
  "src/app/(private)/app/reader/[recordId]/plate-page.tsx",
  "src/app/(private)/app/reader/[recordId]/FavoriteButton.tsx",
  "src/components/reader/plate/ReaderRecordPlateSurface.tsx",
  "src/components/reader/AiWorkspacePanel.tsx",
  "src/lib/reader-ask/browser-paths.ts",
] as const;

const REMOVED_BFF_URL_MARKERS = [
  "/api/web/analysis",
  "/api/web/annotations",
  "/api/web/favorites",
  "/api/web/reader-ask",
  "/api/web/reader-notes",
  "/api/web/reader-plate",
  "/api/web/reading-record",
  "/api/web/reading-records",
  "/api/web/reader/records/plain-text",
  "/api/web/reader-plate/submit",
] as const;

describe("Source guard: final Reader closure has no old BFF fetches", () => {
  it.each(FINAL_READER_SOURCE_FILES)(
    "%s contains no removed BFF URL namespace",
    (relativePath) => {
      const source = readFileSync(resolve(process.cwd(), relativePath), "utf8");
      for (const marker of REMOVED_BFF_URL_MARKERS) {
        expect(source, `${relativePath} contains removed URL ${marker}`).not.toContain(marker);
      }
    },
  );
});
