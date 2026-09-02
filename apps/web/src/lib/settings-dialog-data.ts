import type { ReadingDefaultState } from "@/lib/reading-defaults";
import type { ProfileBffStatus } from "@/services/bff/profile";

/**
 * Minimal, UI-independent DTO for the AppShell Settings Dialog data layer.
 *
 * These types are owned by the data layer (`lib/` + `services/bff/`) and
 * intentionally do NOT import from any React / route-intercept / Settings
 * section component module. They reference only non-UI foundational types:
 *   - `ReadingDefaultState`  (`lib/reading-defaults.ts`, pure TS)
 *   - `ProfileBffStatus`     (`services/bff/profile.ts`, pure TS service)
 *
 * Field shapes are kept compatible with what the existing Settings section
 * components render, but the data layer is the source of truth for this DTO;
 * future Settings Dialog consumers (e.g. the AppShell provider) should depend
 * on this type, not on Settings section view models.
 *
 * This DTO deliberately excludes quota / ledger / subscription fields — the
 * Settings Dialog must not trigger a quota request just to render the
 * "用量与积分" placeholder section.
 */

export interface SettingsDialogAccountData {
  nickname: string;
  displayFallback: string;
  status: ProfileBffStatus;
  avatarText: string;
}

export interface SettingsDialogPreferencesData extends ReadingDefaultState {
  canEdit: boolean;
}

export interface SettingsDialogData {
  accountData: SettingsDialogAccountData;
  preferencesData: SettingsDialogPreferencesData;
}
