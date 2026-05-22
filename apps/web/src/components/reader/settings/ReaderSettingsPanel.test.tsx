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
    expect(screen.getByText("标注层")).toBeTruthy();
    expect(screen.getByText("阅读预设")).toBeTruthy();
    expect(screen.getByText("纸面主题")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /隐藏/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      readingMode: "custom",
      translationDisplay: "hidden",
    });

    fireEvent.click(screen.getByRole("button", { name: /^大/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      readingMode: "custom",
      fontSize: "large",
    });

    fireEvent.click(screen.getByRole("button", { name: /舒展/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      readingMode: "custom",
      density: "roomy",
    });

    fireEvent.click(screen.getByRole("button", { name: /^宽/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      readingMode: "custom",
      columnWidth: "wide",
    });

    fireEvent.click(screen.getByRole("button", { name: /鼠尾草/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      readingMode: "custom",
      readerPaperTheme: "sage",
    });

    fireEvent.click(screen.getByRole("button", { name: /词汇 \/ 短语/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      readingMode: "custom",
      annotationVisibilityGroups: {
        ...defaultReaderSettings.annotationVisibilityGroups,
        lexical: false,
      },
    });
  });

  it("has a renamed annotation group label", () => {
    render(
      <ReaderSettingsPanel
        value={defaultReaderSettings}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryAllByText("我的高亮与笔记").length).toBeGreaterThan(0);
  });
});
