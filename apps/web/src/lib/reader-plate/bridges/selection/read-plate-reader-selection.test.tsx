/** @vitest-environment jsdom */

import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ReaderMockVm, SentenceModel } from "@/types/view/ReaderMockVm";
import { ImmersiveReaderSurface } from "../../../../components/reader/plate";
import { PlateReaderSurface } from "../../../../components/reader/plate";
import { renderSceneToPlateDocument } from "../../projection";
import { readPlateReaderSelection } from "./read-plate-reader-selection";

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

const rangePrototype = Range.prototype as Range & {
  getClientRects?: () => DOMRectList;
  getBoundingClientRect?: () => DOMRect;
};

const originalGetClientRects = Object.getOwnPropertyDescriptor(rangePrototype, "getClientRects");
const originalGetBoundingClientRect = Object.getOwnPropertyDescriptor(rangePrototype, "getBoundingClientRect");

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
          sentenceIds: ["s1", "s2"],
        },
      ],
      sentences: [
        {
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        },
        {
          sentenceId: "s2",
          paragraphId: "p1",
          text: "These choices persist across administrations.",
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

function sentenceByIdFromScene(scene: ReaderMockVm) {
  return new Map<string, SentenceModel>(scene.article.sentences.map((item) => [item.sentenceId, item]));
}

function applySelection(startNode: Node, startOffset: number, endNode: Node, endOffset: number) {
  const selection = window.getSelection();
  if (!selection) {
    throw new Error("Expected window selection");
  }

  selection.removeAllRanges();
  const range = document.createRange();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset);
  selection.addRange(range);
}

function firstTextNode(element: HTMLElement): Text | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const node = walker.nextNode();
  return node instanceof Text ? node : null;
}

function textNodesWithin(element: HTMLElement): Text[] {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let current = walker.nextNode();

  while (current) {
    if (current instanceof Text) {
      nodes.push(current);
    }
    current = walker.nextNode();
  }

  return nodes;
}

