/** @vitest-environment jsdom */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SelectionToolbar } from "./SelectionToolbar";

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

describe("SelectionToolbar", () => {
  it("keeps primary selection actions wired through the toolbar shell", async () => {
    const onAsk = vi.fn();
    const onSelectSentence = vi.fn();

    const { container } = render(
      <SelectionToolbar
        selectedText="policy choices"
        onAsk={onAsk}
        onSelectSentence={onSelectSentence}
      />,
    );
    const toolbar = within(container);

    fireEvent.click(toolbar.getByRole("button", { name: /Ask Claread/i }));
    await openMenu(toolbar.getByRole("button", { name: /更多选区操作/i }), "选择整句");
    fireEvent.click(await screen.findByText("选择整句"));

    expect(onAsk).toHaveBeenCalledWith("policy choices");
    expect(onSelectSentence).toHaveBeenCalledWith("policy choices");
    expect(toolbar.getByRole("button", { name: "高亮" })).toBeTruthy();
  });

  it("uses progressive disclosure for note editing", () => {
    const onNote = vi.fn();
    const onNoteChange = vi.fn();
    const onNoteSave = vi.fn();

    render(
      <SelectionToolbar
        selectedText="policy choices"
        noteOpen
        noteValue="existing note"
        onNote={onNote}
        onNoteChange={onNoteChange}
        onNoteSave={onNoteSave}
      />,
    );

    expect(screen.getByText("笔记")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("写一句和这段文字绑定的笔记。"), {
      target: { value: "updated note" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存/i }));

    expect(onNoteChange).toHaveBeenCalledWith("updated note");
    expect(onNoteSave).toHaveBeenCalled();
  });
});
