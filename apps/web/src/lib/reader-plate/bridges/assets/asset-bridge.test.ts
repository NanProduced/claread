import { describe, expect, it } from "vitest";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { hashAnchorText } from "../../primitives";
import {
  annotationRequestFromAnchorPayload,
  anchorPayloadFromSelection,
  favoriteMutationFromAnchorPayload,
  projectReaderAssets,
} from "./index";

const sentence: SentenceModel = {
  sentenceId: "s1",
  paragraphId: "p1",
  text: "Institutional memory shapes policy choices.",
};

const secondSentence: SentenceModel = {
  sentenceId: "s2",
  paragraphId: "p1",
  text: "These choices persist across administrations.",
};

function createAnnotation(annotation: Partial<WebAnnotationVm> = {}): WebAnnotationVm {
  return {
    id: annotation.id ?? "ann-1",
    recordId: annotation.recordId ?? "record-1",
    type: annotation.type ?? "highlight",
    anchorType: annotation.anchorType ?? "text_range",
    targetKey: annotation.targetKey ?? "record:record-1:range:s1:14:20:hash-memory",
    paragraphId: annotation.paragraphId ?? "p1",
    sentenceId: annotation.sentenceId ?? "s1",
    selectedText: annotation.selectedText ?? "memory",
    startOffset: annotation.startOffset ?? 14,
    endOffset: annotation.endOffset ?? 20,
    textHash: annotation.textHash ?? hashAnchorText("memory"),
    segments: annotation.segments ?? [],
    color: annotation.color ?? "warm_yellow",
    note: annotation.note ?? null,
    createdAt: annotation.createdAt ?? "2026-05-19T00:00:00Z",
    updatedAt: annotation.updatedAt ?? "2026-05-19T00:00:00Z",
  };
}

function createFavorite(favorite: Partial<WebFavoriteTargetVm> = {}): WebFavoriteTargetVm {
  return {
    id: favorite.id ?? "fav-1",
    targetType: favorite.targetType ?? "text_range",
    targetKey: favorite.targetKey ?? "record:record-1:range:s1:28:43:hash-policy",
    recordId: favorite.recordId ?? "record-1",
    anchorType: favorite.anchorType ?? "text_range",
    sentenceId: favorite.sentenceId ?? "s1",
    selectedText: favorite.selectedText ?? "policy choices.",
    startOffset: favorite.startOffset ?? 28,
    endOffset: favorite.endOffset ?? 43,
    textHash: favorite.textHash ?? hashAnchorText("policy choices."),
    segments: favorite.segments ?? [],
  };
}

describe("asset bridge", () => {
  it("converts anchor payloads into annotation requests", () => {
    const payload = anchorPayloadFromSelection("record-1", {
      anchorType: "text_range",
      sentence,
      selectedText: "memory",
      rect: {
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        top: 0,
        left: 0,
        right: 10,
        bottom: 10,
        toJSON: () => ({}),
      } as DOMRect,
      startOffset: 14,
      endOffset: 20,
      textHash: hashAnchorText("memory"),
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
    });

    expect(
      annotationRequestFromAnchorPayload(payload, {
        color: "soft_blue",
        note: "记忆是关键词",
        sentenceTextById: new Map([["s1", sentence.text]]),
        translationBySentence: new Map([["s1", { sentenceId: "s1", translationZh: "制度记忆会塑造政策选择。" }]]),
      }),
    ).toMatchObject({
      recordId: "record-1",
      sentenceId: "s1",
      selectedText: "memory",
      anchorType: "text_range",
      startOffset: 14,
      endOffset: 20,
      textHash: hashAnchorText("memory"),
      color: "soft_blue",
      note: "记忆是关键词",
      payloadJson: {
        offset_unit: "utf16",
        text_hash_algorithm: "fnv1a32-utf16",
      },
    });
  });

  it("converts anchor payloads into favorite mutation payloads", () => {
    const payload = anchorPayloadFromSelection("record-1", {
      anchorType: "multi_text",
      sentence,
      selectedText: "policy choices. These choices",
      rect: {
        x: 0,
        y: 0,
        width: 10,
        height: 10,
        top: 0,
        left: 0,
        right: 10,
        bottom: 10,
        toJSON: () => ({}),
      } as DOMRect,
      startOffset: 28,
      endOffset: 13,
      textHash: hashAnchorText("policy choices. These choices"),
      segments: [
        {
          paragraphId: "p1",
          sentenceId: "s1",
          sentence,
          selectedText: "policy choices.",
          startOffset: 28,
          endOffset: 43,
          textHash: hashAnchorText("policy choices."),
        },
        {
          paragraphId: "p1",
          sentenceId: "s2",
          sentence: secondSentence,
          selectedText: "These choices",
          startOffset: 0,
          endOffset: 13,
          textHash: hashAnchorText("These choices"),
        },
      ],
    });

    expect(favoriteMutationFromAnchorPayload(payload)).toMatchObject({
      recordId: "record-1",
      targetType: "multi_text",
      targetKey: payload.targetKey,
      payloadJson: {
        anchor_type: "multi_text",
        offset_unit: "utf16",
        text_hash_algorithm: "fnv1a32-utf16",
      },
    });
  });

  it("projects annotations and favorites without dropping overlapping ranges", () => {
    const annotationA = createAnnotation({
      id: "ann-a",
      startOffset: 14,
      endOffset: 20,
      selectedText: "memory",
      textHash: hashAnchorText("memory"),
    });
    const annotationB = createAnnotation({
      id: "ann-b",
      startOffset: 18,
      endOffset: 31,
      selectedText: "ory shapes po",
      textHash: hashAnchorText("ory shapes po"),
      color: "soft_green",
      note: "重叠测试",
    });
    const favorite = createFavorite({
      id: "fav-multi",
      targetType: "multi_text",
      targetKey: "record:record-1:multi_text:2:hash",
      anchorType: "multi_text",
      sentenceId: "s1",
      selectedText: "policy choices. These choices",
      startOffset: null,
      endOffset: null,
      textHash: null,
      segments: [
        {
          paragraphId: "p1",
          sentenceId: "s1",
          selectedText: "policy choices.",
          startOffset: 28,
          endOffset: 43,
          textHash: hashAnchorText("policy choices."),
        },
        {
          paragraphId: "p1",
          sentenceId: "s2",
          selectedText: "These choices",
          startOffset: 0,
          endOffset: 13,
          textHash: hashAnchorText("These choices"),
        },
      ],
    });

    const projection = projectReaderAssets({
      annotations: [annotationA, annotationB],
      favoriteTargets: [favorite],
      recordId: "record-1",
    });

    expect(projection.annotationRangesBySentence.get("s1")).toHaveLength(2);
    expect(projection.sentenceAssetProjectionBySentence.get("s1")).toMatchObject({
      hasHighlight: true,
      hasNote: true,
      favoriteCount: 1,
    });
    expect(projection.favoriteRangesBySentence.get("s1")).toHaveLength(1);
    expect(projection.favoriteRangesBySentence.get("s2")).toHaveLength(1);
    expect(projection.slipAnnotationsBySentence.get("s1")?.map((item) => item.id)).toEqual(["ann-b"]);
  });
});
