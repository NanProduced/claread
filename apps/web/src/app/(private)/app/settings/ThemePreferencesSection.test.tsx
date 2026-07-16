/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemePreferencesSection } from "./ThemePreferencesSection";

const setThemePreference = vi.fn();

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    themePreference: "system",
    resolvedTheme: "light",
    setThemePreference,
  }),
}));

describe("ThemePreferencesSection", () => {
  it("renders the system/light/dark preference selector", () => {
    render(<ThemePreferencesSection />);

    expect(screen.getByText("跟随系统")).toBeTruthy();
    expect(screen.getByText("浅色")).toBeTruthy();
    expect(screen.getByText("深色")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /深色 dark/i }));
    expect(setThemePreference).toHaveBeenCalledWith("dark");

    fireEvent.click(screen.getByRole("button", { name: /跟随系统 follow system/i }));
    expect(setThemePreference).toHaveBeenCalledWith("system");
  });

  it("renders only two visual theme-preview cards (light and dark)", () => {
    render(<ThemePreferencesSection />);
    expect(screen.getAllByText("Light").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Dark").length).toBeGreaterThan(0);

    // Paper must not appear as a preference label or preview chip.
    expect(screen.queryByText("Paper")).toBeNull();
    expect(screen.queryByText("纸质")).toBeNull();
  });
});
