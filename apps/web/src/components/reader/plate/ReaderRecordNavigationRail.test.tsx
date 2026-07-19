/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
  type ReaderUnitType,
} from "@/types/api/reader-plate";
import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  type ReaderRecordPlateDocument,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

import { ReaderRecordNavigationRail } from "./ReaderRecordNavigationRail";

type SnapshotUnitInput = {
  unit_id: string;
  order_index: number;
  label?: string | null;
  unit_type?: ReaderUnitType;
};

function makeParagraph(
  unitId: string,
  text: string,
  isUnitStart = false,
): ReaderRecordPlateDocument["children"][number] {
  return {
    type: "paragraph",
    id: `p-${unitId}`,
    children: [
      {
        text,
        owner: "stable",
        lockSource: true,
        sourceRole: "segment_text",
        baseRange: { startUtf16: 0, endUtf16: text.length },
        marks: [],
      },
    ],
    data: {
      anchorSegmentId: `seg-${unitId}`,
      coveredAnchorSegmentIds: [`seg-${unitId}`],
      sentenceId: `sent-${unitId}`,
      unitId,
      isUnitStart,
      baseId: "base_1",
      baseRange: { startUtf16: 0, endUtf16: text.length },
      unitRange: { startUtf16: 0, endUtf16: text.length },
      textHash: "hash",
      hashAlgorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      segmentType: "sentence",
      boundaryQuality: "normal",
    },
  };
}

function makeSnapshot(
  units: SnapshotUnitInput[],
  options?: {
    baseId?: string;
    generation?: number;
    semantic_outline?: ReaderPlateSnapshotDto["semantic_outline"];
  },
): ReaderPlateSnapshotDto {
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "snap_1",
    snapshot_taken_at: "2024-01-01T00:00:00Z",
    last_event_sequence: 1,
    record_id: "record_1",
    record: {
      title: "Title",
      display_title_zh: "中文标题",
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "beginner_reading",
      created_at: "2024-01-01T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      generation: options?.generation ?? 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: options?.baseId ?? "base_1",
      content_sha256: "sha256",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 100,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: {
      units: units.map((u) => ({
        unit_id: u.unit_id,
        order_index: u.order_index,
        label: u.label,
        unit_type: u.unit_type ?? "body",
        boundary_quality: "normal" as const,
        base_start_utf16: 0,
        base_end_utf16: 10,
        text_hash: "hash",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      })),
    },
    anchor_segments: [],
    enhancement_layers: [],
    enhancement_progress: {
      overall_status: "ready",
      layers: [],
    },
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
    semantic_outline: options?.semantic_outline,
  };
}

function makeOutlineDto(
  overrides?: Partial<
    NonNullable<ReaderPlateSnapshotDto["semantic_outline"]>
  >,
  nodes?: NonNullable<
    NonNullable<ReaderPlateSnapshotDto["semantic_outline"]>["nodes"]
  >,
): NonNullable<ReaderPlateSnapshotDto["semantic_outline"]> {
  return {
    schema_kind: "reader_semantic_outline",
    schema_version: 1,
    status: "ready",
    source_identity: { base_id: "base_1", generation: 1 },
    publication: {
      outline_revision: "rev_1",
      layer_id: "layer_ol",
      published_at: "2026-07-17T00:00:00Z",
    },
    provenance: { kind: "llm", builder: "test", model: "m" },
    nodes: nodes ?? [
      {
        node_id: "n1",
        parent_node_id: null,
        depth: 1,
        title: "Root A",
        start_unit_id: "unit_1",
        end_unit_id: "unit_2",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        order_index: 1,
      },
      {
        node_id: "n2",
        parent_node_id: "n1",
        depth: 2,
        title: "Child",
        start_unit_id: "unit_2",
        end_unit_id: "unit_2",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        order_index: 2,
      },
      {
        node_id: "n3",
        parent_node_id: null,
        depth: 1,
        title: "Root B",
        start_unit_id: "unit_3",
        end_unit_id: "unit_3",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        order_index: 3,
      },
    ],
    diagnostics: { drops: [], skipped_node_count: 0 },
    ...overrides,
  };
}

/** unit_count>=6 && heading_count>=2 with lead body. */
function headingRichUnits(): SnapshotUnitInput[] {
  return [
    { unit_id: "u1", order_index: 1, unit_type: "body", label: null },
    { unit_id: "u2", order_index: 2, unit_type: "heading", label: "Chapter One" },
    { unit_id: "u3", order_index: 3, unit_type: "body", label: null },
    { unit_id: "u4", order_index: 4, unit_type: "body", label: null },
    { unit_id: "u5", order_index: 5, unit_type: "heading", label: "Chapter Two" },
    { unit_id: "u6", order_index: 6, unit_type: "body", label: null },
    { unit_id: "u7", order_index: 7, unit_type: "body", label: null },
  ];
}

function plateFromUnits(units: SnapshotUnitInput[]): ReaderRecordPlateDocument {
  return makePlateDocument(
    units.map((u) =>
      makeParagraph(u.unit_id, u.label ?? `Paragraph for ${u.unit_id}.`, true),
    ),
  );
}

