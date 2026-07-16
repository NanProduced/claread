/**
 * @vitest-environment jsdom
 *
 * Locks the appearance provider's contract for the app-shell:
 *   - defaultTheme is "system", not "paper";
 *   - preference enum is "system" | "light" | "dark";
 *   - resolved enum is "light" | "dark";
 *   - the document.documentElement dataset.appTheme attribute carries
 *     ONLY resolved light/dark — never "system" or "paper".
 *
 * Global theme storage is `claread.theme.v1`; no legacy Reader-only
 * theme storage is read, written, or migrated.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

import {
  AppearanceProvider,
  useAppearance,
} from "@/components/providers/appearance-provider";

function Inspector() {
  const { themePreference, resolvedTheme, setThemePreference } = useAppearance();
  return (
    <div
      data-testid="inspector"
      data-pref={themePreference}
      data-resolved={resolvedTheme}
      data-setter={typeof setThemePreference === "function" ? "fn" : "no"}
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

function stubLocalStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      store.delete(key);
    }),
    clear: vi.fn(() => {
      store.clear();
    }),
  });
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
    stubLocalStorage();
    stubMatchMedia(false);
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("class");
      delete document.documentElement.dataset.appTheme;
    }
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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
      expect(node.getAttribute("data-setter")).toBe("fn");
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

  it("resolves a system preference to light/dark based on the OS theme", async () => {
    // system 偏好在系统浅/深色下使用相同全局 resolvedTheme。
    // The Provider resolves "system" against matchMedia; with matchMedia
    // stubbed to light, the resolved value must be "light".
    const { getByTestId } = render(<Harness />);
    await waitFor(() => {
      const node = getByTestId("inspector");
      expect(node.getAttribute("data-pref")).toBe("system");
      expect(node.getAttribute("data-resolved")).toBe("light");
    });
  });
});
