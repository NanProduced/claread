/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultReaderSettings } from "./shared";
import { ReaderSettingsPanel } from "./ReaderSettingsPanel";

describe("ReaderSettingsPanel", () => {
  it("renders only the new calibration controls and emits updated settings", () => {
    const onChange = vi.fn();
    const onThemeChange = vi.fn();

    render(
      <ReaderSettingsPanel
        themeName="paper"
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
