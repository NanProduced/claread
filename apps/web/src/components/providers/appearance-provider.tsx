"use client";

import { createContext, useContext, useEffect, useMemo } from "react";
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
  APPEARANCE_STORAGE_KEY,
  normalizeAppearance,
  themeColorForAppearance,
  type AppearanceState,
  type ResolvedAppearanceState,
} from "@/lib/appearance";

interface AppearanceContextValue {
  appearance: AppearanceState;
  resolvedAppearance: ResolvedAppearanceState;
  isSystem: boolean;
  setAppearance: (value: AppearanceState) => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

function ThemeColorSync() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

    const content = themeColorForAppearance(resolvedTheme as ResolvedAppearanceState | undefined);
    let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');

    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.appendChild(meta);
    }

    meta.content = content;
  }, [resolvedTheme]);

  return null;
}

function AppearanceContextBridge({ children }: { children: React.ReactNode }) {
  const { theme, resolvedTheme, setTheme } = useTheme();

  const value = useMemo<AppearanceContextValue>(
    () => ({
      appearance: normalizeAppearance(theme),
      resolvedAppearance:
        resolvedTheme === "dark" ? "dark" : "light",
      isSystem: normalizeAppearance(theme) === "system",
      setAppearance: (next) => setTheme(next),
    }),
    [resolvedTheme, setTheme, theme],
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
      storageKey={APPEARANCE_STORAGE_KEY}
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
