/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReaderPlateSnapshotSurface } from "@/components/reader/plate/ReaderPlateSnapshotSurface";
import type {
  ReaderContextGlossMarkDto,
  ReaderGrammarNoteMarkDto,
  ReaderPlateValueDto,
  ReaderPhraseGlossMarkDto,
  ReaderSentenceAnalysisNodeDto,
  ReaderTranslationNodeDto,
  ReaderUnitNodeDto,
  ReaderVocabHighlightMarkDto,
  ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";

type ReaderVocabularyMarkOverrides =
  | ({ item_type: "vocab_highlight" } & Partial<ReaderVocabHighlightMarkDto>)
  | ({ item_type: "phrase_gloss" } & Partial<ReaderPhraseGlossMarkDto>)
  | ({ item_type: "context_gloss" } & Partial<ReaderContextGlossMarkDto>);

function makeUnitWithTranslation(overrides: {
  unitId: string;
  sourceText: string;
  translationText?: string;
  anchorSegmentId?: string;
  vocabularyMarks?: ReaderVocabularyMarkDto[];
  grammarNoteMarks?: ReaderGrammarNoteMarkDto[];
  sentenceAnalysis?: ReaderSentenceAnalysisNodeDto;
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
                reader_vocabulary_marks: overrides.vocabularyMarks,
                reader_grammar_note_marks: overrides.grammarNoteMarks,
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
  if (overrides.sentenceAnalysis) {
    unit.children.push(overrides.sentenceAnalysis);
  }

  return unit;
}

function makeVocabularyMark(
  overrides: ReaderVocabularyMarkOverrides,
): ReaderVocabularyMarkDto {
  const { item_type, ...markOverrides } = overrides;
  const base = {
    mark_id: "mark_1",
    layer_id: "layer_vocab_1",
    anchor_segment_id: "s1",
    start_offset: 0,
    end_offset: 5,
    selected_text: "Hello",
    segment_start_utf16: 0,
    segment_end_utf16: 5,
    starts_here: true,
    ends_here: true,
  };

  if (item_type === "vocab_highlight") {
    const highlightOverrides = markOverrides as Partial<ReaderVocabHighlightMarkDto>;
    return {
      ...base,
      item_type,
      headword: "Hello",
      brief_explanation: "问候语",
      reason: "common word",
      ...highlightOverrides,
    };
  }
  if (item_type === "phrase_gloss") {
    const phraseOverrides = markOverrides as Partial<ReaderPhraseGlossMarkDto>;
    return {
      ...base,
      item_type,
      phrase: "turn into",
      phrase_type: "collocation",
      gloss: "转化成",
      example: "turn effort into progress",
      ...phraseOverrides,
    };
  }
  const contextOverrides = markOverrides as Partial<ReaderContextGlossMarkDto>;
  return {
    ...base,
    item_type,
    display: "turn into",
    gloss: "在这里表示逐步转成",
    reason: "依赖当前上下文，不是静态词典义",
    ...contextOverrides,
  };
}

function makeGrammarNoteMark(
  overrides: Partial<ReaderGrammarNoteMarkDto> = {},
): ReaderGrammarNoteMarkDto {
  return {
    mark_id: "mark_grammar_1",
    item_id: "grammar_note_item_1",
    owner: "system_ai",
    layer_id: "layer_grammar_note_1",
    item_type: "grammar_note",
    anchor_segment_id: "s1",
    start_offset: 0,
    end_offset: 8,
    selected_text: "Not only",
    segment_start_utf16: 0,
    segment_end_utf16: 8,
    starts_here: true,
    ends_here: true,
    span_index: 0,
    span_count: 1,
    show_note_chip: true,
    grammar_point: "倒装触发",
    pattern: "not only ... but also",
    note: "前置否定结构触发助动词提前。",
    ...overrides,
  };
}

function makeSentenceAnalysisNode(
  overrides: Partial<ReaderSentenceAnalysisNodeDto> = {},
): ReaderSentenceAnalysisNodeDto {
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
    selected_text: "Not only did the team revise the plan, but they also clarified the timeline.",
    label: "fronted emphasis with inversion",
    analysis: "前置的否定结构触发倒装，后半句补充并列结果。",
    chunks: [
      { order: 1, label: "cue", text: "Not only" },
      { order: 2, label: "result", text: "but they also clarified the timeline" },
    ],
    children: [{ text: "前置的否定结构触发倒装，后半句补充并列结果。" }],
    ...overrides,
  };
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

    expect(container.textContent).toContain("还没有可显示的正文内容");
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

  it("renders vocab_highlight as a marked source span with a read-only chip", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "Hello world.",
        vocabularyMarks: [
          makeVocabularyMark({
            item_type: "vocab_highlight",
            mark_id: "mark_vocab_1",
            selected_text: "Hello",
            headword: "Hello",
            brief_explanation: "问候语",
          }),
        ],
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const mark = container.querySelector('[data-reader-mark-id="mark_vocab_1"]');
    const chip = container.querySelector('[data-reader-vocabulary-chip="vocab_highlight"]');
    expect(mark?.className).toContain("reader-mark--vocab");
    expect(chip?.textContent).toContain("词义");
    expect(chip?.textContent).toContain("问候语");
  });

  it("renders phrase_gloss with phrase styling and subtype-aware chip text", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "turn effort into progress",
        vocabularyMarks: [
          makeVocabularyMark({
            item_type: "phrase_gloss",
            mark_id: "mark_phrase_1",
            selected_text: "turn effort into",
            phrase: "turn into",
            phrase_type: "collocation",
            gloss: "转化成",
          }),
        ],
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const mark = container.querySelector('[data-reader-mark-id="mark_phrase_1"]');
    const chip = container.querySelector('[data-reader-vocabulary-chip="phrase_gloss"]');
    expect(mark?.className).toContain("reader-mark--phrase");
    expect(chip?.textContent).toContain("搭配");
    expect(chip?.textContent).toContain("转化成");
  });

  it("renders context_gloss with context styling and contextual chip text", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "The results prompted the team to rethink.",
        vocabularyMarks: [
          makeVocabularyMark({
            item_type: "context_gloss",
            mark_id: "mark_context_1",
            selected_text: "prompted the team to rethink",
            display: "prompt sb to do sth",
            gloss: "这里强调引发后续动作",
            reason: "依赖当前上下文，不是普通词典义",
          }),
        ],
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const mark = container.querySelector('[data-reader-mark-id="mark_context_1"]');
    const chip = container.querySelector('[data-reader-vocabulary-chip="context_gloss"]');
    expect(mark?.className).toContain("reader-mark--context");
    expect(chip?.textContent).toContain("语境");
    expect(chip?.textContent).toContain("引发后续动作");
  });

  it("renders grammar_note as a stable-source inline mark with a grammar chip", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "Not only did the team revise the plan.",
        grammarNoteMarks: [
          makeGrammarNoteMark({
            mark_id: "mark_grammar_focus_1",
            item_id: "grammar_focus_1",
            selected_text: "Not only",
            grammar_point: "倒装触发",
          }),
        ],
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const mark = container.querySelector('[data-reader-mark-id="mark_grammar_focus_1"]');
    const chip = container.querySelector('[data-reader-grammar-note-chip="grammar_focus_1"]');
    expect(mark?.getAttribute("data-reader-annotation-kind")).toBe("grammar_note");
    expect(mark?.getAttribute("data-reader-mark-tone")).toBe("grammar");
    expect(chip?.textContent).toContain("语法");
    expect(chip?.textContent).toContain("倒装触发");
  });

  it("renders sentence_analysis as a distinct system_ai block with chunks", () => {
    const value: ReaderPlateValueDto = [
      makeUnitWithTranslation({
        unitId: "u1",
        sourceText: "Not only did the team revise the plan, but they also clarified the timeline.",
        translationText: "团队不仅修改了计划，还澄清了时间线。",
        sentenceAnalysis: makeSentenceAnalysisNode(),
      }),
    ];

    const { container } = render(<ReaderPlateSnapshotSurface value={value} />);

    const analysisNode = container.querySelector('[data-reader-node="sentence-analysis"]');
    expect(analysisNode).not.toBeNull();
    expect(analysisNode?.getAttribute("data-layer-id")).toBe("layer_sentence_analysis_1");
    expect(analysisNode?.getAttribute("data-anchor-segment-id")).toBe("s1");
    expect(analysisNode?.textContent).toContain("句式拆解");
    expect(analysisNode?.textContent).toContain("fronted emphasis with inversion");
    expect(analysisNode?.textContent).toContain("Not only");
    expect(analysisNode?.textContent).toContain("but they also clarified the timeline");
  });

  it("snapshot surface files do not reference render_scene_json", () => {
    const surfaceSource = readFileSync(
      resolve(process.cwd(), "src/components/reader/plate/ReaderPlateSnapshotSurface.tsx"),
      "utf-8",
    );
    const dtoSource = readFileSync(
      resolve(process.cwd(), "src/types/api/reader-plate.ts"),
      "utf-8",
    );

    expect(surfaceSource).not.toContain("render_scene_json");
    expect(dtoSource).not.toContain("render_scene_json");
  });
});
