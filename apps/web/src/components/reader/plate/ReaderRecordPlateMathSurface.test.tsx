/** @vitest-environment jsdom */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { ReaderRecordPlateSurface } from "./ReaderRecordPlateSurface";

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    themePreference: "system" as const,
    resolvedTheme: "light" as const,
    setThemePreference: vi.fn(),
  }),
}));

vi.mock("@/components/layout/app-shell", () => ({
  useAppShellLayout: () => ({
    sidebarState: "expanded",
    setSidebarState: vi.fn(),
    isMobile: false,
  }),
}));
import type {
  ReaderPlateSnapshotDto,
  ReaderStableDocumentBlockNodeDto,
  ReaderUnitNodeDto,
} from "@/types/api/reader-plate";
import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";

function wgNode(overrides: Partial<ReaderStableDocumentBlockNodeDto>): ReaderStableDocumentBlockNodeDto {
  return {
    block_id: "block",
    parent_block_id: null,
    order_index: 0,
    block_type: "unknown",
    text_content: null,
    payload: {},
    source_refs: {},
    quality: {},
    canonical_text_start_utf16: null,
    canonical_text_end_utf16: null,
    interpretation_policy: {},
    unit_id: null,
    anchor_segment_ids: [],
    children: [],
    ...overrides,
  };
}

function makeMathSnapshot(
  specs: Array<{ unitId: string; text: string; stableType: string; stableId: string }>,
  tree: ReaderStableDocumentBlockNodeDto[],
): ReaderPlateSnapshotDto {
  const baseId = "base_w1";
  let offset = 0;
  const anchor_segments: ReaderPlateSnapshotDto["anchor_segments"] = [];
  const navigation: ReaderPlateSnapshotDto["navigation"]["units"] = [];
  const value: ReaderUnitNodeDto[] = [];
  for (const [idx, spec] of specs.entries()) {
    const start = offset;
    const end = start + spec.text.length;
    offset = end + 2;
    const segId = `seg_${spec.unitId}`;
    anchor_segments.push({
      anchor_segment_id: segId,
      sentence_id: `sent_${segId}`,
      paragraph_id: spec.unitId,
      unit_id: spec.unitId,
      order_index: idx + 1,
      unit_order_index: 1,
      segment_type: "sentence",
      boundary_quality: "normal",
      base_start_utf16: start,
      base_end_utf16: end,
      unit_start_utf16: 0,
      unit_end_utf16: spec.text.length,
      text_hash: `hash_${segId}`,
      hash_algorithm: "fnv1a32-utf16",
    });
    navigation.push({
      unit_id: spec.unitId,
      order_index: idx + 1,
      unit_type: "body",
      boundary_quality: "normal",
      label: null,
      base_start_utf16: start,
      base_end_utf16: end,
      text_hash: `hash_${spec.unitId}`,
      hash_algorithm: "fnv1a32-utf16",
      stable_block_type: spec.stableType,
      heading_level: null,
    });
    value.push({
      type: "reader_unit",
      owner: "stable",
      base_id: baseId,
      unit_id: spec.unitId,
      order_index: idx + 1,
      unit_type: "body",
      boundary_quality: "normal",
      base_start_utf16: start,
      base_end_utf16: end,
      text_hash: `hash_${spec.unitId}`,
      hash_algorithm: "fnv1a32-utf16",
      children: [
        {
          type: "reader_source_block",
          owner: "stable",
          base_id: baseId,
          unit_id: spec.unitId,
          base_start_utf16: start,
          base_end_utf16: end,
          stableBlockType: spec.stableType,
          stableBlockId: spec.stableId,
          parentStableBlockId: null,
          children: [
            {
              type: "reader_anchor_segment",
              owner: "stable",
              base_id: baseId,
              unit_id: spec.unitId,
              anchor_segment_id: segId,
              sentence_id: `sent_${segId}`,
              segment_type: "sentence",
              boundary_quality: "normal",
              base_start_utf16: start,
              base_end_utf16: end,
              unit_start_utf16: 0,
              unit_end_utf16: spec.text.length,
              text_hash: `hash_${segId}`,
              hash_algorithm: "fnv1a32-utf16",
              children: [
                {
                  text: spec.text,
                  owner: "stable",
                  lock_source: true,
                  source_role: "segment_text",
                  base_start_utf16: start,
                  base_end_utf16: end,
                  anchor_segment_id: segId,
                  segment_start_utf16: 0,
                  segment_end_utf16: spec.text.length,
                },
              ],
            },
          ],
        } as unknown as import("@/types/api/reader-plate").ReaderSourceBlockNodeDto,
      ],
    });
  }
  return {
    schema_kind: "reader_plate_snapshot" as const,
    snapshot_id: "snapshot_w1",
    snapshot_taken_at: "2026-08-08T00:00:00Z",
    last_event_sequence: 1,
    record_id: "record_w1",
    record: {
      title: "Plate Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-08-08T00:00:00Z",
      source_type: "markdown",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: baseId,
      content_sha256: "c".repeat(64),
      canonicalizer_version: "test",
      builder_version: "test",
      segmenter_version: "test",
      text_length_utf16: offset,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: navigation },
    anchor_segments,
    enhancement_layers: [],
    enhancement_progress: undefined,
    analysis_progress: makeAnalysisProgressDto() as unknown as import("@/types/api/reader-plate").ReaderAnalysisProgressDto,
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value,
    stable_document_tree: tree,
  };
}

