import { normalizeThemeName, THEME_STORAGE_KEY } from "@/lib/appearance";
import {
  createDefaultReaderSettings,
  persistReaderSettings,
  readStoredReaderSettings,
  type ReaderSettingsState,
} from "@/components/reader/settings/shared";
import {
  createDefaultWebPreferences,
  persistWebPreferences,
  readStoredWebPreferences,
  type WebPreferences,
  WEB_PREFERENCES_APPLIED_EVENT,
  WEB_PREFERENCES_SYNC_READY_EVENT,
} from "@/lib/web-preferences";

let _syncTimer: ReturnType<typeof setTimeout> | null = null;
let _cloudSyncReady = false;

const SYNC_DEBOUNCE_MS = 500;

function dispatchWindowEvent(name: string, detail?: unknown): void {
  if (typeof window === "undefined") return;

  if (typeof detail === "undefined") {
    window.dispatchEvent(new Event(name));
    return;
  }

  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function markWebPreferencesSyncReady(): void {
  _cloudSyncReady = true;
  dispatchWindowEvent(WEB_PREFERENCES_SYNC_READY_EVENT);
}

export function isWebPreferencesSyncReady(): boolean {
  return _cloudSyncReady;
}

export function notifyWebPreferencesApplied(prefs: WebPreferences): void {
  dispatchWindowEvent(WEB_PREFERENCES_APPLIED_EVENT, prefs);
}

export function syncWebPreferencesToCloud(prefs: WebPreferences): void {
  persistWebPreferences(prefs);

  if (_syncTimer) clearTimeout(_syncTimer);

  _syncTimer = setTimeout(async () => {
    try {
      const res = await fetch("/api/web/profile", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ settings: { web_preferences: prefs } }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        console.warn("[web-preferences-sync] Cloud sync failed:", data.message || res.status);
      }
    } catch {
      console.warn("[web-preferences-sync] Cloud sync network error");
    }
  }, SYNC_DEBOUNCE_MS);
}

export function applyCloudThemeLocally(theme: WebPreferences["theme"]): void {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {}
}

export function applyCloudReaderSettingsLocally(prefs: WebPreferences): ReaderSettingsState {
  const merged: ReaderSettingsState = {
    mode: prefs.reader_mode,
    fontFamily: prefs.font_family,
    fontScale: prefs.font_scale,
    updatedAt: prefs.updated_at || undefined,
  };

  persistReaderSettings(merged);
  return merged;
}

export function buildWebPreferencesFromLocal(): WebPreferences {
  const defaults = createDefaultWebPreferences();
  let theme: WebPreferences["theme"] = defaults.theme;
  let readerSettings: ReaderSettingsState = createDefaultReaderSettings();
  let updatedAt = "";

  if (typeof window !== "undefined") {
    try {
      const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
      theme = normalizeThemeName(storedTheme);
    } catch {}

    try {
      readerSettings = readStoredReaderSettings();
    } catch {}

    const storedWebPreferences = readStoredWebPreferences();
    updatedAt = storedWebPreferences?.updated_at ?? readerSettings.updatedAt ?? "";
  }

  return {
    theme,
    reader_mode: readerSettings.mode,
    font_family: readerSettings.fontFamily,
    font_scale: readerSettings.fontScale,
    updated_at: updatedAt,
  };
}
