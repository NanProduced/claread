import { getPrimaryModifier } from "./platform";

/**
 * Match a keyboard event against a semantic shortcut string.
 *
 * Semantic format: "Primary+K", "Primary+Enter", "Escape", etc.
 * "Primary" maps to Meta on macOS, Ctrl on Windows/Linux.
 */
export function matchShortcut(event: KeyboardEvent, shortcut: string): boolean {
  const parts = shortcut.split("+");
  const key = parts[parts.length - 1];
  const modifiers = parts.slice(0, -1);

  const primary = getPrimaryModifier();
  const needsPrimary = modifiers.includes("Primary");

  if (needsPrimary) {
    if (primary === "meta" && !event.metaKey) return false;
    if (primary === "ctrl" && !event.ctrlKey) return false;
  }

  // Ensure no extra modifiers are pressed that weren't specified
  if (!needsPrimary) {
    if (event.metaKey || event.ctrlKey) return false;
  }
  if (event.shiftKey) return false;
  if (event.altKey) return false;

  return event.key.toLowerCase() === key.toLowerCase();
}
