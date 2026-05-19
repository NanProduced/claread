/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import {
  askAnchorsFromAttachments,
  askAttachmentFromAnnotation,
  askAttachmentFromContentSummary,
  askAttachmentFromFavorite,
  askAttachmentFromRecord,
  askAttachmentFromSelection,
  askAttachmentFromSentence,
  askAttachmentFromStructuredInspect,
  askAttachmentFromTranslation,
  jumpTargetFromAskAttachment,
} from "./adapters";
import { hashAnchorText } from "../../primitives";
import type { ReaderTextSelection } from "../../primitives";
import type { ReaderAskPageIdentity } from "./types";

const pageIdentity: ReaderAskPageIdentity = {
  recordId: "record-1",
  recordTitle: "Policy Memory",
  surface: "reader",
  source: "reader_2_0",
};

const sentence = {
  sentenceId: "s1",
  paragraphId: "p1",
  text: "Institutional memory shapes policy choices.",
};

function createSelection(): ReaderTextSelection {
  return {
    anchorType: "text_range" as const,
    sentence,
    selectedText: "memory",
    rect: {
      x: 0,
      y: 0,
      width: 80,
      height: 18,
      top: 0,
      right: 80,
      bottom: 18,
      left: 0,
      toJSON: () => ({}),
    } as DOMRect,
    segments: [
      {
        paragraphId: "p1",
        sentenceId: "s1",
        sentence,
        selectedText: "memory",
        startOffset: 14,
        endOffset: 20,
        textHash: hashAnchorText("memory"),
      },
    ],
    startOffset: 14,
    endOffset: 20,
    textHash: hashAnchorText("memory"),
  };
}

function createAnnotation(): WebAnnotationVm {
  return {
    id: "ann-1",
    recordId: "record-1",
    type: "highlight",
    anchorType: "text_range",
    targetKey: "record:record-1:range:s1:14:20:hash",
    paragraphId: "p1",
    sentenceId: "s1",
    selectedText: "memory",
    startOffset: 14,
    endOffset: 20,
    textHash: hashAnchorText("memory"),
    segments: [],
    color: "warm_yellow",
    note: "关键词",
    createdAt: "2026-05-20T00:00:00Z",
    updatedAt: "2026-05-20T00:00:00Z",
  };
}

function createFavorite(): WebFavoriteTargetVm {
  return {
    id: "fav-1",
    targetType: "text_range",
    targetKey: "record:record-1:range:s1:28:43:hash",
    recordId: "record-1",
    anchorType: "text_range",
    sentenceId: "s1",
    selectedText: "policy choices",
    startOffset: 28,
    endOffset: 42,
    textHash: hashAnchorText("policy choices"),
    segments: [],
  };
}

describe("ask bridge", () => {
  it("builds attachments from selection, sentence, and translation", () => {
    const selectionAttachment = askAttachmentFromSelection(pageIdentity, createSelection());
    const sentenceAttachment = askAttachmentFromSentence(pageIdentity, sentence);
    const translationAttachment = askAttachmentFromTranslation(
      pageIdentity,
      sentence,
      "制度记忆会塑造政策选择。",
    );

    expect(selectionAttachment.kind).toBe("text_selection");
    expect(selectionAttachment.subtype).toBe("text_range");
    expect(sentenceAttachment.kind).toBe("analysis_ref");
    expect(sentenceAttachment.subtype).toBe("sentence");
    expect(translationAttachment.subtype).toBe("translation");
    expect(translationAttachment.metadata.entryAction).toBe("compare_translation");
  });

  it("preserves structured inspect identity and content summary jump targets", () => {
    const inspectAttachment = askAttachmentFromStructuredInspect(
      pageIdentity,
      {
        kind: "structured_annotation_inspect",
        sentenceId: "s1",
        contextSentence: sentence.text,
        markId: "mark-1",
        annotationType: "phrase_gloss",
        visualTone: "phrase",
        anchorText: "policy choices",
        lookupText: "policy choices",
        title: "固定搭配",
      },
      sentence,
    );
    const summaryAttachment = askAttachmentFromContentSummary(pageIdentity, {
      overview: "本文讨论制度记忆如何影响政策解释。",
    });

    const [inspectAnchor] = askAnchorsFromAttachments([inspectAttachment]);

    expect(inspectAnchor).toEqual(
      expect.objectContaining({
        anchor_type: "sentence_entry",
        entry_type: "structured_inspect",
        query: "policy choices",
      }),
    );
    expect(jumpTargetFromAskAttachment(summaryAttachment)?.targetType).toBe("content_summary");
  });

  it("adapts attachments back to legacy anchors with bridge metadata", () => {
    const attachments = [
      askAttachmentFromSelection(pageIdentity, createSelection()),
      askAttachmentFromAnnotation(pageIdentity, createAnnotation()),
      askAttachmentFromFavorite(pageIdentity, createFavorite()),
      askAttachmentFromRecord(pageIdentity),
    ];

    const anchors = askAnchorsFromAttachments(attachments);

    expect(anchors[0]).toEqual(
      expect.objectContaining({
        anchor_type: "text_range",
        sentence_id: "s1",
        selected_text: "memory",
      }),
    );
    expect(anchors[1].anchor_type).toBe("user_annotation");
    expect(anchors[2].anchor_type).toBe("favorite");
    expect(anchors[3].payload_json).toEqual(
      expect.objectContaining({
        attachment_kind: "record_ref",
        source_surface: "ask_panel",
      }),
    );
  });

  it("resolves jump targets from selection, annotation, and favorite attachments", () => {
    const selectionJump = jumpTargetFromAskAttachment(
      askAttachmentFromSelection(pageIdentity, createSelection()),
    );
    const annotationJump = jumpTargetFromAskAttachment(
      askAttachmentFromAnnotation(pageIdentity, createAnnotation()),
    );
    const favoriteJump = jumpTargetFromAskAttachment(
      askAttachmentFromFavorite(pageIdentity, createFavorite()),
    );
    const recordJump = jumpTargetFromAskAttachment(askAttachmentFromRecord(pageIdentity));

    expect(selectionJump?.targetType).toBe("text_range");
    expect(annotationJump?.targetType).toBe("user_annotation");
    expect(favoriteJump?.targetType).toBe("favorite");
    expect(recordJump).toBeNull();
  });
});
