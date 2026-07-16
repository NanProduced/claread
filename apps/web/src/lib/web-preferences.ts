import type { ThemePreference } from "@/lib/appearance";

export type WebThemePreference = ThemePreference;
export type WebReaderMode = "intensive" | "immersive";
export type WebFontFamily = "editorial" | "book" | "sans";
export type WebFontScale = "sm" | "md" | "lg";

export const WEB_PREFERENCES_STORAGE_KEY = "claread.web.preferences.v1";
export const WEB_PREFERENCES_APPLIED_EVENT = "claread:web-preferences-applied";
export const WEB_PREFERENCES_SYNC_READY_EVENT = "claread:web-preferences-sync-ready";

export interface WebPreferences {
  theme: WebThemePreference;
  reader_mode: WebReaderMode;
  font_family: WebFontFamily;
  font_scale: WebFontScale;
  updated_at: string;
}

const VALID_THEMES: readonly WebThemePreference[] = ["system", "light", "dark"];
const VALID_READER_MODES: readonly WebReaderMode[] = ["intensive", "immersive"];
const VALID_FONT_FAMILIES: readonly WebFontFamily[] = ["editorial", "book", "sans"];
const VALID_FONT_SCALES: readonly WebFontScale[] = ["sm", "md", "lg"];

/** Default preference is "system" — Light/Dark is resolved at render time. */
export function createDefaultWebPreferences(): WebPreferences {
  return {
    theme: "system",
    reader_mode: "intensive",
    font_family: "editorial",
    font_scale: "md",
    updated_at: "",
  };
}

function isValidEnum<T extends string>(value: unknown, valid: readonly T[]): value is T {
  return typeof value === "string" && (valid as readonly string[]).includes(value);
}

export function normalizeWebPreferences(raw: unknown): WebPreferences {
  const defaults = createDefaultWebPreferences();
  if (!raw || typeof raw !== "object") return defaults;

  const payload = raw as Record<string, unknown>;
  return {
    theme: isValidEnum(payload.theme, VALID_THEMES) ? payload.theme : defaults.theme,
    reader_mode: isValidEnum(payload.reader_mode, VALID_READER_MODES) ? payload.reader_mode : defaults.reader_mode,
    font_family: isValidEnum(payload.font_family, VALID_FONT_FAMILIES) ? payload.font_family : defaults.font_family,
    font_scale: isValidEnum(payload.font_scale, VALID_FONT_SCALES) ? payload.font_scale : defaults.font_scale,
    updated_at: typeof payload.updated_at === "string" ? payload.updated_at : "",
  };
}

export function readStoredWebPreferences(): WebPreferences | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(WEB_PREFERENCES_STORAGE_KEY);
    if (!raw) return null;
    return normalizeWebPreferences(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function persistWebPreferences(value: WebPreferences): void {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(WEB_PREFERENCES_STORAGE_KEY, JSON.stringify(normalizeWebPreferences(value)));
  } catch {}
}

export function readWebPreferencesFromSettings(
  settings: Record<string, unknown> | null | undefined,
): WebPreferences | null {
  if (!settings || typeof settings !== "object") return null;
  const raw = settings.web_preferences;
  if (!raw || typeof raw !== "object") return null;
  return normalizeWebPreferences(raw);
}

export function isWebPreferencesNewer(cloud: WebPreferences, localUpdatedAt?: string): boolean {
  if (!cloud.updated_at) return false;
  if (!localUpdatedAt) return true;
  return new Date(cloud.updated_at).getTime() >= new Date(localUpdatedAt).getTime();
}
