export type ThemeName = "paper" | "light" | "dark";

export const THEME_STORAGE_KEY = "claread.theme.v1";
export const LEGACY_APPEARANCE_STORAGE_KEY = "claread.appearance.v1";

export const themeColorByTheme: Record<ThemeName, string> = {
  paper: "#f3efe6",
  light: "#f7f5f0",
  dark: "#121518",
};

export function normalizeThemeName(value: unknown): ThemeName {
  if (value === "paper" || value === "light" || value === "dark") {
    return value;
  }

  if (value === "system") {
    return "paper";
  }

  return "paper";
}

export function migrateLegacyAppearanceTheme(
  value: unknown,
  resolvedTheme: ThemeName = "paper",
): ThemeName {
  if (value === "light" || value === "dark") {
    return value;
  }

  if (value === "system") {
    return resolvedTheme === "dark" ? "dark" : "light";
  }

  return "paper";
}

export function themeColorForTheme(
  value: ThemeName | null | undefined,
): string {
  if (!value) {
    return themeColorByTheme.paper;
  }

  return themeColorByTheme[normalizeThemeName(value)];
}
