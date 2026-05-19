/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultReaderSettings } from "./shared";
import { ReaderSettingsPanel } from "./ReaderSettingsPanel";

describe("ReaderSettingsPanel", () => {
  it("renders and emits updated reader settings", () => {
    const onChange = vi.fn();

    render(
      <ReaderSettingsPanel
        value={defaultReaderSettings}
        onChange={onChange}
      />,
    );

    expect(screen.getByText("阅读显示")).toBeTruthy();
    expect(screen.getByText("标注显示分组")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /原文模式/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      showTranslation: false,
    });

    fireEvent.click(screen.getByRole("button", { name: /^大/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      fontSize: "large",
    });

    fireEvent.click(screen.getByRole("button", { name: /舒展/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      density: "roomy",
    });

    fireEvent.click(screen.getByRole("button", { name: /^宽/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      columnWidth: "wide",
    });

    fireEvent.click(screen.getByRole("button", { name: /护眼/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      theme: "green",
    });

    fireEvent.click(screen.getByRole("button", { name: /词汇 \/ 短语/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      annotationVisibilityGroups: {
        ...defaultReaderSettings.annotationVisibilityGroups,
        lexical: false,
      },
    });
  });
});
