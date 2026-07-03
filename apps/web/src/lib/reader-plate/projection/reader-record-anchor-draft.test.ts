/** @vitest-environment jsdom */

import { computeUtf16FNV1a } from "@claread/contracts";
import { describe, expect, it } from "vitest";

import type {
  ReaderPlateSnapshotDto,
  ReaderSnapshotAnchorSegmentDto,
} from "@/types/api/reader-plate";

import {
  anchorDraftForSelectionSegment,
  anchorDraftsForSelection,
} from "./reader-record-anchor-draft";

const RECORD_ID = "rec_product_1";
const BASE_ID = "base_unit_test";
const UNIT_ID_U1 = "u1";
const UNIT_ID_U2 = "u2";
const SEGMENT_ID_U1_S1 = "seg_u1_s1";
const SEGMENT_ID_U1_S2 = "seg_u1_s2";
const SEGMENT_ID_U2_S1 = "seg_u2_s1";
const SENTENCE_ID_U1_S1 = "s_u1_1";
const SENTENCE_ID_U1_S2 = "s_u1_2";
const SENTENCE_ID_U2_S1 = "s_u2_1";

function makeAnchorSegment(
  overrides: Partial<ReaderSnapshotAnchorSegmentDto>,
): ReaderSnapshotAnchorSegmentDto {
  return {
    anchor_segment_id: "seg_default",
    sentence_id: "s_default",
    paragraph_id: "p_default",
    unit_id: UNIT_ID_U1,
    order_index: 1,
    unit_order_index: 1,
    segment_type: "sentence",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: 10,
    unit_start_utf16: 0,
    unit_end_utf16: 10,
    text_hash: "00000000",
    hash_algorithm: "fnv1a32-utf16",
    ...overrides,
  };
}

function makeSnapshot(
  anchorSegments: ReaderSnapshotAnchorSegmentDto[],
): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "snap_test_1",
    snapshot_taken_at: "2026-06-23T00:00:00Z",
    last_event_sequence: 1,
    record_id: RECORD_ID,
    record: {
      title: "Reader Record Anchor Draft Test",
      display_title_zh: "Reader Record Anchor Draft Test",
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      created_at: "2026-06-23T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: BASE_ID,
      content_sha256: "sha256_test",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 1024,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: [] },
    anchor_segments: anchorSegments,
    enhancement_layers: [],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
  };
}

// Two anchor segments in the SAME unit (u1). The second segment starts at
// unit-local offset 28; this is the case the spec calls out explicitly — the
// helper must not emit identical unit-local offsets for two distinct
// anchor segments that happen to live in the same unit.
const SEGMENT_U1_S1_TEXT = "Institutional memory ";
const SEGMENT_U1_S1_HASH = computeUtf16FNV1a(SEGMENT_U1_S1_TEXT);
const SEGMENT_U1_S2_TEXT = "shapes policy choices.";
const SEGMENT_U1_S2_HASH = computeUtf16FNV1a(SEGMENT_U1_S2_TEXT);
const SEGMENT_U2_S1_TEXT = "These choices persist.";
const SEGMENT_U2_S1_HASH = computeUtf16FNV1a(SEGMENT_U2_S1_TEXT);

