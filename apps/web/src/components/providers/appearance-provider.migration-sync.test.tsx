/**
 * @vitest-environment jsdom
 *
 * Covers the AppearanceProvider legacy Reader theme migration replay:
 *   - When `migrateLegacyReaderThemeStorage` reports a migrated value,
 *     the provider replays it through the same WebPreferences
 *     persistence/cloud-sync path used for explicit preference changes
 *     (persistWebPreferences + syncWebPreferencesToCloud), exactly once.
 *   - When `migrateLegacyReaderThemeStorage` reports no migration
 *     (`migrated === null`), the provider does NOT trigger the
 *     persistence/cloud-sync path.
 *
 * The migration function runs at module load time, so these tests mock
 * `@/lib/appearance` before the provider module is imported. Each test
 * controls the mocked return value via `setMigrationResult`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

const { migrationResultHolder, persistWebPreferencesMock, syncWebPreferencesToCloudMock } =
  vi.hoisted(() => {
    const migrationResultHolder = {
      current: { migrated: null as null | "system" | "light" | "dark" },
    };
    return {
      migrationResultHolder,
      persistWebPreferencesMock: vi.fn(),
      syncWebPreferencesToCloudMock: vi.fn(),
    };
  });

function setMigrationResult(migrated: null | "system" | "light" | "dark") {
  migrationResultHolder.current.migrated = migrated;
}

vi.mock("@/lib/appearance", () => ({
  migrateLegacyReaderThemeStorage: () => migrationResultHolder.current,
  normalizeThemePreference: (value: unknown) => {
    if (value === "light") return "light";
    if (value === "dark") return "dark";
    return "system";
  },
  THEME_STORAGE_KEY: "claread.theme.v1",
  themeColorForTheme: () => "#f8f8f8",
}));

vi.mock("@/lib/web-preferences", () => ({
  persistWebPreferences: persistWebPreferencesMock,
}));

vi.mock("@/lib/web-preferences-sync", () => ({
  buildWebPreferencesFromLocal: () => ({
    theme: "system",
    reader_mode: "intensive",
    font_family: "sans",
    font_scale: "md",
    updated_at: "",
  }),
  syncWebPreferencesToCloud: syncWebPreferencesToCloudMock,
}));

// Import the provider AFTER the mocks are in place so the top-level
// migration call picks up the mocked return value.
import {
  AppearanceProvider,
  useAppearance,
} from "@/components/providers/appearance-provider";

function Inspector() {
  const { themePreference, resolvedTheme } = useAppearance();
  return (
    <div
      data-testid="inspector"
      data-pref={themePreference}
      data-resolved={resolvedTheme}
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

function StrictHarness() {
  return (
    <StrictMode>
      <Harness />
    </StrictMode>
  );
}

function stubMatchMedia(matchesDark: boolean) {
  if (typeof window === "undefined") return;
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

describe("AppearanceProvider legacy Reader theme migration sync", () => {
  beforeEach(() => {
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
    stubMatchMedia(false);
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("class");
      delete document.documentElement.dataset.appTheme;
    }
    persistWebPreferencesMock.mockReset();
    syncWebPreferencesToCloudMock.mockReset();
    migrationResultHolder.current.migrated = null;
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("class");
      delete document.documentElement.dataset.appTheme;
    }
  });

  it("replays the migrated preference through WebPreferences persistence and cloud sync exactly once", async () => {
    setMigrationResult("system");

    render(<Harness />);

    await waitFor(() => {
      expect(persistWebPreferencesMock).toHaveBeenCalledTimes(1);
    });
    expect(syncWebPreferencesToCloudMock).toHaveBeenCalledTimes(1);

    const persistedPayload = persistWebPreferencesMock.mock.calls[0][0];
    expect(persistedPayload.theme).toBe("system");
  });

  it("replays a migrated preference only once in Strict Mode", async () => {
    setMigrationResult("dark");

    render(<StrictHarness />);

    await waitFor(() => {
      expect(persistWebPreferencesMock).toHaveBeenCalledTimes(1);
    });
    expect(syncWebPreferencesToCloudMock).toHaveBeenCalledTimes(1);
  });

  it("does not trigger persistence or cloud sync when no migration happened", async () => {
    setMigrationResult(null);

    render(<Harness />);

    // Give the provider a tick to ensure the effect ran (or didn't).
    await waitFor(() => {
      const node = document.querySelector('[data-testid="inspector"]');
      expect(node).not.toBeNull();
    });
    expect(persistWebPreferencesMock).not.toHaveBeenCalled();
    expect(syncWebPreferencesToCloudMock).not.toHaveBeenCalled();
  });
});
