/**
 * Anchor Debug fixture data for testing and development.
 *
 * Covers:
 *  - range 单片段
 *  - multi_range 多片段
 *  - quote_boundary_violation drop
 *  - quote_not_found drop
 *  - quote_ambiguous drop
 *  - legacy text / multi_text
 *  - warning case
 *  - canonical_stats
 */

export const ANCHOR_DEBUG_FIXTURE = {
  render_scene: {
    schema_version: "3.0.0",
    user_facing_state: "normal",
    translations: [
      { sentence_id: "s1", translation_zh: "机构记忆塑造了政策选择。" },
      { sentence_id: "s2", translation_zh: "结果推动了团队重新思考。" },
    ],
    inline_marks: [
      // range 单片段
      {
        id: "im_vocab_s1",
        annotation_type: "vocab_highlight",
        anchor: {
          kind: "range",
          sentence_id: "s1",
          offset_unit: "utf16",
          start: 0,
          end: 20,
          text: "Institutional memory",
          source_quote: "Institutional memory",
          resolution_kind: "exact",
        },
        render_type: "background",
        visual_tone: "vocab",
        clickable: true,
        lookup_text: "institutional memory",
        lookup_kind: "word",
        glossary: { zh: "机构记忆", reason: "组织层面的经验沉淀" },
      },
      // multi_range 多片段
      {
        id: "im_phrase_s1",
        annotation_type: "phrase_gloss",
        anchor: {
          kind: "multi_range",
          sentence_id: "s1",
          offset_unit: "utf16",
          ranges: [
            { start: 0, end: 20, text: "Institutional memory", role: "subject", source_quote: "Institutional memory", resolution_kind: "exact" },
            { start: 28, end: 43, text: "policy choices.", role: "object", source_quote: "policy choices.", resolution_kind: "exact" },
          ],
        },
        render_type: "background",
        visual_tone: "phrase",
        clickable: true,
        lookup_text: "institutional memory / policy choices",
        lookup_kind: "phrase",
        glossary: { zh: "机构记忆塑造了政策选择", reason: "主谓结构短语" },
      },
      // range 单片段 (context)
      {
        id: "im_context_s2",
        annotation_type: "context_gloss",
        anchor: {
          kind: "range",
          sentence_id: "s2",
          offset_unit: "utf16",
          start: 12,
          end: 20,
          text: "prompted",
          source_quote: "prompted",
          resolution_kind: "exact",
        },
        render_type: "background",
        visual_tone: "context",
        clickable: true,
        lookup_text: "prompt sb to do sth",
        lookup_kind: "phrase",
        glossary: { zh: "促使某人做某事", reason: "动词固定搭配" },
      },
      // grammar_note with range
      {
        id: "im_grammar_s2",
        annotation_type: "grammar_note",
        anchor: {
          kind: "range",
          sentence_id: "s2",
          offset_unit: "utf16",
          start: 12,
          end: 20,
          text: "prompted",
          source_quote: "prompted",
          resolution_kind: "exact",
        },
        render_type: "background",
        visual_tone: "grammar",
        clickable: true,
        lookup_text: "prompted",
        glossary: { zh: "过去式动词", reason: "prompted 是 prompt 的过去式" },
      },
      // legacy text anchor
      {
        id: "im_legacy_text_s1",
        annotation_type: "vocab_highlight",
        anchor: {
          kind: "text",
          sentence_id: "s1",
          anchor_text: "shapes",
          occurrence: 1,
        },
        render_type: "background",
        visual_tone: "vocab",
        clickable: true,
        lookup_text: "shape",
        lookup_kind: "word",
        glossary: { zh: "塑造", reason: "动词用法" },
      },
      // legacy multi_text anchor
      {
        id: "im_legacy_multi_s1",
        annotation_type: "phrase_gloss",
        anchor: {
          kind: "multi_text",
          sentence_id: "s1",
          parts: [
            { anchor_text: "Institutional memory", occurrence: 1, role: "subject" },
            { anchor_text: "policy choices", occurrence: 1, role: "object" },
          ],
        },
        render_type: "background",
        visual_tone: "phrase",
        clickable: true,
        lookup_text: "institutional memory / policy choices",
        lookup_kind: "phrase",
        glossary: { zh: "机构记忆与政策选择", reason: "复合短语" },
      },
    ],
    sentence_entries: [
      { id: "se_grammar_s2", label: "prompted", entry_type: "grammar_note", content: "prompted 是 prompt 的过去式" },
    ],
    warnings: [
      { code: "anchor_resolve_failed", message: "Anchor text 'prompt' not found in sentence s2", sentence_id: "s2", level: "warning" },
    ],
  },
  drop_log: [
    { reason: "anchor_not_found", annotation_type: "vocab_highlight", sentence_id: "s2", anchor_text: "rethink", message: "Anchor text not found in sentence" },
  ],
  canonical_drop_log: [
    {
      reason: "quote_boundary_violation",
      annotation_type: "vocab_highlight",
      sentence_id: "s2",
      quote_text: "prompt",
      drop_reason: "quote_boundary_violation",
      stage: "anchor_resolve",
    },
    {
      reason: "quote_not_found",
      annotation_type: "context_gloss",
      sentence_id: "s3",
      quote_text: "nonexistent",
      drop_reason: "quote_not_found",
      stage: "anchor_resolve",
    },
    {
      reason: "quote_ambiguous",
      annotation_type: "phrase_gloss",
      sentence_id: "s1",
      quote_text: "the",
      drop_reason: "quote_ambiguous",
      stage: "anchor_resolve",
    },
  ],
  annotation_stats: {
    canonical_stats: {
      canonical_normalized_counts: { vocab_highlight: 1, phrase_gloss: 1, context_gloss: 1, grammar_note: 1 },
      canonical_span_count: 5,
      canonical_drop_counts_by_reason: { quote_boundary_violation: 1, quote_not_found: 1, quote_ambiguous: 1 },
      canonical_drop_counts_by_type: { vocab_highlight: 1, context_gloss: 1, phrase_gloss: 1 },
      canonical_anchor_drop_summary: {
        total_anchor_drops: 3,
        by_annotation_type_and_reason: [
          { annotation_type: "vocab_highlight", drop_reason: "quote_boundary_violation", count: 1 },
          { annotation_type: "context_gloss", drop_reason: "quote_not_found", count: 1 },
          { annotation_type: "phrase_gloss", drop_reason: "quote_ambiguous", count: 1 },
        ],
      },
    },
  },
};
