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
import type { ReaderSemanticOutlineProjectionDto } from "@/lib/reader-plate/projection/semantic-outline";

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

/** The three units every outline fixture in this file is built on. */
function threeUnits(): SnapshotUnitInput[] {
  return [
    { unit_id: "unit_1", order_index: 1, label: "U1" },
    { unit_id: "unit_2", order_index: 2, label: "U2" },
    { unit_id: "unit_3", order_index: 3, label: "U3" },
  ];
}

function threeDoc(): ReaderRecordPlateDocument {
  return makePlateDocument([
    makeParagraph("unit_1", "A"),
    makeParagraph("unit_2", "B"),
    makeParagraph("unit_3", "C"),
  ]);
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

/** Render one paragraph per unit and mark each as its unit's start target. */
function renderThreeStartTargets(tops?: number[]): RenderedTarget {
  const { body, paragraphs } = renderTargets(
    ["unit_1", "unit_2", "unit_3"],
    tops,
  );
  paragraphs[1]!.setAttribute("data-reader-record-unit-start", "true");
  paragraphs[2]!.setAttribute("data-reader-record-unit-start", "true");
  return { body, paragraphs };
}

function triggerScroll() {
  window.dispatchEvent(new Event("scroll"));
}

/** Hover a visual tick to open the panel (simulates mouse hover on the rail). */
function hoverTick(index = 0) {
  const miniRail = screen.getByTestId("reader-record-mini-rail");
  // Ticks are keyed by outline node id (depth-1 roots only).
  const ticks = miniRail.querySelectorAll("span[data-navigation-tick-key]");
  fireEvent.mouseEnter(ticks[index]!);
}

async function openPanel() {
  hoverTick(0);
  const panel = screen.getByTestId("reader-record-navigation-panel");
  await waitFor(() =>
    expect(panel.classList.contains("pointer-events-none")).toBe(false),
  );
  return panel;
}

/**
 * Assert the removed two-surface contract stays gone: no 定位/大纲 mode
 * switch, no 解析此段 per-row action, no retry affordance.
 */
function expectRemovedContractAbsent() {
  expect(screen.queryByTestId("reader-record-outline-mode-switch")).toBeNull();
  expect(
    screen.queryByTestId("reader-record-outline-mode-deterministic"),
  ).toBeNull();
  expect(
    screen.queryByTestId("reader-record-outline-mode-semantic"),
  ).toBeNull();
  const resolveNodes = document.querySelectorAll(
    '[data-testid^="reader-record-outline-resolve"]',
  );
  expect(resolveNodes).toHaveLength(0);
  expect(screen.queryByText("解析此段")).toBeNull();
  expect(screen.queryByText("正在解析…")).toBeNull();
  expect(screen.queryByText("重试")).toBeNull();
  expect(screen.queryByText("定位")).toBeNull();
  expect(screen.queryByText("大纲")).toBeNull();
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
});

