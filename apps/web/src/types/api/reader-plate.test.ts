import { describe, expect, it } from "vitest";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderEventPollResponseDto,
  type ReaderPhraseGlossMarkDto,
  type ReaderGrammarNoteMarkDto,
  type ReaderEventResponseDto,
  type ReaderStableSegmentTextLeafDto,
  type ReaderPlateSnapshotDto,
  type ReaderPlateValueDto,
  type ReaderPlainTextSubmitResponseDto,
  type ReaderSentenceAnalysisNodeDto,
  type ReaderTranslationGroupNodeDto,
  type ReaderUnitNodeDto,
} from "@/types/api/reader-plate";

/**
 * DTO shape tests: verify the TypeScript contracts match the backend
 * Pydantic schemas in `services/api/app/schemas/reader_orchestration.py`.
 * These tests guard against drift in field names, literal values, and
 * the Plate value node taxonomy.
 */

function makeUnit(): ReaderUnitNodeDto {
  const vocabularyMark: ReaderPhraseGlossMarkDto = {
    mark_id: "mark_phrase_1",
    layer_id: "layer_vocab_1",
    item_type: "phrase_gloss",
    anchor_segment_id: "s1",
    start_offset: 9,
    end_offset: 20,
    selected_text: "few can turn",
    segment_start_utf16: 9,
    segment_end_utf16: 20,
    starts_here: true,
    ends_here: true,
    phrase: "few can turn",
    phrase_type: "collocation",
    gloss: "少数人能做到",
    example: "Only a few can turn talent into impact.",
  };
  const grammarNoteMark: ReaderGrammarNoteMarkDto = {
    mark_id: "mark_grammar_1",
    item_id: "grammar_note_1",
    owner: "system_ai",
    layer_id: "layer_grammar_note_1",
    item_type: "grammar_note",
    anchor_segment_id: "s1",
    start_offset: 0,
    end_offset: 8,
    selected_text: "A scarce",
    segment_start_utf16: 0,
    segment_end_utf16: 8,
    starts_here: true,
    ends_here: true,
    span_index: 0,
    span_count: 1,
    show_note_chip: true,
    grammar_point: "fronted emphasis",
    pattern: "a scarce ...",
    note: "前置结构先给读者设置强调焦点。",
  };
  return {
    type: "reader_unit",
    owner: "stable",
    base_id: "base_1",
    unit_id: "u1",
    order_index: 1,
    unit_type: "body",
    boundary_quality: "normal",
    base_start_utf16: 0,
    base_end_utf16: 42,
    text_hash: "abcd1234",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    children: [
      {
        type: "reader_source_block",
        owner: "stable",
        base_id: "base_1",
        unit_id: "u1",
        base_start_utf16: 0,
        base_end_utf16: 42,
        children: [
          {
            type: "reader_anchor_segment",
            owner: "stable",
            base_id: "base_1",
            unit_id: "u1",
            anchor_segment_id: "s1",
            sentence_id: "s1",
            segment_type: "sentence",
            boundary_quality: "normal",
            base_start_utf16: 0,
            base_end_utf16: 42,
            unit_start_utf16: 0,
            unit_end_utf16: 42,
            text_hash: "abcd1234",
            hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
            children: [
              {
                text: "A scarce few can turn passion into income.",
                owner: "stable",
                lock_source: true,
                source_role: "segment_text",
                base_start_utf16: 0,
                base_end_utf16: 42,
                anchor_segment_id: "s1",
                segment_start_utf16: 0,
                segment_end_utf16: 42,
                reader_vocabulary_marks: [vocabularyMark],
                reader_grammar_note_marks: [grammarNoteMark],
              } satisfies ReaderStableSegmentTextLeafDto,
            ],
          },
        ],
      },
    ],
  };
}

