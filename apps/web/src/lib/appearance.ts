export type AppearanceState = "light" | "dark" | "system";
export type ResolvedAppearanceState = Exclude<AppearanceState, "system">;

export const APPEARANCE_STORAGE_KEY = "claread.appearance.v1";

export const appearanceThemeColor: Record<ResolvedAppearanceState, string> = {
  light: "#f7f6f2",
  dark: "#181713",
};

export function normalizeAppearance(value: unknown): AppearanceState {
  if (value === "light" || value === "dark" || value === "system") {
    return value;
  }
  return "system";
}

export function themeColorForAppearance(
  value: AppearanceState | ResolvedAppearanceState | null | undefined,
): string {
  if (value === "dark") {
    return appearanceThemeColor.dark;
  }
  return appearanceThemeColor.light;
}
