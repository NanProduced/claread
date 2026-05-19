import { describe, expect, it } from "vitest";
import type { ReaderMockVm } from "@/types/view/ReaderMockVm";
import { renderSceneToPlateDocument } from "./render-scene-to-plate-document";

function createBaseScene(): ReaderMockVm {
  return {
    schemaVersion: "3.0.0",
    request: {
      requestId: "req-1",
      sourceType: "user_input",
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      profileId: "upstream",
    },
    article: {
      paragraphs: [
        {
          paragraphId: "p1",
          sentenceIds: ["s1"],
        },
      ],
      sentences: [
        {
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        },
      ],
    },
    userFacingState: "normal",
    translations: [],
    inlineMarks: [],
    sentenceEntries: [],
    warnings: [],
  };
}

describe("renderSceneToPlateDocument", () => {
  it("projects standard reader sentences, translation, and analysis blocks", () => {
    const scene: ReaderMockVm = {
      ...createBaseScene(),
      translations: [
        {
          sentenceId: "s1",
          translationZh: "制度记忆会塑造政策选择。",
        },
      ],
      inlineMarks: [
        {
          id: "mark-1",
          annotationType: "grammar_note",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "shapes",
            occurrence: 1,
          },
          renderType: "underline",
          visualTone: "grammar",
          clickable: false,
        },
      ],
      sentenceEntries: [
        {
          id: "entry-grammar",
          sentenceId: "s1",
          entryType: "grammar_note",
          label: "语法旁注",
          title: "语法",
          content: "shapes 在这里作谓语。",
        },
        {
          id: "entry-analysis",
          sentenceId: "s1",
          entryType: "sentence_analysis",
          label: "句子拆解",
          title: "句子拆解",
          content: "主语是 Institutional memory，谓语是 shapes。",
        },
      ],
    };

    const document = renderSceneToPlateDocument(scene);
    expect(document.type).toBe("reader_document");
    expect(document.children).toHaveLength(1);

    const paragraph = document.children[0];
    expect(paragraph).toMatchObject({
      type: "reader_paragraph",
      paragraphId: "p1",
      sentenceIds: ["s1"],
    });

    if (paragraph.type !== "reader_paragraph") {
      throw new Error("Expected paragraph node");
    }

    const sentence = paragraph.children[0];
    expect(sentence).toMatchObject({
      type: "reader_sentence",
      sentenceId: "s1",
      paragraphId: "p1",
      sourceText: "Institutional memory shapes policy choices.",
    });

    expect(sentence.children.map((child) => child.type)).toEqual([
      "reader_sentence_text",
      "reader_translation",
      "reader_grammar_note",
      "reader_sentence_analysis",
    ]);

    const sentenceText = sentence.children[0];
    expect(sentenceText.type).toBe("reader_sentence_text");
    if (sentenceText.type !== "reader_sentence_text") {
      throw new Error("Expected sentence text node");
    }
    expect(sentenceText.children).toEqual([
      {
        text: "Institutional memory ",
        readerSentenceId: "s1",
        readerTextStartOffset: 0,
        readerTextEndOffset: 21,
      },
      {
        text: "shapes",
        readerSentenceId: "s1",
        readerTextStartOffset: 21,
        readerTextEndOffset: 27,
        readerMarkAnnotationType: "grammar_note",
        readerMarkAnchorText: "shapes",
        readerMarkClickable: false,
        readerMarkGlossary: undefined,
        readerMarkId: "mark-1",
        readerMarkLookupKind: undefined,
        readerMarkLookupText: undefined,
        readerMarkParentId: undefined,
        readerMarkRenderType: "underline",
        readerMarkVisualTone: "grammar",
      },
      {
        text: " policy choices.",
        readerSentenceId: "s1",
        readerTextStartOffset: 27,
        readerTextEndOffset: 43,
      },
    ]);
    expect(sentenceText.inlineMarks).toHaveLength(1);

    const translation = sentence.children[1];
    expect(translation).toMatchObject({
      type: "reader_translation",
      sentenceId: "s1",
      translationZh: "制度记忆会塑造政策选择。",
    });
  });

  it("projects academic content summary and sentence-owned academic blocks", () => {
    const scene: ReaderMockVm = {
      ...createBaseScene(),
      schemaVersion: "3.0.0-academic",
      request: {
        ...createBaseScene().request,
        readingGoal: "academic",
        readingVariant: "academic_general",
      },
      contentSummary: {
        completeness: "full",
        overview: "本文讨论制度记忆如何影响政策解释。",
        researchQuestion: "制度记忆如何收窄可接受解释范围？",
        methodology: "理论分析",
        keyFindings: ["制度记忆会约束解释空间"],
        limitations: ["缺少实证数据"],
      },
      inlineMarks: [
        {
          id: "mark-term",
          annotationType: "term_note",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "Institutional memory",
            occurrence: 1,
          },
          renderType: "background",
          visualTone: "term",
          clickable: true,
        },
        {
          id: "mark-logic",
          annotationType: "logic_note",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "shapes",
            occurrence: 1,
          },
          renderType: "underline",
          visualTone: "logic",
          clickable: true,
        },
      ],
      sentenceEntries: [
        {
          id: "entry-term",
          sentenceId: "s1",
          entryType: "term_note",
          label: "术语说明",
          title: "Institutional memory",
          content: "指制度沉淀下来的组织记忆。",
        },
        {
          id: "entry-logic",
          sentenceId: "s1",
          entryType: "logic_note",
          label: "逻辑提示",
          title: "因果关系",
          content: "前半句给出原因，后半句给出结果。",
        },
        {
          id: "entry-interpretation",
          sentenceId: "s1",
          entryType: "interpretation_note",
          label: "理解提示",
          title: "核心观点",
          content: "作者强调解释框架的收窄效应。",
        },
        {
          id: "entry-summary",
          sentenceId: "",
          entryType: "content_summary",
          label: "内容概要",
          title: "内容概要",
          content: "扁平降级版内容概要",
        },
      ],
    };

    const document = renderSceneToPlateDocument(scene);
    expect(document.children.map((node) => node.type)).toEqual([
      "reader_content_summary",
      "reader_paragraph",
    ]);

    const contentSummary = document.children[0];
    expect(contentSummary).toMatchObject({
      type: "reader_content_summary",
      completeness: "full",
      overview: "本文讨论制度记忆如何影响政策解释。",
      researchQuestion: "制度记忆如何收窄可接受解释范围？",
      methodology: "理论分析",
      keyFindings: ["制度记忆会约束解释空间"],
      limitations: ["缺少实证数据"],
    });

    const paragraph = document.children[1];
    if (paragraph.type !== "reader_paragraph") {
      throw new Error("Expected paragraph node");
    }

    const sentence = paragraph.children[0];
    const sentenceText = sentence.children[0];
    expect(sentenceText.type).toBe("reader_sentence_text");
    if (sentenceText.type !== "reader_sentence_text") {
      throw new Error("Expected sentence text node");
    }

    expect(sentenceText.inlineMarks.map((mark) => mark.annotationType)).toEqual([
      "term_note",
      "logic_note",
    ]);
    expect(sentenceText.children).toEqual([
      {
        text: "Institutional memory",
        readerSentenceId: "s1",
        readerTextStartOffset: 0,
        readerTextEndOffset: 20,
        readerMarkAnnotationType: "term_note",
        readerMarkAnchorText: "Institutional memory",
        readerMarkClickable: true,
        readerMarkGlossary: undefined,
        readerMarkId: "mark-term",
        readerMarkLookupKind: undefined,
        readerMarkLookupText: undefined,
        readerMarkParentId: undefined,
        readerMarkRenderType: "background",
        readerMarkVisualTone: "term",
      },
      {
        text: " ",
        readerSentenceId: "s1",
        readerTextStartOffset: 20,
        readerTextEndOffset: 21,
      },
      {
        text: "shapes",
        readerSentenceId: "s1",
        readerTextStartOffset: 21,
        readerTextEndOffset: 27,
        readerMarkAnnotationType: "logic_note",
        readerMarkAnchorText: "shapes",
        readerMarkClickable: true,
        readerMarkGlossary: undefined,
        readerMarkId: "mark-logic",
        readerMarkLookupKind: undefined,
        readerMarkLookupText: undefined,
        readerMarkParentId: undefined,
        readerMarkRenderType: "underline",
        readerMarkVisualTone: "logic",
      },
      {
        text: " policy choices.",
        readerSentenceId: "s1",
        readerTextStartOffset: 27,
        readerTextEndOffset: 43,
      },
    ]);
    expect(sentence.children.map((child) => child.type)).toEqual([
      "reader_sentence_text",
      "reader_term_note",
      "reader_logic_note",
      "reader_interpretation_note",
    ]);
  });

  it("drops invalid academic content instead of creating empty shells", () => {
    const scene: ReaderMockVm = {
      ...createBaseScene(),
      schemaVersion: "3.0.0-academic",
      article: {
        paragraphs: [
          {
            paragraphId: "p1",
            sentenceIds: ["s1"],
          },
          {
            paragraphId: "p-orphan",
            sentenceIds: ["missing-sentence"],
          },
        ],
        sentences: createBaseScene().article.sentences,
      },
      translations: [
        {
          sentenceId: "s1",
          translationZh: "   ",
        },
        {
          sentenceId: "missing-sentence",
          translationZh: "不应进入结果",
        },
      ],
      sentenceEntries: [
        {
          id: "entry-invalid",
          sentenceId: "missing-sentence",
          entryType: "logic_note",
          label: "逻辑提示",
          title: "无效句子",
          content: "不应进入结果",
        },
      ],
    };

    const document = renderSceneToPlateDocument(scene);
    expect(document.children).toHaveLength(1);

    const paragraph = document.children[0];
    expect(paragraph.type).toBe("reader_paragraph");
    if (paragraph.type !== "reader_paragraph") {
      throw new Error("Expected paragraph node");
    }

    expect(paragraph.children).toHaveLength(1);
    const sentence = paragraph.children[0];
    expect(sentence.children.map((child) => child.type)).toEqual([
      "reader_sentence_text",
    ]);
  });

  it("preserves canonical anchor text for truncated overlapping marks", () => {
    const scene: ReaderMockVm = {
      ...createBaseScene(),
      inlineMarks: [
        {
          id: "mark-wide",
          annotationType: "phrase_gloss",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "memory shapes",
            occurrence: 1,
          },
          renderType: "background",
          visualTone: "phrase",
          clickable: true,
          lookupKind: "phrase",
        },
        {
          id: "mark-tail",
          annotationType: "context_gloss",
          anchor: {
            kind: "text",
            sentenceId: "s1",
            anchorText: "shapes policy",
            occurrence: 1,
          },
          renderType: "underline",
          visualTone: "context",
          clickable: true,
          lookupKind: "phrase",
        },
      ],
    };

    const document = renderSceneToPlateDocument(scene);
    const paragraph = document.children[0];
    if (paragraph.type !== "reader_paragraph") {
      throw new Error("Expected paragraph node");
    }

    const sentence = paragraph.children[0];
    const sentenceText = sentence.children[0];
    if (sentenceText.type !== "reader_sentence_text") {
      throw new Error("Expected sentence text node");
    }

    const tailMarkLeaf = sentenceText.children.find((leaf) => leaf.readerMarkId === "mark-tail");
    expect(tailMarkLeaf).toMatchObject({
      text: " policy",
      readerMarkAnchorText: "shapes policy",
    });
  });
});
