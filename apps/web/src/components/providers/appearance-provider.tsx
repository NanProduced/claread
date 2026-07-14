"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { ThemeProvider, useTheme } from "next-themes";

/**
 * Suppress React 19 "Encountered a script tag" dev warning from next-themes.
 * next-themes injects an inline <script> for FOUC prevention; React 19 warns
 * about script tags inside client components. The script works correctly;
 * only the console noise is filtered. See: https://github.com/pacocoursey/next-themes/issues/337
 */
if (typeof window !== "undefined" && process.env.NODE_ENV === "development") {
  const _origError = console.error;
  console.error = (...args: unknown[]) => {
    if (
      typeof args[0] === "string" &&
      args[0].includes("Encountered a script tag")
    ) {
      return;
    }
    _origError.apply(console, args);
  };
}

import {
  LEGACY_APPEARANCE_STORAGE_KEY,
  THEME_STORAGE_KEY,
  migrateLegacyAppearanceTheme,
  normalizeThemePreference,
  type ResolvedTheme,
  type ThemeName,
  type ThemePreference,
  themeColorForTheme,
} from "@/lib/appearance";
import {
  buildWebPreferencesFromLocal,
  syncWebPreferencesToCloud,
} from "@/lib/web-preferences-sync";
import {
  normalizeWebPreferences,
  persistWebPreferences,
  WEB_PREFERENCES_STORAGE_KEY,
} from "@/lib/web-preferences";

/**
 * The app-shell contract: `themePreference` is the user's choice
 * ("system" | "light" | "dark"); `resolvedTheme` is the visual contract
 * applied to CSS / Tailwind / dataset attributes ("light" | "dark").
 * Setting `themePreference` persists into the cloud preferences payload
 * alongside the rest of WebPreferences. Reader-internal `ThemeName`
 * (paper|light|dark) callers continue to use the legacy `themeName`
 * field; that API is stable because the Reader sub-system is out of
 * scope for this refactor.
 */
interface AppearanceContextValue {
  themePreference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  /**
   * Reader compatibility projection. Although its legacy type still admits
   * `paper`, this provider only emits the resolved `light` or `dark` value.
   */
  themeName: ThemeName;
  setThemePreference: (value: ThemePreference) => void;
  /**
   * Reader-internal compatibility setter. A legacy `paper` input selects the
   * system preference; it never re-enables a Paper visual theme.
   */
  setThemeName: (value: ThemeName) => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

function readSystemOsTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function ThemeColorSync() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

    const nextResolved = normalizeResolvedTheme(resolvedTheme);
    const content = themeColorForTheme(nextResolved);
    let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');

    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.appendChild(meta);
    }

    meta.content = content;
    /**
     * dataset.appTheme carries ONLY resolved light/dark — never "system"
     * or any preference-mode value. Visual consumers must read this
     * attribute or the `resolvedTheme` from context, not the preference.
     */
    document.documentElement.dataset.appTheme = nextResolved;
  }, [resolvedTheme]);

  return null;
}

export function normalizeResolvedTheme(value: unknown): ResolvedTheme {
  return value === "dark" ? "dark" : "light";
}

function AppearanceContextBridge({ children }: { children: React.ReactNode }) {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true); // eslint-disable-line react-hooks/set-state-in-effect
  }, []);

  const resolvedCurrent = useMemo<ResolvedTheme>(() => {
    if (!mounted) {
      return "light";
    }
    return normalizeResolvedTheme(resolvedTheme ?? theme);
  }, [mounted, resolvedTheme, theme]);

  const preferenceCurrent = useMemo<ThemePreference>(() => {
    if (!mounted) {
      return "system";
    }
    return normalizeThemePreference(theme);
  }, [mounted, theme]);

  /**
   * Reader still consumes the legacy `ThemeName` type, but the app-shell
   * never emits its retired `paper` member. `system` resolves to the same
   * light/dark value used by every other visual consumer.
   */
  const themeNameCurrent = resolvedCurrent as ThemeName;

  const applyThemeName = (next: ThemeName) => {
    const mapped: ThemePreference =
      next === "dark"
        ? "dark"
        : next === "light"
          ? "light"
          : "system";
    setTheme(mapped);

    try {
      const local = buildWebPreferencesFromLocal();
      local.theme = mapped;
      local.updated_at = new Date().toISOString();
      persistWebPreferences(local);
      syncWebPreferencesToCloud(local);
    } catch {}
  };

  const applyPreference = (next: ThemePreference) => {
    setTheme(next);

    try {
      const local = buildWebPreferencesFromLocal();
      local.theme = next;
      local.updated_at = new Date().toISOString();
      persistWebPreferences(local);
      syncWebPreferencesToCloud(local);
    } catch {}
  };

  const value = useMemo<AppearanceContextValue>(
    () => ({
      themePreference: preferenceCurrent,
      resolvedTheme: resolvedCurrent,
      themeName: themeNameCurrent,
      setThemePreference: applyPreference,
      setThemeName: applyThemeName,
    }),
    [preferenceCurrent, resolvedCurrent, themeNameCurrent],
  );

  return (
    <AppearanceContext.Provider value={value}>
      <ThemeColorSync />
      {children}
    </AppearanceContext.Provider>
  );
}

export function AppearanceProvider({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      storageKey={THEME_STORAGE_KEY}
    >
      <AppearanceContextBridge>{children}</AppearanceContextBridge>
    </ThemeProvider>
  );
}

export function useAppearance() {
  const value = useContext(AppearanceContext);

  if (!value) {
    throw new Error("useAppearance must be used within AppearanceProvider");
  }

  return value;
}