const ANCHOR_SEGMENTS: ReaderSnapshotAnchorSegmentDto[] = [
  makeAnchorSegment({
    anchor_segment_id: SEGMENT_ID_U1_S1,
    sentence_id: SENTENCE_ID_U1_S1,
    paragraph_id: UNIT_ID_U1,
    unit_id: UNIT_ID_U1,
    order_index: 1,
    unit_order_index: 1,
    base_start_utf16: 0,
    base_end_utf16: 22,
    unit_start_utf16: 0,
    unit_end_utf16: 22,
    text_hash: SEGMENT_U1_S1_HASH,
  }),
  makeAnchorSegment({
    anchor_segment_id: SEGMENT_ID_U1_S2,
    sentence_id: SENTENCE_ID_U1_S2,
    paragraph_id: UNIT_ID_U1,
    unit_id: UNIT_ID_U1,
    order_index: 2,
    unit_order_index: 2,
    base_start_utf16: 22,
    base_end_utf16: 46,
    // Second anchor in same unit: non-zero unit_start_utf16 baseline.
    unit_start_utf16: 22,
    unit_end_utf16: 46,
    text_hash: SEGMENT_U1_S2_HASH,
  }),
  makeAnchorSegment({
    anchor_segment_id: SEGMENT_ID_U2_S1,
    sentence_id: SENTENCE_ID_U2_S1,
    paragraph_id: UNIT_ID_U2,
    unit_id: UNIT_ID_U2,
    order_index: 3,
    unit_order_index: 1,
    base_start_utf16: 46,
    base_end_utf16: 68,
    unit_start_utf16: 0,
    unit_end_utf16: 22,
    text_hash: SEGMENT_U2_S1_HASH,
  }),
];

function makeSegment(overrides: {
  sentenceId?: string;
  paragraphId?: string;
  selectedText?: string;
  startOffset?: number;
  endOffset?: number;
}) {
  const selectedText = overrides.selectedText ?? SEGMENT_U1_S1_TEXT;
  return {
    paragraphId: overrides.paragraphId ?? UNIT_ID_U1,
    sentenceId: overrides.sentenceId ?? SENTENCE_ID_U1_S1,
    sentence: {
      sentenceId: overrides.sentenceId ?? SENTENCE_ID_U1_S1,
      paragraphId: overrides.paragraphId ?? UNIT_ID_U1,
      text: "Institutional memory shapes policy choices.",
    },
    selectedText,
    startOffset: overrides.startOffset ?? 0,
    endOffset: overrides.endOffset ?? selectedText.length,
    textHash: computeUtf16FNV1a(selectedText),
  };
}

describe("anchorDraftForSelectionSegment - single anchor segment", () => {
  it("produces a draft with record_id, base_id, unit_id and anchor_segment_id from the snapshot", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({});

    const draft = anchorDraftForSelectionSegment(snapshot, segment);

    expect(draft).not.toBeNull();
    expect(draft?.record_id).toBe(RECORD_ID);
    expect(draft?.base_id).toBe(BASE_ID);
    expect(draft?.unit_id).toBe(UNIT_ID_U1);
    expect(draft?.anchor_segment_id).toBe(SEGMENT_ID_U1_S1);
    expect(draft?.scope).toBe("stable_source");
    expect(draft?.offset_unit).toBe("utf16");
    expect(draft?.hash_algorithm).toBe("fnv1a32-utf16");
  });

  it("emits unit-local offsets by adding the anchor segment unit_start_utf16 baseline", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      selectedText: "memory",
      startOffset: 13,
      endOffset: 13 + "memory".length,
    });

    const draft = anchorDraftForSelectionSegment(snapshot, segment);

    expect(draft).not.toBeNull();
    // First anchor in u1 has unit_start_utf16 = 0, so unit-local offset
    // equals segment-local offset.
    expect(draft?.start_offset).toBe(13);
    expect(draft?.end_offset).toBe(13 + "memory".length);
  });

  it("keeps selected_text and text_hash consistent with the segment text", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      selectedText: "memory",
      startOffset: 13,
      endOffset: 13 + "memory".length,
    });

    const draft = anchorDraftForSelectionSegment(snapshot, segment);

    expect(draft).not.toBeNull();
    expect(draft?.selected_text).toBe("memory");
    expect(draft?.text_hash).toBe(computeUtf16FNV1a("memory"));
  });
});

