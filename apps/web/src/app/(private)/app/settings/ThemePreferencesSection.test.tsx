/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemePreferencesSection } from "./ThemePreferencesSection";
import type { ResolvedTheme, ThemePreference } from "@/lib/appearance";

const themeState = vi.hoisted(() => ({
  themePreference: "system" as ThemePreference,
  resolvedTheme: "light" as ResolvedTheme,
  setThemePreference: vi.fn(),
}));

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => themeState,
}));

beforeEach(() => {
  themeState.themePreference = "system";
  themeState.resolvedTheme = "light";
  themeState.setThemePreference.mockClear();
});

afterEach(() => {
  cleanup();
});

describe("ThemePreferencesSection", () => {
  it("renders one compact theme select with all supported values", () => {
    render(<ThemePreferencesSection />);

    const select = screen.getByRole("combobox", { name: "主题" }) as HTMLSelectElement;
    expect(select.value).toBe("system");
    expect(select.querySelectorAll("option")).toHaveLength(3);
    expect(screen.getByRole("option", { name: "跟随系统" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "浅色" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "深色" })).toBeTruthy();
  });

  it("shows the resolved theme only for the system preference", () => {
    const { rerender } = render(<ThemePreferencesSection />);
    expect(screen.getByText("当前：浅色")).toBeTruthy();

    themeState.themePreference = "light";
    rerender(<ThemePreferencesSection />);
    expect(screen.queryByText(/当前：/)).toBeNull();
  });

  it("keeps the three-value AppearanceProvider contract", () => {
    render(<ThemePreferencesSection />);
    const select = screen.getByRole("combobox", { name: "主题" });

    fireEvent.change(select, { target: { value: "dark" } });
    expect(themeState.setThemePreference).toHaveBeenCalledWith("dark");
    fireEvent.change(select, { target: { value: "light" } });
    expect(themeState.setThemePreference).toHaveBeenCalledWith("light");
    fireEvent.change(select, { target: { value: "system" } });
    expect(themeState.setThemePreference).toHaveBeenCalledWith("system");
  });

  it("uses a labelled, token-based focusable control", () => {
    const { container } = render(<ThemePreferencesSection />);
    const select = screen.getByRole("combobox", { name: "主题" });

    expect(select.className).toContain("focus-visible:ring-lens-blue");
    expect(select.className).toContain("border-hairline");
    expect(container.querySelectorAll('input[type="radio"]')).toHaveLength(0);
  });
});