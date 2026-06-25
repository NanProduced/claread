import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Reader Record boundary static guards.
 *
 * These tests lock down the current `/app/reader-record/{recordId}` boundary so
 * the new Reading Record product surface does not silently reintroduce legacy
 * scene / task / notes / highlights data paths while the RR Ask cutover keeps
 * converging.
 *
 * Matrix and rationale are documented in
 *   docs/initiatives/reader-agentic-orchestration/modules/schema-and-domain-contract.md
 *     -> D6-A0 Ask / Notes / Highlights Dependency Audit
 *   docs/initiatives/reader-agentic-orchestration/modules/cutover-and-old-workflow.md
 *     -> D6-A0 Ask / Notes / Highlights Dependency Audit 结论
 *
 * Update both docs together with these guards when an entry point is migrated.
 */

function readSource(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf-8");
}

function extractSelectionToolbarInvocation(source: string): string {
  const start = source.indexOf("<SelectionToolbar");
  expect(start).toBeGreaterThanOrEqual(0);

  const end = source.indexOf("/>", start);
  expect(end).toBeGreaterThan(start);

  return source.slice(start, end + 2);
}

function extractDisabledActionsProp(componentInvocation: string): string {
  const start = componentInvocation.indexOf("disabled={{");
  expect(start).toBeGreaterThanOrEqual(0);

  const end = componentInvocation.indexOf("}}", start);
  expect(end).toBeGreaterThan(start);

  return componentInvocation.slice(start, end + 2);
}

const LEGACY_ROUTE_HELPER = "legacyAppReaderRoute";
const LEGACY_SCENE_PATHS = ["/scene", "render_scene_json", "analysis-tasks"];
const LEGACY_WRITE_ROUTES = [
  "/api/web/reader-ask",
  "/api/web/reader-notes",
  "/api/web/reader-annotations",
];
const LEGACY_WRITE_SURFACES = ["ReaderNotePanel", "AnnotationGutter"];
const RR_ASK_SURFACE = "AiWorkspacePanel";

const READER_RECORD_PAGE =
  "src/app/(private)/app/reader-record/[recordId]/page.tsx";
const READER_RECORD_SURFACE =
  "src/components/reader/ReaderRecordWorkbenchSurface.tsx";
const READER_RECORD_ANCHOR_DRAFT =
  "src/lib/reader-plate/projection/reader-record-anchor-draft.ts";

const READER_RECORD_READ_ONLY_FILES = [
  READER_RECORD_PAGE,
  READER_RECORD_SURFACE,
  READER_RECORD_ANCHOR_DRAFT,
];

describe("reader record boundary - /app/reader-record/{recordId} page must not reference legacy ask / notes / highlights / scene paths", () => {
  it("page.tsx does not import or reference the legacy reader route helper", () => {
    const source = readSource(READER_RECORD_PAGE);

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
  });

  it("page.tsx does not reference legacy scene, render_scene_json or analysis-tasks strings", () => {
    const source = readSource(READER_RECORD_PAGE);

    for (const needle of LEGACY_SCENE_PATHS) {
      expect(source).not.toContain(needle);
    }
  });

  it("page.tsx does not reference the legacy ask / notes / annotations Web API routes", () => {
    const source = readSource(READER_RECORD_PAGE);

    for (const route of LEGACY_WRITE_ROUTES) {
      expect(source).not.toContain(route);
    }
  });

  it("page.tsx does not import any Ask / notes / highlights write surface components", () => {
    const source = readSource(READER_RECORD_PAGE);

    expect(source).not.toMatch(
      new RegExp(`from\\s+["'][^"']*${RR_ASK_SURFACE}["']`),
    );
    for (const surface of LEGACY_WRITE_SURFACES) {
      expect(source).not.toMatch(
        new RegExp(`from\\s+["'][^"']*${surface}["']`),
      );
    }
  });
});

