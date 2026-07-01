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
const READER_RECORD_WORKBENCH_SURFACE =
  "src/components/reader/ReaderRecordWorkbenchSurface.tsx";
const READER_RECORD_PLATE_SURFACE =
  "src/components/reader/plate/ReaderRecordPlateSurface.tsx";
const INLINE_COMMENT_PANEL =
  "src/components/reader/plate/InlineCommentPanel.tsx";
const READER_RECORD_ANCHOR_DRAFT =
  "src/lib/reader-plate/projection/reader-record-anchor-draft.ts";

const READER_RECORD_ENTRY_FILES = [
  READER_RECORD_PAGE,
  READER_RECORD_WORKBENCH_SURFACE,
  READER_RECORD_PLATE_SURFACE,
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
    const source = readSource(READER_RECORD_WORKBENCH_SURFACE);

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
  });

  it("surface does not reference legacy scene, render_scene_json or analysis-tasks strings", () => {
    const source = readSource(READER_RECORD_WORKBENCH_SURFACE);

    for (const needle of LEGACY_SCENE_PATHS) {
      expect(source).not.toContain(needle);
    }
  });

  it("surface does not reference the legacy ask / notes / annotations Web API routes", () => {
    const source = readSource(READER_RECORD_WORKBENCH_SURFACE);

    for (const route of LEGACY_WRITE_ROUTES) {
      expect(source).not.toContain(route);
    }
  });

  it("surface only imports the RR Ask surface and keeps note/highlight write surfaces out", () => {
    const source = readSource(READER_RECORD_WORKBENCH_SURFACE);

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
    const source = readSource(READER_RECORD_WORKBENCH_SURFACE);
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

describe("reader record boundary - ReaderRecordPlateSurface must not reference legacy ask / notes / highlights / scene paths", () => {
  it("plate surface does not import or reference the legacy reader route helper", () => {
    const source = readSource(READER_RECORD_PLATE_SURFACE);

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
  });

  it("plate surface does not reference legacy scene, render_scene_json or analysis-tasks strings", () => {
    const source = readSource(READER_RECORD_PLATE_SURFACE);

    for (const needle of LEGACY_SCENE_PATHS) {
      expect(source).not.toContain(needle);
    }
  });

  it("plate surface only imports the RR Ask surface and keeps legacy note/highlight write surfaces out", () => {
    const source = readSource(READER_RECORD_PLATE_SURFACE);

    expect(source).toMatch(
      new RegExp(`from\\s+["'][^"']*${RR_ASK_SURFACE}["']`),
    );
    for (const surface of LEGACY_WRITE_SURFACES) {
      expect(source).not.toMatch(
        new RegExp(`from\\s+["'][^"']*${surface}["']`),
      );
    }
    expect(source).not.toContain("/api/web/reader-notes");
    expect(source).not.toContain("/api/web/reader-annotations");
    expect(source).not.toContain("/api/web/annotations");
  });

  it("plate surface enables RR Ask, highlight and note while keeping feedback unavailable", () => {
    const source = readSource(READER_RECORD_PLATE_SURFACE);

    // The plate surface wires a ReaderToolbarActionsProvider that exposes
    // Ask, highlight, note and lookup actions for the floating toolbar.
    expect(source).toContain("ReaderToolbarActionsProvider");
    expect(source).toContain("onAsk: () => handleAskFromSelection()");
    expect(source).toContain("onHighlight: () => handleHighlight()");
    expect(source).toContain("onNote: () => handleOpenNoteComposer()");
    expect(source).toContain("onLookup: () => handleLookup()");

    // Feedback stays out of the selection toolbar actions: there is no
    // onFeedback callback wired into the toolbarActions memo.
    expect(source).not.toMatch(/\bonFeedback:\s*\(\)\s*=>/);
    // The read-only selection state is still rendered, but feedback is not
    // surfaced as a toolbar action.
    expect(source).toContain('data-reader-record-actions="selection-state"');
    expect(source).not.toContain(
      'data-reader-record-coming-soon-actions="feedback"',
    );
  });

  it("plate surface delegates the note-level RR Ask entry to InlineCommentPanel without importing legacy note panels", () => {
    const plateSurfaceSource = readSource(READER_RECORD_PLATE_SURFACE);
    const inlineCommentPanelSource = readSource(INLINE_COMMENT_PANEL);

    // Plate surface wires InlineCommentPanel (CommentKit activeId driven)
    // instead of rendering the note action strip inline.
    expect(plateSurfaceSource).toContain("InlineCommentPanel");
    // The note-level Ask action button now lives in InlineCommentPanel.
    expect(inlineCommentPanelSource).toContain(
      'data-reader-record-note-action="ask"',
    );
    // Neither file may import the legacy ReaderNotePanel.
    expect(plateSurfaceSource).not.toContain("ReaderNotePanel");
    expect(inlineCommentPanelSource).not.toContain("ReaderNotePanel");
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
    const source = readSource(READER_RECORD_WORKBENCH_SURFACE);

    expect(source).not.toMatch(
      /from\s+["']@\/lib\/reader-plate\/bridges\/ask\/[^"']*["']/,
    );
  });

  it("plate surface does not import from the Ask bridge adapters directly", () => {
    const source = readSource(READER_RECORD_PLATE_SURFACE);

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

  it("all reader record entry files avoid legacy scene and direct legacy write routes", () => {
    for (const file of READER_RECORD_ENTRY_FILES) {
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
