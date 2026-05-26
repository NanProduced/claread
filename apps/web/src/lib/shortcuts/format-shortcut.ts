import { isMac } from "./platform";

/**
 * Format a semantic shortcut string for display.
 *
 * Semantic format: "Primary+K", "Primary+Enter", etc.
 * "Primary" maps to ⌘ on macOS, Ctrl on Windows/Linux.
 *
 * Examples:
 *   formatShortcut("Primary+K") → "⌘K" (macOS) | "Ctrl+K" (Windows)
 *   formatShortcut("Escape")    → "Esc"
 */
export function formatShortcut(shortcut: string): string {
  const mac = isMac();
  const replaced = shortcut
    .replace("Primary", mac ? "⌘" : "Ctrl")
    .replace("Escape", "Esc");
  // On Mac, ⌘K reads naturally without +; on Windows, Ctrl+K needs the +
  return mac ? replaced.replace(/\+/g, "") : replaced;
}