function makePlateDocument(
  paragraphs: ReaderRecordPlateDocument["children"],
): ReaderRecordPlateDocument {
  return {
    type: "reader_record_plate_document",
    schemaVersion: READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
    record: {
      recordId: "record_1",
      title: "Title",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "snap_1",
      snapshotTakenAt: "2024-01-01T00:00:00Z",
      lastEventSequence: 1,
    },
    base: {
      baseId: "base_1",
      contentSha256: "sha256",
      textLengthUtf16: 100,
      hashAlgorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    progress: {
      overallStatus: "ready",
      layers: [],
    },
    children: paragraphs,
  };
}

function setRectTop(element: HTMLElement, top: number, height = 20) {
  element.getBoundingClientRect = () => ({
    top,
    left: 0,
    right: 0,
    bottom: top + height,
    width: 0,
    height,
    x: 0,
    y: top,
    toJSON: () => ({}),
  });
}

interface RenderedTarget {
  body: HTMLDivElement;
  paragraphs: HTMLParagraphElement[];
}

function renderTargets(unitIds: string[], tops?: number[]): RenderedTarget {
  const body = document.createElement("div");
  body.className = "reader-record-plate-document";
  const paragraphs: HTMLParagraphElement[] = [];

  for (let i = 0; i < unitIds.length; i++) {
    const unitId = unitIds[i];
    const el = document.createElement("p");
    el.setAttribute("data-reader-record-node", "paragraph");
    el.setAttribute("data-unit-id", unitId);
    if (i === 0) {
      el.setAttribute("data-reader-record-unit-start", "true");
    }
    el.textContent = `Paragraph for ${unitId}`;
    setRectTop(el, tops?.[i] ?? i * 200, 100);
    body.appendChild(el);
    paragraphs.push(el);
  }

  document.body.appendChild(body);
  return { body, paragraphs };
}

function triggerScroll() {
  window.dispatchEvent(new Event("scroll"));
}

/** Hover a visual tick to open the panel (simulates mouse hover on the rail). */
function hoverTick(index = 0) {
  const miniRail = screen.getByTestId("reader-record-mini-rail");
  // Prefer surface-agnostic tick key (works for deterministic unit + semantic node).
  const ticks = miniRail.querySelectorAll("span[data-navigation-tick-key]");
  fireEvent.mouseEnter(ticks[index]!);
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
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("ReaderRecordNavigationRail", () => {
  it("does not render when there are no navigation units", () => {
    const snapshot = makeSnapshot([]);
    const plateDocument = makePlateDocument([]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    expect(screen.queryByTestId("reader-record-navigation-rail")).toBeNull();
  });

  it("renders the accessible trigger button and visual ticks for navigation items", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "First unit" },
      { unit_id: "unit_2", order_index: 1, label: "Second unit" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "First paragraph."),
      makeParagraph("unit_2", "Second paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail).toBeTruthy();
    // Visual ticks are spans, not buttons.
    expect(rail.querySelectorAll("span[data-navigation-unit-id]")).toHaveLength(2);
    // Accessible trigger button exists.
    expect(screen.getByTestId("reader-record-outline-trigger")).toBeTruthy();
  });

  it("marks the first item active by default via the trigger aria-label", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "First paragraph."),
      makeParagraph("unit_2", "Second paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    // Active unit is 1 (first item); L0 terminology uses 段落, not 节/目录.
    expect(trigger.getAttribute("aria-label")).toBe("打开段落导航，当前第 1 段");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("opens the detail panel on hover and closes after leaving the combined area", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.classList.contains("invisible")).toBe(true);

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(panel.classList.contains("visible")).toBe(true);

    fireEvent.mouseLeave(rail);
    await waitFor(
      () => expect(panel.classList.contains("pointer-events-none")).toBe(true),
      { timeout: 300 },
    );
    expect(panel.classList.contains("invisible")).toBe(true);
  });

  it("opens the panel via trigger button click (keyboard/touch entry)", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    expect(panel.classList.contains("pointer-events-none")).toBe(true);

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(trigger.getAttribute("aria-label")).toBe("关闭段落导航，当前第 1 段");
  });

  it("closes the panel when trigger is clicked again (toggle)", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(true),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("does not open the detail panel from the nav root or hidden panel geometry", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    fireEvent.mouseEnter(rail);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.classList.contains("invisible")).toBe(true);

    fireEvent.mouseEnter(panel);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.classList.contains("invisible")).toBe(true);

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
  });

  it("makes panel rows non-tabbable while the panel is closed", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const rows = panel.querySelectorAll("button");
    expect(rows).toHaveLength(2);
    // All rows are -1 when closed (no tab stop).
    expect(rows[0]?.getAttribute("tabindex")).toBe("-1");
    expect(rows[1]?.getAttribute("tabindex")).toBe("-1");
  });

  it("uses roving tabindex: only the focused row is tabbable when panel is open", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const rows = panel.querySelectorAll("button");

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    // Only the focused (active) row has tabIndex=0, others are -1.
    const tabbable = Array.from(rows).filter(
      (r) => r.getAttribute("tabindex") === "0",
    );
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0]?.getAttribute("aria-current")).toBe("true");
  });

  it("keeps the panel open when pointer moves from ticks into the detail panel", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    fireEvent.mouseLeave(rail, { relatedTarget: panel });
    fireEvent.mouseEnter(panel);

    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(panel.classList.contains("pointer-events-none")).toBe(false);
  });

  it("closes the panel only after leaving the combined rail and panel area", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    fireEvent.mouseLeave(rail, { relatedTarget: panel });
    fireEvent.mouseEnter(panel);
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(panel.classList.contains("pointer-events-none")).toBe(false);

    fireEvent.mouseLeave(panel);
    await waitFor(
      () => expect(panel.classList.contains("pointer-events-none")).toBe(true),
      { timeout: 300 },
    );
  });

  it("positions the mini rail as a centered viewport affordance", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const className = rail.className;

    expect(className).not.toContain("top-14");
    expect(className).not.toContain("bottom-24");
    expect(className).toContain("top-1/2");
    expect(className).toContain("-translate-y-1/2");
    expect(className).toContain("h-[min(72vh,42rem)]");
  });

  it("renders panel rows with labels and segment indices", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const rows = panel.querySelectorAll("button");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    expect(screen.getByText("第 1 段")).toBeTruthy();
    expect(screen.getByText("第 2 段")).toBeTruthy();
  });

  it("lets the detail panel size to its rows instead of inheriting the full tick rail height", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const panelSurface = panel.firstElementChild as HTMLElement | null;
    const panelScrollArea = panelSurface?.firstElementChild as HTMLElement | null;

    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.className).toContain("max-h-[min(72vh,42rem)]");
    expect(panel.className).not.toContain("h-full");
    expect(panelSurface?.className).toContain("max-h-[min(72vh,42rem)]");
    expect(panelSurface?.className).not.toContain("h-full");
    expect(panelScrollArea?.className).toContain("overflow-y-auto");
    expect(panelScrollArea?.className).not.toContain("flex-1");
  });

  it("keeps the detail panel at a stable vertical position across different hovered ticks", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
      { unit_id: "unit_3", order_index: 2, label: "Gamma" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
      makeParagraph("unit_3", "Gamma paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2", "unit_3"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll<HTMLSpanElement>("span[data-navigation-unit-id]");
    setRectTop(rail, 100, 420);
    setRectTop(ticks[0]!, 250, 20);
    setRectTop(ticks[1]!, 430, 20);

    fireEvent.mouseEnter(ticks[0]!);
    await waitFor(() => {
      expect(panel.classList.contains("pointer-events-none")).toBe(false);
    });
    const firstTop = panel.style.top;
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();

    fireEvent.mouseEnter(ticks[1]!);
    await waitFor(() => {
      expect(panel.classList.contains("pointer-events-none")).toBe(false);
    });
    expect(panel.style.top).toBe(firstTop);
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();
  });

  it("keeps the canvas detail panel at a stable vertical position", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
      { unit_id: "unit_3", order_index: 2, label: "Gamma" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
      makeParagraph("unit_3", "Gamma paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2", "unit_3"]);

    render(
      <ReaderRecordNavigationRail
        snapshot={snapshot}
        plateDocument={plateDocument}
        layout="canvas"
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll<HTMLSpanElement>("span[data-navigation-unit-id]");
    setRectTop(rail, 100, 420);
    setRectTop(ticks[2]!, 430, 20);

    fireEvent.mouseEnter(ticks[2]!);
    await waitFor(() => {
      expect(panel.classList.contains("pointer-events-none")).toBe(false);
    });

    expect(panel.className).toContain("right-[calc(100%+8px)]");
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();
    expect(panel.style.top).toBe("");
  });

  it("scrolls the unit start target into view using window.scrollTo when a panel row is clicked", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    const { paragraphs } = renderTargets(["unit_1"]);
    setRectTop(paragraphs[0]!, 500, 100);
    vi.stubGlobal("scrollY", 120);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    // Open panel and click the row.
    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const panelRow = panel.querySelector("button")!;
    fireEvent.click(panelRow);

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 500 + 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("scrolls the nearest scrollable ancestor instead of window when content lives in a ScrollArea", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);

    const container = document.createElement("div");
    container.style.height = "500px";
    container.style.overflowY = "auto";
    container.style.position = "relative";

    const body = document.createElement("div");
    body.className = "reader-record-plate-document";
    const paragraph = document.createElement("p");
    paragraph.setAttribute("data-reader-record-node", "paragraph");
    paragraph.setAttribute("data-unit-id", "unit_1");
    paragraph.setAttribute("data-reader-record-unit-start", "true");
    paragraph.textContent = "Alpha paragraph";
    setRectTop(paragraph, 800, 100);
    body.appendChild(paragraph);
    container.appendChild(body);
    document.body.appendChild(container);

    const containerScrollTo = vi.fn();
    container.scrollTo = containerScrollTo;
    container.scrollTop = 120;

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    // Open panel and click row.
    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const panelRow = panel.querySelector("button")!;
    fireEvent.click(panelRow);

    expect(window.scrollTo).not.toHaveBeenCalled();
    expect(containerScrollTo).toHaveBeenCalledWith({
      top: 800 + 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("activates the clicked row immediately and marks it active", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const panelRow = panel.querySelector("button")!;
    fireEvent.click(panelRow);

    expect(window.scrollTo).toHaveBeenCalled();
    expect(panelRow.getAttribute("aria-current")).toBe("true");
  });

  it("does not pick the rail tick as target when it shares a unit id with a body paragraph", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const panelRow = panel.querySelector("button")!;
    fireEvent.click(panelRow);

    const scrollToArg = (window.scrollTo as ReturnType<typeof vi.fn>).mock.calls[0]?.[0];
    expect(scrollToArg).toBeDefined();
    // Paragraph at top 0, scrollY 0 -> raw offset -64, clamped to 0.
    expect(scrollToArg.top).toBe(0);
  });

  it("keeps the clicked item active during the scroll lock even if scroll fires", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    const { paragraphs } = renderTargets(["unit_1", "unit_2"], [60, 200]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    // Open panel and click Beta.
    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");
    fireEvent.click(rows[1]!);
    expect(rows[1]?.getAttribute("aria-current")).toBe("true");

    // Simulate smooth-scroll progress: Alpha above the safe line, Beta below.
    setRectTop(paragraphs[0]!, -10, 100);
    setRectTop(paragraphs[1]!, 100, 100);
    triggerScroll();

    // Wait within the lock window.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(rows[1]?.getAttribute("aria-current")).toBe("true");

    // After the lock expires, the deterministic algorithm picks Alpha.
    await new Promise((resolve) => setTimeout(resolve, 600));
    triggerScroll();
    await waitFor(() =>
      expect(rows[0]?.getAttribute("aria-current")).toBe("true"),
    );
  });

  it("computes active unit with a deterministic nearest-start algorithm", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
      { unit_id: "unit_3", order_index: 2, label: "Gamma" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
      makeParagraph("unit_3", "Gamma paragraph."),
    ]);
    const { paragraphs } = renderTargets(["unit_1", "unit_2", "unit_3"], [100, 300, 500]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    triggerScroll();
    await waitFor(() => {
      const trigger = screen.getByTestId("reader-record-outline-trigger");
      expect(trigger.getAttribute("aria-label")).toBe("打开段落导航，当前第 1 段");
    });

    // Move Beta above the safe line; it becomes the last-above active unit.
    setRectTop(paragraphs[0]!, -20, 100);
    setRectTop(paragraphs[1]!, 40, 100);
    setRectTop(paragraphs[2]!, 200, 100);
    triggerScroll();
    await waitFor(() => {
      const trigger = screen.getByTestId("reader-record-outline-trigger");
      expect(trigger.getAttribute("aria-label")).toBe("打开段落导航，当前第 2 段");
    });
  });

  it("falls back to any paragraph with the unit id when no unit start marker exists", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);

    const body = document.createElement("div");
    body.className = "reader-record-plate-document";
    const paragraph = document.createElement("p");
    paragraph.setAttribute("data-reader-record-node", "paragraph");
    paragraph.setAttribute("data-unit-id", "unit_1");
    // No data-reader-record-unit-start attribute.
    paragraph.textContent = "Fallback paragraph";
    setRectTop(paragraph, 120, 100);
    body.appendChild(paragraph);
    document.body.appendChild(body);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const panelRow = panel.querySelector("button")!;
    fireEvent.click(panelRow);

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("scrolls the unit target when a panel row is activated by keyboard (Enter)", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    const { paragraphs } = renderTargets(["unit_1"]);
    setRectTop(paragraphs[0]!, 80, 100);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const panelRow = panel.querySelector("button")!;
    fireEvent.keyDown(panelRow, { key: "Enter" });

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 80 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("applies the ask-open shift class when askOpen is true", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(
      <ReaderRecordNavigationRail
        snapshot={snapshot}
        plateDocument={plateDocument}
        askOpen
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.className).toContain("2xl:right-[clamp");
  });

  it("uses nav semantics with a trigger button and aria-hidden visual ticks", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.tagName.toLowerCase()).toBe("nav");
    expect(rail.getAttribute("aria-label")).toBe("阅读定位");

    // Trigger is a button with proper aria; L0 terminology only (no 文章目录/大纲/节).
    const trigger = screen.getByTestId("reader-record-outline-trigger");
    expect(trigger.tagName.toLowerCase()).toBe("button");
    expect(trigger.getAttribute("aria-label")).toBe("打开段落导航，当前第 1 段");
    expect(trigger.getAttribute("aria-label")).not.toMatch(/文章目录|大纲|第 \d+ 节/);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    // T5.5a: no menu popup semantics; panel is controlled via aria-controls.
    expect(trigger.getAttribute("aria-haspopup")).toBeNull();
    expect(trigger.getAttribute("aria-controls")).toBeTruthy();

    // Visual ticks are aria-hidden spans.
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    expect(miniRail.getAttribute("aria-hidden")).toBe("true");
    const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
    expect(ticks).toHaveLength(1);
    // Ticks are spans, not buttons.
    expect(ticks[0]?.tagName.toLowerCase()).toBe("span");
  });

  it("renders visual ticks with a separate visual bar span", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
    expect(ticks).toHaveLength(2);

    const hitArea = ticks[0]!;
    expect(hitArea.className).toContain("min-h-[7px]");
    expect(hitArea.className).toContain("flex-1");
    expect(hitArea.className).toContain("max-h-4");
    expect(hitArea.className).toContain("w-10");

    const visualBar = hitArea.querySelector("span");
    expect(visualBar).toBeTruthy();
    expect(visualBar?.className).toContain("h-[1.5px]");
    expect(visualBar?.className).toContain("rounded-full");
  });

  it("keeps tick visuals available while the detail panel is open", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    const visualBars = Array.from(miniRail.querySelectorAll("span[data-navigation-unit-id] > span"));
    expect(visualBars).toHaveLength(2);
    expect(visualBars.every((bar) => bar.className.includes("opacity-0"))).toBe(
      false,
    );

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(visualBars.every((bar) => bar.className.includes("opacity-0"))).toBe(
      false,
    );
  });

  it("styles the active panel row with background and weight instead of a side stripe", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");
    expect(rows).toHaveLength(2);

    const activeRow = rows[0]!;
    expect(activeRow.className).not.toContain("border-l-");
    expect(activeRow.className).toContain("bg-[var(--app-control-current)]");
    expect(activeRow.className).toContain("font-medium");

    const inactiveRow = rows[1]!;
    expect(inactiveRow.className).not.toContain("border-l-");
    expect(inactiveRow.className).toContain("text-ink/60");
  });

  it("uses viewport fixed positioning by default", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.dataset.layout).toBe("viewport");
    expect(rail.className).toContain("fixed");
    expect(rail.className).toContain("right-3");
  });

  it("switches to canvas layout when layout prop is canvas", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(
      <ReaderRecordNavigationRail
        snapshot={snapshot}
        plateDocument={plateDocument}
        layout="canvas"
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.dataset.layout).toBe("canvas");
    expect(rail.className).toContain("reader-record-navigation-rail--canvas");
    expect(rail.className).toContain("absolute");
    expect(rail.className).toContain("right-0");
    expect(rail.className).toContain("top-1/2");
    expect(rail.className).toContain("h-[min(72vh,42rem)]");
    expect(rail.className).toContain("w-full");
    expect(rail.className).not.toContain("sticky");
    expect(rail.className).not.toContain("fixed");
    expect(rail.className).not.toContain("right-3");
  });

  it("does not apply the viewport ask-open clamp in canvas layout", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(
      <ReaderRecordNavigationRail
        snapshot={snapshot}
        plateDocument={plateDocument}
        layout="canvas"
        askOpen
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.className).not.toContain("2xl:right-[clamp");
  });

  it("anchors canvas panel and ticks inside the outline slot area", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(
      <ReaderRecordNavigationRail
        snapshot={snapshot}
        plateDocument={plateDocument}
        layout="canvas"
      />,
    );

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const ticks = screen.getByTestId("reader-record-navigation-rail").querySelector(
      '[data-navigation-unit-id]',
    )?.parentElement;

    expect(panel.className).toContain("reader-record-navigation-panel");
    expect(panel.getAttribute("data-reader-record-navigation-panel")).toBe("true");

    expect(panel.className).toContain("z-10");
    expect(panel.className).toContain("origin-right");
    expect(ticks?.className).not.toContain("absolute");
    expect(screen.getByTestId("reader-record-navigation-rail").className).toContain("right-0");
  });

  it("applies hover state to the whole mini tick span, not only the inner bar", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    // The first tick is active by default; test the inactive second tick.
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
    const secondTick = ticks[1]!;
    expect(secondTick.className).toContain("group");
    expect(secondTick.querySelector("span")?.className).toContain("group-hover:bg-ink/40");
  });

  // --- New: keyboard navigation tests ---

  it("supports ArrowDown/ArrowUp to move roving tabindex in the panel", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
      { unit_id: "unit_3", order_index: 2, label: "Gamma" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
      makeParagraph("unit_3", "Gamma paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2", "unit_3"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");
    // Initially, the active (first) row is tabbable.
    expect(rows[0]?.getAttribute("tabindex")).toBe("0");
    expect(rows[1]?.getAttribute("tabindex")).toBe("-1");

    // ArrowDown moves focus to the second row.
    fireEvent.keyDown(rows[0]!, { key: "ArrowDown" });
    await waitFor(() => {
      expect(rows[0]?.getAttribute("tabindex")).toBe("-1");
      expect(rows[1]?.getAttribute("tabindex")).toBe("0");
    });

    // ArrowDown again moves to the third row.
    fireEvent.keyDown(rows[1]!, { key: "ArrowDown" });
    await waitFor(() => {
      expect(rows[1]?.getAttribute("tabindex")).toBe("-1");
      expect(rows[2]?.getAttribute("tabindex")).toBe("0");
    });

    // ArrowUp moves back to the second row.
    fireEvent.keyDown(rows[2]!, { key: "ArrowUp" });
    await waitFor(() => {
      expect(rows[2]?.getAttribute("tabindex")).toBe("-1");
      expect(rows[1]?.getAttribute("tabindex")).toBe("0");
    });
  });

  it("supports Home/End to jump to first/last row", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
      { unit_id: "unit_3", order_index: 2, label: "Gamma" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
      makeParagraph("unit_3", "Gamma paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2", "unit_3"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");

    // End jumps to the last row.
    fireEvent.keyDown(rows[0]!, { key: "End" });
    await waitFor(() => {
      expect(rows[2]?.getAttribute("tabindex")).toBe("0");
      expect(rows[0]?.getAttribute("tabindex")).toBe("-1");
    });

    // Home jumps back to the first row.
    fireEvent.keyDown(rows[2]!, { key: "Home" });
    await waitFor(() => {
      expect(rows[0]?.getAttribute("tabindex")).toBe("0");
      expect(rows[2]?.getAttribute("tabindex")).toBe("-1");
    });
  });

  it("closes the panel on Escape and returns focus to the trigger button", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    // Open panel via trigger.
    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");
    // Press Escape on the focused row.
    fireEvent.keyDown(rows[0]!, { key: "Escape" });

    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(true),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    // Focus should have returned to the trigger.
    expect(document.activeElement).toBe(trigger);
  });

  it("does not produce duplicate tab stops for long articles (all ticks are non-tabbable)", () => {
    // Simulate a long article with 12 units.
    const units = Array.from({ length: 12 }, (_, i) => ({
      unit_id: `unit_${i + 1}`,
      order_index: i,
      label: `Section ${i + 1}`,
    }));
    const snapshot = makeSnapshot(units);
    const plateDocument = makePlateDocument(
      units.map((u) => makeParagraph(u.unit_id, `Paragraph ${u.unit_id}.`)),
    );
    renderTargets(units.map((u) => u.unit_id));

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    // Only the trigger button is tabbable in the rail (not the 12 ticks).
    const tabbableInRail = rail.querySelectorAll('button:not([tabindex="-1"])');
    expect(tabbableInRail).toHaveLength(1);
    expect(tabbableInRail[0]).toBe(screen.getByTestId("reader-record-outline-trigger"));

    // All visual ticks are spans (not buttons) and aria-hidden.
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    expect(miniRail.getAttribute("aria-hidden")).toBe("true");
    expect(miniRail.querySelectorAll("button")).toHaveLength(0);
  });

  it("hidden panel rows do not participate in Tab when panel is closed", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const rows = panel.querySelectorAll("button");
    // All rows are -1 when closed — no hidden tab stops.
    expect(Array.from(rows).every((r) => r.getAttribute("tabindex") === "-1")).toBe(true);
  });

  it("trigger button has min 24x24px accessible hit area", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    expect(trigger.className).toContain("min-h-[24px]");
    expect(trigger.className).toContain("min-w-[24px]");
  });

  it("updates trigger aria-label when active unit changes", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    const { paragraphs } = renderTargets(["unit_1", "unit_2"], [100, 300]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    // Initially active is unit_1 (paragraph 1).
    expect(trigger.getAttribute("aria-label")).toBe("打开段落导航，当前第 1 段");

    // Scroll so Beta is the last-above active unit.
    setRectTop(paragraphs[0]!, -20, 100);
    setRectTop(paragraphs[1]!, 40, 100);
    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe("打开段落导航，当前第 2 段");
    });
  });

  it("hovering a visual tick opens the panel without re-anchoring it", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
      { unit_id: "unit_3", order_index: 2, label: "Gamma" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
      makeParagraph("unit_3", "Gamma paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2", "unit_3"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    // Panel starts closed.
    expect(panel.classList.contains("pointer-events-none")).toBe(true);

    // Hover the second tick (index 1 = unit_2).
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
    fireEvent.mouseEnter(ticks[1]!);

    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    // T5.1e-PUX-Rail-R1: no per-tick anchor attribute; panel is vertically
    // centered via CSS.
    expect(panel.getAttribute("data-reader-record-navigation-panel-anchor-y")).toBeNull();
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");

    // Hover the first tick — panel should stay open and keep the same stable
    // positioning class.
    fireEvent.mouseEnter(ticks[0]!);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(panel.getAttribute("data-reader-record-navigation-panel-anchor-y")).toBeNull();
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
  });

  it("panel top does not change when hovering the topmost vs bottommost tick", async () => {
    const units = Array.from({ length: 12 }, (_, i) => ({
      unit_id: `unit_${i + 1}`,
      order_index: i,
      label: `Section ${i + 1}`,
    }));
    const snapshot = makeSnapshot(units);
    const plateDocument = makePlateDocument(
      units.map((u) => makeParagraph(u.unit_id, `Paragraph ${u.unit_id}.`)),
    );
    renderTargets(units.map((u) => u.unit_id));

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll<HTMLSpanElement>("span[data-navigation-unit-id]");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    fireEvent.mouseEnter(ticks[0]!);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    const topFromFirst = panel.style.top;

    fireEvent.mouseEnter(ticks[ticks.length - 1]!);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(panel.style.top).toBe(topFromFirst);
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();
  });

  it("does not use hover-anchor reposition classes on the panel", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.className).toContain("right-[calc(100%+8px)]");
    expect(panel.style.top).toBe("");
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();
  });

  it("opens the panel to the left of the rail when Ask is open", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
      { unit_id: "unit_2", order_index: 1, label: "Beta" },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "Alpha paragraph."),
      makeParagraph("unit_2", "Beta paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(
      <ReaderRecordNavigationRail
        snapshot={snapshot}
        plateDocument={plateDocument}
        askOpen
      />,
    );

    hoverTick(0);
    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    // Viewport mode keeps the ask-open shift class; panel still opens leftward
    // from the rail with no per-tick anchor.
    expect(rail.className).toContain("2xl:right-[clamp");
    expect(panel.className).toContain("right-[calc(100%+8px)]");
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();
  });

  it("scrolls the keyboard-focused row into view inside the panel scrollport", async () => {
    const units = Array.from({ length: 12 }, (_, i) => ({
      unit_id: `unit_${i + 1}`,
      order_index: i,
      label: `Section ${i + 1}`,
    }));
    const snapshot = makeSnapshot(units);
    const plateDocument = makePlateDocument(
      units.map((u) => makeParagraph(u.unit_id, `Paragraph ${u.unit_id}.`)),
    );
    renderTargets(units.map((u) => u.unit_id));

    // jsdom does not implement scrollIntoView; install a no-op so the effect
    // can be observed without throwing, then spy on it.
    if (!("scrollIntoView" in Element.prototype)) {
      Object.defineProperty(Element.prototype, "scrollIntoView", {
        value: () => {},
        configurable: true,
        writable: true,
      });
    }
    const scrollIntoViewSpy = vi
      .spyOn(Element.prototype, "scrollIntoView")
      .mockImplementation(() => {});

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    hoverTick(0);
    const panel = screen.getByTestId("reader-record-navigation-panel");
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");
    expect(rows.length).toBe(units.length);

    // Move focus down with ArrowDown; the newly focused row must be scrolled
    // into view by the panel's scroll-into-view effect.
    fireEvent.keyDown(rows[0]!, { key: "ArrowDown" });
    await waitFor(() =>
      expect(rows[1]?.getAttribute("tabindex")).toBe("0"),
    );

    expect(scrollIntoViewSpy).toHaveBeenCalled();
    // The last call should target the newly focused row (unit_2).
    const lastCallTarget = scrollIntoViewSpy.mock.contexts.length
      ? scrollIntoViewSpy.mock.contexts[scrollIntoViewSpy.mock.contexts.length - 1]
      : null;
    expect(lastCallTarget).toBe(rows[1]);

    scrollIntoViewSpy.mockRestore();
  });

  it("visual tick layer must not carry pointer-events-none (hover must be reachable in real browsers)", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    // The container must NOT have pointer-events-none — otherwise child tick
    // onMouseEnter handlers are unreachable in real browsers (jsdom's
    // fireEvent bypasses CSS hit-testing, producing false positives).
    expect(miniRail.className).not.toContain("pointer-events-none");

    // Individual tick spans must also be hoverable.
    const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
    expect(ticks.length).toBeGreaterThan(0);
    for (const tick of Array.from(ticks)) {
      expect(tick.className).not.toContain("pointer-events-none");
    }
  });

  // ---------------------------------------------------------------------------
  // L1 deterministic heading navigation (T5.1c)
  // ---------------------------------------------------------------------------

  describe("L1 heading navigation", () => {
    it("F1: heading-rich uses L1 mode with only heading ticks and 第 N 项 rows", async () => {
      const units = headingRichUnits();
      const snapshot = makeSnapshot(units);
      const plateDocument = plateFromUnits(units);
      // Render all units so heading targets exist; L1 only spies headings.
      renderTargets(units.map((u) => u.unit_id), [20, 100, 200, 300, 400, 500, 600]);

      render(
        <ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />,
      );

      const rail = screen.getByTestId("reader-record-navigation-rail");
      expect(rail.getAttribute("data-navigation-mode")).toBe("L1");
      expect(rail.getAttribute("aria-label")).toBe("阅读定位");

      const miniRail = screen.getByTestId("reader-record-mini-rail");
      const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
      expect(ticks).toHaveLength(2);
      expect(ticks[0]?.getAttribute("data-navigation-unit-id")).toBe("u2");
      expect(ticks[1]?.getAttribute("data-navigation-unit-id")).toBe("u5");

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      expect(screen.getByText("Chapter One")).toBeTruthy();
      expect(screen.getByText("Chapter Two")).toBeTruthy();
      expect(screen.getByText("第 1 项")).toBeTruthy();
      expect(screen.getByText("第 2 项")).toBeTruthy();
      expect(screen.queryByText("第 1 段")).toBeNull();
    });

    it("F2: pure-body stays L0 段落导航", () => {
      const units: SnapshotUnitInput[] = Array.from({ length: 6 }, (_, i) => ({
        unit_id: `u${i + 1}`,
        order_index: i + 1,
        unit_type: "body" as const,
        label: `Body ${i + 1}`,
      }));
      renderTargets(units.map((u) => u.unit_id));

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      const rail = screen.getByTestId("reader-record-navigation-rail");
      expect(rail.getAttribute("data-navigation-mode")).toBe("L0");
      const ticks = screen
        .getByTestId("reader-record-mini-rail")
        .querySelectorAll("span[data-navigation-unit-id]");
      expect(ticks).toHaveLength(6);
      expect(screen.getByTestId("reader-record-outline-trigger").getAttribute("aria-label")).toBe(
        "打开段落导航，当前第 1 段",
      );
    });

    it("F4b: single heading long article stays L0 (must not swallow full list)", () => {
      const units: SnapshotUnitInput[] = [
        { unit_id: "u1", order_index: 1, unit_type: "body", label: "A" },
        { unit_id: "u2", order_index: 2, unit_type: "heading", label: "Only" },
        { unit_id: "u3", order_index: 3, unit_type: "body", label: "B" },
        { unit_id: "u4", order_index: 4, unit_type: "body", label: "C" },
        { unit_id: "u5", order_index: 5, unit_type: "body", label: "D" },
        { unit_id: "u6", order_index: 6, unit_type: "body", label: "E" },
      ];
      renderTargets(units.map((u) => u.unit_id));

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute("data-navigation-mode"),
      ).toBe("L0");
      expect(
        screen
          .getByTestId("reader-record-mini-rail")
          .querySelectorAll("span[data-navigation-unit-id]"),
      ).toHaveLength(6);
    });

    it("F5: document-fallback (empty nav units) forces L0", () => {
      const plateDocument = makePlateDocument([
        makeParagraph("unit_first", "First"),
        makeParagraph("unit_second", "Second"),
      ]);
      renderTargets(["unit_first", "unit_second"]);

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot([])}
          plateDocument={plateDocument}
        />,
      );

      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute("data-navigation-mode"),
      ).toBe("L0");
      expect(
        screen
          .getByTestId("reader-record-mini-rail")
          .querySelectorAll("span[data-navigation-unit-id]"),
      ).toHaveLength(2);
    });

    it("F6: empty nav + empty document does not render rail", () => {
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot([])}
          plateDocument={makePlateDocument([])}
        />,
      );
      expect(screen.queryByTestId("reader-record-navigation-rail")).toBeNull();
    });

    it("F9: lead zone has null active, no aria-current, trigger without 当前第 N 项", async () => {
      const units = headingRichUnits();
      // All heading targets below safeTop (64) → lead zone.
      renderTargets(units.map((u) => u.unit_id), [100, 200, 300, 400, 500, 600, 700]);

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toBe("打开章节导航");
      });
      expect(trigger.getAttribute("aria-label")).not.toMatch(/当前第/);

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      const rows = panel.querySelectorAll("button");
      expect(Array.from(rows).every((r) => r.getAttribute("aria-current") !== "true")).toBe(
        true,
      );
      // Keyboard focus may land on first heading without making it active.
      const tabbable = Array.from(rows).filter((r) => r.getAttribute("tabindex") === "0");
      expect(tabbable).toHaveLength(1);
      expect(tabbable[0]?.getAttribute("aria-current")).toBeNull();
    });

    it("F1 spy: reading body under a heading keeps that heading active", async () => {
      const units = headingRichUnits();
      // u2 (heading) above safe line; body units not in L1 candidates.
      // tops for all units; L1 only maps u2 and u5.
      renderTargets(units.map((u) => u.unit_id), [20, 40, 200, 300, 500, 600, 700]);

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toBe(
          "打开章节导航，当前第 1 项",
        );
      });
    });

    it("L1 click scrolls to heading unit-start and locks active", async () => {
      const units = headingRichUnits();
      const { paragraphs } = renderTargets(
        units.map((u) => u.unit_id),
        [20, 500, 600, 700, 800, 900, 1000],
      );
      // paragraphs[1] is u2 heading unit-start (first unit in each is unit-start only for index 0
      // in renderTargets — fix: renderTargets only marks i===0 as unit-start).
      // Mark heading paragraphs as unit-start.
      paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");
      setRectTop(paragraphs[1]!, 500, 100);
      vi.stubGlobal("scrollY", 0);

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      const rows = panel.querySelectorAll("button");
      fireEvent.click(rows[0]!);

      expect(window.scrollTo).toHaveBeenCalledWith({
        top: 500 - 56 - 8,
        behavior: "smooth",
      });
      expect(rows[0]?.getAttribute("aria-current")).toBe("true");
      expect(
        screen.getByTestId("reader-record-outline-trigger").getAttribute("aria-label"),
      ).toMatch(/关闭章节导航，当前第 1 项|打开章节导航，当前第 1 项/);
    });

    it("F12: L1 a11y copy never uses 文章目录/大纲/第 N 节; open/close + lead/active", async () => {
      const units = headingRichUnits();
      const { paragraphs } = renderTargets(
        units.map((u) => u.unit_id),
        [20, 40, 200, 300, 500, 600, 700],
      );
      paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      const forbidden = /文章目录|大纲|第 \d+ 节/;

      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toMatch(/章节导航/);
      });
      expect(trigger.getAttribute("aria-label")).not.toMatch(forbidden);

      fireEvent.click(trigger);
      await waitFor(() => {
        expect(trigger.getAttribute("aria-expanded")).toBe("true");
      });
      expect(trigger.getAttribute("aria-label")).toMatch(/关闭章节导航/);
      expect(trigger.getAttribute("aria-label")).not.toMatch(forbidden);

      // Lead: move all unit targets below safe line (validated live rects).
      paragraphs.forEach((el) => setRectTop(el, 200, 100));
      triggerScroll();
      await waitFor(() => {
        const label = trigger.getAttribute("aria-label");
        expect(label).toBe("关闭章节导航");
        expect(label).not.toMatch(/当前第/);
      });
    });

    it("F11: L1 display strips markdown # from heading labels", async () => {
      const units: SnapshotUnitInput[] = [
        { unit_id: "u1", order_index: 1, unit_type: "body", label: null },
        {
          unit_id: "u2",
          order_index: 2,
          unit_type: "heading",
          label: "# Markdown Title",
        },
        { unit_id: "u3", order_index: 3, unit_type: "body", label: null },
        {
          unit_id: "u4",
          order_index: 4,
          unit_type: "heading",
          label: "## Second",
        },
        { unit_id: "u5", order_index: 5, unit_type: "body", label: null },
        { unit_id: "u6", order_index: 6, unit_type: "body", label: null },
      ];
      renderTargets(units.map((u) => u.unit_id));

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      expect(screen.getByText("Markdown Title")).toBeTruthy();
      expect(screen.getByText("Second")).toBeTruthy();
      expect(screen.queryByText("# Markdown Title")).toBeNull();
    });

    it("F8b: same unit ids across new base_id must reset active/scroll-lock state", async () => {
      const units = headingRichUnits();
      const tops = [20, 40, 200, 300, 500, 600, 700];
      const { paragraphs } = renderTargets(
        units.map((u) => u.unit_id),
        tops,
      );
      paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");

      const plateDocument = plateFromUnits(units);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, { baseId: "base_old", generation: 1 })}
          plateDocument={plateDocument}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toBe(
          "打开章节导航，当前第 1 项",
        );
      });

      // Click second heading to lock active = u5.
      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(panel.querySelectorAll("button")[1]!);
      expect(panel.querySelectorAll("button")[1]?.getAttribute("aria-current")).toBe(
        "true",
      );

      // New base, same u1..u7 labels — must not keep active=u5.
      // Place all headings in lead zone so after reset active stays null.
      paragraphs.forEach((p) => setRectTop(p, 200, 100));

      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, { baseId: "base_new", generation: 1 })}
          plateDocument={plateDocument}
        />,
      );

      triggerScroll();
      await waitFor(() => {
        // Panel may still be open; identity reset only clears active/focus/lock/map.
        expect(trigger.getAttribute("aria-label")).toMatch(
          /^(打开|关闭)章节导航$/,
        );
        expect(trigger.getAttribute("aria-label")).not.toMatch(/当前第/);
      });
      const rows = screen
        .getByTestId("reader-record-navigation-panel")
        .querySelectorAll("button");
      expect(Array.from(rows).every((r) => r.getAttribute("aria-current") !== "true")).toBe(
        true,
      );
    });

    it("revalidates target cache: detached nodes with same unit ids cannot keep false active", async () => {
      const units = headingRichUnits();
      // First mount: headings above safeTop → active chapter one.
      const first = renderTargets(
        units.map((u) => u.unit_id),
        [20, 40, 200, 300, 500, 600, 700],
      );
      first.paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      first.paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, { baseId: "base_cache", generation: 1 })}
          plateDocument={plateFromUnits(units)}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toBe(
          "打开章节导航，当前第 1 项",
        );
      });

      // Simulate Plate setValue: detach old paragraphs, mount new ones with the
      // same unit ids but all below safeTop (lead). Stale cache must not win.
      first.body.remove();
      const second = renderTargets(
        units.map((u) => u.unit_id),
        [200, 220, 400, 500, 600, 700, 800],
      );
      second.paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      second.paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");

      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toBe("打开章节导航");
        expect(trigger.getAttribute("aria-label")).not.toMatch(/当前第/);
      });
    });

    it("click resolves live target after detached remount with same unit ids", async () => {
      const units = headingRichUnits();
      const first = renderTargets(
        units.map((u) => u.unit_id),
        [20, 40, 200, 300, 500, 600, 700],
      );
      first.paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      first.paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, { baseId: "base_click", generation: 1 })}
          plateDocument={plateFromUnits(units)}
        />,
      );

      // Warm the target cache via spy.
      triggerScroll();
      await waitFor(() => {
        expect(
          screen.getByTestId("reader-record-outline-trigger").getAttribute("aria-label"),
        ).toMatch(/当前第 1 项/);
      });

      first.body.remove();
      const second = renderTargets(
        units.map((u) => u.unit_id),
        [100, 500, 600, 700, 800, 900, 1000],
      );
      second.paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      second.paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");
      setRectTop(second.paragraphs[4]!, 500, 100);
      vi.stubGlobal("scrollY", 0);

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(panel.querySelectorAll("button")[1]!);

      // Must scroll using the *new* connected u5 node, not the detached cache.
      expect(window.scrollTo).toHaveBeenCalledWith({
        top: 500 - 56 - 8,
        behavior: "smooth",
      });
      expect(panel.querySelectorAll("button")[1]?.getAttribute("aria-current")).toBe(
        "true",
      );
    });

    it("F8: generation change resets state even when base_id and unit ids match", async () => {
      const units = headingRichUnits();
      const { paragraphs } = renderTargets(
        units.map((u) => u.unit_id),
        [20, 40, 200, 300, 500, 600, 700],
      );

      const plateDocument = plateFromUnits(units);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, { baseId: "base_1", generation: 1 })}
          plateDocument={plateDocument}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toMatch(/当前第 1 项/);
      });

      paragraphs.forEach((p) => setRectTop(p, 200, 100));
      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, { baseId: "base_1", generation: 2 })}
          plateDocument={plateDocument}
        />,
      );
      triggerScroll();
      await waitFor(() => {
        expect(trigger.getAttribute("aria-label")).toBe("打开章节导航");
      });
    });

    it("F7: base_id switch with different identity clears prior active", async () => {
      const unitsA = headingRichUnits();
      const unitsB: SnapshotUnitInput[] = [
        { unit_id: "x1", order_index: 1, unit_type: "body", label: null },
        { unit_id: "x2", order_index: 2, unit_type: "heading", label: "New One" },
        { unit_id: "x3", order_index: 3, unit_type: "body", label: null },
        { unit_id: "x4", order_index: 4, unit_type: "heading", label: "New Two" },
        { unit_id: "x5", order_index: 5, unit_type: "body", label: null },
        { unit_id: "x6", order_index: 6, unit_type: "body", label: null },
      ];

      const { body: bodyA } = renderTargets(
        unitsA.map((u) => u.unit_id),
        [20, 40, 200, 300, 500, 600, 700],
      );
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(unitsA, { baseId: "base_a", generation: 1 })}
          plateDocument={plateFromUnits(unitsA)}
        />,
      );

      await waitFor(() => {
        expect(
          screen.getByTestId("reader-record-outline-trigger").getAttribute("aria-label"),
        ).toMatch(/当前第 1 项/);
      });

      // Swap plate document targets without wiping the React root.
      bodyA.remove();
      renderTargets(unitsB.map((u) => u.unit_id), [200, 300, 400, 500, 600, 700]);

      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(unitsB, { baseId: "base_b", generation: 1 })}
          plateDocument={plateFromUnits(unitsB)}
        />,
      );

      triggerScroll();
      await waitFor(() => {
        const label = screen
          .getByTestId("reader-record-outline-trigger")
          .getAttribute("aria-label");
        expect(label).toMatch(/^(打开|关闭)章节导航$/);
        expect(label).not.toMatch(/当前第/);
      });
      expect(
        screen
          .getByTestId("reader-record-mini-rail")
          .querySelectorAll("span[data-navigation-unit-id]"),
      ).toHaveLength(2);
      expect(screen.getByText("New One")).toBeTruthy();
    });

    it("keeps L1 scroll lock for 700ms after click", async () => {
      const units = headingRichUnits();
      const { paragraphs } = renderTargets(
        units.map((u) => u.unit_id),
        [20, 40, 200, 300, 500, 600, 700],
      );
      paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      paragraphs[4]!.setAttribute("data-reader-record-unit-start", "true");

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units)}
          plateDocument={plateFromUnits(units)}
        />,
      );

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      const rows = panel.querySelectorAll("button");
      fireEvent.click(rows[1]!);
      expect(rows[1]?.getAttribute("aria-current")).toBe("true");

      // During lock, spy would prefer first heading — must stay on second.
      setRectTop(paragraphs[1]!, 40, 100);
      setRectTop(paragraphs[4]!, 200, 100);
      triggerScroll();
      await new Promise((resolve) => setTimeout(resolve, 200));
      expect(rows[1]?.getAttribute("aria-current")).toBe("true");

      await new Promise((resolve) => setTimeout(resolve, 600));
      triggerScroll();
      await waitFor(() =>
        expect(rows[0]?.getAttribute("aria-current")).toBe("true"),
      );
    });
  });

  describe("T5.5a semantic outline (L2)", () => {
    const threeUnits: SnapshotUnitInput[] = [
      { unit_id: "unit_1", order_index: 1, label: "U1" },
      { unit_id: "unit_2", order_index: 2, label: "U2" },
      { unit_id: "unit_3", order_index: 3, label: "U3" },
    ];

    function threeDoc() {
      return makePlateDocument([
        makeParagraph("unit_1", "A"),
        makeParagraph("unit_2", "B"),
        makeParagraph("unit_3", "C"),
      ]);
    }

    it("shows mode switch for ready outline; ticks are depth=1 only", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto(),
          })}
          plateDocument={threeDoc()}
        />,
      );

      const rail = screen.getByTestId("reader-record-navigation-rail");
      expect(rail.getAttribute("data-has-semantic-outline")).toBe("true");
      expect(rail.getAttribute("data-outline-surface")).toBe("deterministic");
      // Default deterministic ticks = 3 units.
      expect(
        screen
          .getByTestId("reader-record-mini-rail")
          .querySelectorAll("[data-navigation-tick-key]"),
      ).toHaveLength(3);

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      expect(
        screen.getByTestId("reader-record-outline-mode-switch"),
      ).toBeTruthy();

      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      await waitFor(() =>
        expect(rail.getAttribute("data-outline-surface")).toBe("semantic"),
      );
      expect(rail.getAttribute("aria-label")).toBe("内容大纲");
      expect(rail.getAttribute("data-navigation-mode")).toBe("L2");

      // Semantic ticks: only roots n1, n3 — use outline node ids, not unit ids.
      const ticks = screen
        .getByTestId("reader-record-mini-rail")
        .querySelectorAll("[data-navigation-tick-key]");
      expect(ticks).toHaveLength(2);
      expect(ticks[0]?.getAttribute("data-navigation-tick-key")).toBe("n1");
      expect(ticks[0]?.getAttribute("data-outline-node-id")).toBe("n1");
      expect(ticks[0]?.getAttribute("data-navigation-unit-id")).toBeNull();
      expect(ticks[1]?.getAttribute("data-navigation-tick-key")).toBe("n3");
      expect(ticks[1]?.getAttribute("data-outline-node-id")).toBe("n3");
      expect(ticks[1]?.getAttribute("data-navigation-unit-id")).toBeNull();

      // Panel shows full tree including depth-2 child.
      expect(
        screen.getByTestId("reader-record-outline-node-n2"),
      ).toBeTruthy();
      expect(
        screen.getByTestId("reader-record-outline-node-n2").getAttribute(
          "data-outline-depth",
        ),
      ).toBe("2");

      // Mode switch a11y: group + pressed state.
      const modeGroup = screen.getByTestId("reader-record-outline-mode-switch");
      expect(modeGroup.getAttribute("role")).toBe("group");
      expect(modeGroup.getAttribute("aria-label")).toBe("导航方式");
      expect(
        screen
          .getByTestId("reader-record-outline-mode-semantic")
          .getAttribute("aria-pressed"),
      ).toBe("true");
      expect(
        screen
          .getByTestId("reader-record-outline-mode-deterministic")
          .getAttribute("aria-pressed"),
      ).toBe("false");
    });

    it("partial shows quiet hint; untrusted statuses hide L2", () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "partial" }),
          })}
          plateDocument={threeDoc()}
        />,
      );
      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute(
          "data-has-semantic-outline",
        ),
      ).toBe("true");

      for (const status of [
        "pending",
        "failed",
        "stale",
        "unavailable",
      ] as const) {
        rerender(
          <ReaderRecordNavigationRail
            snapshot={makeSnapshot(threeUnits, {
              semantic_outline: makeOutlineDto({ status }),
            })}
            plateDocument={threeDoc()}
          />,
        );
        expect(
          screen.getByTestId("reader-record-navigation-rail").getAttribute(
            "data-has-semantic-outline",
          ),
        ).toBe("false");
        expect(
          screen.queryByTestId("reader-record-outline-mode-switch"),
        ).toBeNull();
      }

      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, { semantic_outline: null })}
          plateDocument={threeDoc()}
        />,
      );
      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute(
          "data-has-semantic-outline",
        ),
      ).toBe("false");
    });

    it("source mismatch and missing start_unit fail-closed", () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({
              source_identity: { base_id: "other", generation: 1 },
            }),
          })}
          plateDocument={threeDoc()}
        />,
      );
      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute(
          "data-has-semantic-outline",
        ),
      ).toBe("false");

      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({}, [
              {
                node_id: "nx",
                parent_node_id: null,
                depth: 1,
                title: "Ghost",
                start_unit_id: "unit_missing",
                end_unit_id: "unit_missing",
                start_anchor_segment_id: null,
                end_anchor_segment_id: null,
                order_index: 1,
              },
            ]),
          })}
          plateDocument={threeDoc()}
        />,
      );
      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute(
          "data-has-semantic-outline",
        ),
      ).toBe("false");
    });

    it("click with DOM scrolls and sets active; missing DOM is no-op", async () => {
      const { paragraphs } = renderTargets(
        ["unit_1", "unit_2", "unit_3"],
        [100, 300, 500],
      );
      paragraphs[0]!.setAttribute("data-reader-record-unit-start", "true");
      paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
      paragraphs[2]!.setAttribute("data-reader-record-unit-start", "true");

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto(),
          })}
          plateDocument={threeDoc()}
        />,
      );

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );

      const rootBtn = screen.getByTestId("reader-record-outline-node-n1");
      fireEvent.click(rootBtn);
      expect(window.scrollTo).toHaveBeenCalled();
      expect(rootBtn.getAttribute("aria-current")).toBe("true");

      // Remove all plate DOM → click child is no-op for active/scroll.
      document
        .querySelector(".reader-record-plate-document")
        ?.remove();
      vi.mocked(window.scrollTo).mockClear();
      const childBtn = screen.getByTestId("reader-record-outline-node-n2");
      fireEvent.click(childBtn);
      expect(window.scrollTo).not.toHaveBeenCalled();
      expect(childBtn.getAttribute("aria-current")).toBeNull();
      // Previous active remains.
      expect(rootBtn.getAttribute("aria-current")).toBe("true");
    });

    it("prefers start_anchor_segment_id when consistent with unit", async () => {
      const body = document.createElement("div");
      body.className = "reader-record-plate-document";
      const unitStart = document.createElement("p");
      unitStart.setAttribute("data-reader-record-node", "paragraph");
      unitStart.setAttribute("data-unit-id", "unit_1");
      unitStart.setAttribute("data-reader-record-unit-start", "true");
      unitStart.setAttribute("data-anchor-segment-id", "seg_other");
      setRectTop(unitStart, 400, 100);
      const anchored = document.createElement("p");
      anchored.setAttribute("data-reader-record-node", "paragraph");
      anchored.setAttribute("data-unit-id", "unit_1");
      anchored.setAttribute("data-anchor-segment-id", "seg_precise");
      setRectTop(anchored, 120, 100);
      body.appendChild(unitStart);
      body.appendChild(anchored);
      document.body.appendChild(body);

      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({}, [
              {
                node_id: "na",
                parent_node_id: null,
                depth: 1,
                title: "Anchored",
                start_unit_id: "unit_1",
                end_unit_id: "unit_1",
                start_anchor_segment_id: "seg_precise",
                end_anchor_segment_id: null,
                order_index: 1,
              },
            ]),
          })}
          plateDocument={threeDoc()}
        />,
      );

      hoverTick(0);
      await waitFor(() =>
        expect(
          screen
            .getByTestId("reader-record-navigation-panel")
            .classList.contains("pointer-events-none"),
        ).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      fireEvent.click(screen.getByTestId("reader-record-outline-node-na"));
      expect(window.scrollTo).toHaveBeenCalledWith({
        top: 120 - 56 - 8,
        behavior: "smooth",
      });
    });

    it("source identity change forces deterministic and clears L2 active", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto(),
          })}
          plateDocument={threeDoc()}
        />,
      );

      hoverTick(0);
      await waitFor(() =>
        expect(
          screen
            .getByTestId("reader-record-navigation-panel")
            .classList.contains("pointer-events-none"),
        ).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      expect(
        screen.getByTestId("reader-record-navigation-rail").getAttribute(
          "data-outline-surface",
        ),
      ).toBe("semantic");

      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            baseId: "base_2",
            generation: 1,
            semantic_outline: makeOutlineDto({
              source_identity: { base_id: "base_2", generation: 1 },
            }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-navigation-rail").getAttribute(
            "data-outline-surface",
          ),
        ).toBe("deterministic"),
      );
    });

    it("roving keyboard on semantic list and Escape returns to trigger", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto(),
          })}
          plateDocument={threeDoc()}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      fireEvent.click(trigger);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );

      const n1 = screen.getByTestId("reader-record-outline-node-n1");
      const n2 = screen.getByTestId("reader-record-outline-node-n2");
      n1.focus();
      fireEvent.keyDown(n1, { key: "ArrowDown" });
      await waitFor(() => expect(document.activeElement).toBe(n2));
      fireEvent.keyDown(n2, { key: "Home" });
      await waitFor(() => expect(document.activeElement).toBe(n1));
      fireEvent.keyDown(n1, { key: "End" });
      const n3 = screen.getByTestId("reader-record-outline-node-n3");
      await waitFor(() => expect(document.activeElement).toBe(n3));
      fireEvent.keyDown(n3, { key: "Escape" });
      await waitFor(() => expect(document.activeElement).toBe(trigger));
    });

    it("L1 + L2 coexist: switching L2 does not change L1 items", async () => {
      const units = headingRichUnits();
      renderTargets(units.map((u) => u.unit_id));
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, {
            semantic_outline: makeOutlineDto(
              {},
              [
                {
                  node_id: "ol1",
                  parent_node_id: null,
                  depth: 1,
                  title: "Outline",
                  start_unit_id: "u1",
                  end_unit_id: "u7",
                  start_anchor_segment_id: null,
                  end_anchor_segment_id: null,
                  order_index: 1,
                },
              ],
            ),
          })}
          plateDocument={plateFromUnits(units)}
        />,
      );

      const rail = screen.getByTestId("reader-record-navigation-rail");
      expect(rail.getAttribute("data-navigation-mode")).toBe("L1");
      expect(rail.getAttribute("data-has-semantic-outline")).toBe("true");

      hoverTick(0);
      await waitFor(() =>
        expect(
          screen
            .getByTestId("reader-record-navigation-panel")
            .classList.contains("pointer-events-none"),
        ).toBe(false),
      );
      // Deterministic L1 rows still present (Chapter labels).
      expect(screen.getByText("Chapter One")).toBeTruthy();

      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      expect(screen.getByText("Outline")).toBeTruthy();
      expect(screen.queryByText("Chapter One")).toBeNull();

      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-deterministic"),
      );
      expect(screen.getByText("Chapter One")).toBeTruthy();
      expect(rail.getAttribute("data-navigation-mode")).toBe("L1");
    });

    it("row ref namespace: node_id equal to unitId does not collide across surfaces", async () => {
      // Collision fixture: outline node_id "unit_1" equals deterministic unitId.
      const outline = makeOutlineDto({}, [
        {
          node_id: "unit_1",
          parent_node_id: null,
          depth: 1,
          title: "Semantic Twin",
          start_unit_id: "unit_1",
          end_unit_id: "unit_2",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
        {
          node_id: "unit_2",
          parent_node_id: null,
          depth: 1,
          title: "Semantic Two",
          start_unit_id: "unit_3",
          end_unit_id: "unit_3",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 2,
        },
      ]);
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, { semantic_outline: outline })}
          plateDocument={threeDoc()}
        />,
      );

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );

      // Deterministic: first row is unit_1 label "U1".
      const detRows = panel.querySelectorAll(
        "ol button:not([data-outline-node-id])",
      );
      // Mode switch buttons are outside ol — ol only has nav rows.
      const detOlButtons = panel.querySelectorAll("ol button");
      expect(detOlButtons[0]?.textContent).toContain("U1");
      (detOlButtons[0] as HTMLButtonElement).focus();
      fireEvent.keyDown(detOlButtons[0]!, { key: "ArrowDown" });
      await waitFor(() =>
        expect(document.activeElement).toBe(detOlButtons[1]),
      );

      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      const semRow = screen.getByTestId("reader-record-outline-node-unit_1");
      const semRow2 = screen.getByTestId("reader-record-outline-node-unit_2");
      semRow.focus();
      fireEvent.keyDown(semRow, { key: "ArrowDown" });
      await waitFor(() => expect(document.activeElement).toBe(semRow2));
      // Must be the semantic row, not a leftover deterministic unit_2 row.
      expect(
        (document.activeElement as HTMLElement).getAttribute(
          "data-outline-node-id",
        ),
      ).toBe("unit_2");
      expect(
        (document.activeElement as HTMLElement).getAttribute("aria-label"),
      ).toMatch(/Semantic Two/);
    });

    it("source identity switch cancels pending close timer", async () => {
      vi.useFakeTimers();
      try {
        renderTargets(["unit_1", "unit_2", "unit_3"]);
        const { rerender } = render(
          <ReaderRecordNavigationRail
            snapshot={makeSnapshot(threeUnits, {
              semantic_outline: makeOutlineDto(),
            })}
            plateDocument={threeDoc()}
          />,
        );

        hoverTick(0);
        const panel = screen.getByTestId("reader-record-navigation-panel");
        expect(panel.classList.contains("pointer-events-none")).toBe(false);

        // Leave rail → schedule 220ms close.
        fireEvent.mouseLeave(screen.getByTestId("reader-record-navigation-rail"));

        // Switch source before the timer fires.
        rerender(
          <ReaderRecordNavigationRail
            snapshot={makeSnapshot(threeUnits, {
              baseId: "base_2",
              generation: 1,
              semantic_outline: makeOutlineDto({
                source_identity: { base_id: "base_2", generation: 1 },
              }),
            })}
            plateDocument={threeDoc()}
          />,
        );

        // Open panel under the new source.
        hoverTick(0);
        expect(panel.classList.contains("pointer-events-none")).toBe(false);

        // Old source's delayed close must not close the new panel.
        await vi.advanceTimersByTimeAsync(300);
        expect(panel.classList.contains("pointer-events-none")).toBe(false);
        expect(panel.classList.contains("visible")).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // -------------------------------------------------------------------------
  // T5.6c — explicit-section "解析此段" per-row action.
  //
  // Only trusted L2 (ready|partial, source identity match, units resolved)
  // surfaces the action chip. Clicking sends the full range witness — never
  // node-only — to the BFF endpoint. Succeeded → onRequestSnapshotReload +
  // row state cleared. Other outcomes → inline accessible feedback. Per-row
  // in-flight guard prevents double-submit. L0/L1 surfaces never show the
  // chip; rail roving, keyboard, and snapshot identity semantics are
  // preserved.
  // -------------------------------------------------------------------------
  describe("T5.6c section translation per-row action", () => {
    const threeUnits: SnapshotUnitInput[] = [
      { unit_id: "unit_1", order_index: 1, label: "U1" },
      { unit_id: "unit_2", order_index: 2, label: "U2" },
      { unit_id: "unit_3", order_index: 3, label: "U3" },
    ];

    function threeDoc() {
      return makePlateDocument([
        makeParagraph("unit_1", "A"),
        makeParagraph("unit_2", "B"),
        makeParagraph("unit_3", "C"),
      ]);
    }

    /** Open the panel and switch to the semantic surface. */
    async function openSemanticPanel() {
      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      await waitFor(() =>
        expect(
          screen
            .getByTestId("reader-record-navigation-rail")
            .getAttribute("data-outline-surface"),
        ).toBe("semantic"),
      );
    }

    function mockFetchSuccess(
      outcome: string,
      body: { outcome: string; job_id: string | null; detail: string | null } = {
        outcome: "succeeded",
        job_id: "job_1",
        detail: null,
      },
    ) {
      vi.mocked(globalThis.fetch).mockResolvedValue(
        new Response(JSON.stringify({ ok: true, ...body }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
      void outcome;
    }

    function mockFetchBffError(
      status: number,
      code: string,
      message: string,
    ) {
      vi.mocked(globalThis.fetch).mockResolvedValue(
        new Response(
          JSON.stringify({ ok: false, status, code, message }),
          { status, headers: { "content-type": "application/json" } },
        ),
      );
    }

    beforeEach(() => {
      vi.spyOn(globalThis, "fetch").mockReset();
    });

    it("L2 trusted ready: shows '解析此段' chip on each semantic row", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();

      // Three nodes (n1, n2, n3) → three resolve chips.
      expect(
        screen.getByTestId("reader-record-outline-resolve-n1"),
      ).toBeTruthy();
      expect(
        screen.getByTestId("reader-record-outline-resolve-n2"),
      ).toBeTruthy();
      expect(
        screen.getByTestId("reader-record-outline-resolve-n3"),
      ).toBeTruthy();
    });

    it("L2 trusted partial: chip still shown", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "partial" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();

      expect(
        screen.getByTestId("reader-record-outline-resolve-n1"),
      ).toBeTruthy();
    });

    it("L0/L1 deterministic surface: no resolve chip even with trusted outline", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      // Default surface is deterministic — no chip.
      expect(
        screen.queryByTestId("reader-record-outline-resolve-n1"),
      ).toBeNull();

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      // Panel open on deterministic; still no chip.
      expect(
        screen.queryByTestId("reader-record-outline-resolve-n1"),
      ).toBeNull();
    });

    it("untrusted statuses hide L2 → no chip", () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      for (const status of [
        "pending",
        "failed",
        "stale",
        "unavailable",
      ] as const) {
        const { unmount } = render(
          <ReaderRecordNavigationRail
            snapshot={makeSnapshot(threeUnits, {
              semantic_outline: makeOutlineDto({ status }),
            })}
            plateDocument={threeDoc()}
          />,
        );
        expect(
          screen.queryByTestId("reader-record-outline-resolve-n1"),
        ).toBeNull();
        unmount();
      }
    });

    it("null outline: no chip", () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, { semantic_outline: null })}
          plateDocument={threeDoc()}
        />,
      );
      expect(
        screen.queryByTestId("reader-record-outline-resolve-n1"),
      ).toBeNull();
    });

    it("click sends full range witness (start/end unit + anchors + audit fields) — never node-only", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }, [
              {
                node_id: "n_anchored",
                parent_node_id: null,
                depth: 1,
                title: "Anchored",
                start_unit_id: "unit_1",
                end_unit_id: "unit_2",
                start_anchor_segment_id: "seg_a",
                end_anchor_segment_id: "seg_b",
                order_index: 1,
              },
            ]),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      fireEvent.click(
        screen.getByTestId("reader-record-outline-resolve-n_anchored"),
      );

      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));

      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]!;
      expect(url).toBe(
        "/api/web/reader-plate/records/record_1/section-translation",
      );
      expect(init?.method).toBe("POST");
      const body = JSON.parse((init?.body as string) ?? "{}");
      // Full range witness — never node-only.
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        startAnchorSegmentId: "seg_a",
        endAnchorSegmentId: "seg_b",
        nodeId: "n_anchored",
        outlineRevision: "rev_1",
      });
    });

    it("click on row without anchors sends null anchors (still full range)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));
      const body = JSON.parse(
        (vi.mocked(globalThis.fetch).mock.calls[0]![1]?.body as string) ?? "{}",
      );
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        startAnchorSegmentId: null,
        endAnchorSegmentId: null,
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("loading state shows '正在解析…' and disables repeat click for the same row", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();

      // Fetch never resolves → stays in loading.
      vi.mocked(globalThis.fetch).mockImplementation(
        () => new Promise(() => {}),
      );

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-loading-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.queryByTestId("reader-record-outline-resolve-n1"),
      ).toBeNull();

      // Click the parent row button (chip is gone) — must not trigger a
      // second fetch (in-flight guard).
      fireEvent.click(screen.getByTestId("reader-record-outline-node-n1"));
      expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    });

    it("succeeded outcome clears row state and triggers onRequestSnapshotReload", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const onRequestSnapshotReload = vi.fn().mockResolvedValue(undefined);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
          onRequestSnapshotReload={onRequestSnapshotReload}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(onRequestSnapshotReload).toHaveBeenCalledTimes(1),
      );
      // Row state cleared → chip returns.
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.queryByTestId("reader-record-outline-resolve-loading-n1"),
      ).toBeNull();
      expect(
        screen.queryByTestId("reader-record-outline-resolve-feedback-n1"),
      ).toBeNull();
    });

    it.each([
      ["retry_later", "稍后重试"],
      ["already_covered_or_inflight", "已在解析中"],
      ["budget_exhausted", "解析额度已用完"],
      ["rejected", "无法解析此段"],
      ["superseded", "已过期，请刷新"],
    ] as const)(
      "outcome '%s' surfaces inline accessible feedback message",
      async (outcome, expectedMessage) => {
        renderTargets(["unit_1", "unit_2", "unit_3"]);
        render(
          <ReaderRecordNavigationRail
            snapshot={makeSnapshot(threeUnits, {
              semantic_outline: makeOutlineDto({ status: "ready" }),
            })}
            plateDocument={threeDoc()}
          />,
        );

        await openSemanticPanel();
        mockFetchSuccess(outcome, {
          outcome,
          job_id: null,
          detail: null,
        });

        fireEvent.click(
          screen.getByTestId("reader-record-outline-resolve-n1"),
        );

        await waitFor(() =>
          expect(
            screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
          ).toBeTruthy(),
        );
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1")
            .textContent,
        ).toBe(expectedMessage);
        // No snapshot reload on non-succeeded outcomes.
        // (onRequestSnapshotReload not provided → not called.)
      },
    );

    it("BFF error (ok:false) surfaces '无法解析此段' feedback", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchBffError(409, "section_translation_conflict", "段落内容已更新");

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );
      // BFF never leaks upstream message; UI shows generic rejected message.
      expect(
        screen.getByTestId("reader-record-outline-resolve-feedback-n1")
          .textContent,
      ).toBe("无法解析此段");
    });

    it("network failure surfaces '网络异常，请稍后重试'", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      vi.mocked(globalThis.fetch).mockRejectedValue(new Error("network"));

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.getByTestId("reader-record-outline-resolve-feedback-n1")
          .textContent,
      ).toBe("网络异常，请稍后重试");
    });

    it("per-row isolation: clicking row A does not put row B in loading", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      // Never resolves → both would stay in loading if clicked.
      vi.mocked(globalThis.fetch).mockImplementation(
        () => new Promise(() => {}),
      );

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-loading-n1"),
        ).toBeTruthy(),
      );
      // Row n3 still idle — its resolve chip still present.
      expect(
        screen.getByTestId("reader-record-outline-resolve-n3"),
      ).toBeTruthy();
      expect(
        screen.queryByTestId("reader-record-outline-resolve-loading-n3"),
      ).toBeNull();
    });

    it("snapshot identity change clears all row state", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      vi.mocked(globalThis.fetch).mockImplementation(
        () => new Promise(() => {}),
      );

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-loading-n1"),
        ).toBeTruthy(),
      );

      // Switch source identity → all row state clears.
      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            baseId: "base_2",
            generation: 1,
            semantic_outline: makeOutlineDto({
              status: "ready",
              source_identity: { base_id: "base_2", generation: 1 },
            }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      // Even though fetch never resolved, the chip returns because the row
      // state map was reset.
      await waitFor(() =>
        expect(
          screen.queryByTestId("reader-record-outline-resolve-loading-n1"),
        ).toBeNull(),
      );
    });

    it("outline revision change clears all row state", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("rejected", {
        outcome: "rejected",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      // New outline revision → state cleared.
      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({
              status: "ready",
              publication: {
                outline_revision: "rev_2",
                layer_id: "layer_ol",
                published_at: "2026-07-18T00:00:00Z",
              },
            }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await waitFor(() =>
        expect(
          screen.queryByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeNull(),
      );
      // Chip returns (after re-opening semantic panel since rerender may
      // force deterministic).
      hoverTick(0);
      await waitFor(() =>
        expect(
          screen
            .getByTestId("reader-record-navigation-panel")
            .classList.contains("pointer-events-none"),
        ).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-n1"),
        ).toBeTruthy(),
      );
    });

    it("resolve chip is its own accessible command tab stop (tabIndex=0)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();

      // The chip is its own accessible command tab stop — not trapped
      // behind the parent row's roving tabindex.
      const chip = screen.getByTestId("reader-record-outline-resolve-n1");
      expect(chip.getAttribute("tabindex")).toBe("0");
      expect(chip.tagName).toBe("BUTTON");
    });

    it("resolve chip can receive focus via Tab and is independently focusable", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();

      const chip = screen.getByTestId("reader-record-outline-resolve-n1");
      chip.focus();
      expect(document.activeElement).toBe(chip);
    });

    it("Enter on resolve chip sends full range witness (keyboard activation)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      const chip = screen.getByTestId("reader-record-outline-resolve-n1");
      chip.focus();
      fireEvent.keyDown(chip, { key: "Enter" });

      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
      const call = vi.mocked(globalThis.fetch).mock.calls[0]!;
      const body = JSON.parse(String(call[1]?.body));
      // Full range witness — never node-only.
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        startAnchorSegmentId: null,
        endAnchorSegmentId: null,
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("Space on resolve chip sends full range witness (keyboard activation)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      const chip = screen.getByTestId("reader-record-outline-resolve-n1");
      chip.focus();
      fireEvent.keyDown(chip, { key: " " });

      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
      const call = vi.mocked(globalThis.fetch).mock.calls[0]!;
      const body = JSON.parse(String(call[1]?.body));
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("chip keyboard activation does not propagate to parent row (no body scroll)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      vi.mocked(window.scrollTo).mockClear();
      const chip = screen.getByTestId("reader-record-outline-resolve-n1");
      chip.focus();
      fireEvent.keyDown(chip, { key: "Enter" });

      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
      // Parent row's onClick triggers body scroll — must not fire when
      // chip is keyboard-activated.
      expect(window.scrollTo).not.toHaveBeenCalled();
    });

    it("chip ArrowDown/Escape do not bubble to parent row's roving keyboard handler", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();

      const chip = screen.getByTestId("reader-record-outline-resolve-n1");
      const n1 = screen.getByTestId("reader-record-outline-node-n1");
      const n2 = screen.getByTestId("reader-record-outline-node-n2");

      // Put roving focus on n1.
      n1.focus();
      expect(document.activeElement).toBe(n1);

      // Move focus to the chip and press ArrowDown — must NOT move roving
      // to n2 (the parent row's roving handler must not fire).
      chip.focus();
      fireEvent.keyDown(chip, { key: "ArrowDown" });
      expect(document.activeElement).toBe(chip);
      // Roving tabindex on rows unchanged: n1 stays at 0, n2 stays at -1.
      expect(n1.getAttribute("tabindex")).toBe("0");
      expect(n2.getAttribute("tabindex")).toBe("-1");

      // Escape on chip must NOT return focus to the trigger.
      fireEvent.keyDown(chip, { key: "Escape" });
      expect(document.activeElement).toBe(chip);
    });

    it("chip click does not propagate to the parent row (no body scroll)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("succeeded");

      vi.mocked(window.scrollTo).mockClear();
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
      // Parent row's onClick triggers body scroll — must not fire when chip
      // is clicked.
      expect(window.scrollTo).not.toHaveBeenCalled();
    });

    it("rail roving keyboard navigation unaffected by chips", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      fireEvent.click(trigger);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );

      const n1 = screen.getByTestId("reader-record-outline-node-n1");
      const n2 = screen.getByTestId("reader-record-outline-node-n2");
      n1.focus();
      fireEvent.keyDown(n1, { key: "ArrowDown" });
      await waitFor(() => expect(document.activeElement).toBe(n2));
      fireEvent.keyDown(n2, { key: "Escape" });
      await waitFor(() => expect(document.activeElement).toBe(trigger));
    });

    it("L0/L1 navigation items unchanged when outline is trusted but surface is deterministic", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      hoverTick(0);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );

      // Deterministic L0 items (U1/U2/U3) are present and clickable; no chip.
      const detButtons = panel.querySelectorAll("ol button[data-unit-id], ol button:not([data-outline-node-id])");
      expect(detButtons.length).toBeGreaterThan(0);
      expect(
        screen.queryByTestId("reader-record-outline-resolve-n1"),
      ).toBeNull();
    });

    // -------------------------------------------------------------------------
    // T5.6c-P2 — retry action on non-success outcomes
    //
    // The P1 implementation replaced the action button with a feedback span
    // on retry_later / rejected / superseded / etc., leaving the user with
    // no in-row way to retry. P2 keeps the feedback span AND renders a
    // "重试" button next to it so the user can retry without waiting for a
    // snapshot refresh. The retry button uses the same testid as the idle
    // action so the accessible action locator is stable across states.
    // -------------------------------------------------------------------------

    it("retry_later surfaces feedback AND a retry action button (same testid, label '重试')", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      // Feedback span appears with the message.
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.getByTestId("reader-record-outline-resolve-feedback-n1")
          .textContent,
      ).toBe("稍后重试");

      // The retry action button is also present, with the same testid as
      // the idle action — the accessible action locator is stable.
      const retryAction = screen.getByTestId(
        "reader-record-outline-resolve-n1",
      );
      expect(retryAction.tagName).toBe("BUTTON");
      expect(retryAction.getAttribute("data-resolve-action")).toBe("retry");
      expect(retryAction.textContent).toBe("重试");
      expect(retryAction.getAttribute("aria-label")).toBe("重试：Root A");
      // The retry action is its own accessible command tab stop.
      expect(retryAction.getAttribute("tabindex")).toBe("0");
    });

    it("clicking retry sends a second full-range witness request", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      // First click → retry_later feedback.
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      // Second mock: succeeded (the user retries after a transient failure).
      mockFetchSuccess("succeeded");

      // Retry click → second fetch with full range witness.
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      const secondCall = vi.mocked(globalThis.fetch).mock.calls[1]!;
      const body = JSON.parse(String(secondCall[1]?.body));
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        startAnchorSegmentId: null,
        endAnchorSegmentId: null,
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("Enter on retry button sends a second full-range witness (keyboard activation)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      mockFetchSuccess("succeeded");
      const retryAction = screen.getByTestId(
        "reader-record-outline-resolve-n1",
      );
      retryAction.focus();
      fireEvent.keyDown(retryAction, { key: "Enter" });

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      const body = JSON.parse(
        String(vi.mocked(globalThis.fetch).mock.calls[1]![1]?.body),
      );
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("Space on retry button sends a second full-range witness (keyboard activation)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("rejected", {
        outcome: "rejected",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      mockFetchSuccess("succeeded");
      const retryAction = screen.getByTestId(
        "reader-record-outline-resolve-n1",
      );
      retryAction.focus();
      fireEvent.keyDown(retryAction, { key: " " });

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      const body = JSON.parse(
        String(vi.mocked(globalThis.fetch).mock.calls[1]![1]?.body),
      );
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("retry clears old feedback and enters loading state", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      // Second fetch never resolves → retry puts the row back into loading.
      vi.mocked(globalThis.fetch).mockImplementation(
        () => new Promise(() => {}),
      );

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      // Old feedback cleared; loading span visible; retry action button
      // temporarily hidden (loading state replaces both feedback and
      // action).
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-loading-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.queryByTestId("reader-record-outline-resolve-feedback-n1"),
      ).toBeNull();
      expect(
        screen.queryByTestId("reader-record-outline-resolve-n1"),
      ).toBeNull();
    });

    it("already_covered_or_inflight retry still hits fetch (queued-recovery re-trigger)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("already_covered_or_inflight", {
        outcome: "already_covered_or_inflight",
        job_id: "job_existing",
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.getByTestId("reader-record-outline-resolve-feedback-n1")
          .textContent,
      ).toBe("已在解析中");

      // The retry action is present even for already_covered_or_inflight —
      // the user can re-trigger the request path so the backend
      // queued-recovery drain can be re-invoked.
      expect(
        screen.getByTestId("reader-record-outline-resolve-n1"),
      ).toBeTruthy();

      mockFetchSuccess("succeeded");
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      // Second request still goes through the same fetch path (no
      // client-side short-circuit for already_covered_or_inflight).
      const secondCall = vi.mocked(globalThis.fetch).mock.calls[1]!;
      expect(secondCall[0]).toBe(
        "/api/web/reader-plate/records/record_1/section-translation",
      );
      expect(secondCall[1]?.method).toBe("POST");
    });

    it("BFF error retry sends a second full-range witness", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchBffError(409, "section_translation_conflict", "段落内容已更新");

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      mockFetchSuccess("succeeded");
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      const body = JSON.parse(
        String(vi.mocked(globalThis.fetch).mock.calls[1]![1]?.body),
      );
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("network failure retry sends a second full-range witness", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      vi.mocked(globalThis.fetch).mockRejectedValue(new Error("network"));

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen.getByTestId("reader-record-outline-resolve-feedback-n1")
          .textContent,
      ).toBe("网络异常，请稍后重试");

      mockFetchSuccess("succeeded");
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      const body = JSON.parse(
        String(vi.mocked(globalThis.fetch).mock.calls[1]![1]?.body),
      );
      expect(body).toMatchObject({
        startUnitId: "unit_1",
        endUnitId: "unit_2",
        nodeId: "n1",
        outlineRevision: "rev_1",
      });
    });

    it("retry button is its own accessible command tab stop (tabIndex=0)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("superseded", {
        outcome: "superseded",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      const retryAction = screen.getByTestId(
        "reader-record-outline-resolve-n1",
      );
      expect(retryAction.getAttribute("tabindex")).toBe("0");
      expect(retryAction.tagName).toBe("BUTTON");

      // Independently focusable.
      retryAction.focus();
      expect(document.activeElement).toBe(retryAction);
    });

    it("retry click does not propagate to parent row (no body scroll)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      vi.mocked(window.scrollTo).mockClear();
      mockFetchSuccess("succeeded");
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      // Parent row's onClick triggers body scroll — must not fire when
      // retry button is clicked.
      expect(window.scrollTo).not.toHaveBeenCalled();
    });

    it("retry keyboard activation does not propagate to parent row (no body scroll)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      vi.mocked(window.scrollTo).mockClear();
      mockFetchSuccess("succeeded");
      const retryAction = screen.getByTestId(
        "reader-record-outline-resolve-n1",
      );
      retryAction.focus();
      fireEvent.keyDown(retryAction, { key: "Enter" });

      await waitFor(() =>
        expect(globalThis.fetch).toHaveBeenCalledTimes(2),
      );
      expect(window.scrollTo).not.toHaveBeenCalled();
    });

    it("retry ArrowDown/Escape do not bubble to parent row's roving keyboard handler", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      const retryAction = screen.getByTestId(
        "reader-record-outline-resolve-n1",
      );
      const n1 = screen.getByTestId("reader-record-outline-node-n1");
      const n2 = screen.getByTestId("reader-record-outline-node-n2");

      // Put roving focus on n1, then move to retry action.
      n1.focus();
      expect(document.activeElement).toBe(n1);
      retryAction.focus();

      // ArrowDown on retry action must NOT move roving to n2.
      fireEvent.keyDown(retryAction, { key: "ArrowDown" });
      expect(document.activeElement).toBe(retryAction);
      expect(n1.getAttribute("tabindex")).toBe("0");
      expect(n2.getAttribute("tabindex")).toBe("-1");

      // Escape on retry action must NOT return focus to the trigger.
      fireEvent.keyDown(retryAction, { key: "Escape" });
      expect(document.activeElement).toBe(retryAction);
    });

    it("rail roving keyboard navigation unaffected by retry button presence", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      const trigger = screen.getByTestId("reader-record-outline-trigger");
      fireEvent.click(trigger);
      const panel = screen.getByTestId("reader-record-navigation-panel");
      await waitFor(() =>
        expect(panel.classList.contains("pointer-events-none")).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );

      // Put row n1 into feedback state so the retry button renders.
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });
      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );

      // Row roving still works alongside the retry button.
      const n1 = screen.getByTestId("reader-record-outline-node-n1");
      const n2 = screen.getByTestId("reader-record-outline-node-n2");
      n1.focus();
      fireEvent.keyDown(n1, { key: "ArrowDown" });
      await waitFor(() => expect(document.activeElement).toBe(n2));
      fireEvent.keyDown(n2, { key: "Escape" });
      await waitFor(() => expect(document.activeElement).toBe(trigger));
    });

    it("snapshot identity change clears feedback + retry state (row returns to idle)", async () => {
      renderTargets(["unit_1", "unit_2", "unit_3"]);
      const { rerender } = render(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            semantic_outline: makeOutlineDto({ status: "ready" }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      await openSemanticPanel();
      mockFetchSuccess("retry_later", {
        outcome: "retry_later",
        job_id: null,
        detail: null,
      });

      fireEvent.click(screen.getByTestId("reader-record-outline-resolve-n1"));
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeTruthy(),
      );
      // Retry action present.
      expect(
        screen.getByTestId("reader-record-outline-resolve-n1"),
      ).toBeTruthy();

      // Switch source identity → all row state clears.
      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(threeUnits, {
            baseId: "base_2",
            generation: 1,
            semantic_outline: makeOutlineDto({
              status: "ready",
              source_identity: { base_id: "base_2", generation: 1 },
            }),
          })}
          plateDocument={threeDoc()}
        />,
      );

      // Feedback cleared.
      await waitFor(() =>
        expect(
          screen.queryByTestId("reader-record-outline-resolve-feedback-n1"),
        ).toBeNull(),
      );
      // Idle action returns (resolve, not retry).
      hoverTick(0);
      await waitFor(() =>
        expect(
          screen
            .getByTestId("reader-record-navigation-panel")
            .classList.contains("pointer-events-none"),
        ).toBe(false),
      );
      fireEvent.click(
        screen.getByTestId("reader-record-outline-mode-semantic"),
      );
      await waitFor(() =>
        expect(
          screen.getByTestId("reader-record-outline-resolve-n1"),
        ).toBeTruthy(),
      );
      expect(
        screen
          .getByTestId("reader-record-outline-resolve-n1")
          .getAttribute("data-resolve-action"),
      ).toBe("resolve");
    });
  });
});
