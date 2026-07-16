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
 * values (including the retired "paper" sentinel) fall back to "system".
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

// --- Legacy Reader theme migration ---------------------------------------

/**
 * Legacy Reader-only theme storage key. The Reader sub-system previously
 * persisted its own theme here (`paper` | `light` | `dark`). Only the
 * appearance domain is permitted to reference this constant; Reader
 * components must not read or write it. After migration the key is
 * always cleared so Reader code never touches it again.
 */
export const LEGACY_READER_THEME_STORAGE_KEY = "claread.reader.themeName";

/**
 * Result of attempting to migrate the legacy Reader theme key. `migrated`
 * is `null` when no migration happened (no legacy key, invalid value,
 * or an existing valid global preference was preserved). When `migrated`
 * is non-null it carries the ThemePreference value that was written into
 * the global theme storage, so the caller can run the same persistence
 * and cloud-sync path it uses for explicit preference changes.
 */
export interface LegacyReaderThemeMigrationResult {
  migrated: ThemePreference | null;
}

function isValidThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

/**
 * Map a legacy Reader-only theme value to a global ThemePreference.
 * The retired "paper" sentinel maps to "system"; "light" / "dark" map
 * to themselves. Invalid values return null so callers know not to
 * override an existing global preference.
 */
export function mapLegacyReaderThemeValue(value: unknown): ThemePreference | null {
  if (value === "paper") return "system";
  if (value === "light") return "light";
  if (value === "dark") return "dark";
  return null;
}

/**
 * Migrate the legacy Reader-only theme storage key into the global theme
 * preference storage. The mapped value (paper -> system, light -> light,
 * dark -> dark) is written to the global theme storage ONLY when it does
 * not already hold a valid preference. If a valid global preference
 * already exists, the legacy value is ignored. The legacy key is always
 * cleared afterwards so Reader code never reads or writes it again.
 *
 * This must run before the theme provider initializes so the migrated
 * value is picked up on first paint.
 *
 * Returns a {@link LegacyReaderThemeMigrationResult} describing whether a
 * migration actually happened and, if so, which ThemePreference value was
 * persisted. Callers that want to keep WebPreferences in sync should only
 * trigger their persistence/cloud-sync path when `migrated` is non-null.
 */
export function migrateLegacyReaderThemeStorage(): LegacyReaderThemeMigrationResult {
  if (typeof window === "undefined") {
    return { migrated: null };
  }
  try {
    const legacyRaw = window.localStorage.getItem(
      LEGACY_READER_THEME_STORAGE_KEY,
    );
    if (legacyRaw === null) {
      return { migrated: null };
    }

    const mapped = mapLegacyReaderThemeValue(legacyRaw);
    const globalRaw = window.localStorage.getItem(THEME_STORAGE_KEY);
    const globalValid = isValidThemePreference(globalRaw);

    let migrated: ThemePreference | null = null;
    if (mapped !== null && !globalValid) {
      window.localStorage.setItem(THEME_STORAGE_KEY, mapped);
      migrated = mapped;
    }

    window.localStorage.removeItem(LEGACY_READER_THEME_STORAGE_KEY);
    return { migrated };
  } catch {
    return { migrated: null };
  }
}
