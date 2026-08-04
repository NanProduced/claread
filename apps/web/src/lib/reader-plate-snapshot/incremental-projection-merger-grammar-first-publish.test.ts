// task-history: T4.2a-PUX-R4-R2.2-P2c-R1
/**
 * grammar_note 首发语义 insert 纯函数测试。
 *
 * 覆盖 merger 的 grammar 首发专用 merge 路径（detectGrammarFirstPublish +
 * mergeGrammarFirstPublish）：
 * - Happy path：单 / 多 descriptor grammar 首发 → targeted_apply（insert 操作，
 *   path 降序，应用后 blockId 序列 === next）。
 * - Fallback：descriptor 与 next callout-group 不匹配、item_ids 集合不等、
 *   layer_id 不一致、cross-unit、mixed event、fence mismatch、非目标 block 变化、
 *   canonical order 不匹配等 → fallback_full_reload。
 * - 回归：无 P2b 扩展字段的 layer_published 仍走 R2.1E changed-block-only 路径。
 */

import type { Descendant } from "platejs";
import { describe, expect, it } from "vitest";

import { mergeIncrementalProjection } from "@/lib/reader-plate-snapshot/incremental-projection-merger";
import type {
  ReaderEventResponseDto,
  ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const BASE_ID = "base_test_p2c";
const GENERATION = 1;
const RECORD_ID = "rec_1";
const UNIT_ID = "unit_1";
const LAYER_ID = "layer_grammar_1";

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

/**
 * 创建 paragraph block，id = `paragraph:{anchorSegmentId}`，
 * 携带 data.unitId / data.anchorSegmentId。
 */
function makeParagraphBlock(
  anchorSegmentId: string,
  unitId: string,
  text: string,
): Descendant {
  return {
    type: "paragraph",
    id: `paragraph:${anchorSegmentId}`,
    children: [{ text }],
    data: { anchorSegmentId, unitId },
  } as unknown as Descendant;
}

/**
 * 创建 grammar callout 子节点（callout-group 的 child），携带 data.itemId。
 */
function makeGrammarCalloutChild(
  itemId: string,
  unitId: string,
  anchorSegmentId: string,
  layerId: string,
  text: string,
): Descendant {
  return {
    type: "callout",
    id: `callout:grammar:${itemId}`,
    variant: "grammar",
    icon: "📖",
    children: [{ text }],
    data: { unitId, layerId, itemId, anchorSegmentId },
  } as unknown as Descendant;
}

/**
 * 创建 callout-group block，id = `callout-group:{unitId}:{anchorSegmentId}`，
 * data.unitId / data.anchorSegmentId，children 为每个 itemId 生成 grammar callout。
 */
function makeCalloutGroupBlock(
  unitId: string,
  anchorSegmentId: string,
  layerId: string,
  itemIds: string[],
): Descendant {
  return {
    type: "callout-group",
    id: `callout-group:${unitId}:${anchorSegmentId}`,
    children: itemIds.map((itemId, idx) =>
      makeGrammarCalloutChild(
        itemId,
        unitId,
        anchorSegmentId,
        layerId,
        `note ${idx + 1}`,
      ),
    ),
    data: { unitId, anchorSegmentId },
  } as unknown as Descendant;
}

/**
 * 创建 sentence_analysis block，id = `sentence_analysis:{anchorSegmentId}`。
 */
function makeSentenceAnalysisBlock(
  anchorSegmentId: string,
  unitId: string,
): Descendant {
  return {
    type: "sentence_analysis",
    id: `sentence_analysis:${anchorSegmentId}`,
    icon: "🔬",
    children: [{ text: "analysis" }],
    data: { unitId, anchorSegmentId },
  } as unknown as Descendant;
}

/**
 * 创建 translation blockquote block（用于 R2.1E 回归测试）。
 */
function makeTranslationBlockquote(
  layerId: string,
  groupId: string,
  text: string,
  unitId: string = UNIT_ID,
): Descendant {
  return {
    type: "blockquote",
    id: `blockquote:${layerId}:${groupId}`,
    children: [{ text }],
    data: { unitId, layerId, groupId },
  } as unknown as Descendant;
}

/**
 * 创建最小 ReaderPlateSnapshotDto。
 */
function makeBaseSnapshot(
  generation: number = GENERATION,
  baseId: string = BASE_ID,
): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: `snap_${generation}`,
    snapshot_taken_at: "2026-07-14T00:00:00Z",
    last_event_sequence: 1,
    record_id: RECORD_ID,
    record: {
      title: "Test Record",
      display_title_zh: "测试标题",
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intensive_reading",
      created_at: "2026-07-14T00:00:00Z",
      source_type: "url",
      source_metadata: {},
      generation,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: baseId,
      content_sha256: "sha256_test",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 100,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: [] },
    anchor_segments: [],
    enhancement_layers: [],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
  };
}

