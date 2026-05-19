/** @vitest-environment jsdom */

import {
  buildSentenceTargetKey,
  buildTextRangeTargetKey,
} from "@claread/contracts";
import { describe, expect, it, vi } from "vitest";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import {
  annotationMatchesSelection,
  hashAnchorText,
  rectForTextOffsets,
  targetKeyForSelection,
  textOffsetWithinElement,
  type ReaderTextSelection,
} from "..";

function createRect(x: number, y: number, width: number, height: number): DOMRect {
  return {
    x,
    y,
    width,
    height,
    top: y,
    left: x,
    right: x + width,
    bottom: y + height,
    toJSON() {
      return {
        x,
        y,
        width,
        height,
        top: y,
        left: x,
        right: x + width,
        bottom: y + height,
      };
    },
  } as DOMRect;
}

const sentence: SentenceModel = {
  sentenceId: "s1",
  paragraphId: "p1",
  text: "Institutional memory shapes policy choices.",
};

function createTextRangeSelection(): ReaderTextSelection {
  return {
    anchorType: "text_range",
    sentence,
    selectedText: "memory",
    rect: createRect(10, 10, 40, 14),
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
  };
}

function createAnnotation(): WebAnnotationVm {
  return {
    id: "ann-1",
    recordId: "record-1",
    type: "highlight",
    anchorType: "text_range",
    targetKey: "target-1",
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

describe("reader-plate selection primitives", () => {
  it("hashes anchor text deterministically", () => {
    expect(hashAnchorText("memory")).toBe(hashAnchorText("memory"));
    expect(hashAnchorText("memory")).not.toBe(hashAnchorText("policy"));
  });

  it("matches annotation payloads against text selections", () => {
    const selection = createTextRangeSelection();
    expect(annotationMatchesSelection(createAnnotation(), selection)).toBe(true);
    expect(
      annotationMatchesSelection(
        {
          ...createAnnotation(),
          endOffset: 21,
        },
        selection,
      ),
    ).toBe(false);
  });

  it("builds target keys from selection anchors", () => {
    const sentenceSelection: ReaderTextSelection = {
      ...createTextRangeSelection(),
      anchorType: "sentence",
      selectedText: sentence.text,
      startOffset: 0,
      endOffset: sentence.text.length,
      textHash: hashAnchorText(sentence.text),
      segments: [
        {
          paragraphId: "p1",
          sentenceId: "s1",
          sentence,
          selectedText: sentence.text,
          startOffset: 0,
          endOffset: sentence.text.length,
          textHash: hashAnchorText(sentence.text),
        },
      ],
    };

    expect(targetKeyForSelection("record-1", sentenceSelection)).toBe(
      buildSentenceTargetKey("record-1", "s1"),
    );
    expect(targetKeyForSelection("record-1", createTextRangeSelection())).toBe(
      buildTextRangeTargetKey("record-1", "s1", 14, 20, hashAnchorText("memory")),
    );
  });

  it("reads text offsets within nested sentence text nodes", () => {
    const element = document.createElement("p");
    element.innerHTML = 'Hello <span data-reader-mark-id="m1">world</span>!';
    document.body.appendChild(element);

    const nestedTextNode = element.querySelector("span")?.firstChild;
    if (!nestedTextNode) {
      throw new Error("Expected nested text node");
    }

    expect(textOffsetWithinElement(element, nestedTextNode, 3)).toBe(9);
  });

  it("returns rects for valid ranges and rejects invalid offsets", () => {
    const element = document.createElement("p");
    element.textContent = "Hello world!";
    document.body.appendChild(element);

    const expectedRect = createRect(12, 16, 44, 18);
    const detach = vi.fn();
    const setStart = vi.fn();
    const setEnd = vi.fn();
    const createRangeSpy = vi.spyOn(document, "createRange").mockImplementation(
      () =>
        ({
          setStart,
          setEnd,
          detach,
          getClientRects: () => [expectedRect],
          getBoundingClientRect: () => expectedRect,
        }) as unknown as Range,
    );

    expect(rectForTextOffsets(element, 0, 5)).toMatchObject({
      x: 12,
      y: 16,
      width: 44,
      height: 18,
    });
    expect(setStart).toHaveBeenCalled();
    expect(setEnd).toHaveBeenCalled();
    expect(detach).toHaveBeenCalled();
    expect(rectForTextOffsets(element, -1, 5)).toBeNull();
    expect(rectForTextOffsets(element, 4, 4)).toBeNull();

    createRangeSpy.mockRestore();
  });
});
