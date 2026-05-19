import { buildMultiTextTargetKey, buildSentenceTargetKey, buildTextRangeTargetKey } from "@claread/contracts";
import { describe, expect, it } from "vitest";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { hashAnchorText, type ReaderTextSelection } from "../../primitives";
import {
  anchorPayloadFromSelection,
  annotationToTargetRef,
  favoriteToTargetRef,
  jumpToAnchorPayload,
  jumpToTargetKey,
  jumpToTargetRef,
  selectionToTargetRef,
  sentenceToTargetRef,
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

function createAnnotation(): WebAnnotationVm {
  return {
    id: "ann-1",
    recordId: "record-1",
    type: "highlight",
    anchorType: "text_range",
    targetKey: buildTextRangeTargetKey("record-1", "s1", 14, 20, hashAnchorText("memory")),
    paragraphId: "p1",
    sentenceId: "s1",
    selectedText: "memory",
    startOffset: 14,
    endOffset: 20,
    textHash: hashAnchorText("memory"),
    segments: [],
    color: "warm_yellow",
    note: null,
    createdAt: "2026-05-19T00:00:00Z",
    updatedAt: "2026-05-19T00:00:00Z",
  };
}

function createFavorite(): WebFavoriteTargetVm {
  return {
    id: "fav-1",
    targetType: "multi_text",
    targetKey: buildMultiTextTargetKey("record-1", [
      {
        paragraphId: "p1",
        sentenceId: "s1",
        startOffset: 28,
        endOffset: 43,
        textHash: hashAnchorText("policy choices."),
      },
      {
        paragraphId: "p1",
        sentenceId: "s2",
        startOffset: 0,
        endOffset: 13,
        textHash: hashAnchorText("These choices"),
      },
    ]),
    recordId: "record-1",
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
  };
}

function createMultiTextSelection(): Extract<ReaderTextSelection, { anchorType: "multi_text" }> {
  return {
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
  };
}

describe("jump bridge", () => {
  it("parses sentence and text_range target keys directly", () => {
    expect(jumpToTargetKey(buildSentenceTargetKey("record-1", "s1"))).toMatchObject({
      targetType: "sentence",
      sentenceIds: ["s1"],
      primarySentenceId: "s1",
      highlightMode: "sentence_frame",
    });

    expect(
      jumpToTargetKey(buildTextRangeTargetKey("record-1", "s1", 14, 20, hashAnchorText("memory"))),
    ).toMatchObject({
      targetType: "text_range",
      sentenceIds: ["s1"],
      primarySentenceId: "s1",
      highlightMode: "range_segments",
      rangeSegments: [
        {
          sentenceId: "s1",
          startOffset: 14,
          endOffset: 20,
          textHash: hashAnchorText("memory"),
        },
      ],
    });
  });

  it("resolves multi_text precisely when loaded payload is available and degrades otherwise", () => {
    const favorite = createFavorite();
    expect(
      jumpToTargetKey(favorite.targetKey, {
        favoriteTargets: [favorite],
      }),
    ).toMatchObject({
      targetType: "favorite",
      sentenceIds: ["s1", "s2"],
      primarySentenceId: "s1",
      highlightMode: "range_segments",
    });

    expect(jumpToTargetKey(favorite.targetKey)).toBeNull();
  });

  it("converts anchor payloads into jump targets", () => {
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

    expect(jumpToAnchorPayload(payload)).toMatchObject({
      targetType: "text_range",
      sentenceIds: ["s1"],
      highlightMode: "range_segments",
    });
  });

  it("converts target refs into jump targets", () => {
    expect(jumpToTargetRef(sentenceToTargetRef("record-1", sentence))).toMatchObject({
      targetType: "sentence",
      sentenceIds: ["s1"],
      highlightMode: "sentence_frame",
    });

    expect(
      jumpToTargetRef(selectionToTargetRef("record-1", createMultiTextSelection())),
    ).toMatchObject({
      targetType: "multi_text",
      sentenceIds: ["s1", "s2"],
      highlightMode: "range_segments",
    });

    expect(jumpToTargetRef(annotationToTargetRef(createAnnotation()))).toMatchObject({
      targetType: "user_annotation",
      sentenceIds: ["s1"],
      highlightMode: "range_segments",
    });

    expect(jumpToTargetRef(favoriteToTargetRef(createFavorite()))).toMatchObject({
      targetType: "favorite",
      sentenceIds: ["s1", "s2"],
      highlightMode: "range_segments",
    });
  });
});