describe("anchorDraftForSelectionSegment - same-unit second anchor segment", () => {
  it("adds the second anchor's unit_start_utf16 baseline so unit-local offsets differ from the first", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    // Selection inside the SECOND anchor segment of u1, starting at
    // segment-local offset 0 (i.e. the first char of "shapes policy choices.").
    const segment = makeSegment({
      sentenceId: SENTENCE_ID_U1_S2,
      paragraphId: UNIT_ID_U1,
      selectedText: "shapes",
      startOffset: 0,
      endOffset: 6,
    });

    const draft = anchorDraftForSelectionSegment(snapshot, segment);

    expect(draft).not.toBeNull();
    // unit_start_utf16 baseline for the second anchor in u1 is 22.
    expect(draft?.start_offset).toBe(22 + 0);
    expect(draft?.end_offset).toBe(22 + 6);
    expect(draft?.anchor_segment_id).toBe(SEGMENT_ID_U1_S2);
    expect(draft?.unit_id).toBe(UNIT_ID_U1);
  });

  it("produces strictly different unit-local offsets for two selections in two anchor segments of the same unit", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const firstSegment = makeSegment({
      selectedText: "memory",
      startOffset: 13,
      endOffset: 19,
    });
    const secondSegment = makeSegment({
      sentenceId: SENTENCE_ID_U1_S2,
      paragraphId: UNIT_ID_U1,
      selectedText: "policy",
      startOffset: 13,
      endOffset: 19,
    });

    const firstDraft = anchorDraftForSelectionSegment(snapshot, firstSegment);
    const secondDraft = anchorDraftForSelectionSegment(
      snapshot,
      secondSegment,
    );

    expect(firstDraft).not.toBeNull();
    expect(secondDraft).not.toBeNull();

    // Both selections start at segment-local 13. Without the baseline fix,
    // they would emit the SAME unit-local start_offset (=13). With the fix,
    // the second anchor contributes +22.
    expect(firstDraft?.start_offset).toBe(13);
    expect(secondDraft?.start_offset).toBe(22 + 13);
    expect(firstDraft?.start_offset).not.toBe(secondDraft?.start_offset);
    expect(firstDraft?.anchor_segment_id).toBe(SEGMENT_ID_U1_S1);
    expect(secondDraft?.anchor_segment_id).toBe(SEGMENT_ID_U1_S2);
  });
});

describe("anchorDraftForSelectionSegment - cross-unit selection", () => {
  it("resolves a segment in u2 even though it shares a sentence_id prefix with u1 segments", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      sentenceId: SENTENCE_ID_U2_S1,
      paragraphId: UNIT_ID_U2,
      selectedText: "persist",
      startOffset: 14,
      endOffset: 14 + "persist".length,
    });

    const draft = anchorDraftForSelectionSegment(snapshot, segment);

    expect(draft).not.toBeNull();
    expect(draft?.unit_id).toBe(UNIT_ID_U2);
    expect(draft?.anchor_segment_id).toBe(SEGMENT_ID_U2_S1);
    // u2 anchor has unit_start_utf16 = 0, so unit-local = segment-local.
    expect(draft?.start_offset).toBe(14);
    expect(draft?.end_offset).toBe(14 + "persist".length);
  });
});

describe("anchorDraftForSelectionSegment - rejection cases", () => {
  it("returns null when no anchor segment matches the selection's sentenceId", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      sentenceId: "s_unknown",
      selectedText: "memory",
      startOffset: 13,
      endOffset: 19,
    });

    expect(anchorDraftForSelectionSegment(snapshot, segment)).toBeNull();
  });

  it("returns null when end_offset <= start_offset", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      selectedText: "memory",
      startOffset: 13,
      endOffset: 13,
    });

    expect(anchorDraftForSelectionSegment(snapshot, segment)).toBeNull();
  });

  it("returns null when selected_text UTF-16 length disagrees with offset span", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    // end_offset lies about the slice length.
    const segment = makeSegment({
      selectedText: "memory",
      startOffset: 13,
      endOffset: 30,
    });

    expect(anchorDraftForSelectionSegment(snapshot, segment)).toBeNull();
  });

  it("returns null when selection offsets start before the anchor segment range", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      selectedText: "memo",
      startOffset: -1,
      endOffset: 4,
    });

    expect(anchorDraftForSelectionSegment(snapshot, segment)).toBeNull();
  });

  it("returns null when selection offsets end after the anchor segment range", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = makeSegment({
      selectedText: "memory",
      startOffset: 20,
      endOffset: 26,
    });

    expect(anchorDraftForSelectionSegment(snapshot, segment)).toBeNull();
  });

  it("returns null when text_hash disagrees with selected_text", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const segment = {
      ...makeSegment({ selectedText: "memory", startOffset: 13, endOffset: 19 }),
      textHash: "deadbeef",
    };

    expect(anchorDraftForSelectionSegment(snapshot, segment)).toBeNull();
  });
});

