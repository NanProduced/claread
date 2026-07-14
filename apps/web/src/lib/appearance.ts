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
 * "system". `ThemeName = "paper" | "light" | "dark"` is retained
 * solely for the Reader sub-system, which is out of scope for this
 * refactor; the app-shell appearance code does not consume it.
 */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

/**
 * @deprecated Use `ThemePreference` for app-shell preference APIs and
 * `ResolvedTheme` for visual consumers. `ThemeName` remains only because
 * the Reader sub-system still uses it; this module is the single
 * declared location, but new callers must not consume it.
 */
export type ThemeName = "paper" | "light" | "dark";

export const THEME_STORAGE_KEY = "claread.theme.v1";
export const LEGACY_APPEARANCE_STORAGE_KEY = "claread.appearance.v1";

export const themeColorByTheme: Record<ResolvedTheme, string> = {
  light: "#f7f5f0",
  dark: "#121518",
};

/**
 * Normalize an arbitrary stored value into a ThemePreference. The legacy
 * "paper" sentinel is rejected as an invalid value rather than folded
 * into a working option. The legacy "system" preference (when this
 * domain model did not yet exist) maps onto the new "system" preference.
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

/**
 * @deprecated Reader-only helper. New code must use
 * `normalizeThemePreference` / `resolveThemePreference`.
 */
export function normalizeThemeName(value: unknown): ThemeName {
  if (value === "paper" || value === "light" || value === "dark") {
    return value;
  }
  return "light";
}

/**
 * @deprecated Reader-only helper that maps a stored legacy appearance
 * ("system" | "light" | "dark" | anything) into a Reader ThemeName.
 * App-shell consumers must call `resolveThemePreference`.
 */
export function migrateLegacyAppearanceTheme(
  value: unknown,
  resolvedTheme: ThemeName = "light",
): ThemeName {
  if (value === "paper" || value === "light" || value === "dark") {
    return value;
  }
  return resolvedTheme === "dark" ? "dark" : "light";
}

export function themeColorForTheme(
  value: ResolvedTheme | null | undefined,
): string {
  if (value === "dark") return themeColorByTheme.dark;
  return themeColorByTheme.light;
}
