/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OutlineItem,
  ReaderOutlineViewModel,
} from "@/lib/reader-plate/projection/reader-outline-view";
import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";

// The rail computes its outline via projectReaderOutlineView. To exercise a
// sourceKind switch at the DOM level (semantic ↔ Markdown sharing one
// base_id:generation) without implementing a Markdown parser, we mock that one
// builder to return a model we control per render. Everything else the rail
// imports from the module (the scope-key helper, the scroll-spy selector) stays
// real — proving the rail reacts to sourceKind purely through the scope key.
const modelRef = vi.hoisted(() => ({
  current: null as ReaderOutlineViewModel | null,
}));

vi.mock(
  "@/lib/reader-plate/projection/reader-outline-view",
  async (importOriginal) => {
    const actual = await importOriginal<
      typeof import("@/lib/reader-plate/projection/reader-outline-view")
    >();
    return {
      ...actual,
      projectReaderOutlineView: () => modelRef.current,
    };
  },
);

import { ReaderRecordNavigationRail } from "./ReaderRecordNavigationRail";

function setRectTop(element: HTMLElement, top: number, height = 20) {
  element.getBoundingClientRect = () => ({
    top,
    left: 0,
    right: 0,
    bottom: top + height,
    width: 0,
    height,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
}

function buildTargets(): { u1: HTMLElement; u2: HTMLElement } {
  const body = document.createElement("div");
  body.className = "reader-record-plate-document";
  const u1 = document.createElement("p");
  u1.setAttribute("data-reader-record-node", "paragraph");
  u1.setAttribute("data-unit-id", "u1");
  u1.setAttribute("data-reader-record-unit-start", "true");
  setRectTop(u1, 20, 100);
  const u2 = document.createElement("p");
  u2.setAttribute("data-reader-record-node", "paragraph");
  u2.setAttribute("data-unit-id", "u2");
  u2.setAttribute("data-reader-record-unit-start", "true");
  setRectTop(u2, 400, 100);
  body.appendChild(u1);
  body.appendChild(u2);
  document.body.appendChild(body);
  return { u1, u2 };
}

let snapshotSeq = 0;
function snap(): ReaderPlateSnapshotDto {
  snapshotSeq += 1;
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: `snap_${snapshotSeq}`,
    snapshot_taken_at: "2026-07-21T00:00:00Z",
    last_event_sequence: 1,
    record_id: "record_1",
    record: {
      title: "T",
      display_title_zh: null,
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "beginner_reading",
      created_at: "2026-07-21T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "shared",
      content_sha256: "sha",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 10,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: { units: [] },
    anchor_segments: [],
    enhancement_layers: [],
    enhancement_progress: { overall_status: "ready", layers: [] },
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
    semantic_outline: null,
  };
}

const plateDocument = {} as unknown as ReaderRecordPlateDocument;

function mkModel(
  sourceKind: ReaderOutlineViewModel["identity"]["sourceKind"],
  keys: [string, string],
): ReaderOutlineViewModel {
  const items: OutlineItem[] = keys.map((key, i) => ({
    key,
    parentKey: null,
    depth: 1,
    title: key,
    target: { unitId: i === 0 ? "u1" : "u2", anchorSegmentId: null },
    coverage: {
      startUnitId: i === 0 ? "u1" : "u2",
      endUnitId: i === 0 ? "u1" : "u2",
    },
    orderIndex: i + 1,
    fallbackIndex: i,
    role: "section",
  }));
  return {
    available: true,
    status: "ready",
    isPartial: false,
    identity: { sourceKind, sourceIdentityKey: "shared:1", revision: "r1" },
    panelItems: items,
    tickItems: items,
    orderedUnitIds: ["u1", "u2"],
    unitOrderById: new Map<string, number>([
      ["u1", 1],
      ["u2", 2],
    ]),
  };
}

beforeEach(() => {
  vi.stubGlobal(
    "IntersectionObserver",
    class IntersectionObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal("scrollY", 0);
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  modelRef.current = null;
});

describe("ReaderRecordNavigationRail source isolation (semantic<->markdown, shared base:generation)", () => {
  it("fully resets state and closes the panel on a source-kind switch (both directions)", async () => {
    buildTargets();
    const semantic = mkModel("semantic", ["s1", "s2"]);
    const markdown = mkModel("markdown", ["m1", "m2"]);

    modelRef.current = semantic;
    const { rerender } = render(
      <ReaderRecordNavigationRail snapshot={snap()} plateDocument={plateDocument} />,
    );

    // Open + activate a row under the semantic source.
    fireEvent.click(screen.getByTestId("reader-record-outline-trigger"));
    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-navigation-panel")
          .getAttribute("aria-hidden"),
      ).toBe("false"),
    );
    fireEvent.click(screen.getByTestId("reader-record-outline-node-s2"));
    expect(
      screen
        .getByTestId("reader-record-outline-node-s2")
        .getAttribute("aria-current"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("reader-record-navigation-rail")
        .getAttribute("data-outline-source"),
    ).toBe("semantic");

    // semantic → markdown (SAME sourceIdentityKey): full reset, panel closed,
    // old rows gone, scroll-spy fence tracks the markdown identity.
    modelRef.current = markdown;
    rerender(
      <ReaderRecordNavigationRail snapshot={snap()} plateDocument={plateDocument} />,
    );
    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-navigation-rail")
          .getAttribute("data-outline-source"),
      ).toBe("markdown"),
    );
    expect(screen.queryByTestId("reader-record-outline-node-s2")).toBeNull();
    expect(screen.getByTestId("reader-record-outline-node-m1")).toBeTruthy();
    expect(
      screen
        .getByTestId("reader-record-navigation-panel")
        .getAttribute("aria-hidden"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("reader-record-outline-trigger")
        .getAttribute("aria-expanded"),
    ).toBe("false");
    // The spy re-derives an active row for the new source (u1 is above safeTop).
    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-outline-node-m1")
          .getAttribute("aria-current"),
      ).toBe("true"),
    );

    // markdown → semantic (same key): resets again, panel stays closed.
    modelRef.current = semantic;
    rerender(
      <ReaderRecordNavigationRail snapshot={snap()} plateDocument={plateDocument} />,
    );
    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-navigation-rail")
          .getAttribute("data-outline-source"),
      ).toBe("semantic"),
    );
    expect(screen.queryByTestId("reader-record-outline-node-m1")).toBeNull();
    expect(
      screen
        .getByTestId("reader-record-navigation-panel")
        .getAttribute("aria-hidden"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("reader-record-outline-trigger")
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });
});