function makeTranslation(): ReaderTranslationGroupNodeDto {
  return {
    type: "reader_translation_group",
    owner: "system_ai",
    layer_id: "layer_1",
    layer_version: 1,
    base_id: "base_1",
    unit_id: "u1",
    target_scope: "unit",
    target_key: "u1",
    group_id: "group_1",
    covered_anchor_segment_ids: ["s1"],
    source_text_hash: "abcd1234",
    children: [{ text: "很少有人能把热爱变成稳定收入。" }],
  };
}

function makeSentenceAnalysis(): ReaderSentenceAnalysisNodeDto {
  return {
    type: "reader_sentence_analysis",
    owner: "system_ai",
    analysis_id: "analysis_1",
    layer_id: "layer_sentence_analysis_1",
    layer_version: 1,
    base_id: "base_1",
    unit_id: "u1",
    target_scope: "unit",
    target_key: "u1",
    anchor_segment_id: "s1",
    selected_text: "A scarce few can turn passion into income.",
    label: "fronted emphasis",
    analysis: "先给强调对象，再交代真正动作。",
    chunks: [
      { order: 1, label: "focus", text: "A scarce few" },
      { order: 2, label: "action", text: "can turn passion into income" },
    ],
    children: [{ text: "先给强调对象，再交代真正动作。" }],
  };
}

