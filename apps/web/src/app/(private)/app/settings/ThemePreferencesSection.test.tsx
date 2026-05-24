/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemePreferencesSection } from "./ThemePreferencesSection";

const setThemeName = vi.fn();

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    themeName: "paper",
    setThemeName,
  }),
}));

describe("ThemePreferencesSection", () => {
  it("renders only the unified three-theme selector", () => {
    render(<ThemePreferencesSection />);

    expect(screen.getByText("主题")).toBeTruthy();
    expect(screen.getByText("纸质")).toBeTruthy();
    expect(screen.getByText("浅色")).toBeTruthy();
    expect(screen.getByText("深色")).toBeTruthy();
    expect(screen.queryByText("跟随系统")).toBeNull();
    expect(screen.queryByText("Reader 默认纸面")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /深色 dark/i }));
    expect(setThemeName).toHaveBeenCalledWith("dark");
  });
});
