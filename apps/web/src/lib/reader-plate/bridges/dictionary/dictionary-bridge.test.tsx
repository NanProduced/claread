/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";
import {
  inspectIntentFromStructuredMark,
  lookupIntentFromMark,
  lookupIntentFromSelection,
  lookupIntentFromStructuredInspect,
  lookupIntentFromTokenClick,
  readerLookupSnapshotFromIntent,
} from "./adapters";
import type { ReaderTextSelection } from "../../primitives";
import { hashAnchorText } from "../../primitives";
import type { SentenceModel } from "@/types/view/ReaderMockVm";

function createSentence(text = "Institutional memory shapes policy choices."): Pick<SentenceModel, "sentenceId" | "text"> {
  return {
    sentenceId: "s1",
    text,
  };
}

function createSelection(): Extract<ReaderTextSelection, { anchorType: "text_range" }> {
  return {
    anchorType: "text_range",
    sentence: {
      sentenceId: "s1",
      paragraphId: "p1",
      text: "Institutional memory shapes policy choices.",
    },
    selectedText: "memory",
    startOffset: 14,
    endOffset: 20,
    textHash: "hash-memory",
    rect: {
      x: 0,
      y: 0,
      width: 80,
      height: 20,
      top: 0,
      left: 0,
      right: 80,
      bottom: 20,
      toJSON() {
        return this;
      },
    } as DOMRect,
    range: undefined,
    segments: [
      {
        sentenceId: "s1",
        paragraphId: "p1",
        sentence: {
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        },
        selectedText: "memory",
        startOffset: 14,
        endOffset: 20,
        textHash: "hash-memory",
      },
    ],
  };
}

describe("dictionary bridge adapters", () => {
  it("creates a lexical lookup intent from plain token clicks", () => {
    const element = document.createElement("p");
    element.textContent = "Institutional memory shapes policy choices.";
    document.body.appendChild(element);

    const textNode = element.firstChild;
    if (!textNode) {
      throw new Error("Expected text node");
    }

    (document as Document & { caretRangeFromPoint?: (x: number, y: number) => Range | null }).caretRangeFromPoint = () => {
      const range = document.createRange();
      range.setStart(textNode, 15);
      range.collapse(true);
      return range;
    };

    const result = lookupIntentFromTokenClick({
      element,
      sentence: createSentence(),
      sourceContext: "制度记忆会塑造政策选择。",
      clientX: 8,
      clientY: 8,
    });

    expect(result?.intent).toMatchObject({
      kind: "lexical_lookup",
      query: "memory",
      lookupType: "word",
      sentenceId: "s1",
      anchorText: "memory",
    });
    expect(result?.anchor).toMatchObject({
      sentenceId: "s1",
      startOffset: 14,
      endOffset: 20,
    });
  });

  it("creates manual span lookup intents from reader text selections", () => {
    const intent = lookupIntentFromSelection(createSelection(), "制度记忆会塑造政策选择。");

    expect(intent).toMatchObject({
      kind: "manual_span_lookup",
      query: "memory",
      lookupType: "word",
      sentenceId: "s1",
      anchorText: "memory",
      sourceContext: "制度记忆会塑造政策选择。",
    });
  });

  it("distinguishes word marks from structured marks", () => {
    const sentence = createSentence();

    const lookupIntent = lookupIntentFromMark({
      mark: {
        id: "mark-word",
        annotationType: "vocab_highlight",
        visualTone: "vocab",
        lookupKind: "word",
        lookupText: undefined,
        glossary: undefined,
      },
      sentence,
      anchorText: "memory",
      sourceContext: "制度记忆会塑造政策选择。",
      startOffset: 14,
      endOffset: 20,
    });

    expect(lookupIntent).toMatchObject({
      kind: "lexical_lookup",
      query: "memory",
      lookupType: "word",
      label: "词典",
    });

    const inspectIntent = inspectIntentFromStructuredMark({
      mark: {
        id: "mark-phrase",
        annotationType: "phrase_gloss",
        visualTone: "phrase",
        lookupKind: "phrase",
        lookupText: "policy choices",
        glossary: {
          zh: "政策选择",
          phraseType: "fixed_collocation",
        },
      },
      sentence,
      anchorText: "policy choices",
      sourceContext: "制度记忆会塑造政策选择。",
      startOffset: 28,
      endOffset: 42,
    });

    expect(inspectIntent).toMatchObject({
      kind: "structured_annotation_inspect",
      markId: "mark-phrase",
      anchorText: "policy choices",
      lookupText: "policy choices",
      label: "固定搭配",
    });

    expect(lookupIntentFromStructuredInspect(inspectIntent)).toMatchObject({
      kind: "lexical_lookup",
      query: "policy choices",
      lookupType: "phrase",
      title: "查短语",
    });
  });

  it("keeps context_gloss multi_text lookups on the phrase route", () => {
    const sentence = createSentence("They apply the rule to unfamiliar cases.");

    const inspectIntent = inspectIntentFromStructuredMark({
      mark: {
        id: "mark-context",
        annotationType: "context_gloss",
        visualTone: "context",
        lookupKind: "phrase",
        lookupText: "apply ... to",
        glossary: {
          gloss: "把……应用到……",
          reason: "这里强调把规则迁移到新情境中的用法。",
        },
      },
      sentence,
      anchorText: "apply",
      sourceContext: "他们把这条规则应用到不熟悉的案例中。",
      startOffset: 5,
      endOffset: 10,
    });

    expect(inspectIntent).toMatchObject({
      kind: "structured_annotation_inspect",
      annotationType: "context_gloss",
      lookupText: "apply ... to",
      lookupKind: "phrase",
      anchorText: "apply",
    });

    expect(lookupIntentFromStructuredInspect(inspectIntent)).toMatchObject({
      kind: "lexical_lookup",
      query: "apply ... to",
      lookupType: "phrase",
      annotationType: "context_gloss",
    });
  });

  it("converts lookup intents into dictionary snapshots", () => {
    const snapshot = readerLookupSnapshotFromIntent(
      "record-1",
      {
        kind: "lexical_lookup",
        query: "memory",
        lookupType: "word",
        sentenceId: "s1",
        contextSentence: "Institutional memory shapes policy choices.",
        anchorOffsets: {
          startOffset: 14,
          endOffset: 20,
        },
        anchorText: "memory",
        title: "查词",
      },
      { kind: "loading" },
    );

    expect(snapshot).toMatchObject({
      recordId: "record-1",
      query: "memory",
      lookupType: "word",
      sentenceId: "s1",
      anchorOffsets: {
        startOffset: 14,
        endOffset: 20,
      },
      textHash: hashAnchorText("memory"),
      title: "查词",
      state: { kind: "loading" },
    });
  });
});