describe("ReaderRecordNavigationRail", () => {
  // ---------------------------------------------------------------------------
  // Single source-agnostic outline: auto-show + negative contract
  // ---------------------------------------------------------------------------

  it("auto-shows the single semantic outline rail without hover and without the removed two-surface contract", () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    // Rail + trigger + ticks render immediately — no hover required.
    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.tagName.toLowerCase()).toBe("nav");
    expect(rail.getAttribute("aria-label")).toBe("内容大纲");
    expect(rail.getAttribute("data-outline-source")).toBe("semantic");
    expect(rail.getAttribute("data-layout")).toBe("viewport");

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    expect(trigger.getAttribute("data-reader-record-outline-trigger")).toBe(
      "true",
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-controls")).toBeTruthy();

    // One tick per depth-1 root (n1, n3); keyed by outline node id.
    const ticks = screen
      .getByTestId("reader-record-mini-rail")
      .querySelectorAll("span[data-navigation-tick-key]");
    expect(ticks).toHaveLength(2);
    expect(ticks[0]?.getAttribute("data-navigation-tick-key")).toBe("n1");
    expect(ticks[0]?.getAttribute("data-outline-node-id")).toBe("n1");
    expect(ticks[1]?.getAttribute("data-navigation-tick-key")).toBe("n3");

    // The panel is already rendered (hidden via aria-hidden until opened).
    const panel = screen.getByTestId("reader-record-navigation-panel");
    expect(panel.getAttribute("aria-hidden")).toBe("true");

    expectRemovedContractAbsent();
  });

  it("renders nothing — no rail, no mini-rail, no trigger, no unit-list fallback — when no outline is usable", () => {
    renderThreeStartTargets();
    const units = threeUnits();
    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, { semantic_outline: null })}
        plateDocument={threeDoc()}
      />,
    );
    expect(screen.queryByTestId("reader-record-navigation-rail")).toBeNull();

    for (const status of ["pending", "failed", "stale", "unavailable"] as const) {
      rerender(
        <ReaderRecordNavigationRail
          snapshot={makeSnapshot(units, {
            semantic_outline: makeOutlineDto({ status }),
          })}
          plateDocument={threeDoc()}
        />,
      );
      expect(screen.queryByTestId("reader-record-navigation-rail")).toBeNull();
    }

    // Source-identity mismatch → fail closed.
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({
            source_identity: { base_id: "other", generation: 1 },
          }),
        })}
        plateDocument={threeDoc()}
      />,
    );
    expect(screen.queryByTestId("reader-record-navigation-rail")).toBeNull();

    // Every node start/end unit missing from the unit universe → fail closed.
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
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
    expect(screen.queryByTestId("reader-record-navigation-rail")).toBeNull();

    // No unit-list fallback, no placeholder of any kind.
    expect(screen.queryByTestId("reader-record-mini-rail")).toBeNull();
    expect(screen.queryByTestId("reader-record-outline-trigger")).toBeNull();
    expect(screen.queryByTestId("reader-record-navigation-panel")).toBeNull();
    expect(document.querySelector("nav")).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // Open / close contract
  // ---------------------------------------------------------------------------

  it("opens the panel on tick hover and closes it after leaving the rail", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");
    const trigger = screen.getByTestId("reader-record-outline-trigger");

    expect(panel.classList.contains("pointer-events-none")).toBe(true);
    expect(panel.getAttribute("aria-hidden")).toBe("true");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(panel.classList.contains("visible")).toBe(true);
    expect(panel.getAttribute("aria-hidden")).toBe("false");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.mouseLeave(rail);
    await waitFor(
      () => expect(panel.classList.contains("pointer-events-none")).toBe(true),
      { timeout: 500 },
    );
    expect(panel.classList.contains("invisible")).toBe(true);
    expect(panel.getAttribute("aria-hidden")).toBe("true");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("opens the panel via trigger click and closes it on a second click (toggle)", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    expect(panel.classList.contains("pointer-events-none")).toBe(true);

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(trigger.getAttribute("aria-label")).toBe("关闭内容大纲");

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(true),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps the panel open while the pointer moves from the ticks into the panel", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    fireEvent.mouseLeave(rail, { relatedTarget: panel });
    fireEvent.mouseEnter(panel);

    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(panel.classList.contains("pointer-events-none")).toBe(false);
    expect(panel.getAttribute("aria-hidden")).toBe("false");

    // Leaving the panel itself closes it.
    fireEvent.mouseLeave(panel);
    await waitFor(
      () => expect(panel.classList.contains("pointer-events-none")).toBe(true),
      { timeout: 500 },
    );
  });

  it("closes the panel when focus leaves the rail wrapper", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );

    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    fireEvent.blur(panel, { relatedTarget: outside });

    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(true),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  // ---------------------------------------------------------------------------
  // Click-to-scroll: navigation only, never a network request
  // ---------------------------------------------------------------------------

  it("clicking a row scrolls via window.scrollTo and never calls fetch", async () => {
    const { paragraphs } = renderThreeStartTargets([100, 500, 700]);
    vi.stubGlobal("scrollY", 0);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-n1"));

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 100 - 56 - 8,
      behavior: "smooth",
    });
    expect(
      screen.getByTestId("reader-record-outline-node-n1").getAttribute("aria-current"),
    ).toBe("true");
    // Row clicks are pure navigation — the section-translation fetch is gone.
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    // Clicking the depth-2 child scrolls to its own start unit (unit_2).
    vi.mocked(window.scrollTo).mockClear();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-n2"));
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 500 - 56 - 8,
      behavior: "smooth",
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    void paragraphs;
  });

  it("prefers the start anchor segment paragraph when it matches the start unit", async () => {
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
        snapshot={makeSnapshot(threeUnits(), {
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

    const panel = await openPanel();
    void panel;
    fireEvent.click(screen.getByTestId("reader-record-outline-node-na"));
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 120 - 56 - 8,
      behavior: "smooth",
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("falls back to any paragraph with the unit id when no unit start marker exists", async () => {
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

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto({}, [
            {
              node_id: "na",
              parent_node_id: null,
              depth: 1,
              title: "Anchored",
              start_unit_id: "unit_1",
              end_unit_id: "unit_1",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 1,
            },
          ]),
        })}
        plateDocument={threeDoc()}
      />,
    );

    await openPanel();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-na"));

    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 120 - 56 - 8,
      behavior: "smooth",
    });
  });

  it("treats a row click as a no-op when the row has no DOM target", async () => {
    renderThreeStartTargets([100, 300, 500]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    // Activate n1 first so we can prove a failed click keeps prior state.
    fireEvent.click(screen.getByTestId("reader-record-outline-node-n1"));
    expect(
      screen.getByTestId("reader-record-outline-node-n1").getAttribute("aria-current"),
    ).toBe("true");

    // Remove the whole plate document → no target can resolve.
    document.querySelector(".reader-record-plate-document")?.remove();
    vi.mocked(window.scrollTo).mockClear();

    const childBtn = screen.getByTestId("reader-record-outline-node-n2");
    fireEvent.click(childBtn);

    expect(window.scrollTo).not.toHaveBeenCalled();
    expect(childBtn.getAttribute("aria-current")).toBeNull();
    // Previous active remains.
    expect(
      screen.getByTestId("reader-record-outline-node-n1").getAttribute("aria-current"),
    ).toBe("true");
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    void panel;
  });

  it("keeps the clicked item active during the 700ms scroll lock even if scroll fires", async () => {
    const { paragraphs } = renderThreeStartTargets([60, 300, 500]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    const n1 = screen.getByTestId("reader-record-outline-node-n1");
    const n3 = screen.getByTestId("reader-record-outline-node-n3");

    fireEvent.click(n3);
    expect(n3.getAttribute("aria-current")).toBe("true");

    // During the smooth scroll, n1 would be the deterministic pick — the lock
    // must keep n3 active for 700ms.
    setRectTop(paragraphs[0]!, -10, 100);
    setRectTop(paragraphs[2]!, 100, 100);
    triggerScroll();

    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(n3.getAttribute("aria-current")).toBe("true");

    // After the lock expires, the scroll-spy algorithm picks n1.
    await new Promise((resolve) => setTimeout(resolve, 600));
    triggerScroll();
    await waitFor(() => expect(n1.getAttribute("aria-current")).toBe("true"));
    void panel;
  });

  // ---------------------------------------------------------------------------
  // Scroll-spy active algorithm
  // ---------------------------------------------------------------------------

  it("updates the active row on body scroll with the deepest covering outline item", async () => {
    const { paragraphs } = renderThreeStartTargets([60, 300, 500]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    // unit_1 under safeTop → n1 (the only covering item).
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "打开内容大纲，当前第 1 项",
      );
    });

    // unit_2 under safeTop → deepest covering item is the depth-2 child n2.
    setRectTop(paragraphs[0]!, -20, 100);
    setRectTop(paragraphs[1]!, 40, 100);
    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "打开内容大纲，当前第 2 项",
      );
    });
    await waitFor(() => {
      expect(
        screen
          .getByTestId("reader-record-outline-node-n2")
          .getAttribute("aria-current"),
      ).toBe("true");
    });
    expect(
      screen
        .getByTestId("reader-record-outline-node-n1")
        .getAttribute("aria-current"),
    ).toBeNull();

    // unit_3 under safeTop → n3 (root B) is the covering item.
    setRectTop(paragraphs[2]!, 50, 100);
    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "打开内容大纲，当前第 3 项",
      );
    });
    await waitFor(() => {
      expect(
        screen
          .getByTestId("reader-record-outline-node-n3")
          .getAttribute("aria-current"),
      ).toBe("true");
    });
  });

  it("has no active item in the lead zone (every root start below safeTop)", async () => {
    // All targets far below safeTop=64 → lead zone.
    renderThreeStartTargets([200, 400, 600]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe("打开内容大纲");
    });
    expect(trigger.getAttribute("aria-label")).not.toMatch(/当前第/);

    const panel = await openPanel();
    const rows = panel.querySelectorAll("button");
    expect(
      Array.from(rows).every((r) => r.getAttribute("aria-current") !== "true"),
    ).toBe(true);
    // Keyboard focus still lands on the first row without making it active.
    const tabbable = Array.from(rows).filter(
      (r) => r.getAttribute("tabindex") === "0",
    );
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0]?.getAttribute("aria-current")).toBeNull();
  });

  it("revalidates the target cache across detached/remounted nodes with the same ids", async () => {
    // First mount: all roots below safeTop → lead zone, but the spy warms the
    // target cache with the detached-soon nodes.
    const first = renderThreeStartTargets([200, 400, 600]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe("打开内容大纲");
    });

    // Simulate Plate setValue: detach old paragraphs, mount new ones with the
    // same unit ids but unit_1 above safeTop. The stale cache must not win.
    first.body.remove();
    renderThreeStartTargets([20, 400, 600]);

    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "打开内容大纲，当前第 1 项",
      );
    });
  });

  it("click resolves the live target after a detached remount with the same unit ids", async () => {
    const first = renderThreeStartTargets([20, 400, 600]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    // Warm the target cache via the scroll spy.
    triggerScroll();
    await waitFor(() => {
      expect(
        screen
          .getByTestId("reader-record-outline-trigger")
          .getAttribute("aria-label"),
      ).toMatch(/当前第 1 项/);
    });

    // Remount with the same unit ids; the n3 target now lives at top 500.
    first.body.remove();
    const second = renderThreeStartTargets([100, 300, 500]);
    setRectTop(second.paragraphs[2]!, 500, 100);
    vi.stubGlobal("scrollY", 0);

    await openPanel();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-n3"));

    // Must scroll using the *new* connected unit_3 node, not the detached cache.
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 500 - 56 - 8,
      behavior: "smooth",
    });
    expect(
      screen
        .getByTestId("reader-record-outline-node-n3")
        .getAttribute("aria-current"),
    ).toBe("true");
  });

  // ---------------------------------------------------------------------------
  // Keyboard: roving tabindex + Escape returns to trigger
  // ---------------------------------------------------------------------------

  it("makes panel rows non-tabbable while the panel is closed", () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const rows = panel.querySelectorAll("button");
    expect(rows).toHaveLength(3);
    // All rows are -1 when closed — no hidden tab stops.
    expect(
      Array.from(rows).every((r) => r.getAttribute("tabindex") === "-1"),
    ).toBe(true);
  });

  it("uses roving tabindex: exactly the focused row is tabbable when the panel is open", async () => {
    renderThreeStartTargets([60, 300, 500]);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    // Wait for the scroll spy to make n1 active (rAF tick after mount).
    await waitFor(() => {
      expect(
        screen
          .getByTestId("reader-record-outline-node-n1")
          .getAttribute("aria-current"),
      ).toBe("true");
    });

    const rows = panel.querySelectorAll("button");
    const tabbable = Array.from(rows).filter(
      (r) => r.getAttribute("tabindex") === "0",
    );
    expect(tabbable).toHaveLength(1);
    // Focus initializes to the active row (n1).
    expect(tabbable[0]?.getAttribute("aria-current")).toBe("true");
    expect(tabbable[0]?.getAttribute("data-outline-node-id")).toBe("n1");
  });

  it("supports ArrowDown/ArrowUp/Home/End to move the roving focus", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    const n1 = screen.getByTestId("reader-record-outline-node-n1");
    const n2 = screen.getByTestId("reader-record-outline-node-n2");
    const n3 = screen.getByTestId("reader-record-outline-node-n3");

    expect(n1.getAttribute("tabindex")).toBe("0");
    expect(n2.getAttribute("tabindex")).toBe("-1");

    // ArrowDown moves focus to the second row.
    fireEvent.keyDown(n1, { key: "ArrowDown" });
    await waitFor(() => {
      expect(n1.getAttribute("tabindex")).toBe("-1");
      expect(n2.getAttribute("tabindex")).toBe("0");
    });

    // ArrowDown again moves to the third row.
    fireEvent.keyDown(n2, { key: "ArrowDown" });
    await waitFor(() => {
      expect(n2.getAttribute("tabindex")).toBe("-1");
      expect(n3.getAttribute("tabindex")).toBe("0");
    });

    // ArrowUp moves back to the second row.
    fireEvent.keyDown(n3, { key: "ArrowUp" });
    await waitFor(() => {
      expect(n3.getAttribute("tabindex")).toBe("-1");
      expect(n2.getAttribute("tabindex")).toBe("0");
    });

    // End jumps to the last row; Home back to the first.
    fireEvent.keyDown(n2, { key: "End" });
    await waitFor(() => expect(n3.getAttribute("tabindex")).toBe("0"));
    fireEvent.keyDown(n3, { key: "Home" });
    await waitFor(() => expect(n1.getAttribute("tabindex")).toBe("0"));
    void panel;
  });

  it("activates the focused row with Enter/Space", async () => {
    const { paragraphs } = renderThreeStartTargets([100, 500, 700]);
    vi.stubGlobal("scrollY", 0);
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    await openPanel();
    const n1 = screen.getByTestId("reader-record-outline-node-n1");

    fireEvent.keyDown(n1, { key: "Enter" });
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 100 - 56 - 8,
      behavior: "smooth",
    });
    expect(n1.getAttribute("aria-current")).toBe("true");

    // Space moves to n2 and activates it.
    vi.mocked(window.scrollTo).mockClear();
    fireEvent.keyDown(n1, { key: "ArrowDown" });
    const n2 = screen.getByTestId("reader-record-outline-node-n2");
    await waitFor(() => expect(n2.getAttribute("tabindex")).toBe("0"));
    fireEvent.keyDown(n2, { key: " " });
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 500 - 56 - 8,
      behavior: "smooth",
    });
    expect(n2.getAttribute("aria-current")).toBe("true");
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    void paragraphs;
  });

  it("closes the panel on Escape and returns focus to the trigger button", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    const panel = await openPanel();
    const n1 = screen.getByTestId("reader-record-outline-node-n1");

    fireEvent.keyDown(n1, { key: "Escape" });

    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(true),
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
  });

  it("keeps ticks non-tabbable and aria-hidden even for long outlines", () => {
    // 12 depth-1 roots: only the trigger button may be a tab stop.
    const units: SnapshotUnitInput[] = Array.from({ length: 12 }, (_, i) => ({
      unit_id: `unit_${i + 1}`,
      order_index: i + 1,
      label: `U${i + 1}`,
    }));
    const nodes = units.map((u, i) => ({
      node_id: `n${i + 1}`,
      parent_node_id: null,
      depth: 1,
      title: `Section ${i + 1}`,
      start_unit_id: u.unit_id,
      end_unit_id: u.unit_id,
      start_anchor_segment_id: null,
      end_anchor_segment_id: null,
      order_index: i + 1,
    }));
    const plateDocument = makePlateDocument(
      units.map((u) => makeParagraph(u.unit_id, `Paragraph ${u.unit_id}.`)),
    );
    renderTargets(units.map((u) => u.unit_id));

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({}, nodes),
        })}
        plateDocument={plateDocument}
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const tabbableInRail = rail.querySelectorAll(
      'button:not([tabindex="-1"])',
    );
    expect(tabbableInRail).toHaveLength(1);
    expect(tabbableInRail[0]).toBe(
      screen.getByTestId("reader-record-outline-trigger"),
    );

    const miniRail = screen.getByTestId("reader-record-mini-rail");
    expect(miniRail.getAttribute("aria-hidden")).toBe("true");
    expect(miniRail.querySelectorAll("button")).toHaveLength(0);
    expect(
      miniRail.querySelectorAll("span[data-navigation-tick-key]"),
    ).toHaveLength(12);
  });

  it("scrolls the keyboard-focused row into view inside the panel scrollport", async () => {
    const units: SnapshotUnitInput[] = Array.from({ length: 12 }, (_, i) => ({
      unit_id: `unit_${i + 1}`,
      order_index: i + 1,
      label: `U${i + 1}`,
    }));
    const nodes = units.map((u, i) => ({
      node_id: `n${i + 1}`,
      parent_node_id: null,
      depth: 1,
      title: `Section ${i + 1}`,
      start_unit_id: u.unit_id,
      end_unit_id: u.unit_id,
      start_anchor_segment_id: null,
      end_anchor_segment_id: null,
      order_index: i + 1,
    }));
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

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({}, nodes),
        })}
        plateDocument={plateDocument}
      />,
    );

    const panel = await openPanel();
    const rows = panel.querySelectorAll("button");
    expect(rows.length).toBe(units.length);

    // Move focus down; the newly focused row must be scrolled into view by
    // the panel's scroll-into-view effect.
    fireEvent.keyDown(rows[0]!, { key: "ArrowDown" });
    await waitFor(() => expect(rows[1]?.getAttribute("tabindex")).toBe("0"));

    expect(scrollIntoViewSpy).toHaveBeenCalled();
    const lastCallTarget = scrollIntoViewSpy.mock.contexts.length
      ? scrollIntoViewSpy.mock.contexts[scrollIntoViewSpy.mock.contexts.length - 1]
      : null;
    expect(lastCallTarget).toBe(rows[1]);

    scrollIntoViewSpy.mockRestore();
  });

  // ---------------------------------------------------------------------------
  // ARIA + visual contract
  // ---------------------------------------------------------------------------

  it("uses nav semantics with a trigger button and aria-hidden visual ticks", () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.tagName.toLowerCase()).toBe("nav");
    expect(rail.getAttribute("aria-label")).toBe("内容大纲");

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    expect(trigger.tagName.toLowerCase()).toBe("button");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-haspopup")).toBeNull();
    expect(trigger.getAttribute("aria-controls")).toBeTruthy();
    const controlsId = trigger.getAttribute("aria-controls")!;
    expect(document.getElementById(controlsId)).toBe(
      screen.getByTestId("reader-record-navigation-panel"),
    );

    // Visual ticks are aria-hidden spans, never buttons.
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    expect(miniRail.getAttribute("aria-hidden")).toBe("true");
    const ticks = miniRail.querySelectorAll("span[data-navigation-tick-key]");
    expect(ticks).toHaveLength(2);
    expect(ticks[0]?.tagName.toLowerCase()).toBe("span");
  });

  it("gives the trigger a min 24x24px accessible hit area", () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    expect(trigger.className).toContain("min-h-[24px]");
    expect(trigger.className).toContain("min-w-[24px]");
  });

  it("renders rows with level+title aria labels, depth indent, and node testids", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    const rows = panel.querySelectorAll("button");
    expect(rows).toHaveLength(3);

    const n1 = screen.getByTestId("reader-record-outline-node-n1");
    const n2 = screen.getByTestId("reader-record-outline-node-n2");
    const n3 = screen.getByTestId("reader-record-outline-node-n3");

    expect(n1.getAttribute("aria-label")).toBe("一级，Root A");
    expect(n2.getAttribute("aria-label")).toBe("二级，Child");
    expect(n3.getAttribute("aria-label")).toBe("一级，Root B");
    expect(n1.getAttribute("data-outline-node-id")).toBe("n1");
    expect(n2.getAttribute("data-outline-depth")).toBe("2");

    // Depth indent: 8px → 20px → 32px (depth is clamped to 1..3).
    expect(n1.style.paddingLeft).toBe("8px");
    expect(n2.style.paddingLeft).toBe("20px");
    expect(n3.style.paddingLeft).toBe("8px");

    expect(screen.getByText("Root A")).toBeTruthy();
    expect(screen.getByText("Child")).toBeTruthy();
    expect(screen.getByText("Root B")).toBeTruthy();
  });

  it("renders a depth-3 row indented at 32px", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto({}, [
            {
              node_id: "d1",
              parent_node_id: null,
              depth: 1,
              title: "Root",
              start_unit_id: "unit_1",
              end_unit_id: "unit_3",
              start_anchor_segment_id: "seg_d1",
              end_anchor_segment_id: null,
              order_index: 1,
            },
            {
              node_id: "d2",
              parent_node_id: "d1",
              depth: 2,
              title: "Child",
              start_unit_id: "unit_1",
              end_unit_id: "unit_2",
              start_anchor_segment_id: "seg_d2",
              end_anchor_segment_id: null,
              order_index: 2,
            },
            {
              node_id: "d3",
              parent_node_id: "d2",
              depth: 3,
              title: "Grandchild",
              start_unit_id: "unit_2",
              end_unit_id: "unit_2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 3,
            },
          ]),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    void panel;
    const d3 = screen.getByTestId("reader-record-outline-node-d3");
    expect(d3.getAttribute("data-outline-depth")).toBe("3");
    expect(d3.getAttribute("aria-label")).toBe("三级，Grandchild");
    expect(d3.style.paddingLeft).toBe("32px");

    // Only the single depth-1 root produces a tick.
    const ticks = screen
      .getByTestId("reader-record-mini-rail")
      .querySelectorAll("span[data-navigation-tick-key]");
    expect(ticks).toHaveLength(1);
    expect(ticks[0]?.getAttribute("data-navigation-tick-key")).toBe("d1");
  });

  it("shows the partial hint only when the outline is partial", async () => {
    renderThreeStartTargets();
    const units = threeUnits();
    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({ status: "ready" }),
        })}
        plateDocument={threeDoc()}
      />,
    );

    await openPanel();
    expect(
      screen.queryByTestId("reader-record-outline-partial-hint"),
    ).toBeNull();

    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({ status: "partial" }),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const hint = await screen.findByTestId(
      "reader-record-outline-partial-hint",
    );
    expect(hint.textContent).toBe("部分内容大纲");
  });

  // ---------------------------------------------------------------------------
  // Layout
  // ---------------------------------------------------------------------------

  it("uses viewport fixed positioning by default and applies the askOpen clamp class", () => {
    renderThreeStartTargets();
    const units = threeUnits();
    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, { semantic_outline: makeOutlineDto() })}
        plateDocument={threeDoc()}
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.getAttribute("data-layout")).toBe("viewport");
    expect(rail.className).toContain("fixed");
    expect(rail.className).toContain("right-3");
    expect(rail.className).toContain("top-1/2");
    expect(rail.className).toContain("h-[min(72vh,42rem)]");
    expect(rail.className).not.toContain("2xl:right-[clamp");

    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, { semantic_outline: makeOutlineDto() })}
        plateDocument={threeDoc()}
        askOpen
      />,
    );
    expect(rail.className).toContain("2xl:right-[clamp");
  });

  it("switches to canvas layout and ignores the viewport askOpen clamp", () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
        layout="canvas"
        askOpen
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    expect(rail.getAttribute("data-layout")).toBe("canvas");
    expect(rail.className).toContain("reader-record-navigation-rail--canvas");
    expect(rail.className).toContain("absolute");
    expect(rail.className).toContain("right-0");
    expect(rail.className).toContain("top-1/2");
    expect(rail.className).toContain("h-[min(72vh,42rem)]");
    expect(rail.className).toContain("w-full");
    expect(rail.className).not.toContain("sticky");
    expect(rail.className).not.toContain("fixed");
    expect(rail.className).not.toContain("right-3");
    expect(rail.className).not.toContain("2xl:right-[clamp");
  });

  it("keeps the panel at a stable vertical position across hovered ticks", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const miniRail = screen.getByTestId("reader-record-mini-rail");
    const ticks = miniRail.querySelectorAll<HTMLSpanElement>(
      "span[data-navigation-tick-key]",
    );

    fireEvent.mouseEnter(ticks[0]!);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    const firstTop = panel.style.top;
    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    // Notion-style overlay: the panel shares the rail's right anchor (it covers
    // the ticks rather than floating to their left with a gap).
    expect(panel.className).toContain("right-0");
    expect(panel.className).not.toContain("right-[calc(100%+8px)]");

    // Hover the second root tick — the panel keeps its stable position.
    fireEvent.mouseEnter(ticks[1]!);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(panel.style.top).toBe(firstTop);
    expect(panel.getAttribute("data-reader-record-navigation-panel-anchor-y")).toBeNull();
    expect(panel.dataset.readerRecordNavigationPanelAnchorY).toBeUndefined();
  });

  it("lets the detail panel size to its rows with its own scrollport", () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = screen.getByTestId("reader-record-navigation-panel");
    const panelSurface = panel.firstElementChild as HTMLElement | null;
    const panelScrollArea = panelSurface?.firstElementChild as HTMLElement | null;

    expect(panel.className).toContain("top-1/2");
    expect(panel.className).toContain("-translate-y-1/2");
    expect(panel.className).toContain("max-h-[min(72vh,42rem)]");
    expect(panel.className).not.toContain("h-full");
    expect(panelSurface?.className).toContain("max-h-[min(72vh,42rem)]");
    expect(panelScrollArea?.className).toContain("overflow-y-auto");
    expect(panelScrollArea?.className).not.toContain("flex-1");
  });

  // ---------------------------------------------------------------------------
  // Source-identity vs same-source revision semantics
  // ---------------------------------------------------------------------------

  it("clears the active item on a source-identity (base_id:generation) change", async () => {
    const { paragraphs } = renderThreeStartTargets([60, 300, 500]);
    const units = threeUnits();
    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, { semantic_outline: makeOutlineDto() })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe(
        "打开内容大纲，当前第 1 项",
      );
    });

    const panel = await openPanel();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-n3"));
    expect(
      screen.getByTestId("reader-record-outline-node-n3").getAttribute("aria-current"),
    ).toBe("true");

    // Move every target into the lead zone so a cleared active stays null.
    paragraphs.forEach((p) => setRectTop(p, 300, 100));

    // New base_id:generation with a matching outline → identity reset.
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          baseId: "base_2",
          generation: 1,
          semantic_outline: makeOutlineDto({
            source_identity: { base_id: "base_2", generation: 1 },
          }),
        })}
        plateDocument={threeDoc()}
      />,
    );

    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toMatch(
        /^(打开|关闭)内容大纲$/,
      );
      expect(trigger.getAttribute("aria-label")).not.toMatch(/当前第/);
    });
    const rows = screen
      .getByTestId("reader-record-navigation-panel")
      .querySelectorAll("button");
    expect(
      Array.from(rows).every((r) => r.getAttribute("aria-current") !== "true"),
    ).toBe(true);
    void panel;
  });

  it("clears the active item on a generation change even when base_id and unit ids match", async () => {
    const { paragraphs } = renderThreeStartTargets([60, 300, 500]);
    const units = threeUnits();
    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, { semantic_outline: makeOutlineDto() })}
        plateDocument={threeDoc()}
      />,
    );

    const trigger = screen.getByTestId("reader-record-outline-trigger");
    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toMatch(/当前第 1 项/);
    });

    paragraphs.forEach((p) => setRectTop(p, 300, 100));
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          generation: 2,
          semantic_outline: makeOutlineDto({
            source_identity: { base_id: "base_1", generation: 2 },
          }),
        })}
        plateDocument={threeDoc()}
      />,
    );

    triggerScroll();
    await waitFor(() => {
      expect(trigger.getAttribute("aria-label")).toBe("打开内容大纲");
      expect(trigger.getAttribute("aria-label")).not.toMatch(/当前第/);
    });
  });

  it("prunes a now-missing active row on a same-source revision change but keeps the panel open", async () => {
    renderThreeStartTargets([100, 300, 500]);
    const units = threeUnits();
    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({ status: "ready" }, [
            {
              node_id: "nA",
              parent_node_id: null,
              depth: 1,
              title: "Alpha",
              start_unit_id: "unit_1",
              end_unit_id: "unit_2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 1,
            },
            {
              node_id: "nB",
              parent_node_id: null,
              depth: 1,
              title: "Beta",
              start_unit_id: "unit_3",
              end_unit_id: "unit_3",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 2,
            },
          ]),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const panel = await openPanel();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-nA"));
    expect(
      screen.getByTestId("reader-record-outline-node-nA").getAttribute("aria-current"),
    ).toBe("true");
    expect(panel.getAttribute("aria-hidden")).toBe("false");

    // Same source identity, new revision: nA is gone, nB (new id) remains.
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto(
            {
              status: "ready",
              publication: {
                outline_revision: "rev_2",
                layer_id: "layer_ol",
                published_at: "2026-07-18T00:00:00Z",
              },
            },
            [
              {
                node_id: "nB",
                parent_node_id: null,
                depth: 1,
                title: "Beta",
                start_unit_id: "unit_3",
                end_unit_id: "unit_3",
                start_anchor_segment_id: null,
                end_anchor_segment_id: null,
                order_index: 1,
              },
            ],
          ),
        })}
        plateDocument={threeDoc()}
      />,
    );

    // Panel stays open; the missing active row is pruned.
    await waitFor(() => {
      expect(
        screen.queryByTestId("reader-record-outline-node-nA"),
      ).toBeNull();
    });
    expect(panel.getAttribute("aria-hidden")).toBe("false");
    expect(panel.classList.contains("pointer-events-none")).toBe(false);
    expect(
      screen.getByTestId("reader-record-outline-node-nB").getAttribute("aria-current"),
    ).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // Group (non-navigable parent) — kept for hierarchy, skipped by interaction
  // ---------------------------------------------------------------------------

  it("keeps a shared-start parent as a non-navigable group with children at their own depth", async () => {
    // bc8afd86 record: the parent topic shares its navigation start with its
    // first child, so it is a non-navigable `group` — KEPT for hierarchy (not
    // deleted, not flattened); the two children remain navigable sections.
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
    ];
    const plateDocument = makePlateDocument([
      makeParagraph("u1", "A"),
      makeParagraph("u2", "B"),
    ]);
    renderTargets(["u1", "u2"], [100, 500]);

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({}, [
            {
              node_id: "root",
              parent_node_id: null,
              depth: 1,
              title: "哈里王子与王室关系紧张",
              start_unit_id: "u1",
              end_unit_id: "u2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 1,
            },
            {
              node_id: "childA",
              parent_node_id: "root",
              depth: 2,
              title: "哈里王子被拒住白金汉宫",
              start_unit_id: "u1",
              end_unit_id: "u1",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 2,
            },
            {
              node_id: "childB",
              parent_node_id: "root",
              depth: 2,
              title: "媒体关注关系恶化",
              start_unit_id: "u2",
              end_unit_id: "u2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 3,
            },
          ]),
        })}
        plateDocument={plateDocument}
      />,
    );

    const panel = await openPanel();
    // Parent is present as a group element (NOT a button), children are buttons.
    const root = screen.getByTestId("reader-record-outline-node-root");
    expect(root.tagName.toLowerCase()).not.toBe("button");
    expect(root.getAttribute("data-outline-role")).toBe("group");
    expect(root.getAttribute("data-outline-depth")).toBe("1");
    expect(root.getAttribute("tabindex")).toBeNull();
    // Heading structure semantics at normal contrast (not a disabled item).
    expect(root.getAttribute("role")).toBe("heading");
    expect(root.getAttribute("aria-level")).toBe("1");
    expect(screen.getByText("哈里王子与王室关系紧张")).toBeTruthy();

    const childA = screen.getByTestId("reader-record-outline-node-childA");
    const childB = screen.getByTestId("reader-record-outline-node-childB");
    expect(childA.tagName.toLowerCase()).toBe("button");
    expect(childB.tagName.toLowerCase()).toBe("button");
    expect(childA.getAttribute("data-outline-role")).toBe("section");
    // Children keep their own depth (NOT flattened to depth 1).
    expect(childA.getAttribute("data-outline-depth")).toBe("2");
    expect(childB.getAttribute("data-outline-depth")).toBe("2");
    expect(childA.style.paddingLeft).toBe("20px");
    expect(childB.style.paddingLeft).toBe("20px");
    // Only navigable rows are buttons (the group is excluded).
    expect(panel.querySelectorAll("button")).toHaveLength(2);
    // The group root is the single depth-1 tick (the top-level theme).
    expect(
      screen
        .getByTestId("reader-record-mini-rail")
        .querySelectorAll("span[data-navigation-tick-key]"),
    ).toHaveLength(1);

    // The two children scroll to DIFFERENT targets; no fetch.
    vi.mocked(window.scrollTo).mockClear();
    fireEvent.click(childA);
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 100 - 56 - 8,
      behavior: "smooth",
    });
    vi.mocked(window.scrollTo).mockClear();
    fireEvent.click(childB);
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 500 - 56 - 8,
      behavior: "smooth",
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("roving keyboard navigation skips the non-navigable group", async () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
    ];
    const plateDocument = makePlateDocument([
      makeParagraph("u1", "A"),
      makeParagraph("u2", "B"),
    ]);
    // Targets below safeTop → lead zone → no active row; focus starts on the
    // first NAVIGABLE row (the group is skipped).
    renderTargets(["u1", "u2"], [100, 500]);

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({}, [
            {
              node_id: "root",
              parent_node_id: null,
              depth: 1,
              title: "Group",
              start_unit_id: "u1",
              end_unit_id: "u2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 1,
            },
            {
              node_id: "childA",
              parent_node_id: "root",
              depth: 2,
              title: "Child A",
              start_unit_id: "u1",
              end_unit_id: "u1",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 2,
            },
            {
              node_id: "childB",
              parent_node_id: "root",
              depth: 2,
              title: "Child B",
              start_unit_id: "u2",
              end_unit_id: "u2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 3,
            },
          ]),
        })}
        plateDocument={plateDocument}
      />,
    );

    await openPanel();
    const root = screen.getByTestId("reader-record-outline-node-root");
    const childA = screen.getByTestId("reader-record-outline-node-childA");
    const childB = screen.getByTestId("reader-record-outline-node-childB");

    await waitFor(() => expect(childA.getAttribute("tabindex")).toBe("0"));
    expect(childB.getAttribute("tabindex")).toBe("-1");
    expect(root.getAttribute("tabindex")).toBeNull(); // group never tabbable

    // ArrowUp at the first navigable row does NOT move into the group.
    fireEvent.keyDown(childA, { key: "ArrowUp" });
    await waitFor(() => expect(childA.getAttribute("tabindex")).toBe("0"));

    // ArrowDown jumps to the other child (group skipped).
    fireEvent.keyDown(childA, { key: "ArrowDown" });
    await waitFor(() => expect(childB.getAttribute("tabindex")).toBe("0"));
    expect(childA.getAttribute("tabindex")).toBe("-1");

    // Home/End bound to navigable rows only.
    fireEvent.keyDown(childB, { key: "Home" });
    await waitFor(() => expect(childA.getAttribute("tabindex")).toBe("0"));
    fireEvent.keyDown(childA, { key: "End" });
    await waitFor(() => expect(childB.getAttribute("tabindex")).toBe("0"));
  });

  it("the group triggers no scroll, no scrollIntoView, and no fetch", async () => {
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

    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
    ];
    const plateDocument = makePlateDocument([
      makeParagraph("u1", "A"),
      makeParagraph("u2", "B"),
    ]);
    renderTargets(["u1", "u2"], [100, 500]);

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({}, [
            {
              node_id: "root",
              parent_node_id: null,
              depth: 1,
              title: "Group",
              start_unit_id: "u1",
              end_unit_id: "u2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 1,
            },
            {
              node_id: "childA",
              parent_node_id: "root",
              depth: 2,
              title: "Child A",
              start_unit_id: "u1",
              end_unit_id: "u1",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 2,
            },
          ]),
        })}
        plateDocument={plateDocument}
      />,
    );

    // Opening the panel scrolls the focused (navigable) row into view — never
    // the group node.
    await openPanel();
    const root = screen.getByTestId("reader-record-outline-node-root");
    expect(scrollIntoViewSpy.mock.contexts).not.toContain(root);

    // Clicking the group element is a no-op: not a button, no handler.
    vi.mocked(window.scrollTo).mockClear();
    fireEvent.click(root);
    expect(window.scrollTo).not.toHaveBeenCalled();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    expect(root.getAttribute("aria-current")).toBeNull();

    scrollIntoViewSpy.mockRestore();
  });

  // ---------------------------------------------------------------------------
  // Source-agnostic DOM navigation contract
  // ---------------------------------------------------------------------------

  it("resolves a non-paragraph navigable node (e.g. heading) via the same contract", async () => {
    const body = document.createElement("div");
    body.className = "reader-record-plate-document";
    const heading = document.createElement("h2");
    heading.setAttribute("data-reader-record-node", "heading");
    heading.setAttribute("data-unit-id", "unit_1");
    heading.setAttribute("data-reader-record-unit-start", "true");
    setRectTop(heading, 140, 40);
    const paragraph = document.createElement("p");
    paragraph.setAttribute("data-reader-record-node", "paragraph");
    paragraph.setAttribute("data-unit-id", "unit_2");
    paragraph.setAttribute("data-reader-record-unit-start", "true");
    setRectTop(paragraph, 460, 100);
    body.appendChild(heading);
    body.appendChild(paragraph);
    document.body.appendChild(body);

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto({}, [
            {
              node_id: "h",
              parent_node_id: null,
              depth: 1,
              title: "Heading",
              start_unit_id: "unit_1",
              end_unit_id: "unit_1",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 1,
            },
            {
              node_id: "p",
              parent_node_id: null,
              depth: 1,
              title: "Paragraph",
              start_unit_id: "unit_2",
              end_unit_id: "unit_2",
              start_anchor_segment_id: null,
              end_anchor_segment_id: null,
              order_index: 2,
            },
          ]),
        })}
        plateDocument={threeDoc()}
      />,
    );

    await openPanel();
    // The heading node (non-paragraph) is located via the generic contract.
    fireEvent.click(screen.getByTestId("reader-record-outline-node-h"));
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 140 - 56 - 8,
      behavior: "smooth",
    });
    // The paragraph path still resolves (no regression).
    vi.mocked(window.scrollTo).mockClear();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-p"));
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 460 - 56 - 8,
      behavior: "smooth",
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------------
  // Notion-style overlay expansion
  // ---------------------------------------------------------------------------

  it("overlays the rail on expand (shared right anchor, stacked above the trigger, no passthrough)", async () => {
    renderThreeStartTargets();
    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(threeUnits(), {
          semantic_outline: makeOutlineDto(),
        })}
        plateDocument={threeDoc()}
      />,
    );

    const rail = screen.getByTestId("reader-record-navigation-rail");
    const panel = screen.getByTestId("reader-record-navigation-panel");

    // Closed: ticks interactive, the panel does not capture the pointer.
    expect(panel.classList.contains("pointer-events-none")).toBe(true);

    hoverTick(0);
    await waitFor(() =>
      expect(panel.classList.contains("pointer-events-none")).toBe(false),
    );
    // Overlay geometry contract (jsdom has no layout engine → assert classes):
    // same right anchor as the rail and a stacking level above the trigger.
    expect(panel.className).toContain("right-0");
    expect(panel.className).toContain("z-20");
    expect(panel.className).not.toContain("right-[calc(100%+8px)]");
    // Expanded panel captures the pointer (no mouse passthrough to the body).
    expect(panel.getAttribute("aria-hidden")).toBe("false");

    // Pointer moving into the panel keeps it open.
    fireEvent.mouseLeave(rail, { relatedTarget: panel });
    fireEvent.mouseEnter(panel);
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(panel.classList.contains("pointer-events-none")).toBe(false);

    // Leaving the whole area closes it.
    fireEvent.mouseLeave(panel);
    await waitFor(
      () => expect(panel.classList.contains("pointer-events-none")).toBe(true),
      { timeout: 500 },
    );
  });

  // ---------------------------------------------------------------------------
  // Revision refresh: section → group, and partial → ready
  // ---------------------------------------------------------------------------

  it("revision: a section that becomes a group clears active/focus/lock, keeps the panel open, and resumes scroll-spy", async () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
      { unit_id: "u3", order_index: 3 },
      { unit_id: "u4", order_index: 4 },
    ];
    const plateDocument = makePlateDocument(
      units.map((u) => makeParagraph(u.unit_id, u.unit_id)),
    );
    // u4 above safeTop so the scroll-spy resolves Y as active once it resumes.
    renderTargets(["u1", "u2", "u3", "u4"], [200, 200, 200, 20]);

    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto(
            {
              status: "partial",
              publication: {
                outline_revision: "rev_1",
                layer_id: "l",
                published_at: "2026-07-21T00:00:00Z",
              },
            },
            [
              { node_id: "X", parent_node_id: null, depth: 1, title: "X", start_unit_id: "u1", end_unit_id: "u3", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 1 },
              { node_id: "Y", parent_node_id: null, depth: 1, title: "Y", start_unit_id: "u4", end_unit_id: "u4", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 2 },
            ],
          ),
        })}
        plateDocument={plateDocument}
      />,
    );

    await openPanel();
    // Make X the active + focused row and engage the scroll lock on it.
    fireEvent.click(screen.getByTestId("reader-record-outline-node-X"));
    expect(
      screen
        .getByTestId("reader-record-outline-node-X")
        .getAttribute("aria-current"),
    ).toBe("true");
    expect(
      screen.getByTestId("reader-record-outline-node-X").tagName.toLowerCase(),
    ).toBe("button");

    // Same source identity, new revision, status ready: X now has a child sharing
    // its start → X becomes a non-navigable group.
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto(
            {
              status: "ready",
              publication: {
                outline_revision: "rev_2",
                layer_id: "l",
                published_at: "2026-07-21T00:00:01Z",
              },
            },
            [
              { node_id: "X", parent_node_id: null, depth: 1, title: "X", start_unit_id: "u1", end_unit_id: "u3", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 1 },
              { node_id: "Xc", parent_node_id: "X", depth: 2, title: "Xc", start_unit_id: "u1", end_unit_id: "u1", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 2 },
              { node_id: "Y", parent_node_id: null, depth: 1, title: "Y", start_unit_id: "u4", end_unit_id: "u4", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 3 },
            ],
          ),
        })}
        plateDocument={plateDocument}
      />,
    );

    // Panel stays open.
    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-navigation-panel")
          .getAttribute("aria-hidden"),
      ).toBe("false"),
    );
    // X is now a group: not a button, not focusable, not active.
    const root = screen.getByTestId("reader-record-outline-node-X");
    expect(root.tagName.toLowerCase()).not.toBe("button");
    expect(root.getAttribute("data-outline-role")).toBe("group");
    expect(root.getAttribute("tabindex")).toBeNull();
    expect(root.getAttribute("aria-current")).toBeNull();
    // Focus migrated to the first remaining section (Xc).
    expect(
      screen.getByTestId("reader-record-outline-node-Xc").getAttribute("tabindex"),
    ).toBe("0");
    // Scroll-spy resumed (the lock on X was released): Y becomes active.
    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-outline-node-Y")
          .getAttribute("aria-current"),
      ).toBe("true"),
    );
  });

  it("revision partial→ready with unchanged roles keeps the focused/active row", async () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
    ];
    const plateDocument = makePlateDocument([
      makeParagraph("u1", "A"),
      makeParagraph("u2", "B"),
    ]);
    // u2 above safeTop so Y is the row we activate + focus.
    renderTargets(["u1", "u2"], [300, 20]);

    const { rerender } = render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto(
            {
              status: "partial",
              publication: {
                outline_revision: "rev_1",
                layer_id: "l",
                published_at: "2026-07-21T00:00:00Z",
              },
            },
            [
              { node_id: "X", parent_node_id: null, depth: 1, title: "X", start_unit_id: "u1", end_unit_id: "u1", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 1 },
              { node_id: "Y", parent_node_id: null, depth: 1, title: "Y", start_unit_id: "u2", end_unit_id: "u2", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 2 },
            ],
          ),
        })}
        plateDocument={plateDocument}
      />,
    );

    await openPanel();
    fireEvent.click(screen.getByTestId("reader-record-outline-node-Y"));
    expect(
      screen.getByTestId("reader-record-outline-node-Y").getAttribute("tabindex"),
    ).toBe("0");

    // Same nodes/roles, status ready, new revision → focus must stay on Y (the
    // second section), NOT reset to the first section X.
    rerender(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto(
            {
              status: "ready",
              publication: {
                outline_revision: "rev_2",
                layer_id: "l",
                published_at: "2026-07-21T00:00:01Z",
              },
            },
            [
              { node_id: "X", parent_node_id: null, depth: 1, title: "X", start_unit_id: "u1", end_unit_id: "u1", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 1 },
              { node_id: "Y", parent_node_id: null, depth: 1, title: "Y", start_unit_id: "u2", end_unit_id: "u2", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 2 },
            ],
          ),
        })}
        plateDocument={plateDocument}
      />,
    );

    await waitFor(() =>
      expect(
        screen
          .getByTestId("reader-record-navigation-panel")
          .getAttribute("aria-hidden"),
      ).toBe("false"),
    );
    expect(
      screen.getByTestId("reader-record-outline-node-Y").getAttribute("tabindex"),
    ).toBe("0");
    expect(
      screen.getByTestId("reader-record-outline-node-X").getAttribute("tabindex"),
    ).toBe("-1");
    expect(
      screen
        .getByTestId("reader-record-outline-node-Y")
        .getAttribute("aria-current"),
    ).toBe("true");
  });

  it("trigger label numbers navigable sections only (group excluded)", async () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
    ];
    const plateDocument = makePlateDocument([
      makeParagraph("u1", "A"),
      makeParagraph("u2", "B"),
    ]);
    renderTargets(["u1", "u2"], [100, 500]);

    render(
      <ReaderRecordNavigationRail
        snapshot={makeSnapshot(units, {
          semantic_outline: makeOutlineDto({}, [
            { node_id: "root", parent_node_id: null, depth: 1, title: "Group", start_unit_id: "u1", end_unit_id: "u2", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 1 },
            { node_id: "childA", parent_node_id: "root", depth: 2, title: "Child A", start_unit_id: "u1", end_unit_id: "u1", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 2 },
            { node_id: "childB", parent_node_id: "root", depth: 2, title: "Child B", start_unit_id: "u2", end_unit_id: "u2", start_anchor_segment_id: null, end_anchor_segment_id: null, order_index: 3 },
          ]),
        })}
        plateDocument={plateDocument}
      />,
    );

    await openPanel();
    // childB is index 3 in the full list but the 2nd section; the label must use
    // the section-only position, not the full-list position.
    fireEvent.click(screen.getByTestId("reader-record-outline-node-childB"));
    const label = screen
      .getByTestId("reader-record-outline-trigger")
      .getAttribute("aria-label");
    expect(label).toContain("当前第 2 项");
    expect(label).not.toContain("当前第 3 项");
  });
});
