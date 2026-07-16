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
  normalizeThemePreference,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type ThemePreference,
  themeColorForTheme,
} from "@/lib/appearance";
import {
  buildWebPreferencesFromLocal,
  syncWebPreferencesToCloud,
} from "@/lib/web-preferences-sync";
import {
  persistWebPreferences,
} from "@/lib/web-preferences";

/**
 * The app-shell contract: `themePreference` is the user's choice
 * ("system" | "light" | "dark"); `resolvedTheme` is the visual contract
 * applied to CSS / Tailwind / dataset attributes ("light" | "dark").
 * Setting `themePreference` persists into the cloud preferences payload
 * alongside the rest of WebPreferences.
 *
 * AppearanceProvider is the single theme owner for the whole Web app,
 * including every Reader page. Reader sub-systems consume
 * `themePreference` / `resolvedTheme` / `setThemePreference` only.
 */
interface AppearanceContextValue {
  themePreference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  setThemePreference: (value: ThemePreference) => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

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

/**
 * Persist a theme preference into the local WebPreferences payload and
 * trigger the cloud sync path. Used for explicit preference changes
 * (via `setThemePreference`) so the local payload and cloud profile
 * stay in sync.
 */
function persistThemePreference(next: ThemePreference) {
  try {
    const local = buildWebPreferencesFromLocal();
    local.theme = next;
    local.updated_at = new Date().toISOString();
    persistWebPreferences(local);
    syncWebPreferencesToCloud(local);
  } catch {}
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

  const applyPreference = (next: ThemePreference) => {
    setTheme(next);
    persistThemePreference(next);
  };

  const value = useMemo<AppearanceContextValue>(
    () => ({
      themePreference: preferenceCurrent,
      resolvedTheme: resolvedCurrent,
      setThemePreference: applyPreference,
    }),
    [preferenceCurrent, resolvedCurrent],
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