describe("anchorDraftsForSelection - multi-segment selection", () => {
  it("emits one draft per resolvable segment and skips unresolvable ones", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const drafts = anchorDraftsForSelection(snapshot, {
      segments: [
        makeSegment({
          sentenceId: SENTENCE_ID_U1_S1,
          selectedText: "memory",
          startOffset: 13,
          endOffset: 19,
        }),
        makeSegment({
          sentenceId: "s_unknown",
          selectedText: "missing",
          startOffset: 0,
          endOffset: 7,
        }),
        makeSegment({
          sentenceId: SENTENCE_ID_U2_S1,
          paragraphId: UNIT_ID_U2,
          selectedText: "persist",
          startOffset: 14,
          endOffset: 21,
        }),
      ],
    });

    expect(drafts).toHaveLength(2);
    expect(drafts[0]?.anchor_segment_id).toBe(SEGMENT_ID_U1_S1);
    expect(drafts[1]?.anchor_segment_id).toBe(SEGMENT_ID_U2_S1);
  });

  it("preserves the segment order from the input selection", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const drafts = anchorDraftsForSelection(snapshot, {
      segments: [
        makeSegment({
          sentenceId: SENTENCE_ID_U2_S1,
          paragraphId: UNIT_ID_U2,
          selectedText: "persist",
          startOffset: 14,
          endOffset: 21,
        }),
        makeSegment({
          sentenceId: SENTENCE_ID_U1_S1,
          selectedText: "memory",
          startOffset: 13,
          endOffset: 19,
        }),
      ],
    });

    expect(drafts.map((d) => d.unit_id)).toEqual([UNIT_ID_U2, UNIT_ID_U1]);
  });
});

describe("anchorDraftForSelectionSegment - generation fence", () => {
  it("emits the current Reading Record generation from the snapshot", () => {
    const snapshot = makeSnapshot(ANCHOR_SEGMENTS);
    const draft = anchorDraftForSelectionSegment(
      snapshot,
      makeSegment({}),
    );

    expect(draft).not.toBeNull();
    expect(draft?.generation).toBe(1);
  });
});

describe("anchorDraftForSelectionSegment - UTF-16 multi-codeunit handling", () => {
  it("computes unit-local end_offset using UTF-16 code-unit length for surrogate-pair text", () => {
    // 🧠 is U+1F9E0, encoded as a UTF-16 surrogate pair (2 code units).
    const selectedText = "🧠";
    const textHash = computeUtf16FNV1a(selectedText);
    const anchorSegments: ReaderSnapshotAnchorSegmentDto[] = [
      makeAnchorSegment({
        anchor_segment_id: "seg_surrogate",
        sentence_id: "s_surrogate",
        paragraph_id: UNIT_ID_U1,
        unit_id: UNIT_ID_U1,
        unit_start_utf16: 100,
        unit_end_utf16: 102,
        text_hash: textHash,
      }),
    ];
    const snapshot = makeSnapshot(anchorSegments);
    const segment = makeSegment({
      sentenceId: "s_surrogate",
      paragraphId: UNIT_ID_U1,
      selectedText,
      startOffset: 0,
      endOffset: 2,
    });

    const draft = anchorDraftForSelectionSegment(snapshot, segment);

    expect(draft).not.toBeNull();
    expect(draft?.start_offset).toBe(100);
    expect(draft?.end_offset).toBe(102);
    expect(draft?.text_hash).toBe(textHash);
  });
});