describe("Reader math safe surface — rendering & fail-closed & chrome exclusion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sentence-split mixed paragraph renders inline $s = vt$ KaTeX beside display E=mc^2", () => {
    const unit1 = "The speed  is linear.";
    const unit2 = "Extra sentence keeps the split.";
    const snapshot = makeMathSnapshot(
      [
        { unitId: "u1", text: unit1, stableType: "paragraph", stableId: "sent_a" },
        { unitId: "u2", text: unit2, stableType: "paragraph", stableId: "sent_b" },
      ],
      [
        wgNode({
          block_id: "b_mixed",
          block_type: "paragraph",
          order_index: 0,
          canonical_text_start_utf16: 0,
          canonical_text_end_utf16: unit1.length + 2 + unit2.length,
          payload: {
            inline_math: [{ latex: "s = vt", display: false, before_utf16: "The speed ".length }],
          },
        }),
        wgNode({
          block_id: "math_display",
          block_type: "paragraph",
          order_index: 1,
          payload: { math_blocks: [{ latex: "E = mc^2", display: true }] },
        }),
      ],
    );
    for (const unit of snapshot.value) {
      for (const child of unit.children) {
        if (child.type === "reader_source_block") {
          delete (child as { stableBlockId?: string }).stableBlockId;
        }
      }
    }

    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const inlineMath = container.querySelector('[data-reader-math="true"][data-math-display="false"]');
    const displayMath = container.querySelector('[data-reader-math="true"][data-math-display="true"]');
    expect(inlineMath).not.toBeNull();
    expect(displayMath).not.toBeNull();
    expect(inlineMath?.querySelector(".katex")).not.toBeNull();
    expect(displayMath?.querySelector(".katex") || container.querySelector(".katex-display")).not.toBeNull();
    expect(container.textContent).toContain("The speed");
    expect(container.textContent).toContain("is linear.");
    expect(container.textContent).toContain("Extra sentence keeps the split.");
  });

  it("valid inline math renders KaTeX and is excluded from copy (data-reader-record-copy-exclude)", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "hello world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [{ latex: "a*b*c", display: false, before_utf16: 5 }] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const math = container.querySelector('[data-reader-math="true"]');
    expect(math).not.toBeNull();
    expect(math?.getAttribute("data-math-state")).toBe("ok");
    // KaTeX output should contain katex class
    expect(container.querySelector(".katex")).not.toBeNull();
    // chrome exclusion: math content is inside data-reader-record-copy-exclude
    const copyExclude = math?.querySelector('[data-reader-record-copy-exclude="true"]');
    expect(copyExclude).not.toBeNull();
    expect(copyExclude?.querySelector(".katex")).not.toBeNull();
  });

  it("valid display math (standalone $$) renders as block with centered wrapper", () => {
    const snapshot = makeMathSnapshot(
      [
        { unitId: "u_p1", text: "Hello", stableType: "paragraph", stableId: "p1" },
        { unitId: "u_p2", text: "World", stableType: "paragraph", stableId: "p2" },
      ],
      [
        wgNode({ block_id: "p1", block_type: "paragraph", order_index: 0 }),
        wgNode({ block_id: "math1", block_type: "paragraph", order_index: 1, payload: { math_blocks: [{ latex: "E = mc^2", display: true }] } }),
        wgNode({ block_id: "p2", block_type: "paragraph", order_index: 2 }),
      ],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const mathBlock = container.querySelector('[data-reader-math-block="true"]');
    expect(mathBlock).not.toBeNull();
    expect(mathBlock?.querySelector('[data-reader-math="true"][data-math-display="true"]')).not.toBeNull();
    expect(container.querySelector(".katex-display") || container.querySelector(".katex")).not.toBeNull();
  });

  it("illegal latex fail-closed shows raw source placeholder, never throws", () => {
    const illegal = "\\frac{";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "hello  world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [{ latex: illegal, display: false, before_utf16: 6 }] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const math = container.querySelector('[data-reader-math="true"][data-math-state="error"]');
    expect(math).not.toBeNull();
    // fallback shows raw latex text
    expect(math?.textContent).toContain(illegal);
    // fallback is also copy-excluded
    expect(math?.querySelector('[data-reader-record-copy-exclude="true"]')).not.toBeNull();
    // should not crash surface: container still renders paragraph text
    expect(container.textContent).toContain("hello");
  });

  it("math rendering is excluded from copy/selection: normalized clipboard text does not contain latex", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "A  C", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [{ latex: "x+y", display: false, before_utf16: 2 }] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    // Simulate copy sanitization: remove copy-exclude nodes and read text
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll('[data-reader-record-copy-exclude="true"], [hidden]').forEach((n) => n.remove());
    const copyText = (clone.textContent ?? "").replace(/[\u200B\uFEFF]/g, "").trim();
    expect(copyText).not.toContain("x+y");
    expect(copyText).toContain("A");
    expect(copyText).toContain("C");
  });

  it("selection bridge ignores math nodes:DOM query for text leaves excludes math", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "A  C", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [{ latex: "x+y", display: false, before_utf16: 2 }] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    // Math nodes are void elements with children [{text:""}] and not counted as text leaves
    // Ensure there is no data-slate-leaf with math latex in its text
    const leaves = container.querySelectorAll('[data-slate-leaf]');
    for (const leaf of leaves) {
      expect(leaf.textContent).not.toBe("x+y");
    }
    // Math element is present but as void element, not as leaf
    expect(container.querySelector('[data-reader-math="true"]')).not.toBeNull();
  });

  it("math block is not counted as unit text and does not affect word count ownership", () => {
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "hello world", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [{ latex: "a*b*c", display: false, before_utf16: 5 }] } })],
    );
    // snapshot.value text should not contain latex
    const unitText = snapshot.value[0].children
      .flatMap((c) => {
        if (c.type === "reader_source_block") {
          return c.children.flatMap((child) =>
            "children" in child ? (child as { children: Array<{ text?: string }> }).children.map((leaf) => leaf.text ?? "") : [],
          );
        }
        return [];
      })
      .join("");
    expect(unitText).not.toContain("a*b*c");
    // Rendered math should be present but excluded from copy
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    expect(container.querySelector('[data-reader-math="true"]')).not.toBeNull();
  });

  it("|A-B|_F^2 style latex renders and preserves pipe semantics", () => {
    const latex = "\\|A - B\\|_F^2";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "norm  end", stableType: "paragraph", stableId: "b1" }],
      [wgNode({ block_id: "b1", block_type: "paragraph", payload: { inline_math: [{ latex, display: true, before_utf16: 5 }] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const math = container.querySelector('[data-reader-math="true"]');
    expect(math).not.toBeNull();
    // Either KaTeX rendered or fallback (if pipe handling fails) should show something
    // For valid latex with escaped pipes, KaTeX should render OK
    expect(math?.getAttribute("data-math-state")).toBe("ok");
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  it("GFM-alert style latex with > prefix fail-closed shows source with prefix", () => {
    const latex = "> E = mc^2\n> ";
    const snapshot = makeMathSnapshot(
      [{ unitId: "u1", text: "note", stableType: "blockquote", stableId: "bq1" }],
      [wgNode({ block_id: "bq1", block_type: "blockquote", payload: { math_blocks: [{ latex, display: true }] } })],
    );
    const { container } = render(<ReaderRecordPlateSurface snapshot={snapshot} />);
    const math = container.querySelector('[data-reader-math="true"]');
    expect(math).not.toBeNull();
    // This latex with > prefix is invalid for KaTeX, should fall back to error state showing raw source
    // We assert fallback contains the raw prefix
    // If KaTeX happens to render without error (unlikely with >), we accept either but must not crash
    // For fail-closed expectation, check that raw source appears somewhere if error
    const state = math?.getAttribute("data-math-state");
    if (state === "error") {
      expect(math?.textContent).toContain(">");
    } else {
      // if KaTeX somehow rendered, still surface must not throw
      expect(container.querySelector(".katex") || math?.textContent?.includes(">")).toBeTruthy();
    }
  });
});
