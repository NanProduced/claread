/**
 * Minimal focus restoration helper for the AppShell Settings Dialog.
 *
 * On close, the Provider restores focus in this priority:
 *   1. The opener element captured when the dialog was opened, IF it is
 *      still connected to the document (regardless of visibility).
 *   2. A visible, focusable `[data-mobile-user-menu-trigger="true"]`.
 *   3. A visible, focusable `[data-desktop-user-menu-trigger="true"]`.
 *   4. `null` — let Radix Dialog's default behavior handle focus.
 *
 * This helper is the AppShell Settings Dialog''s single source of truth for
 * focus restoration (captured opener → mobile user trigger → desktop user
 * trigger → Radix default).
 */

/** Whether an element and all its ancestors are actually visible. */
function isElementVisible(el: HTMLElement): boolean {
  let current: Element | null = el;
  while (current) {
    if (current instanceof HTMLElement && current.hidden) {
      return false;
    }
    const style = getComputedStyle(current);
    if (style.display === "none") return false;
    if (style.visibility === "hidden" || style.visibility === "collapse") {
      return false;
    }
    current = current.parentElement;
  }
  return true;
}

/**
 * Find a visible, focusable user-menu trigger to restore focus to.
 * Returns `null` when no candidate is connected.
 */
export function querySettingsDialogFocusFallback(): HTMLElement | null {
  const selectors = [
    '[data-mobile-user-menu-trigger="true"]',
    '[data-desktop-user-menu-trigger="true"]',
  ];

  for (const selector of selectors) {
    const el = document.querySelector<HTMLElement>(selector);
    if (!el || !el.isConnected) continue;

    // Skip disabled form controls; anchors are never "disabled".
    if (
      (el instanceof HTMLButtonElement ||
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement) &&
      el.disabled
    ) {
      continue;
    }

    // Reject candidates hidden by themselves OR by any ancestor.
    if (!isElementVisible(el)) continue;

    return el;
  }

  return null;
}

/**
 * Restore focus after the Settings Dialog closes.
 *
 * Priority: opener (if connected) → mobile user trigger → desktop user
 * trigger → no-op (Radix default).
 *
 * Returns `true` when focus was explicitly moved (so the caller can
 * suppress Radix's default focus behavior); `false` otherwise.
 */
export function restoreSettingsDialogFocus(
  opener: HTMLElement | null,
): boolean {
  if (opener && opener.isConnected) {
    opener.focus();
    return true;
  }
  const fallback = querySettingsDialogFocusFallback();
  if (fallback) {
    fallback.focus();
    return true;
  }
  return false;
}

/**
 * Capture the element that currently has focus, if any.
 * Used by the Provider's `openSettings` action.
 */
export function captureSettingsDialogOpener(): HTMLElement | null {
  if (
    typeof document !== "undefined" &&
    document.activeElement instanceof HTMLElement &&
    document.activeElement !== document.body
  ) {
    return document.activeElement;
  }
  return null;
}
