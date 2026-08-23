import { describe, expect, it } from "vitest";
import {
  buildReaderRecordL1NavigationItems,
  buildReaderRecordNavigationItems,
  buildReaderRecordSourceIdentityKey,
  isL1NavigationEnabled,
  projectReaderRecordNavigation,
  stripHeadingDisplayMarkers,
} from "./reader-record-navigation";
import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
  type ReaderUnitType,
} from "@/types/api/reader-plate";
import { makeAnalysisProgressDto } from "@/test/fixtures/reader-analysis-progress";
import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  type ReaderRecordPlateDocument,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

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
  options?: { baseId?: string; generation?: number },
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
      source_type: "text",
      source_metadata: {},
      generation: options?.generation ?? 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
      created_at: "2024-01-01T00:00:00Z",
      reading_goal: "daily_reading",
      reading_variant: "beginner_reading",
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
    analysis_progress: makeAnalysisProgressDto(),
  };
}

/** F1 heading-rich fixture: unit_count>=6, heading_count>=2, with lead + body. */
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

function docFromUnits(units: SnapshotUnitInput[]): ReaderRecordPlateDocument {
  return makeDocument(
    units.map((u) =>
      makeParagraph(u.unit_id, u.label ?? `Text for ${u.unit_id}.`, true),
    ),
  );
}

function makeDocument(
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

describe("buildReaderRecordNavigationItems", () => {
  it("sorts units by order_index", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_b", order_index: 2 },
      { unit_id: "unit_a", order_index: 1 },
    ]);
    const document = makeDocument([
      makeParagraph("unit_a", "First unit text."),
      makeParagraph("unit_b", "Second unit text."),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items.map((i) => i.unitId)).toEqual(["unit_a", "unit_b"]);
  });

  it("prefers explicit unit label", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: "Explicit label" },
    ]);
    const document = makeDocument([
      makeParagraph("unit_1", "This text should not be used."),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items[0].label).toBe("Explicit label");
  });

  it("derives label from the first paragraph source text when unit label is empty", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: null },
    ]);
    const document = makeDocument([
      makeParagraph("unit_1", "The quick brown fox jumps over the lazy dog."),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items[0].label).toBe("The quick brown fox jumps over the lazy dog.");
  });

  it("derives labels from text around an inline image without leaking image metadata", () => {
    const paragraph = makeParagraph("u1", "hello ");
    if (paragraph.type !== "paragraph") {
      throw new Error("expected paragraph");
    }
    const firstLeaf = paragraph.children[0];
    if (!("text" in firstLeaf)) {
      throw new Error("expected text leaf");
    }
    paragraph.children = [
      firstLeaf,
      {
        type: "image",
        id: "image:block_1:0",
        children: [{ text: "" }],
        data: {
          sourceUrl: "https://example.com/private.png",
          effectiveUrl: "https://example.com/private.png",
          altText: "private alt",
          title: "private title",
          positionKind: "inline",
          stableBlockId: "block_1",
          parentStableBlockId: null,
          inlineOrdinal: 0,
          beforeUtf16: 6,
        },
      },
      {
        ...firstLeaf,
        text: "world",
        baseRange: { startUtf16: 6, endUtf16: 11 },
      },
    ];

    expect(
      buildReaderRecordNavigationItems(
        makeSnapshot([{ unit_id: "u1", order_index: 0, label: null }]),
        makeDocument([paragraph]),
      )[0]?.label,
    ).toBe("hello world");
  });

  it("trims whitespace and collapses multiple spaces in derived labels", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: null },
    ]);
    const document = makeDocument([
      makeParagraph("unit_1", "  Multiple   spaces\tand\nnewlines  "),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items[0].label).toBe("Multiple spaces and newlines");
  });

  it("truncates derived labels longer than 48 characters", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: null },
    ]);
    const longText =
      "This is a very long paragraph that should definitely be truncated because it exceeds the maximum label length.";
    const document = makeDocument([makeParagraph("unit_1", longText)]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items[0].label.length).toBeLessThanOrEqual(49);
    expect(items[0].label.endsWith("…")).toBe(true);
  });

  it("falls back to '第 N 段' when no source text is available", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: null },
      { unit_id: "unit_2", order_index: 1, label: "" },
    ]);
    const document = makeDocument([
      makeParagraph("unit_1", ""),
      makeParagraph("unit_2", ""),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items[0].label).toBe("第 1 段");
    expect(items[1].label).toBe("第 2 段");
  });

  it("ignores non-paragraph blocks when deriving labels", () => {
    const snapshot = makeSnapshot([
      { unit_id: "unit_1", order_index: 0, label: null },
    ]);
    const document = makeDocument([
      {
        type: "sentence_analysis",
        id: "sa-1",
        icon: "sparkles",
        children: [],
        data: {
          anchorSegmentId: "seg-1",
          unitId: "unit_1",
          layerId: "layer-1",
          analysisId: "analysis-1",
          label: "Analysis",
          analysis: "",
          chunks: [],
        },
      },
      makeParagraph("unit_1", "Actual paragraph text."),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items[0].label).toBe("Actual paragraph text.");
  });
  it("derives outline entries from document units when navigation units are absent", () => {
    const snapshot = makeSnapshot([]);
    const document = makeDocument([
      makeParagraph("unit_first", "First paragraph for the fallback outline."),
      makeParagraph("unit_second", "Second paragraph for the fallback outline."),
    ]);

    const items = buildReaderRecordNavigationItems(snapshot, document);
    expect(items).toEqual([
      expect.objectContaining({
        unitId: "unit_first",
        orderIndex: 0,
        fallbackIndex: 0,
        label: "First paragraph for the fallback outline.",
      }),
      expect.objectContaining({
        unitId: "unit_second",
        orderIndex: 1,
        fallbackIndex: 1,
        label: "Second paragraph for the fallback outline.",
      }),
    ]);
  });
});