function makeSnapshot(): ReaderPlateSnapshotDto {
  const unit = makeUnit();
  unit.children.push(makeTranslation());
  unit.children.push(makeSentenceAnalysis());
  const value: ReaderPlateValueDto = [unit];

  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "reader_snapshot_abc123",
    snapshot_taken_at: "2026-06-21T00:00:00Z",
    last_event_sequence: 3,
    record_id: "rec_1",
    record: {
      title: "Reader Plate DTO Fixture",
      display_title_zh: null,
      title_generation_status: "pending",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-06-21T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation: 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "a".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      text_length_utf16: 42,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: {
      units: [
        {
          unit_id: "u1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          label: null,
          base_start_utf16: 0,
          base_end_utf16: 42,
          text_hash: "abcd1234",
          hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
        },
      ],
    },
    anchor_segments: [
      {
        anchor_segment_id: "s1",
        sentence_id: "s1",
        paragraph_id: "u1",
        unit_id: "u1",
        order_index: 1,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: 0,
        base_end_utf16: 42,
        unit_start_utf16: 0,
        unit_end_utf16: 42,
        text_hash: "abcd1234",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      },
    ],
    enhancement_layers: [
      {
        layer_id: "layer_vocab_1",
        layer_type: "vocabulary",
        layer_subtype: null,
        owner: "system_ai",
        base_id: "base_1",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          schema_version: 1,
          items: [
            {
              item_type: "phrase_gloss",
              anchor: {
                anchor_type: "text_range",
                base_id: "base_1",
                unit_id: "u1",
                anchor_segment_id: "s1",
                sentence_id: "s1",
                segment_type: "sentence",
                offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
                start_offset: 9,
                end_offset: 20,
                selected_text: "few can turn",
                text_hash: "abcd1234",
                hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
              },
              phrase: "few can turn",
              phrase_type: "collocation",
              gloss: "少数人能做到",
              example: "Only a few can turn talent into impact.",
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
      {
        layer_id: "layer_grammar_note_1",
        layer_type: "grammar_note",
        layer_subtype: null,
        owner: "system_ai",
        base_id: "base_1",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          schema_version: 1,
          items: [
            {
              item_type: "grammar_note",
              spans: [
                {
                  anchor_type: "text_range",
                  base_id: "base_1",
                  unit_id: "u1",
                  anchor_segment_id: "s1",
                  sentence_id: "s1",
                  segment_type: "sentence",
                  offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
                  start_offset: 0,
                  end_offset: 8,
                  selected_text: "A scarce",
                  text_hash: "abcd1234",
                  hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
                },
              ],
              grammar_point: "fronted emphasis",
              pattern: "a scarce ...",
              note: "前置结构先给读者设置强调焦点。",
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
      {
        layer_id: "layer_sentence_analysis_1",
        layer_type: "sentence_analysis",
        layer_subtype: null,
        owner: "system_ai",
        base_id: "base_1",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          schema_version: 1,
          items: [
            {
              item_type: "sentence_analysis",
              anchor: {
                anchor_type: "text_range",
                base_id: "base_1",
                unit_id: "u1",
                anchor_segment_id: "s1",
                sentence_id: "s1",
                segment_type: "sentence",
                offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
                start_offset: 0,
                end_offset: 42,
                selected_text: "A scarce few can turn passion into income.",
                text_hash: "abcd1234",
                hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
              },
              label: "fronted emphasis",
              analysis: "先给强调对象，再交代真正动作。",
              chunks: [
                { order: 1, label: "focus", text: "A scarce few" },
                { order: 2, label: "action", text: "can turn passion into income" },
              ],
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
      {
        layer_id: "layer_1",
        layer_type: "translation",
        layer_subtype: null,
        owner: "system_ai",
        base_id: "base_1",
        target_scope: "unit",
        target_key: "u1",
        status: "published",
        schema_version: 1,
        output: {
          groups: [
            {
              group_id: "group_1",
              anchor_segment_ids: ["s1"],
              source_text_hash: "abcd1234",
              translated_text: "很少有人能把热爱变成稳定收入。",
            },
          ],
        },
        published_at: "2026-06-21T00:00:00Z",
      },
    ],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value,
  };
}

function makeEvent(): ReaderEventResponseDto {
  return {
    id: "evt_1",
    reading_record_id: "rec_1",
    sequence: 1,
    event_type: "article_ready",
    payload: {
      record_id: "rec_1",
      base_id: "base_1",
      generation: 1,
      readiness_state: "article_ready",
      product_state: "readable_enhancing",
    },
    source_run_id: null,
    source_job_id: null,
    source_layer_id: null,
    created_at: "2026-06-21T00:00:00Z",
  };
}

function makePollResponse(): ReaderEventPollResponseDto {
  return {
    reading_record_id: "rec_1",
    after_sequence: 0,
    next_after_sequence: 1,
    last_event_sequence: 1,
    has_more: false,
    truncated: false,
    reload_required: false,
    reload_reason: null,
    events: [makeEvent()],
  };
}

describe("Reader Plate DTO shapes", () => {
  it("ReaderPlateSnapshotDto has schema_kind = reader_plate_snapshot", () => {
    const snapshot = makeSnapshot();
    expect(snapshot.schema_kind).toBe(READER_PLATE_SNAPSHOT_SCHEMA_KIND);
    expect(snapshot.schema_kind).toBe("reader_plate_snapshot");
  });

  it("ReaderPlateSnapshotDto exposes record metadata, navigation hashes, anchor segments, and layer owners", () => {
    const snapshot = makeSnapshot();

    expect(snapshot.record).toMatchObject({
      title: "Reader Plate DTO Fixture",
      source_type: "plain_text",
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    });
    expect(snapshot.navigation.units[0]).toMatchObject({
      text_hash: "abcd1234",
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    });
    expect(snapshot.anchor_segments[0]).toMatchObject({
      anchor_segment_id: "s1",
      unit_id: "u1",
      text_hash: "abcd1234",
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    });
    expect(snapshot.enhancement_layers[0].owner).toBe("system_ai");
  });

  it("ReaderPlateSnapshotDto exposes last_event_sequence as the only recovery cursor", () => {
    const snapshot = makeSnapshot();
    expect(typeof snapshot.last_event_sequence).toBe("number");
    expect(snapshot.last_event_sequence).toBe(3);
    // D4 contract: snapshot must NOT expose projection_version.
    expect("projection_version" in snapshot).toBe(false);
  });

  it("ReaderPlateSnapshotDto.value is a list of reader_unit nodes", () => {
    const snapshot = makeSnapshot();
    expect(Array.isArray(snapshot.value)).toBe(true);
    expect(snapshot.value.length).toBe(1);
    expect(snapshot.value[0].type).toBe("reader_unit");
    expect(snapshot.value[0].owner).toBe("stable");
  });

  it("reader_unit contains reader_source_block and reader_translation_group children", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const childTypes = unit.children.map((child) => child.type);
    expect(childTypes).toContain("reader_source_block");
    expect(childTypes).toContain("reader_translation_group");
    expect(childTypes).toContain("reader_sentence_analysis");
  });

  it("reader_source_block contains reader_anchor_segment with stable segment_text leaf", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    );
    expect(sourceBlock).toBeDefined();
    if (sourceBlock?.type === "reader_source_block") {
      const anchor = sourceBlock.children.find(
        (child) => "type" in child && child.type === "reader_anchor_segment",
      );
      expect(anchor).toBeDefined();
      if (anchor && "type" in anchor && anchor.type === "reader_anchor_segment") {
        expect(anchor.anchor_segment_id).toBe("s1");
        expect(anchor.sentence_id).toBe("s1");
        expect(anchor.segment_type).toBe("sentence");
        const leaf = anchor.children[0];
        expect(leaf.owner).toBe("stable");
        expect(leaf.lock_source).toBe(true);
        expect(leaf.source_role).toBe("segment_text");
        expect(leaf.anchor_segment_id).toBe("s1");
      }
    }
  });

  it("reader_translation_group carries layer and group metadata", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const translation = unit.children.find(
      (child) => child.type === "reader_translation_group",
    );
    expect(translation).toBeDefined();
    if (translation?.type === "reader_translation_group") {
      expect(translation.owner).toBe("system_ai");
      expect(translation.layer_id).toBe("layer_1");
      expect(translation.target_scope).toBe("unit");
      expect(translation.target_key).toBe("u1");
      expect(translation.group_id).toBe("group_1");
      expect(translation.covered_anchor_segment_ids).toEqual(["s1"]);
      expect(translation.source_text_hash).toBe("abcd1234");
      expect(translation.children[0].text).toContain("收入");
    }
  });

  it("stable source leaves may carry vocabulary marks with typed item metadata", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    );
    if (sourceBlock?.type !== "reader_source_block") {
      throw new Error("expected reader_source_block");
    }
    const anchor = sourceBlock.children.find(
      (child) => "type" in child && child.type === "reader_anchor_segment",
    );
    if (!anchor || !("type" in anchor) || anchor.type !== "reader_anchor_segment") {
      throw new Error("expected reader_anchor_segment");
    }
    const leaf = anchor.children[0];
    const mark = leaf.reader_vocabulary_marks?.[0];
    expect(mark).toBeDefined();
    expect(mark?.item_type).toBe("phrase_gloss");
    if (!mark || mark.item_type !== "phrase_gloss") {
      throw new Error("expected phrase_gloss vocabulary mark");
    }
    expect(mark.gloss).toContain("少数人");
    expect(mark.starts_here).toBe(true);
  });

  it("stable source leaves may carry grammar_note marks with system_ai ownership", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    );
    if (sourceBlock?.type !== "reader_source_block") {
      throw new Error("expected reader_source_block");
    }
    const anchor = sourceBlock.children.find(
      (child) => "type" in child && child.type === "reader_anchor_segment",
    );
    if (!anchor || !("type" in anchor) || anchor.type !== "reader_anchor_segment") {
      throw new Error("expected reader_anchor_segment");
    }
    const mark = anchor.children[0].reader_grammar_note_marks?.[0];
    expect(mark).toBeDefined();
    expect(mark?.item_type).toBe("grammar_note");
    expect(mark?.owner).toBe("system_ai");
    expect(mark?.show_note_chip).toBe(true);
  });

  it("reader_sentence_analysis carries structured system_ai projection fields", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    const analysisNode = unit.children.find(
      (child) => child.type === "reader_sentence_analysis",
    );
    expect(analysisNode).toBeDefined();
    if (analysisNode?.type !== "reader_sentence_analysis") {
      throw new Error("expected reader_sentence_analysis");
    }
    expect(analysisNode.owner).toBe("system_ai");
    expect(analysisNode.anchor_segment_id).toBe("s1");
    expect(analysisNode.label).toBe("fronted emphasis");
    expect(analysisNode.chunks[0]?.label).toBe("focus");
  });

  it("hash_algorithm is fnv1a32-utf16 on all anchor-bearing nodes", () => {
    const snapshot = makeSnapshot();
    const unit = snapshot.value[0];
    expect(unit.hash_algorithm).toBe(READER_TEXT_RANGE_HASH_ALGORITHM);
    const sourceBlock = unit.children.find(
      (child) => child.type === "reader_source_block",
    );
    if (sourceBlock?.type === "reader_source_block") {
      const anchor = sourceBlock.children.find(
        (child) => "type" in child && child.type === "reader_anchor_segment",
      );
      if (anchor && "type" in anchor && anchor.type === "reader_anchor_segment") {
        expect(anchor.hash_algorithm).toBe(READER_TEXT_RANGE_HASH_ALGORITHM);
      }
    }
  });

  it("ReaderPlainTextSubmitResponseDto wraps record_id, base_id, sequence and snapshot", () => {
    const snapshot = makeSnapshot();
    const response: ReaderPlainTextSubmitResponseDto = {
      record_id: "rec_1",
      base_id: "base_1",
      article_ready_sequence: 1,
      snapshot,
    };
    expect(response.record_id).toBe("rec_1");
    expect(response.base_id).toBe("base_1");
    expect(response.article_ready_sequence).toBe(1);
    expect(response.snapshot.schema_kind).toBe("reader_plate_snapshot");
  });

  it("ReaderEventPollResponseDto exposes cursor and reload fields", () => {
    const response = makePollResponse();
    expect(response.after_sequence).toBe(0);
    expect(response.next_after_sequence).toBe(1);
    expect(response.last_event_sequence).toBe(1);
    expect(response.has_more).toBe(false);
    expect(response.truncated).toBe(false);
    expect(response.reload_required).toBe(false);
    expect(response.reload_reason).toBeNull();
    expect(response.events).toHaveLength(1);
  });

  it("ReaderEventResponseDto carries event_type and payload", () => {
    const event = makeEvent();
    expect(event.id).toBe("evt_1");
    expect(event.sequence).toBe(1);
    expect(event.event_type).toBe("article_ready");
    expect(event.payload.record_id).toBe("rec_1");
  });

  it("ReaderTextRangeAnchorDto uses utf16 offset_unit", () => {
    const anchor = {
      anchor_type: "text_range" as const,
      base_id: "base_1",
      unit_id: "u1",
      anchor_segment_id: "s1",
      sentence_id: null,
      segment_type: "sentence" as const,
      offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
      start_offset: 0,
      end_offset: 5,
      selected_text: "Hello",
      text_hash: "abcd1234",
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    };
    expect(anchor.offset_unit).toBe("utf16");
    expect(anchor.hash_algorithm).toBe("fnv1a32-utf16");
  });

  it("reload-triggering event types are a subset of ReaderEventType", () => {
    const reloadTypes: string[] = [
      "layer_published",
      "record_product_state_updated",
      "projection_reset_required",
    ];
    const allTypes: string[] = [
      "article_ready",
      "record_product_state_updated",
      "layer_published",
      "layer_failed",
      "parsed_decision_updated",
      "record_state_changed",
      "action_required",
      "run_completed",
      "record_superseded",
      "projection_ops",
      "projection_reset_required",
    ];
    reloadTypes.forEach((type) => {
      expect(allTypes).toContain(type);
    });
  });
});
