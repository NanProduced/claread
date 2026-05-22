/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  SelectionToolbar,
  defaultSelectionToolbarColorOptions,
} from "./SelectionToolbar";

describe("SelectionToolbar", () => {
  it("uses the default color on the first highlighter click", () => {
    const onHighlight = vi.fn();

    render(<SelectionToolbar selectedText="memory" onHighlight={onHighlight} />);

    fireEvent.click(screen.getByLabelText("高亮"));

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