describe("L1 navigation projection", () => {
  it("heading-rich projects L1 with heading-only rows and closed coverage", () => {
    const units = headingRichUnits();
    const snapshot = makeSnapshot(units);
    const document = docFromUnits(units);

    expect(isL1NavigationEnabled(snapshot)).toBe(true);
    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L1");
    expect(projection.items.map((i) => i.unitId)).toEqual(["u2", "u5"]);
    expect(projection.l1Items).toEqual([
      expect.objectContaining({
        unitId: "u2",
        startUnitId: "u2",
        endUnitId: "u4",
        coveredUnitIds: ["u2", "u3", "u4"],
        label: "Chapter One",
        fallbackIndex: 0,
      }),
      expect.objectContaining({
        unitId: "u5",
        startUnitId: "u5",
        endUnitId: "u7",
        coveredUnitIds: ["u5", "u6", "u7"],
        label: "Chapter Two",
        fallbackIndex: 1,
      }),
    ]);
    // Lead body is not a row.
    expect(projection.items.some((i) => i.unitId === "u1")).toBe(false);
    // No depth/tree fields.
    expect(projection.l1Items?.[0]).not.toHaveProperty("depth");
    expect(projection.l1Items?.[0]).not.toHaveProperty("children");
  });

  it("pure-body stays L0 with full unit list", () => {
    const units: SnapshotUnitInput[] = Array.from({ length: 6 }, (_, i) => ({
      unit_id: `u${i + 1}`,
      order_index: i + 1,
      unit_type: "body" as const,
      label: null,
    }));
    const snapshot = makeSnapshot(units);
    const document = docFromUnits(units);

    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L0");
    expect(projection.l1Items).toBeNull();
    expect(projection.items).toHaveLength(6);
    expect(projection.items.map((i) => i.unitId)).toEqual(
      units.map((u) => u.unit_id),
    );
  });

  it("heading + list/quote/body only lists headings; coverage includes non-heading", () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1, unit_type: "body", label: null },
      { unit_id: "u2", order_index: 2, unit_type: "heading", label: "Intro" },
      { unit_id: "u3", order_index: 3, unit_type: "list", label: null },
      { unit_id: "u4", order_index: 4, unit_type: "quote", label: null },
      { unit_id: "u5", order_index: 5, unit_type: "heading", label: "Body" },
      { unit_id: "u6", order_index: 6, unit_type: "body", label: null },
    ];
    const snapshot = makeSnapshot(units);
    const document = docFromUnits(units);

    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L1");
    expect(projection.items.map((i) => i.unitId)).toEqual(["u2", "u5"]);
    expect(projection.l1Items?.[0]?.coveredUnitIds).toEqual(["u2", "u3", "u4"]);
    expect(projection.l1Items?.[1]?.coveredUnitIds).toEqual(["u5", "u6"]);
  });

  it("single heading with unit_count < 6 stays L0", () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1, unit_type: "heading", label: "Only" },
      { unit_id: "u2", order_index: 2, unit_type: "body", label: null },
      { unit_id: "u3", order_index: 3, unit_type: "body", label: null },
    ];
    const snapshot = makeSnapshot(units);
    const document = docFromUnits(units);

    expect(isL1NavigationEnabled(snapshot)).toBe(false);
    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L0");
    expect(projection.items).toHaveLength(3);
  });

  it("F4b: long text with exactly one heading must not swallow L0", () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1, unit_type: "body", label: null },
      { unit_id: "u2", order_index: 2, unit_type: "heading", label: "False short" },
      { unit_id: "u3", order_index: 3, unit_type: "body", label: null },
      { unit_id: "u4", order_index: 4, unit_type: "body", label: null },
      { unit_id: "u5", order_index: 5, unit_type: "body", label: null },
      { unit_id: "u6", order_index: 6, unit_type: "body", label: null },
    ];
    const snapshot = makeSnapshot(units);
    const document = docFromUnits(units);

    expect(isL1NavigationEnabled(snapshot)).toBe(false);
    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L0");
    expect(projection.items).toHaveLength(6);
    // Not a single-item "chapter" list.
    expect(projection.items.map((i) => i.unitId)).not.toEqual(["u2"]);
  });

  it("F4c: short multi-heading (unit_count < 6) stays L0", () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1, unit_type: "heading", label: "A" },
      { unit_id: "u2", order_index: 2, unit_type: "body", label: null },
      { unit_id: "u3", order_index: 3, unit_type: "heading", label: "B" },
      { unit_id: "u4", order_index: 4, unit_type: "body", label: null },
    ];
    const snapshot = makeSnapshot(units);
    const document = docFromUnits(units);

    expect(isL1NavigationEnabled(snapshot)).toBe(false);
    expect(projectReaderRecordNavigation(snapshot, document).mode).toBe("L0");
  });

  it("empty navigation.units with document paragraphs forces L0 (no L1 guess)", () => {
    const snapshot = makeSnapshot([]);
    const document = makeDocument([
      makeParagraph("unit_first", "First paragraph."),
      makeParagraph("unit_second", "Second paragraph."),
      makeParagraph("unit_third", "Third paragraph."),
      makeParagraph("unit_fourth", "Fourth paragraph."),
      makeParagraph("unit_fifth", "Fifth paragraph."),
      makeParagraph("unit_sixth", "Sixth paragraph."),
    ]);

    expect(isL1NavigationEnabled(snapshot)).toBe(false);
    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L0");
    expect(projection.items).toHaveLength(6);
    expect(buildReaderRecordL1NavigationItems(snapshot, document)).toEqual([]);
  });

  it("empty nav + empty document yields empty items (rail will not render)", () => {
    const snapshot = makeSnapshot([]);
    const document = makeDocument([]);
    const projection = projectReaderRecordNavigation(snapshot, document);
    expect(projection.mode).toBe("L0");
    expect(projection.items).toEqual([]);
  });

  it("sourceIdentityKey is base_id:generation", () => {
    const snapshot = makeSnapshot(headingRichUnits(), {
      baseId: "base_xyz",
      generation: 4,
    });
    expect(buildReaderRecordSourceIdentityKey(snapshot)).toBe("base_xyz:4");
    expect(projectReaderRecordNavigation(snapshot, docFromUnits(headingRichUnits())).sourceIdentityKey).toBe(
      "base_xyz:4",
    );
  });

  it("F11: strips leading markdown # only for L1 display labels", () => {
    expect(stripHeadingDisplayMarkers("# Title")).toBe("Title");
    expect(stripHeadingDisplayMarkers("## Nested")).toBe("Nested");
    expect(stripHeadingDisplayMarkers("Not a heading # mid")).toBe(
      "Not a heading # mid",
    );

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
    const snapshot = makeSnapshot(units);
    const l1 = buildReaderRecordL1NavigationItems(snapshot, docFromUnits(units));
    expect(l1[0]?.unitId).toBe("u2");
    expect(l1[0]?.label).toBe("Markdown Title");
    expect(l1[1]?.label).toBe("Second");
  });

  it("keeps L1 order by order_index, not label sort", () => {
    const units: SnapshotUnitInput[] = [
      { unit_id: "u1", order_index: 1, unit_type: "body", label: null },
      { unit_id: "u2", order_index: 2, unit_type: "heading", label: "Zulu" },
      { unit_id: "u3", order_index: 3, unit_type: "body", label: null },
      { unit_id: "u4", order_index: 4, unit_type: "heading", label: "Alpha" },
      { unit_id: "u5", order_index: 5, unit_type: "body", label: null },
      { unit_id: "u6", order_index: 6, unit_type: "body", label: null },
    ];
    const l1 = buildReaderRecordL1NavigationItems(
      makeSnapshot(units),
      docFromUnits(units),
    );
    expect(l1.map((i) => i.label)).toEqual(["Zulu", "Alpha"]);
  });
});
