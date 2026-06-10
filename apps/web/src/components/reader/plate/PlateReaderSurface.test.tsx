/** @vitest-environment jsdom */

import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderSceneToPlateDocument } from "@/lib/reader-plate";
import type { ReaderMockVm } from "@/types/view/ReaderMockVm";
import { PlateReaderSurface } from "./PlateReaderSurface";

function createMultiTextScene(): ReaderMockVm {
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
          text: "A scarce few can turn their passion into a stable income.",
        },
      ],
    },
    userFacingState: "normal",
    translations: [],
    inlineMarks: [
      {
        id: "mark-multi",
        annotationType: "phrase_gloss",
        anchor: {
          kind: "multi_text",
          sentenceId: "s1",
          parts: [
            { anchorText: "turn" },
            { anchorText: "into" },
          ],
        },
        renderType: "background",
        visualTone: "phrase",
        clickable: true,
        lookupKind: "phrase",
        lookupText: "turn ... into",
      },
    ],
    sentenceEntries: [],
    warnings: [],
  };
}

describe("PlateReaderSurface", () => {
  it("links hover feedback across multi_text mark parts", () => {
    const { container } = render(
      <PlateReaderSurface
        document={renderSceneToPlateDocument(createMultiTextScene())}
        showTranslation={false}
        readingClassName="reader-serif text-ink"
      />,
    );

    const markLeaves = Array.from(
      container.querySelectorAll<HTMLElement>('[data-reader-mark-id="mark-multi"]'),
    );

    expect(markLeaves).toHaveLength(2);
    fireEvent.mouseEnter(markLeaves[0]!);
    expect(markLeaves.every((leaf) => leaf.classList.contains("reader-mark--group-hovered"))).toBe(true);

    fireEvent.mouseLeave(markLeaves[0]!);
    expect(markLeaves.some((leaf) => leaf.classList.contains("reader-mark--group-hovered"))).toBe(false);
  });

  it("keeps all multi_text mark parts active while the inspect card is active", () => {
    const { container } = render(
      <PlateReaderSurface
        document={renderSceneToPlateDocument(createMultiTextScene())}
        showTranslation={false}
        readingClassName="reader-serif text-ink"
        activeInlineMarkKey="mark-multi"
      />,
    );

    const markLeaves = Array.from(
      container.querySelectorAll<HTMLElement>('[data-reader-mark-id="mark-multi"]'),
    );

    expect(markLeaves).toHaveLength(2);
    expect(markLeaves.every((leaf) => leaf.classList.contains("reader-mark--group-active"))).toBe(true);
  });

  it("links focus feedback across multi_text mark parts", () => {
    const { container } = render(
      <PlateReaderSurface
        document={renderSceneToPlateDocument(createMultiTextScene())}
        showTranslation={false}
        readingClassName="reader-serif text-ink"
      />,
    );

    const markLeaves = Array.from(
      container.querySelectorAll<HTMLElement>('[data-reader-mark-id="mark-multi"]'),
    );

    expect(markLeaves).toHaveLength(2);
    fireEvent.focus(markLeaves[0]!);
    expect(markLeaves.every((leaf) => leaf.classList.contains("reader-mark--group-focused"))).toBe(true);

    fireEvent.blur(markLeaves[0]!);
    expect(markLeaves.some((leaf) => leaf.classList.contains("reader-mark--group-focused"))).toBe(false);
  });
});
