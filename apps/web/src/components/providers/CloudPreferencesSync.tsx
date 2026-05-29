"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "next-themes";

import { readStoredReaderSettings } from "@/components/reader/settings/shared";
import { readWebPreferencesFromSettings, type WebPreferences } from "@/lib/web-preferences";
import {
  applyCloudReaderSettingsLocally,
  applyCloudThemeLocally,
  isWebPreferencesSyncReady,
  markWebPreferencesSyncReady,
  notifyWebPreferencesApplied,
} from "@/lib/web-preferences-sync";
import { isWebPreferencesNewer, persistWebPreferences, readStoredWebPreferences } from "@/lib/web-preferences";

export function CloudPreferencesSync() {
  const { setTheme } = useTheme();
  const applied = useRef(false);

  useEffect(() => {
    if (applied.current || isWebPreferencesSyncReady()) return;
    applied.current = true;

    async function loadAndApply() {
      let cloudPreferences: WebPreferences | null = null;
      let appliedCloudPreferences = false;

      try {
        const res = await fetch("/api/web/profile", {
          method: "GET",
          credentials: "include",
        });

        if (!res.ok) return;

        const data = await res.json();
        cloudPreferences = readWebPreferencesFromSettings(
          data?.settings && typeof data.settings === "object"
            ? (data.settings as Record<string, unknown>)
            : null,
        );

        if (!cloudPreferences?.updated_at) return;

        const localPreferences = readStoredWebPreferences();
        const localUpdatedAt = localPreferences?.updated_at ?? readStoredReaderSettings().updatedAt;
        if (!isWebPreferencesNewer(cloudPreferences, localUpdatedAt)) return;

        applyCloudThemeLocally(cloudPreferences.theme);
        setTheme(cloudPreferences.theme);
        applyCloudReaderSettingsLocally(cloudPreferences);
        persistWebPreferences(cloudPreferences);
        notifyWebPreferencesApplied(cloudPreferences);
        appliedCloudPreferences = true;
      } catch {
      } finally {
        if (cloudPreferences && appliedCloudPreferences) {
          persistWebPreferences(cloudPreferences);
        }
        markWebPreferencesSyncReady();
      }
    }

    loadAndApply();
  }, [setTheme]);

  return null;
}
