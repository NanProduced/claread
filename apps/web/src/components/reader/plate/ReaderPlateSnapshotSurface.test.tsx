/** @vitest-environment jsdom */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReaderPlateSnapshotSurface } from "@/components/reader/plate/ReaderPlateSnapshotSurface";
import type {
  ReaderPlateValueDto,
  ReaderTranslationNodeDto,
  ReaderUnitNodeDto,
} from "@/types/api/reader-plate";

function makeUnitWithTranslation(overrides: {
  unitId: string;
  sourceText: string;
  translationText?: string;
  anchorSegmentId?: string;
}): ReaderUnitNodeDto {
  const anchorSegmentId = overrides.anchorSegmentId ?? "s1";
  const unit: ReaderUnitNodeDto = {
    type: "reader_unit",
    owner: "stable",
    base_id: "base_1",
    unit_id: overrides.unitId,
    order_index: 1,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: overrides.sourceText.length,
    text_hash: "abcd1234",
    hash_algorithm: "fnv1a32-utf16",
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_1",
        unit_id: overrides.unitId,
        base_start_utf16: 0,
        base_end_utf16: overrides.sourceText.length,
        children: [
          {
            type: "reader_anchor_segment",
            owner: "stable",
            base_id: "base_1",
            unit_id: overrides.unitId,
            anchor_segment_id: anchorSegmentId,
            sentence_id: anchorSegmentId,
            segment_type: "sentence",
            boundary_quality: "normal",
            base_start_utf16: 0,
            base_end_utf16: overrides.sourceText.length,
            unit_start_utf16: 0,
            unit_end_utf16: overrides.sourceText.length,
            text_hash: "abcd1234",
            hash_algorithm: "fnv1a32-utf16",
            children: [
              {
                text: overrides.sourceText,
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: 0,
                base_end_utf16: overrides.sourceText.length,
                anchor_segment_id: anchorSegmentId,
                segment_start_utf16: 0,
                segment_end_utf16: overrides.sourceText.length,
              },
            ],
          },
        ],
      },
    ],
  };

  if (overrides.translationText !== undefined) {
    const translation: ReaderTranslationNodeDto = {
      type: "reader_translation",
      owner: "system_ai",
      layer_id: "layer_1",
      layer_version: 1,
      base_id: "base_1",
      unit_id: overrides.unitId,
      target_scope: "unit",
      target_key: overrides.unitId,
      target_language: "zh",
      confidence: "normal",
      notes: [],
      children: [{ text: overrides.translationText }],
    };
    unit.children.push(translation);
  }

  return unit;
}

describe("ReaderPlateSnapshotSurface", () => {
  it("renders reader_unit, reader_source_block, reader_anchor_segment nodes", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "A scarce few can turn passion into income.",
      }),
    ];

    const { container } = render(
      <ReaderPlateSnapshotSurface value={value} />,
    );

    expect(container.querySelector('[data-reader-node="unit"]')).not.toBeNull();
    expect(container.querySelector('[data-reader-node="source-block"]')).not.toBeNull();
    expect(container.querySelector('[data-reader-node="anchor-segment"]')).not.toBeNull();
    expect(container.querySelector('[data-unit-id="u1"]')).not.toBeNull();
    expect(container.querySelector('[data-anchor-segment-id="s1"]')).not.toBeNull();
  });

  it("renders stable segment_text leaves with owner=stable", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "Hello world.",
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const stableLeaf = container.querySelector('[data-reader-leaf="segment_text"]');
    expect(stableLeaf).not.toBeNull();
    expect(stableLeaf?.getAttribute("data-owner")).toBe("stable");
    expect(stableLeaf?.getAttribute("data-anchor-segment-id")).toBe("s1");
    expect(stableLeaf?.textContent).toContain("Hello world.");
  });

  it("renders reader_translation projection node with target metadata", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "Hello world.",
        translationText: "你好，世界。",
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const translationNode = container.querySelector('[data-reader-node="translation"]');
    expect(translationNode).not.toBeNull();
    expect(translationNode?.getAttribute("data-target-language")).toBe("zh");
    expect(translationNode?.getAttribute("data-target-scope")).toBe("unit");
    expect(translationNode?.getAttribute("data-target-key")).toBe("u1");
    expect(translationNode?.textContent).toContain("你好，世界。");
  });

  it("distinguishes source text and translation via CSS class hooks", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "Source text.",
        translationText: "译文内容。",
      }),
    ];

    const { container } = render(
      <ReaderPlateSnapshotSurface
        value={value}
        readingClassName="source-text-marker"
        translationClassName="translation-marker"
      />,
    );

    const sourceBlock = container.querySelector('[data-reader-node="source-block"]');
    const translation = container.querySelector('[data-reader-node="translation"]');
    expect(sourceBlock?.className).toContain("source-text-marker");
    expect(translation?.className).toContain("translation-marker");
  });

  it("renders multiple units in order", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "First paragraph.",
      }),
      makeUnitWithTranslation({
        unitId: "u2",
        sourceText: "Second paragraph.",
        anchorSegmentId: "s2",
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const units = container.querySelectorAll('[data-reader-node="unit"]');
    expect(units).toHaveLength(2);
    expect(units[0]?.getAttribute("data-unit-id")).toBe("u1");
    expect(units[1]?.getAttribute("data-unit-id")).toBe("u2");
  });

  it("renders empty-state message when value is empty", () => {
    const { container } = render(<ReaderPlateSnapshotSurface value={[]} />);

    expect(container.textContent).toContain("暂无可渲染的 Reader Plate 内容");
    expect(container.querySelector('[data-reader-node="unit"]')).toBeNull();
  });

  it("renders anchor_segment_id and sentence_id as data attributes", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "An anchor sentence.",
        anchorSegmentId: "anchor_42",
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const anchor = container.querySelector('[data-reader-node="anchor-segment"]');
    expect(anchor?.getAttribute("data-anchor-segment-id")).toBe("anchor_42");
    expect(anchor?.getAttribute("data-sentence-id")).toBe("anchor_42");
    expect(anchor?.getAttribute("data-segment-type")).toBe("sentence");
  });
});
