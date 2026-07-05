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
      // First unit is the unit start by default.
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

  it("renders the mini rail ticks for navigation items", () => {
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
    expect(rail.querySelectorAll("button[data-navigation-unit-id]")).toHaveLength(2);
    expect(screen.getByLabelText("First unit")).toBeTruthy();
    expect(screen.getByLabelText("Second unit")).toBeTruthy();
  });

  it("marks the first item active by default", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0 },
      { unit_id: "unit_2", order_index: 1 },
    ]);
    const plateDocument = makePlateDocument([
      makeParagraph("unit_1", "First paragraph."),
      makeParagraph("unit_2", "Second paragraph."),
    ]);
    renderTargets(["unit_1", "unit_2"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const ticks = screen.getAllByRole("button");
    expect(ticks).toHaveLength(2);
    expect(ticks[0]?.getAttribute("aria-current")).toBe("true");
    expect(ticks[1]?.getAttribute("aria-current")).toBeNull();
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
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.classList.contains("invisible")).toBe(true);

    fireEvent.mouseEnter(miniRail);
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

  it("does not open the detail panel from the nav root or hidden panel geometry", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    const miniRail = screen.getByTestId("reader-record-mini-rail");

    fireEvent.mouseEnter(rail);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.classList.contains("invisible")).toBe(true);

    fireEvent.mouseEnter(panel);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.classList.contains("invisible")).toBe(true);

    fireEvent.mouseEnter(miniRail);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
  });

  it("makes panel rows non-tabbable while the panel is closed", async () => {
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
    expect(rows[0]?.getAttribute("tabindex")).toBe("-1");
    expect(rows[1]?.getAttribute("tabindex")).toBe("-1");

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    fireEvent.mouseEnter(miniRail);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    expect(rows[0]?.getAttribute("tabindex")).toBe("0");
    expect(rows[1]?.getAttribute("tabindex")).toBe("0");
  });

  it("keeps the panel open when pointer moves from ticks into the detail panel", async () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    fireEvent.mouseEnter(miniRail);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    // Move from rail into panel (relatedTarget is the panel element).
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
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    fireEvent.mouseEnter(miniRail);
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

  it("renders panel rows with labels and segment indices", () => {
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

  it("scrolls the unit start target into view using window.scrollTo", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    const { paragraphs } = renderTargets(["unit_1"]);
    setRectTop(paragraphs[0]!, 500, 100);
    vi.stubGlobal("scrollY", 120);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const panelRow = panel.querySelector("button");
    expect(panelRow).toBeTruthy();

    fireEvent.click(panelRow!);

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 500 + 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("scrolls the nearest scrollable ancestor instead of window when content lives in a ScrollArea", () => {
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

    const tick = screen.getByLabelText("Alpha");
    fireEvent.click(tick);

    expect(window.scrollTo).not.toHaveBeenCalled();
    expect(containerScrollTo).toHaveBeenCalledWith({
      top: 800 + 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("activates the clicked row immediately and does not scroll the rail tick", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const tick = screen.getByLabelText("Alpha");
    fireEvent.click(tick);

    // The rail tick itself is not the scroll target.
    expect(window.scrollTo).toHaveBeenCalled();
    expect(tick.getAttribute("aria-current")).toBe("true");
  });

  it("does not pick the rail tick as target when it shares a unit id with a body paragraph", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const tick = screen.getByLabelText("Alpha");
    fireEvent.click(tick);

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
    // Alpha sits just above the safe line, Beta well below it.
    const { paragraphs } = renderTargets(["unit_1", "unit_2"], [60, 200]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    // Click Beta; active locks to Beta.
    const betaTick = screen.getByLabelText("Beta");
    fireEvent.click(betaTick);
    expect(betaTick.getAttribute("aria-current")).toBe("true");

    // Simulate smooth-scroll progress: Alpha above the safe line, Beta below.
    setRectTop(paragraphs[0]!, -10, 100);
    setRectTop(paragraphs[1]!, 100, 100);
    triggerScroll();

    // Wait within the lock window.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.getByLabelText("Beta").getAttribute("aria-current")).toBe("true");

    // After the lock expires, the deterministic algorithm picks Alpha as the
    // last unit above the safe line.
    await new Promise((resolve) => setTimeout(resolve, 600));
    triggerScroll();
    await waitFor(() =>
      expect(screen.getByLabelText("Alpha").getAttribute("aria-current")).toBe("true"),
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

    // All targets are below the safe line (64px); the first below is active.
    triggerScroll();
    await waitFor(() =>
      expect(screen.getByLabelText("Alpha").getAttribute("aria-current")).toBe("true"),
    );

    // Move Beta above the safe line; it becomes the last-above active unit.
    setRectTop(paragraphs[0]!, -20, 100);
    setRectTop(paragraphs[1]!, 40, 100);
    setRectTop(paragraphs[2]!, 200, 100);
    triggerScroll();
    await waitFor(() =>
      expect(screen.getByLabelText("Beta").getAttribute("aria-current")).toBe("true"),
    );
  });

  it("falls back to any paragraph with the unit id when no unit start marker exists", () => {
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

    const tick = screen.getByLabelText("Alpha");
    fireEvent.click(tick);

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("scrolls the unit target when a tick is activated by keyboard", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    const { paragraphs } = renderTargets(["unit_1"]);
    setRectTop(paragraphs[0]!, 80, 100);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const tick = screen.getByLabelText("Alpha");
    fireEvent.keyDown(tick, { key: "Enter" });

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

  it("uses nav semantics and keeps ticks as plain buttons, not menuitems", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    renderTargets(["unit_1"]);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.tagName.toLowerCase()).toBe("nav");
    expect(rail.getAttribute("aria-label")).toBe("阅读定位");

    const tick = screen.getByLabelText("Alpha");
    expect(tick.tagName.toLowerCase()).toBe("button");
    expect(tick.getAttribute("role")).toBeNull();
    expect(tick.getAttribute("aria-current")).toBe("true");
  });

  it("activates ticks via keyboard without relying on menuitem role", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Alpha" },
    ]);
    const plateDocument = makePlateDocument([makeParagraph("unit_1", "Alpha paragraph.")]);
    const { paragraphs } = renderTargets(["unit_1"]);
    setRectTop(paragraphs[0]!, 80, 100);

    render(<ReaderRecordNavigationRail snapshot={snapshot} plateDocument={plateDocument} />);

    const tick = screen.getByLabelText("Alpha");
    fireEvent.keyDown(tick, { key: "Enter" });

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 80 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("renders compressed ticks with a flexible hit area and a separate visual bar", () => {
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
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = rail.querySelectorAll("button[data-navigation-unit-id]");
    expect(ticks).toHaveLength(2);
    expect(Array.from(miniRail.children).every((child) => child.tagName === "BUTTON")).toBe(
      true,
    );

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

  it("hides tick visuals while the detail panel is open", async () => {
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
    const visualBars = Array.from(miniRail.querySelectorAll("button span"));
    expect(visualBars).toHaveLength(2);
    expect(visualBars.every((bar) => bar.className.includes("opacity-0"))).toBe(
      false,
    );

    fireEvent.mouseEnter(miniRail);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(visualBars.every((bar) => bar.className.includes("opacity-0"))).toBe(
      true,
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

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    fireEvent.mouseEnter(miniRail);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const rows = panel.querySelectorAll("button");
    expect(rows).toHaveLength(2);

    const activeRow = rows[0]!;
    expect(activeRow.className).not.toContain("border-l-");
    expect(activeRow.className).toContain("bg-ink/[0.055]");
    expect(activeRow.className).toContain("font-medium");
    expect(activeRow.querySelector("span[aria-hidden='true']")).toBeNull();

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
    expect(rail.className).toContain("sticky");
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

    // Panel carries a semantic class/data attribute for production styling,
    // not just a test id.
    expect(panel.className).toContain("reader-record-navigation-panel");
    expect(panel.getAttribute("data-reader-record-navigation-panel")).toBe("true");

    // Panel is rendered first and overlays the mini tick strip, matching the
    // Notion-style outline popover instead of creating a second adjacent focus.
    expect(panel.className).toContain("right-0");
    expect(panel.className).toContain("z-10");
    expect(panel.className).toContain("origin-right");
    expect(ticks?.className).toContain("right-0");
  });

  it("applies hover state to the whole mini tick button, not only the inner span", () => {
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
    const tick = screen.getByLabelText("Beta");
    expect(tick.className).toContain("group");
    expect(tick.querySelector("span")?.className).toContain("group-hover:bg-ink/40");
  });
});
