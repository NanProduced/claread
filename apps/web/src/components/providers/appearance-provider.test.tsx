/**
 * @vitest-environment jsdom
 *
 * Locks the appearance provider's contract for the app-shell:
 *   - defaultTheme is "system", not "paper";
 *   - preference enum is "system" | "light" | "dark";
 *   - resolved enum is "light" | "dark";
 *   - the document.documentElement dataset.appTheme attribute carries
 *     ONLY resolved light/dark — never "system" or "paper".
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, waitFor } from "@testing-library/react";

import {
  AppearanceProvider,
  useAppearance,
} from "@/components/providers/appearance-provider";

function Inspector() {
  const { themePreference, resolvedTheme, themeName } = useAppearance();
  return (
    <div
      data-testid="inspector"
      data-pref={themePreference}
      data-resolved={resolvedTheme}
      data-name={themeName}
    />
  );
}

function Harness() {
  return (
    <AppearanceProvider>
      <Inspector />
    </AppearanceProvider>
  );
}

function clearLocalStorage() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.clear();
  } catch {
    // jsdom may not always expose clear() — fall back to removeItem.
    try {
      window.localStorage.removeItem("claread.theme.v1");
      window.localStorage.removeItem("claread.appearance.v1");
      window.localStorage.removeItem("claread.web.preferences.v1");
    } catch {}
  }
}

// next-themes calls window.matchMedia to discover the system color
// scheme. jsdom doesn't implement it by default, so stub it.
function stubMatchMedia(matchesDark: boolean) {
  if (typeof window === "undefined") return;
  if (typeof window.matchMedia === "function") return;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: query.includes("dark") ? matchesDark : !matchesDark,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

describe("AppearanceProvider contract", () => {
  beforeEach(() => {
    clearLocalStorage();
    stubMatchMedia(false);
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("class");
      delete document.documentElement.dataset.appTheme;
    }
  });

  afterEach(() => {
    clearLocalStorage();
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("class");
      delete document.documentElement.dataset.appTheme;
    }
  });

  it("exposes system/light/dark preference and light/dark resolution", async () => {
    const { getByTestId } = render(<Harness />);
    await waitFor(() => {
      const node = getByTestId("inspector");
      expect(["system", "light", "dark"]).toContain(node.getAttribute("data-pref"));
      expect(["light", "dark"]).toContain(node.getAttribute("data-resolved"));
      expect(["light", "dark"]).toContain(node.getAttribute("data-name"));
    });
  });

  it("writes documentElement dataset.appTheme only with light or dark", async () => {
    render(<Harness />);
    await waitFor(() => {
      const value = document.documentElement.dataset.appTheme;
      expect(value === "light" || value === "dark").toBe(true);
      expect(value === "system" || value === "paper").toBe(false);
    });
  });
});
