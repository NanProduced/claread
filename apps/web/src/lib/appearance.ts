/**
 * Web appearance domain model.
 *
 * The user-facing preference is one of three values:
 *   - "system": follow the OS prefers-color-scheme
 *   - "light": force Light at all times
 *   - "dark": force Dark at all times
 *
 * The theme actually painted on screen is always one of two values
 * (ResolvedTheme = "light" | "dark"); "system" is a preference mode,
 * not a third token set. CSS, Tailwind, dataset attributes, and any
 * visual consumer must read ResolvedTheme only — never the string
 * "system".
 *
 * AppearanceProvider is the single theme owner for the whole Web app,
 * including every Reader page. Reader sub-systems must not hold their
 * own theme state, themeName field, or theme localStorage.
 *
 * The global theme storage key is `claread.theme.v1`; no legacy
 * Reader-only theme storage is read, written, or migrated.
 */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "claread.theme.v1";

export const themeColorByTheme: Record<ResolvedTheme, string> = {
  light: "#f8f8f8",
  dark: "#161616",
};

/**
 * Normalize an arbitrary stored value into a ThemePreference. Invalid
 * values fall back to "system".
 */
export function normalizeThemePreference(value: unknown): ThemePreference {
  if (value === "system") return "system";
  if (value === "light") return "light";
  if (value === "dark") return "dark";
  return "system";
}

/**
 * Resolve a ThemePreference against an explicit OS preference
 * ("light" | "dark"). Callers that need to read prefers-color-scheme
 * from the DOM should pass a snapshot taken at the time of resolution
 * (this keeps the function pure and SSR-safe).
 */
export function resolveThemePreference(
  preference: ThemePreference,
  osTheme: ResolvedTheme,
): ResolvedTheme {
  if (preference === "light" || preference === "dark") return preference;
  return osTheme;
}

export function themeColorForTheme(
  value: ResolvedTheme | null | undefined,
): string {
  if (value === "dark") return themeColorByTheme.dark;
  return themeColorByTheme.light;
}