describe("readPlateReaderSelection", () => {
  beforeEach(() => {
    Object.defineProperty(rangePrototype, "getClientRects", {
      configurable: true,
      value: () => [createRect(20, 24, 80, 20)] as unknown as DOMRectList,
    });
    Object.defineProperty(rangePrototype, "getBoundingClientRect", {
      configurable: true,
      value: () => createRect(20, 24, 80, 20),
    });
  });

  afterEach(() => {
    window.getSelection()?.removeAllRanges();
    if (originalGetClientRects) {
      Object.defineProperty(rangePrototype, "getClientRects", originalGetClientRects);
    } else {
      delete (rangePrototype as { getClientRects?: () => DOMRectList }).getClientRects;
    }
    if (originalGetBoundingClientRect) {
      Object.defineProperty(rangePrototype, "getBoundingClientRect", originalGetBoundingClientRect);
    } else {
      delete (rangePrototype as { getBoundingClientRect?: () => DOMRect }).getBoundingClientRect;
    }
    document.body.innerHTML = "";
  });

  it("extracts sentence, text_range, and multi_text selections from Plate surface DOM", () => {
    const scene = createBaseScene();
    const documentValue = renderSceneToPlateDocument(scene);
    const { container } = render(
      <article data-testid="reader-article">
        <PlateReaderSurface
          document={documentValue}
          showTranslation
          readingClassName="reader-serif text-ink"
        />
      </article>,
    );

    const articleElement = container.querySelector("article");
    const firstSentence = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s1"] [data-reader-sentence-text="true"]',
    );
    const secondSentence = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s2"] [data-reader-sentence-text="true"]',
    );
    const firstSentenceTextNode = firstTextNode(firstSentence ?? document.createElement("p"));
    const secondSentenceTextNode = firstTextNode(secondSentence ?? document.createElement("p"));
    if (!articleElement || !firstSentenceTextNode || !secondSentenceTextNode) {
      throw new Error("Expected rendered sentence text nodes");
    }

    applySelection(
      firstSentenceTextNode,
      0,
      firstSentenceTextNode,
      firstSentenceTextNode.textContent?.length ?? 0,
    );
    const sentenceSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(sentenceSelection?.anchorType).toBe("sentence");
    expect(sentenceSelection?.selectedText).toBe(scene.article.sentences[0].text);

    applySelection(firstSentenceTextNode, 14, firstSentenceTextNode, 20);
    const textRangeSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(textRangeSelection?.anchorType).toBe("text_range");
    expect(textRangeSelection?.selectedText).toBe("memory");
    expect(textRangeSelection?.startOffset).toBe(14);
    expect(textRangeSelection?.endOffset).toBe(20);

    applySelection(firstSentenceTextNode, 22, secondSentenceTextNode, 20);
    const multiTextSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(multiTextSelection?.anchorType).toBe("multi_text");
    expect(multiTextSelection?.segments).toHaveLength(2);
    expect(multiTextSelection?.segments[0]).toMatchObject({
      sentenceId: "s1",
      startOffset: 22,
      endOffset: scene.article.sentences[0].text.length,
    });
    expect(multiTextSelection?.segments[1]).toMatchObject({
      sentenceId: "s2",
      startOffset: 0,
      endOffset: 20,
    });
  });

  it("drops whitespace-only and out-of-article selections", () => {
    const scene = createBaseScene();
    const documentValue = renderSceneToPlateDocument(scene);
    const { container } = render(
      <article data-testid="reader-article">
        <PlateReaderSurface
          document={documentValue}
          showTranslation
          readingClassName="reader-serif text-ink"
        />
      </article>,
    );

    const articleElement = container.querySelector("article");
    const firstSentence = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s1"] [data-reader-sentence-text="true"]',
    );
    const firstSentenceTextNode = firstTextNode(firstSentence ?? document.createElement("p"));
    if (!articleElement || !firstSentenceTextNode) {
      throw new Error("Expected rendered sentence text node");
    }

    applySelection(firstSentenceTextNode, 13, firstSentenceTextNode, 14);
    expect(readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene))).toBeNull();

    const external = document.createElement("div");
    external.textContent = "Outside selection";
    document.body.appendChild(external);
    if (!external.firstChild) {
      throw new Error("Expected external text node");
    }

    applySelection(external.firstChild, 0, external.firstChild, 7);
    expect(readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene))).toBeNull();
  });

  it("keeps immersive selections readable through the same anchor contract", () => {
    const scene = createBaseScene();
    const documentValue = renderSceneToPlateDocument(scene);
    const { container } = render(
      <article data-testid="reader-article">
        <ImmersiveReaderSurface
          document={documentValue}
          readingClassName="reader-serif text-ink"
        />
      </article>,
    );

    const articleElement = container.querySelector("article");
    const firstSentence = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s1"] [data-reader-sentence-text="true"]',
    );
    const secondSentence = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s2"] [data-reader-sentence-text="true"]',
    );
    const firstSentenceTextNode = firstTextNode(firstSentence ?? document.createElement("span"));
    const secondSentenceTextNode = firstTextNode(secondSentence ?? document.createElement("span"));
    if (!articleElement || !firstSentenceTextNode || !secondSentenceTextNode) {
      throw new Error("Expected rendered immersive sentence text nodes");
    }

    applySelection(firstSentenceTextNode, 14, firstSentenceTextNode, 20);
    const textRangeSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(textRangeSelection?.anchorType).toBe("text_range");
    expect(textRangeSelection?.selectedText).toBe("memory");

    applySelection(firstSentenceTextNode, 22, secondSentenceTextNode, 20);
    const multiTextSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(multiTextSelection?.anchorType).toBe("multi_text");
    expect(multiTextSelection?.segments).toHaveLength(2);
  });

  it("reads selections whose boundaries sit exactly on a vocab mark edge", () => {
    const scene = createBaseScene();
    scene.article.sentences[0] = {
      ...scene.article.sentences[0],
      text: "At last I felt that subtle change in the air.",
    };
    scene.inlineMarks = [
      {
        id: "mark-1",
        annotationType: "vocab_highlight",
        anchor: {
          kind: "text",
          sentenceId: "s1",
          anchorText: "subtle",
        },
        renderType: "background",
        visualTone: "vocab",
        clickable: true,
        lookupKind: "word",
        lookupText: "subtle",
      },
    ];

    const documentValue = renderSceneToPlateDocument(scene);
    const { container } = render(
      <article data-testid="reader-article">
        <PlateReaderSurface
          document={documentValue}
          showTranslation
          readingClassName="reader-serif text-ink"
        />
      </article>,
    );

    const articleElement = container.querySelector("article");
    const sentenceTextElement = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s1"] [data-reader-sentence-text="true"]',
    );
    const markElement = sentenceTextElement?.querySelector<HTMLElement>("[data-reader-mark-id='mark-1']");
    const markTextNode = firstTextNode(markElement ?? document.createElement("span"));
    const firstSentenceTextNode = firstTextNode(sentenceTextElement ?? document.createElement("p"));
    const textNodes = sentenceTextElement ? textNodesWithin(sentenceTextElement) : [];
    const trailingTextNode = textNodes.at(-1) ?? null;

    if (!articleElement || !sentenceTextElement || !markElement || !markTextNode || !firstSentenceTextNode || !trailingTextNode) {
      throw new Error("Expected rendered marked sentence text nodes");
    }

    applySelection(firstSentenceTextNode, 0, markElement, 0);
    const beforeMarkSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(beforeMarkSelection?.anchorType).toBe("text_range");
    expect(beforeMarkSelection?.selectedText).toBe("At last I felt that ");
    expect(beforeMarkSelection?.startOffset).toBe(0);
    expect(beforeMarkSelection?.endOffset).toBe(20);

    applySelection(markElement, markElement.childNodes.length, trailingTextNode, 18);
    const afterMarkSelection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(afterMarkSelection?.anchorType).toBe("text_range");
    expect(afterMarkSelection?.selectedText).toBe(" change in the air");
    expect(afterMarkSelection?.startOffset).toBe(26);
  });

  it("falls back to sentence bounds when live range rects disappear on mark-edge selections", () => {
    const scene = createBaseScene();
    scene.article.sentences[0] = {
      ...scene.article.sentences[0],
      text: "At last I felt that subtle change in the air.",
    };
    scene.inlineMarks = [
      {
        id: "mark-1",
        annotationType: "vocab_highlight",
        anchor: {
          kind: "text",
          sentenceId: "s1",
          anchorText: "subtle",
        },
        renderType: "background",
        visualTone: "vocab",
        clickable: true,
        lookupKind: "word",
        lookupText: "subtle",
      },
    ];

    Object.defineProperty(rangePrototype, "getClientRects", {
      configurable: true,
      value: () => [] as unknown as DOMRectList,
    });
    Object.defineProperty(rangePrototype, "getBoundingClientRect", {
      configurable: true,
      value: () => createRect(0, 0, 0, 0),
    });

    const documentValue = renderSceneToPlateDocument(scene);
    const { container } = render(
      <article data-testid="reader-article">
        <PlateReaderSurface
          document={documentValue}
          showTranslation
          readingClassName="reader-serif text-ink"
        />
      </article>,
    );

    const articleElement = container.querySelector("article");
    const sentenceTextElement = container.querySelector<HTMLElement>(
      '[data-reader-anchor="sentence"][data-sentence-id="s1"] [data-reader-sentence-text="true"]',
    );
    const markElement = sentenceTextElement?.querySelector<HTMLElement>("[data-reader-mark-id='mark-1']");
    const firstSentenceTextNode = firstTextNode(sentenceTextElement ?? document.createElement("p"));

    if (!articleElement || !sentenceTextElement || !markElement || !firstSentenceTextNode) {
      throw new Error("Expected rendered marked sentence text nodes");
    }

    sentenceTextElement.getBoundingClientRect = () => createRect(40, 80, 320, 48);

    applySelection(firstSentenceTextNode, 0, markElement, 0);
    const selection = readPlateReaderSelection(articleElement, sentenceByIdFromScene(scene));
    expect(selection?.anchorType).toBe("text_range");
    expect(selection?.rect).toMatchObject({
      x: 40,
      y: 80,
      width: 320,
      height: 48,
    });
  });
});
