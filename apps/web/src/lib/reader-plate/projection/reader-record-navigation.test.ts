import { describe, expect, it } from "vitest";
import {
  buildReaderRecordNavigationItems,
  type ReaderRecordNavigationItem,
} from "./reader-record-navigation";
import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";
import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  type ReaderRecordPlateDocument,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

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
      source_type: "text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
      created_at: "2024-01-01T00:00:00Z",
      reading_goal: "daily_reading",
      reading_variant: "beginner_reading",
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
});
