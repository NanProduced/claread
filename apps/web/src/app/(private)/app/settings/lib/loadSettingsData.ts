import "server-only";

import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import { getProfileSettings } from "@/services/bff/profile";
import type { AccountData, PreferencesData } from "../sections/SettingsSectionContent";

export interface SettingsData {
  accountData: AccountData;
  preferencesData: PreferencesData;
}

/**
 * Shared server loader for Settings data.
 * Used by both the fallback page (`/app/settings`) and the intercepted
 * dialog route (`@settings/(.)settings`). Does not alter any API contract —
 * it only restructures the derivation logic that previously lived inline
 * in `page.tsx`.
 */
export async function loadSettingsData(): Promise<SettingsData> {
  const settings = await getProfileSettings();

  const displayName = settings.profile?.nickname || settings.session.phone || "Web User";
  const realNickname = settings.profile?.nickname || "";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";

  const readingDefaults = readReadingDefaultsFromSettings(settings.profile?.settings);
  const canEditSharedDefaults = settings.status === "ready";

  return {
    accountData: {
      nickname: realNickname,
      displayFallback: displayName,
      phone: settings.session.phone,
      status: settings.status,
      avatarText,
    },
    preferencesData: {
      readingGoal: readingDefaults.readingGoal,
      readingVariant: readingDefaults.readingVariant,
      canEdit: canEditSharedDefaults,
    },
  };
}
