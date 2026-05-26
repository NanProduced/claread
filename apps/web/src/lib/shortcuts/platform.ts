/**
 * Platform detection utilities for keyboard shortcut handling.
 */

export function isMac(): boolean {
  if (typeof navigator === "undefined") return false;

  // Prefer userAgentData if available (modern browsers)
  if ("userAgentData" in navigator) {
    const uaData = navigator.userAgentData as { platform?: string };
    if (uaData?.platform) {
      return uaData.platform.toLowerCase().includes("mac");
    }
  }

  return /mac|iphone|ipad|ipod/i.test(navigator.platform ?? "");
}

export type PrimaryModifier = "meta" | "ctrl";

export function getPrimaryModifier(): PrimaryModifier {
  return isMac() ? "meta" : "ctrl";
}
