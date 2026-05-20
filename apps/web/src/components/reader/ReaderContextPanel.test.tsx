/** @vitest-environment jsdom */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReaderContextPanel } from "./ReaderContextPanel";

async function openMenu(button: HTMLElement, itemLabel: string) {
  fireEvent.pointerDown(button, { button: 0, ctrlKey: false });
  if (screen.queryByText(itemLabel)) {
    return;
  }

  fireEvent.click(button);
  if (screen.queryByText(itemLabel)) {
    return;
  }

  fireEvent.mouseDown(button, { button: 0 });
  if (screen.queryByText(itemLabel)) {
    return;
  }

  fireEvent.keyDown(button, { key: "Enter" });
  await screen.findByText(itemLabel);
}

describe("ReaderContextPanel", () => {
  it("shows only sentence context actions and no settings controls", () => {
    const { container } = render(
      <ReaderContextPanel
        sentence={{
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        }}
        note=""
        color="warm_yellow"
        saveState={{ kind: "idle" }}
        sentenceAnnotations={[]}
        onNoteChange={vi.fn()}
        onColorChange={vi.fn()}
        onSaveAnnotation={vi.fn()}
        onAsk={vi.fn()}
      />,
    );

    expect(screen.getByText("当前句")).toBeTruthy();
    expect(screen.getByRole("button", { name: "高亮" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "笔记" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /更多/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /带入 Ask/i })).toBeNull();
    expect(screen.queryByText("阅读显示")).toBeNull();
    expect(screen.queryByText("字号与行距")).toBeNull();
    expect(screen.queryByText("标注显示分组")).toBeNull();
  });

  it("shows sentence asset actions for annotation and favorite ask/jump", async () => {
    const onAnnotationJump = vi.fn();
    const onAnnotationAsk = vi.fn();
    const onFavoriteJump = vi.fn();
    const onFavoriteAsk = vi.fn();

    const { container } = render(
      <ReaderContextPanel
        sentence={{
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        }}
        note=""
        color="warm_yellow"
        saveState={{ kind: "idle" }}
        sentenceAnnotations={[
          {
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
            textHash: "hash",
            segments: [],
            color: "warm_yellow",
            note: "关键词",
            createdAt: "2026-05-20T00:00:00Z",
            updatedAt: "2026-05-20T00:00:00Z",
          },
        ]}
        sentenceFavorites={[
          {
            id: "fav-1",
            targetType: "text_range",
            targetKey: "record:record-1:range:s1:14:20:hash",
            recordId: "record-1",
            anchorType: "text_range",
            sentenceId: "s1",
            selectedText: "memory",
            startOffset: 14,
            endOffset: 20,
            textHash: "hash",
            segments: [],
          },
        ]}
        onNoteChange={vi.fn()}
        onColorChange={vi.fn()}
        onSaveAnnotation={vi.fn()}
        onAsk={vi.fn()}
        onAnnotationJump={onAnnotationJump}
        onAnnotationAsk={onAnnotationAsk}
        onFavoriteJump={onFavoriteJump}
        onFavoriteAsk={onFavoriteAsk}
      />,
    );
    const panel = within(container);

    await openMenu(panel.getByRole("button", { name: /更多/i }), "带入 Ask");
    expect(await screen.findByRole("menuitem", { name: "带入 Ask" })).toBeTruthy();
    fireEvent.click(await screen.findByRole("menuitem", { name: "查看已保存资产" }));
    let jumpButtons = screen.getAllByText("跳转");
    let askButtons = screen.getAllByText("带入 Ask");
    fireEvent.click(jumpButtons[0] as HTMLElement);
    fireEvent.click(askButtons[askButtons.length - 2] as HTMLElement);
    jumpButtons = screen.getAllByText("跳转");
    askButtons = screen.getAllByText("带入 Ask");
    fireEvent.click(jumpButtons[1] as HTMLElement);
    fireEvent.click(askButtons[askButtons.length - 1] as HTMLElement);

    expect(onAnnotationJump).toHaveBeenCalled();
    expect(onAnnotationAsk).toHaveBeenCalled();
    expect(onFavoriteJump).toHaveBeenCalled();
    expect(onFavoriteAsk).toHaveBeenCalled();
  });
});
