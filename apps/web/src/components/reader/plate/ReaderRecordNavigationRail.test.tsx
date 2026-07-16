/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";
import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  type ReaderRecordPlateDocument,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

import { ReaderRecordNavigationRail } from "./ReaderRecordNavigationRail";

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
  units: { unit_id: string; order_index: number; label?: string | null }[],
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
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "sha256",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 100,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: {
      units: units.map((u) => ({
        ...u,
        unit_type: "body" as const,
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
  };
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
  const ticks = miniRail.querySelectorAll("span[data-navigation-unit-id]");
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

  it("anchors the detail panel to the hovered outline tick", async () => {
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
      expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBe("160");
    });
    expect(panel.style.top).toBe("160px");

    fireEvent.mouseEnter(ticks[1]!);
    await waitFor(() => {
      expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBe("340");
    });
    expect(panel.style.top).toBe("340px");
  });

  it("anchors the canvas detail panel to the hovered outline tick", async () => {
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

    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBe("340");
    expect(panel.style.top).toBe("340px");
    expect(panel.className).toContain("right-[calc(100%+8px)]");
    expect(panel.className).not.toContain("top-1/2");
    expect(panel.className).not.toContain("-translate-y-1/2");
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
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");

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
    expect(activeRow.className).toContain("bg-ink/[0.055]");
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

  it("hovering a visual tick anchors the panel to that tick's position", async () => {
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

    // The anchor-y attribute should be set (non-empty) and correspond to
    // the hovered tick's vertical center within the rail wrapper.
    const anchorY = panel.getAttribute("data-reader-record-navigation-panel-anchor-y");
    expect(anchorY).not.toBeNull();
    expect(anchorY).not.toBe("");

    // Hover the first tick — anchor should change to a different value.
    fireEvent.mouseEnter(ticks[0]!);
    await waitFor(() => {
      const newAnchorY = panel.getAttribute("data-reader-record-navigation-panel-anchor-y");
      expect(newAnchorY).not.toBeNull();
      // The values may differ because the ticks are at different vertical
      // positions; at minimum the attribute must be present and numeric.
      expect(Number.isFinite(Number(newAnchorY))).toBe(true);
    });
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
});
