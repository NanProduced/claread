/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReaderContextPanel } from "./ReaderContextPanel";

describe("ReaderContextPanel", () => {
  it("shows only sentence context actions and no settings controls", () => {
    render(
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
    expect(screen.getByRole("button", { name: /追问/i })).toBeTruthy();
    expect(screen.queryByText("阅读显示")).toBeNull();
    expect(screen.queryByText("字号与行距")).toBeNull();
    expect(screen.queryByText("标注显示分组")).toBeNull();
  });

  it("shows sentence asset actions for annotation and favorite ask/jump", () => {
    const onAnnotationJump = vi.fn();
    const onAnnotationAsk = vi.fn();
    const onFavoriteJump = vi.fn();
    const onFavoriteAsk = vi.fn();

    render(
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

    expect(screen.getByText("本句已保存")).toBeTruthy();
    fireEvent.click(screen.getAllByText("跳转")[0] as HTMLElement);
    fireEvent.click(screen.getAllByText("带入 Ask")[0] as HTMLElement);
    fireEvent.click(screen.getAllByText("跳转")[1] as HTMLElement);
    fireEvent.click(screen.getAllByText("带入 Ask")[1] as HTMLElement);

    expect(onAnnotationJump).toHaveBeenCalled();
    expect(onAnnotationAsk).toHaveBeenCalled();
    expect(onFavoriteJump).toHaveBeenCalled();
    expect(onFavoriteAsk).toHaveBeenCalled();
  });
});