describe("reader record boundary - ReaderRecordWorkbenchSurface must not reference legacy ask / notes / highlights / scene paths", () => {
  it("surface does not import or reference the legacy reader route helper", () => {
    const source = readSource(READER_RECORD_SURFACE);

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
  });

  it("surface does not reference legacy scene, render_scene_json or analysis-tasks strings", () => {
    const source = readSource(READER_RECORD_SURFACE);

    for (const needle of LEGACY_SCENE_PATHS) {
      expect(source).not.toContain(needle);
    }
  });

  it("surface does not reference the legacy ask / notes / annotations Web API routes", () => {
    const source = readSource(READER_RECORD_SURFACE);

    for (const route of LEGACY_WRITE_ROUTES) {
      expect(source).not.toContain(route);
    }
  });

  it("surface only imports the RR Ask surface and keeps note/highlight write surfaces out", () => {
    const source = readSource(READER_RECORD_SURFACE);

    expect(source).toMatch(
      new RegExp(`from\\s+["'][^"']*${RR_ASK_SURFACE}["']`),
    );
    for (const surface of LEGACY_WRITE_SURFACES) {
      expect(source).not.toMatch(
        new RegExp(`from\\s+["'][^"']*${surface}["']`),
      );
    }
  });

  it("surface enables RR Ask while keeping other SelectionToolbar write actions read-only", () => {
    const source = readSource(READER_RECORD_SURFACE);
    const selectionToolbar = extractSelectionToolbarInvocation(source);
    const disabledActions = extractDisabledActionsProp(selectionToolbar);

    // RR Ask is now wired, but other user-write actions remain explicitly
    // unavailable in the workbench shell until their RR persistence cutovers
    // land.
    expect(selectionToolbar).toContain("onAsk={handleAskFromSelection}");
    expect(disabledActions).not.toMatch(/\bask:\s*true/);
    expect(disabledActions).toMatch(/\bhighlight:\s*true/);
    expect(disabledActions).toMatch(/\bnote:\s*true/);
    expect(disabledActions).toMatch(/\bfeedback:\s*true/);
  });
});

describe("reader record boundary - bridge layer is not yet wired into /app/reader-record/{recordId}", () => {
  it("page.tsx does not import from the Ask bridge adapters", () => {
    const source = readSource(READER_RECORD_PAGE);

    expect(source).not.toMatch(
      /from\s+["']@\/lib\/reader-plate\/bridges\/ask\/[^"']*["']/,
    );
  });

  it("surface does not import from the Ask bridge adapters", () => {
    const source = readSource(READER_RECORD_SURFACE);

    expect(source).not.toMatch(
      /from\s+["']@\/lib\/reader-plate\/bridges\/ask\/[^"']*["']/,
    );
  });
});

describe("reader record boundary - D6-A1 anchor draft helper remains read-only", () => {
  it("anchor draft helper does not reference legacy route, scene or write routes", () => {
    const source = readSource(READER_RECORD_ANCHOR_DRAFT);

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
    for (const needle of LEGACY_SCENE_PATHS) {
      expect(source).not.toContain(needle);
    }
    for (const route of LEGACY_WRITE_ROUTES) {
      expect(source).not.toContain(route);
    }
  });

  it("anchor draft helper does not import Ask bridge adapters or write surfaces", () => {
    const source = readSource(READER_RECORD_ANCHOR_DRAFT);

    expect(source).not.toMatch(
      /from\s+["']@\/lib\/reader-plate\/bridges\/ask\/[^"']*["']/,
    );
    expect(source).not.toMatch(
      new RegExp(`from\\s+["'][^"']*${RR_ASK_SURFACE}["']`),
    );
    for (const surface of LEGACY_WRITE_SURFACES) {
      expect(source).not.toMatch(
        new RegExp(`from\\s+["'][^"']*${surface}["']`),
      );
    }
  });

  it("all read-only reader record entry files avoid legacy scene and write routes", () => {
    for (const file of READER_RECORD_READ_ONLY_FILES) {
      const source = readSource(file);

      expect(source).not.toContain(LEGACY_ROUTE_HELPER);
      for (const needle of LEGACY_SCENE_PATHS) {
        expect(source).not.toContain(needle);
      }
      for (const route of LEGACY_WRITE_ROUTES) {
        expect(source).not.toContain(route);
      }
    }
  });
});

// Note: the Python-side static checks for `user_editorial_assets` schema-only
// status and `reader_orchestration` non-import of `reader_ask` live in
// services/api/tests/test_d6_a0_static_boundary.py.
