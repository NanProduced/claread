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
  it("renders the three radio options", () => {
    render(<ThemePreferencesSection />);

    expect(screen.getByRole("radio", { name: /跟随系统/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /浅色/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /深色/ })).toBeTruthy();
  });

  it("checks only one option at a time", () => {
    const { rerender } = render(<ThemePreferencesSection />);
    const radios = screen.getAllByRole("radio");

    expect(radios).toHaveLength(3);
    expect(radios.filter((radio) => (radio as HTMLInputElement).checked)).toHaveLength(1);
    expect((screen.getByRole("radio", { name: /跟随系统/ }) as HTMLInputElement).checked).toBe(
      true,
    );

    themeState.themePreference = "light";
    rerender(<ThemePreferencesSection />);

    expect((screen.getByRole("radio", { name: /浅色/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("radio", { name: /跟随系统/ }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect((screen.getByRole("radio", { name: /深色/ }) as HTMLInputElement).checked).toBe(false);
  });

  it("shows resolved theme hint only in system mode", () => {
    const { rerender } = render(<ThemePreferencesSection />);

    expect(screen.getByText("当前显示：浅色")).toBeTruthy();

    themeState.themePreference = "light";
    rerender(<ThemePreferencesSection />);
    expect(screen.queryByText(/当前显示/)).toBeNull();

    themeState.themePreference = "dark";
    themeState.resolvedTheme = "dark";
    rerender(<ThemePreferencesSection />);
    expect(screen.queryByText(/当前显示/)).toBeNull();
  });

  it("calls setThemePreference with the correct values", () => {
    const { rerender } = render(<ThemePreferencesSection />);

    fireEvent.click(screen.getByRole("radio", { name: /深色/ }));
    expect(themeState.setThemePreference).toHaveBeenCalledWith("dark");

    fireEvent.click(screen.getByRole("radio", { name: /浅色/ }));
    expect(themeState.setThemePreference).toHaveBeenCalledWith("light");

    themeState.themePreference = "light";
    rerender(<ThemePreferencesSection />);

    fireEvent.click(screen.getByRole("radio", { name: /跟随系统/ }));
    expect(themeState.setThemePreference).toHaveBeenCalledWith("system");
  });

  it("exposes a visible focus-within ring contract on each option label", () => {
    const { container } = render(<ThemePreferencesSection />);

    const labels = container.querySelectorAll("label");
    expect(labels.length).toBe(3);
    labels.forEach((label) => {
      expect(label.className).toContain("focus-within:ring-2");
      expect(label.className).toContain("focus-within:ring-lens-blue");
    });
  });
});
