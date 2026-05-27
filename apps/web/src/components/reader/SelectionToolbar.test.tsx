/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  SelectionToolbar,
  defaultSelectionToolbarColorOptions,
} from "./SelectionToolbar";

describe("SelectionToolbar", () => {
  it("does not render grammar or breakdown quick actions", () => {
    render(<SelectionToolbar selectedText="memory" />);

    expect(screen.queryByLabelText("语法解析")).toBeNull();
    expect(screen.queryByLabelText("句子拆分")).toBeNull();
  });

  it("uses the default color on the first highlighter click", () => {
    const onHighlight = vi.fn();

    const { container } = render(<SelectionToolbar selectedText="memory" onHighlight={onHighlight} />);

    const trigger = container.querySelector(
      '[role="toolbar"] button[aria-label="高亮"]:not([disabled])',
    ) as HTMLButtonElement | null;

    expect(trigger).toBeTruthy();
    fireEvent.click(trigger as HTMLButtonElement);

    expect(onHighlight).toHaveBeenCalledWith(
      defaultSelectionToolbarColorOptions[0]?.value,
      "memory",
      defaultSelectionToolbarColorOptions[0],
    );
  });

  it("toggles the palette instead of creating a new highlight when one already exists", () => {
    const onHighlight = vi.fn();
    const onToggleHighlightPalette = vi.fn();

    render(
      <SelectionToolbar
        selectedText="memory"
        hasHighlight
        canToggleHighlightPalette
        activeColor="warm_yellow"
        onHighlight={onHighlight}
        onToggleHighlightPalette={onToggleHighlightPalette}
      />,
    );

    fireEvent.click(screen.getByLabelText("切换高亮颜色"));

    expect(onToggleHighlightPalette).toHaveBeenCalledTimes(1);
    expect(onHighlight).not.toHaveBeenCalled();
  });

  it("creates a highlight when the current selection is not an exact saved highlight", () => {
    const onHighlight = vi.fn();
    const onToggleHighlightPalette = vi.fn();

    const { container } = render(
      <SelectionToolbar
        selectedText="memory"
        hasHighlight
        activeColor="warm_yellow"
        onHighlight={onHighlight}
        onToggleHighlightPalette={onToggleHighlightPalette}
      />,
    );

    const trigger = container.querySelector(
      '[role="toolbar"] button[aria-label="高亮"]:not([disabled])',
    ) as HTMLButtonElement | null;

    expect(trigger).toBeTruthy();
    fireEvent.click(trigger as HTMLButtonElement);

    expect(onHighlight).toHaveBeenCalledWith(
      defaultSelectionToolbarColorOptions[0]?.value,
      "memory",
      defaultSelectionToolbarColorOptions[0],
    );
  });

  it("shows the inline color strip when the palette is open", () => {
    const onHighlight = vi.fn();

    render(
      <SelectionToolbar
        selectedText="memory"
        hasHighlight
        activeColor="warm_yellow"
        highlightPaletteOpen
        onHighlight={onHighlight}
      />,
    );

    expect(screen.getByLabelText("切换为暖黄")).toBeTruthy();
    expect(screen.getByLabelText("切换为雾青")).toBeTruthy();
    expect(screen.getByLabelText("切换为灰绿")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("切换为雾青"));

    expect(onHighlight).toHaveBeenCalledWith(
      "soft_blue",
      "memory",
      defaultSelectionToolbarColorOptions[1],
    );
  });
});
