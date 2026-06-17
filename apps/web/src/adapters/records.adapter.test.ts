import { describe, expect, it } from "vitest";

import { adaptReaderSceneResponseToReaderRecord } from "@/adapters/records.adapter";
import { renderSceneToPlateDocument } from "@/lib/reader-plate/projection";
import type { ReaderSceneResponseDto } from "@/types/api/reader-scene";

function createReaderSceneResponse(): ReaderSceneResponseDto {
  return {
    record_meta: {
      id: "617812dc-9043-44cc-be4d-d9a6c4699da4",
      client_record_id: null,
      title: "马斯克成全球首位万亿富翁",
      source_type: "user_input",
      source_text: "Elon Musk has become the world's first ever trillionaire.",
      request_payload_json: {},
      reading_goal: "exam",
      reading_variant: "gaokao",
      analysis_status: "ready",
      user_facing_state: "normal",
      workflow_version: "article_analysis_v3",
      schema_version: "3.0.0",
      created_at: "2026-06-15T02:25:09.995Z",
      updated_at: "2026-06-15T03:06:20.878Z",
    },
    reader_scene: {
      schema_version: "3.0.0",
      request: {
        request_id: "req-1",
        source_type: "user_input",
        reading_goal: "exam",
        reading_variant: "gaokao",
        profile_id: "exam_gaokao",
      },
      article: {
        source_type: "user_input",
        source_text: "Elon Musk has become the world's first ever trillionaire.",
        render_text: "Elon Musk has become the world's first ever trillionaire.",
        paragraphs: [{ paragraph_id: "p1", text: "", sentence_ids: ["s1"] }],
        sentences: [
          {
            sentence_id: "s1",
            paragraph_id: "p1",
            text: "Elon Musk has become the world's first ever trillionaire.",
            sentence_span: { start: 0, end: 57 },
          },
        ],
      },
      user_facing_state: "normal",
      translations: [
        {
          sentence_id: "s1",
          translation_zh: "埃隆·马斯克已成为全球首位万亿富翁。",
        },
      ],
      inline_marks: [
        {
          id: "im-trillionaire",
          annotation_type: "vocab_highlight",
          anchor: {
            kind: "range",
            sentence_id: "s1",
            offset_unit: "utf16",
            range: {
              start: 44,
              end: 56,
              text: "trillionaire",
              source_quote: "trillionaire",
              resolution_kind: "exact",
            },
          },
          render_type: "background",
          visual_tone: "vocab",
          clickable: true,
          lookup_text: "trillionaire",
          lookup_kind: "word",
          glossary: null,
        },
      ],
      sentence_entries: [
        {
          id: "se-analysis",
          sentence_id: "s1",
          entry_type: "sentence_analysis",
          label: "直接引语中的复杂宾语结构",
          title: "直接引语中的复杂宾语结构",
          content: "句子主干是 he said: \"...\"。",
          analysis_text: "句子主干是 he said: \"...\"。",
          chunks: [
            {
              order: 1,
              label: "主句引述",
              text: "Elon Musk has become",
            },
            {
              order: 2,
              label: "表语",
              text: "the world's first ever trillionaire",
            },
          ],
        },
      ],
      warnings: [],
    },
    view_meta: {
      view_version: "reader_scene_v1",
      data_source: "render_scene_snapshot",
      fallback_mode: "none",
      supplements_merged: false,
    },
  };
}

describe("records adapter", () => {
  it("maps backend nested range anchors into renderable Web range anchors", () => {
    const record = adaptReaderSceneResponseToReaderRecord(createReaderSceneResponse());

    expect(record.reader.inlineMarks).toHaveLength(1);
    expect(record.reader.sentenceEntries[0]).toMatchObject({
      id: "se-analysis",
      entryType: "sentence_analysis",
      analysisText: "句子主干是 he said: \"...\"。",
      chunks: [
        {
          order: 1,
          label: "主句引述",
          text: "Elon Musk has become",
          occurrence: null,
        },
        {
          order: 2,
          label: "表语",
          text: "the world's first ever trillionaire",
          occurrence: null,
        },
      ],
    });
    expect(record.reader.inlineMarks[0]?.anchor).toMatchObject({
      kind: "range",
      sentenceId: "s1",
      start: 44,
      end: 56,
      text: "trillionaire",
      sourceQuote: "trillionaire",
      resolutionKind: "exact",
    });

    const document = renderSceneToPlateDocument(record.reader);
    const paragraph = document.children[0];
    if (paragraph?.type !== "reader_paragraph") {
      throw new Error("Expected reader paragraph");
    }
    const sentence = paragraph.children[0];
    if (sentence?.type !== "reader_sentence") {
      throw new Error("Expected reader sentence");
    }
    const textNode = sentence.children.find((node) => node.type === "reader_sentence_text");
    if (textNode?.type !== "reader_sentence_text") {
      throw new Error("Expected reader sentence text");
    }
    const markedLeaf = textNode?.children.find(
      (leaf) => leaf.readerMarkId === "im-trillionaire",
    );

    expect(markedLeaf).toMatchObject({
      text: "trillionaire",
      readerMarkAnnotationType: "vocab_highlight",
      readerMarkVisualTone: "vocab",
    });
  });
});
