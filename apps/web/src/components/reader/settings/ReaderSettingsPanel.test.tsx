/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultReaderSettings } from "./shared";
import { ReaderSettingsPanel } from "./ReaderSettingsPanel";

describe("ReaderSettingsPanel", () => {
  it("renders the floating variant as a single shell without lookup chrome", () => {
    const { container, unmount } = render(
      <ReaderSettingsPanel
        themePreference="system"
        variant="floating"
        value={defaultReaderSettings}
        onChange={vi.fn()}
        onThemeChange={vi.fn()}
      />,
    );

    const panel = container.querySelector('[data-reader-settings-panel="floating"]');
    expect(panel).not.toBeNull();
    expect(panel?.classList.contains("reader-tool-panel")).toBe(true);
    expect(panel?.classList.contains("reader-lookup-preview")).toBe(false);
    expect(panel?.classList.contains("border-border\/55")).toBe(false);
    expect(panel?.classList.contains("bg-transparent")).toBe(false);
    unmount();
  });

  it("renders only the new calibration controls and emits updated settings", () => {
    const onChange = vi.fn();
    const onThemeChange = vi.fn();

    render(
      <ReaderSettingsPanel
        themePreference="system"
        value={defaultReaderSettings}
        onChange={onChange}
        onThemeChange={onThemeChange}
      />,
    );

    expect(screen.getByText("阅读设置")).toBeTruthy();
    expect(screen.getByText("主题")).toBeTruthy();
    expect(screen.getByText("字号")).toBeTruthy();
    expect(screen.getByText("字体")).toBeTruthy();

    expect(screen.queryByText("阅读预设")).toBeNull();
    expect(screen.queryByText("译文")).toBeNull();
    expect(screen.queryByText("行距")).toBeNull();
    expect(screen.queryByText("版心")).toBeNull();
    expect(screen.queryByText("纸面主题")).toBeNull();
    expect(screen.queryByText("标注层")).toBeNull();

    // Reader 只提供 system/light/dark，不存在 Paper 选项
    expect(screen.queryByText("Paper")).toBeNull();
    expect(screen.getByText("跟随系统")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /深色 dark/i }));
    expect(onThemeChange).toHaveBeenCalledWith("dark");

    fireEvent.click(screen.getByRole("button", { name: /^大$/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      fontScale: "lg",
    });

    fireEvent.click(screen.getByRole("button", { name: /书页衬线/i }));
    expect(onChange).toHaveBeenCalledWith({
      ...defaultReaderSettings,
      fontFamily: "book",
    });
  });
});
