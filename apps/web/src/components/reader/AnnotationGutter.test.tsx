/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WebAnnotationVm } from "@/types/api/annotations";
import { AnnotationGutter } from "./AnnotationGutter";

function createHighlight(overrides: Partial<WebAnnotationVm> = {}): WebAnnotationVm {
  return {
    id: overrides.id ?? "ann-1",
    recordId: "record-1",
    type: "highlight",
    anchorType: "text_range",
    targetKey: overrides.targetKey ?? "record:record-1:range:s1:0:6:hash-1",
    paragraphId: "p1",
    sentenceId: "s1",
    selectedText: overrides.selectedText ?? "memory",
    startOffset: overrides.startOffset ?? 14,
    endOffset: overrides.endOffset ?? 20,
    textHash: overrides.textHash ?? "hash-1",
    segments: overrides.segments ?? [],
    color: "warm_yellow",
    createdAt: "2026-05-22T00:00:00Z",
    updatedAt: "2026-05-22T00:00:00Z",
    ...overrides,
  };
}

describe("AnnotationGutter", () => {
  it("jumps directly for a single highlight", () => {
    const onAnnotationJump = vi.fn();
    const annotation = createHighlight();

    render(
      <AnnotationGutter
        sentenceId="s1"
        annotations={[annotation]}
        onAnnotationJump={onAnnotationJump}
      />,
    );

    fireEvent.click(screen.getByLabelText("打开本句高亮"));

    expect(onAnnotationJump).toHaveBeenCalledWith(annotation, expect.any(HTMLButtonElement), "s1");
  });

  it("opens a strip for multiple highlights and lets the user jump to one item", () => {
    const onAnnotationJump = vi.fn();
    const first = createHighlight({
      id: "ann-1",
      targetKey: "record:record-1:range:s1:0:6:hash-1",
      selectedText: "subtle",
      startOffset: 14,
      endOffset: 20,
      textHash: "hash-1",
    });
    const second = createHighlight({
      id: "ann-2",
      targetKey: "record:record-1:range:s1:28:35:hash-2",
      selectedText: "morning",
      startOffset: 28,
      endOffset: 35,
      textHash: "hash-2",
    });

    render(
      <AnnotationGutter
        sentenceId="s1"
        annotations={[first, second]}
        onAnnotationJump={onAnnotationJump}
      />,
    );

    fireEvent.click(screen.getByLabelText("查看本句 2 处高亮"));

    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);

    fireEvent.click(screen.getByText("morning"));

    expect(onAnnotationJump).toHaveBeenCalledWith(second, expect.any(HTMLButtonElement), "s1");
  });

  it("shows a multi_text gutter marker only on the first sentence", () => {
    const multiText = createHighlight({
      id: "ann-multi",
      anchorType: "multi_text",
      targetKey: "record:record-1:multi_text:2:hash",
      sentenceId: "s1",
      startOffset: null,
      endOffset: null,
      textHash: null,
      segments: [
        {
          paragraphId: "p1",
          sentenceId: "s1",
          selectedText: "no sign could I find",
          startOffset: 0,
          endOffset: 20,
          textHash: "hash-1",
        },
        {
          paragraphId: "p2",
          sentenceId: "s2",
          selectedText: "Then I stopped",
          startOffset: 0,
          endOffset: 14,
          textHash: "hash-2",
        },
      ],
      selectedText: "no sign could I find Then I stopped",
    });

    const view = render(
      <AnnotationGutter sentenceId="s1" annotations={[multiText]} />,
    );

    expect(view.container.querySelector('[aria-label="打开本句高亮"]')).toBeTruthy();

    view.rerender(<AnnotationGutter sentenceId="s2" annotations={[multiText]} />);

    expect(view.container.querySelector('[aria-label="打开本句高亮"]')).toBeNull();
  });
});
