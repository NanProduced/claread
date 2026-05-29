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
  normalizeThemeName,
  themeColorForTheme,
  type ThemeName,
} from "@/lib/appearance";
import {
  buildWebPreferencesFromLocal,
  syncWebPreferencesToCloud,
} from "@/lib/web-preferences-sync";
import { persistWebPreferences } from "@/lib/web-preferences";

interface AppearanceContextValue {
  themeName: ThemeName;
  setThemeName: (value: ThemeName) => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

function ThemeColorSync() {
  const { resolvedTheme, theme } = useTheme();

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

    const nextTheme = normalizeThemeName(resolvedTheme ?? theme);
    const content = themeColorForTheme(nextTheme);
    let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');

    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.appendChild(meta);
    }

    meta.content = content;
    document.documentElement.dataset.appTheme = nextTheme;
  }, [resolvedTheme, theme]);

  return null;
}

function AppearanceContextBridge({ children }: { children: React.ReactNode }) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true); // eslint-disable-line react-hooks/set-state-in-effect
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    try {
      const nextStoredTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (nextStoredTheme) return;

      const legacyStoredTheme = window.localStorage.getItem(LEGACY_APPEARANCE_STORAGE_KEY);
      if (!legacyStoredTheme) return;

      const migratedTheme = migrateLegacyAppearanceTheme(
        legacyStoredTheme,
        typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light",
      );

      setTheme(migratedTheme);
    } catch {}
  }, [setTheme]);

  const value = useMemo<AppearanceContextValue>(
    () => ({
      themeName: mounted ? normalizeThemeName(theme) : "paper",
      setThemeName: (next) => {
        setTheme(next);
        try {
          const local = buildWebPreferencesFromLocal();
          local.theme = next;
          local.updated_at = new Date().toISOString();
          persistWebPreferences(local);
          syncWebPreferencesToCloud(local);
        } catch {}
      },
    }),
    [mounted, setTheme, theme],
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
      defaultTheme="paper"
      enableSystem={false}
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