/**
 * 创建 grammar_note 首发 layer_published 事件（携带 P2b 扩展字段）。
 * 单 descriptor 版本。
 */
function makeGrammarFirstPublishEvent(
  layerId: string,
  unitId: string,
  anchorSegmentId: string,
  itemIds: string[],
  generation: number = GENERATION,
  options: {
    baseId?: string;
    recordId?: string;
    sequence?: number;
    descriptorLayerId?: string;
    descriptorUnitId?: string;
  } = {},
): ReaderEventResponseDto {
  const {
    baseId = BASE_ID,
    recordId = RECORD_ID,
    sequence = 2,
    descriptorLayerId = layerId,
    descriptorUnitId = unitId,
  } = options;
  return {
    id: `evt_${sequence}`,
    reading_record_id: recordId,
    sequence,
    event_type: "layer_published",
    payload: {
      record_id: recordId,
      base_id: baseId,
      layer_id: layerId,
      layer_type: "grammar_note",
      target_scope: "unit",
      target_key: unitId,
      generation,
      schema_version: 1,
      operation: "insert_after_anchor",
      insertions: [
        {
          unit_id: descriptorUnitId,
          anchor_segment_id: anchorSegmentId,
          kind: "grammar_note",
          layer_id: descriptorLayerId,
          item_ids: itemIds,
        },
      ],
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

/**
 * 创建 grammar_note 首发 layer_published 事件（多 descriptor 版本）。
 */
function makeGrammarFirstPublishEventMultiDescriptor(
  layerId: string,
  unitId: string,
  descriptors: Array<{
    anchorSegmentId: string;
    itemIds: string[];
    unitId?: string;
    layerId?: string;
  }>,
  generation: number = GENERATION,
  sequence: number = 2,
): ReaderEventResponseDto {
  return {
    id: `evt_${sequence}`,
    reading_record_id: RECORD_ID,
    sequence,
    event_type: "layer_published",
    payload: {
      record_id: RECORD_ID,
      base_id: BASE_ID,
      layer_id: layerId,
      layer_type: "grammar_note",
      target_scope: "unit",
      target_key: unitId,
      generation,
      schema_version: 1,
      operation: "insert_after_anchor",
      insertions: descriptors.map((d) => ({
        unit_id: d.unitId ?? unitId,
        anchor_segment_id: d.anchorSegmentId,
        kind: "grammar_note",
        layer_id: d.layerId ?? layerId,
        item_ids: d.itemIds,
      })),
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

/**
 * 创建无 P2b 扩展字段的 layer_published 事件（用于 R2.1E 回归测试）。
 */
function makePlainLayerPublishedEvent(
  layerType: "translation" | "vocabulary" | "grammar_note" | "sentence_analysis",
  targetKey: string = UNIT_ID,
  options: {
    layerId?: string;
    generation?: number;
    sequence?: number;
  } = {},
): ReaderEventResponseDto {
  const {
    layerId = `layer_${layerType}_1`,
    generation = GENERATION,
    sequence = 2,
  } = options;
  return {
    id: `evt_${sequence}`,
    reading_record_id: RECORD_ID,
    sequence,
    event_type: "layer_published",
    payload: {
      record_id: RECORD_ID,
      base_id: BASE_ID,
      layer_id: layerId,
      layer_type: layerType,
      target_scope: "unit",
      target_key: targetKey,
      generation,
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

/**
 * 创建携带 P2b operation 字段但非 grammar_note 的 layer_published 事件
 * （用于 unsupported_layer_type 测试）。
 */
function makeNonGrammarP2bEvent(
  layerType: "translation" | "vocabulary" | "sentence_analysis",
  layerId: string,
  unitId: string,
  anchorSegmentId: string,
  itemIds: string[],
  generation: number = GENERATION,
): ReaderEventResponseDto {
  return {
    id: "evt_2",
    reading_record_id: RECORD_ID,
    sequence: 2,
    event_type: "layer_published",
    payload: {
      record_id: RECORD_ID,
      base_id: BASE_ID,
      layer_id: layerId,
      layer_type: layerType,
      target_scope: "unit",
      target_key: unitId,
      generation,
      schema_version: 1,
      operation: "insert_after_anchor",
      insertions: [
        {
          unit_id: unitId,
          anchor_segment_id: anchorSegmentId,
          kind: "grammar_note",
          layer_id: layerId,
          item_ids: itemIds,
        },
      ],
    },
    created_at: "2026-07-14T00:00:00Z",
  };
}

/** 创建 SnapshotFenceContext。 */
function makeFence(
  generation: number = GENERATION,
  baseId: string = BASE_ID,
) {
  return { generation, baseId };
}

/** 从 children 提取 blockId 序列，用于模拟 insert 后的顺序比较。 */
function extractBlockIds(children: Descendant[]): Array<string | null> {
  return children.map((node) => {
    const id = (node as unknown as { id?: unknown }).id;
    return typeof id === "string" ? id : null;
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("mergeIncrementalProjection — grammar 首发语义 insert", () => {
  const snapshotFence = makeFence();

  // --- Happy path ---

  it("test_single_descriptor_grammar_first_publish_targeted_insert", () => {
    // 单 descriptor grammar 首发：next 在 paragraph 后插入 callout-group，
    // 期望 targeted_apply 含 1 个 insert 操作，path = paragraph_index + 1 = 1。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("targeted_apply");
    if (result.kind !== "targeted_apply") return;
    expect(result.operations).toHaveLength(1);
    expect(result.operations[0]!.type).toBe("insert");
    expect(result.operations[0]!.path).toEqual([1]);
    expect(result.operations[0]!.blockId).toBe("callout-group:unit_1:seg_1");
    expect(result.operations[0]!.nodes).toEqual([nextChildren[1]]);
  });

  it("test_multi_descriptor_path_descending_insert_final_order_equals_next", () => {
    // 多 descriptor：2 个 anchor 的 callout-group 首发，operations 按 path 降序，
    // 模拟降序插入后 blockId 序列 === next。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEventMultiDescriptor(
      LAYER_ID,
      UNIT_ID,
      [
        { anchorSegmentId: "seg_1", itemIds: ["layer_1:grammar_note:0"] },
        { anchorSegmentId: "seg_2", itemIds: ["layer_1:grammar_note:1"] },
      ],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source one"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      makeParagraphBlock("seg_2", UNIT_ID, "source two"),
      makeSentenceAnalysisBlock("seg_2", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source one"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      makeParagraphBlock("seg_2", UNIT_ID, "source two"),
      makeCalloutGroupBlock(UNIT_ID, "seg_2", LAYER_ID, [
        "layer_1:grammar_note:1",
      ]),
      makeSentenceAnalysisBlock("seg_2", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("targeted_apply");
    if (result.kind !== "targeted_apply") return;
    expect(result.operations).toHaveLength(2);
    expect(result.operations.every((op) => op.type === "insert")).toBe(true);

    // path 降序：seg_2 的 paragraph 在 prev index 2 → insertPath 3；
    // seg_1 的 paragraph 在 prev index 0 → insertPath 1。降序 → [3, 1]。
    const paths = result.operations.map((op) => op.path[0]);
    expect(paths).toEqual([3, 1]);

    // 模拟降序插入：将 prev 副本按 op 顺序 splice insert，结果 blockId 序列
    // 必须与 nextChildren 完全一致。
    const simulated: Descendant[] = [...prevChildren];
    for (const op of result.operations) {
      const insertIndex = op.path[0]!;
      const node = op.nodes![0]!;
      simulated.splice(insertIndex, 0, node);
    }
    expect(extractBlockIds(simulated)).toEqual(extractBlockIds(nextChildren));
  });

  it("test_grammar_group_located_after_paragraph_before_sentence_analysis", () => {
    // 验证 insert path = prev paragraph index + 1，且 callout-group 在 next 中
    // 位于 paragraph 后、sentence_analysis 前。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("targeted_apply");
    if (result.kind !== "targeted_apply") return;
    expect(result.operations).toHaveLength(1);
    expect(result.operations[0]!.type).toBe("insert");
    // prev paragraph:seg_1 在 index 0 → insertPath = 0 + 1 = 1。
    expect(result.operations[0]!.path).toEqual([1]);
    // next 中 callout-group 在 index 1，sentence_analysis 在 index 2。
    expect(extractBlockIds(nextChildren)).toEqual([
      "paragraph:seg_1",
      "callout-group:unit_1:seg_1",
      "sentence_analysis:seg_1",
    ]);
  });

  it("test_real_grammar_mark_paragraph_change_is_targeted_replace_plus_insert", () => {
    // 真实 projection 在 grammar 首发时不仅新增 callout-group，也会把目标
    // paragraph 切成带 grammar_data 的 leaf。该变化是 descriptor 已声明的
    // 合法表示变化，必须产生 paragraph replace + group insert，而不能误判
    // 为 unrepresented_projection_change 后全量 setValue。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const itemId = "layer_1:grammar_note:0";
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      [itemId],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextParagraph = {
      type: "paragraph",
      id: "paragraph:seg_1",
      data: { anchorSegmentId: "seg_1", unitId: UNIT_ID },
      children: [
        { text: "source " },
        {
          text: "text",
          grammar: true,
          grammar_data: { itemId, markId: "grammar_mark_1" },
        },
      ],
    } as unknown as Descendant;
    const nextChildren = [
      nextParagraph,
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [itemId]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("targeted_apply");
    if (result.kind !== "targeted_apply") return;
    expect(result.operations).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "replace",
          blockId: "paragraph:seg_1",
          path: [0],
          nodes: [nextParagraph],
        }),
        expect.objectContaining({
          type: "insert",
          blockId: "callout-group:unit_1:seg_1",
          path: [1],
        }),
      ]),
    );
  });

  // --- Fallback ---

  it("test_descriptor_group_not_in_next_fallback", () => {
    // descriptor 引用的 callout-group 在 nextChildren 中不存在 → fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );

    // next 中没有 callout-group —— descriptor 找不到对应 block。
    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("grammar_first_publish_group_not_in_next");
  });

  it("test_descriptor_group_already_in_prev_fallback", () => {
    // callout-group 已存在于 prevChildren（非首发）→ fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );

    const groupBlock = makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
      "layer_1:grammar_note:0",
    ]);
    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      groupBlock,
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      groupBlock,
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("grammar_first_publish_group_already_in_prev");
  });

  it("test_item_ids_set_mismatch_fallback", () => {
    // descriptor.item_ids 与 callout-group children 的 item id 集合不等 → fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      // descriptor 声明 item_a + item_c
      ["item_a", "item_c"],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      // 实际 callout-group 含 item_a + item_b（集合不等）
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "item_a",
        "item_b",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("grammar_first_publish_item_ids_mismatch");
  });

  it("test_group_child_without_item_identity_fallback", () => {
    // group child 缺少 data.itemId 时，即使数量与 descriptor 一致也不能
    // 猜测为同一 grammar item，必须 fail-closed。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );
    const malformedGroup = makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
      "layer_1:grammar_note:0",
    ]) as unknown as { children: Array<{ data?: Record<string, unknown> }> };
    delete malformedGroup.children[0]!.data!.itemId;

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren: [
        makeParagraphBlock("seg_1", UNIT_ID, "source text"),
        makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      ],
      nextChildren: [
        makeParagraphBlock("seg_1", UNIT_ID, "source text"),
        malformedGroup as unknown as Descendant,
        makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      ],
      snapshotFence,
    });

    expect(result).toEqual({
      kind: "fallback_full_reload",
      reason: "grammar_first_publish_item_identity_missing",
    });
  });
  it("test_layer_id_mismatch_fallback", () => {
    // descriptor.layer_id !== payload.layer_id → fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    // payload layer_id = LAYER_ID，但 descriptor layer_id = "layer_other"。
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
      GENERATION,
      { descriptorLayerId: "layer_other" },
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    // callout-group 用 LAYER_ID 构建（与 payload 一致），但 descriptor 用 layer_other。
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("grammar_first_publish_layer_id_mismatch");
  });

  it("test_cross_unit_fallback", () => {
    // 2 个 descriptor 的 unit_id 不同 → fallback "grammar_first_publish_cross_unit"。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEventMultiDescriptor(
      LAYER_ID,
      UNIT_ID,
      [
        { anchorSegmentId: "seg_1", itemIds: ["item_1"], unitId: "unit_1" },
        { anchorSegmentId: "seg_2", itemIds: ["item_2"], unitId: "unit_2" },
      ],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", "unit_1", "source one"),
      makeSentenceAnalysisBlock("seg_1", "unit_1"),
      makeParagraphBlock("seg_2", "unit_2", "source two"),
      makeSentenceAnalysisBlock("seg_2", "unit_2"),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", "unit_1", "source one"),
      makeCalloutGroupBlock("unit_1", "seg_1", LAYER_ID, ["item_1"]),
      makeSentenceAnalysisBlock("seg_1", "unit_1"),
      makeParagraphBlock("seg_2", "unit_2", "source two"),
      makeCalloutGroupBlock("unit_2", "seg_2", LAYER_ID, ["item_2"]),
      makeSentenceAnalysisBlock("seg_2", "unit_2"),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("grammar_first_publish_cross_unit");
  });

  it("test_mixed_event_fallback", () => {
    // 2 个事件（grammar_note 首发 + translation）→ detectGrammarFirstPublish
    // 返回 null（非单一事件），回落到 R2.1E，由结构变化触发 fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const grammarEvent = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
      GENERATION,
      { sequence: 2 },
    );
    const translationEvent = makePlainLayerPublishedEvent(
      "translation",
      UNIT_ID,
      { sequence: 3 },
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    // next 含新增 callout-group —— R2.1E 检测到目标 unit block 数量变化。
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [grammarEvent, translationEvent],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
  });

  it("test_fence_mismatch_fallback", () => {
    // 事件 generation 与 snapshotFence.generation 不一致 → fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
      99, // generation mismatch
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence, // generation = 1
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("fence_mismatch_in_batch");
  });

  it("test_non_target_block_changed_fallback", () => {
    // 非目标 block（如另一个 paragraph）在 prev/next 间内容变化 → fallback
    // "unrepresented_projection_change"。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source one"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      makeParagraphBlock("seg_2", UNIT_ID, "original two"),
      makeSentenceAnalysisBlock("seg_2", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source one"),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      // seg_2 paragraph 内容变化 —— 非目标 block 语义不等。
      makeParagraphBlock("seg_2", UNIT_ID, "CHANGED two"),
      makeSentenceAnalysisBlock("seg_2", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe("unrepresented_projection_change");
  });

  it("layer_published without grammar extension fields still uses the changed-block-only path", () => {
    // 回归：无 P2b 扩展字段的 layer_published 仍走 R2.1E changed-block-only 路径。
    // 通过构造 translation 同拓扑 revision（产生 targeted_apply replace）来验证
    // 未进入 grammar 首发路径（否则会因找不到 callout-group 而 fallback）。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makePlainLayerPublishedEvent("translation", UNIT_ID);

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeTranslationBlockquote(
        "layer_translation_1",
        "group_1",
        "old translation",
      ),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeTranslationBlockquote(
        "layer_translation_1",
        "group_1",
        "new translation",
      ),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    // 走 R2.1E → targeted_apply replace blockquote；若误进 grammar 首发路径
    // 会 fallback。
    expect(result.kind).toBe("targeted_apply");
    if (result.kind !== "targeted_apply") return;
    expect(result.operations).toHaveLength(1);
    expect(result.operations[0]!.type).toBe("replace");
    expect(result.operations[0]!.blockId).toBe(
      "blockquote:layer_translation_1:group_1",
    );
  });

  it("layer_published with grammar extension fields on a non-grammar layer falls back to full reload", () => {
    // 事件携带 P2b operation 字段但 layer_type === "translation" → fallback
    // "grammar_first_publish_unsupported_layer_type"。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeNonGrammarP2bEvent(
      "translation",
      "layer_translation_1",
      UNIT_ID,
      "seg_1",
      ["item_1"],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe(
      "grammar_first_publish_unsupported_layer_type",
    );
  });

  it("test_canonical_order_mismatch_fallback", () => {
    // callout-group 在 next 中不位于 paragraph_index + 1（例如在 sentence_analysis
    // 之后）→ C7 canonical order proof 失败 → fallback。
    const prevSnapshot = makeBaseSnapshot();
    const nextSnapshot = makeBaseSnapshot();
    const event = makeGrammarFirstPublishEvent(
      LAYER_ID,
      UNIT_ID,
      "seg_1",
      ["layer_1:grammar_note:0"],
    );

    const prevChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
    ];
    // callout-group 放在 sentence_analysis 之后 —— 不符合 canonical 顺序。
    const nextChildren = [
      makeParagraphBlock("seg_1", UNIT_ID, "source text"),
      makeSentenceAnalysisBlock("seg_1", UNIT_ID),
      makeCalloutGroupBlock(UNIT_ID, "seg_1", LAYER_ID, [
        "layer_1:grammar_note:0",
      ]),
    ];

    const result = mergeIncrementalProjection({
      prevSnapshot,
      nextSnapshot,
      triggerEvents: [event],
      prevChildren,
      nextChildren,
      snapshotFence,
    });

    expect(result.kind).toBe("fallback_full_reload");
    if (result.kind !== "fallback_full_reload") return;
    expect(result.reason).toBe(
      "grammar_first_publish_canonical_order_mismatch",
    );
  });
});
