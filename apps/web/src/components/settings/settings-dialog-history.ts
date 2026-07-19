/**
 * Pure history-state machine for the AppShell Settings Dialog.
 *
 * Contract:
 *   - Marker key is namespaced as `clareadSettingsDialog` so it does not
 *     collide with host page / Next.js history state.
 *   - Marker shape: `{ version: 1, section: SettingsSection }`.
 *   - Open uses `history.pushState` so Back/Forward can close the dialog.
 *   - Section switch uses `history.replaceState` (no history accumulation).
 *   - Close calls `history.back()` ONLY when the current entry was created
 *     by us (i.e. owns a marker); otherwise it returns `'local-only'` and
 *     the caller closes local UI without disturbing host page history.
 *   - Every action passes `location.href` as the URL so pathname / search /
 *     hash are preserved verbatim.
 *   - Every action merges with the existing `history.state` object so
 *     other fields (Next internal key, host page state) survive.
 *
 * `pushState` / `replaceState` themselves do NOT fire `popstate`. The
 * Provider must update its local React state directly after calling these
 * helpers. `popstate` is the ONLY Back/Forward sync entry point.
 */

export type SettingsSection = "account" | "preferences" | "usage" | "support";

/** Namespaced key under which the Settings Dialog marker lives in history.state. */
export const HISTORY_MARKER_KEY = "clareadSettingsDialog" as const;

/** Marker version. Bumped only on incompatible marker shape changes. */
export const HISTORY_MARKER_VERSION = 1 as const;

export interface SettingsDialogMarker {
  version: typeof HISTORY_MARKER_VERSION;
  section: SettingsSection;
}

const VALID_SECTIONS: ReadonlyArray<SettingsSection> = [
  "account",
  "preferences",
  "usage",
  "support",
];

/**
 * Parse the `?section=` query param (or any string) into a valid section.
 * Falls back to `"preferences"` for null / undefined / invalid values —
 * matching the legacy `parseSettingsSection` semantics used by the route
 * intercept so the open/close contract stays identical.
 */
export function parseSettingsSection(
  value: string | null | undefined,
): SettingsSection {
  if (value && VALID_SECTIONS.includes(value as SettingsSection)) {
    return value as SettingsSection;
  }
  return "preferences";
}

function isSettingsSection(value: unknown): value is SettingsSection {
  return typeof value === "string" && VALID_SECTIONS.includes(value as SettingsSection);
}

/**
 * Read the Settings Dialog marker from the current `history.state`.
 * Returns `null` when state is null, the namespaced key is missing, the
 * marker is malformed, the version is wrong, or the section is invalid.
 *
 * The marker is the only authoritative signal that the current history
 * entry was created by `openSettingsDialogHistory` — used by
 * `isOwnedBySettingsDialog` and `closeSettingsDialogHistory`.
 */
export function readSettingsDialogMarker(): SettingsDialogMarker | null {
  const state = window.history.state;
  if (!state || typeof state !== "object") {
    return null;
  }
  const raw = (state as Record<string, unknown>)[HISTORY_MARKER_KEY];
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const marker = raw as { version?: unknown; section?: unknown };
  if (marker.version !== HISTORY_MARKER_VERSION) {
    return null;
  }
  if (!isSettingsSection(marker.section)) {
    return null;
  }
  return { version: HISTORY_MARKER_VERSION, section: marker.section };
}

/**
 * Whether the current history entry was created by
 * `openSettingsDialogHistory` (i.e. owns a valid Settings Dialog marker).
 *
 * Used by the Provider's close handler: only call `history.back()` when
 * this returns `true`. When `false`, the Provider closes local UI and
 * does not disturb host page history.
 */
export function isOwnedBySettingsDialog(): boolean {
  return readSettingsDialogMarker() !== null;
}

/**
 * Merge a partial update into the current `history.state` without
 * overwriting unrelated fields (host / Next internal state).
 *
 * Returns a shallow copy of the current state with the marker field set
 * to `marker`. Used by both push and replace paths.
 */
function mergeStateWithMarker(
  marker: SettingsDialogMarker,
): Record<string, unknown> {
  const current = (window.history.state ?? {}) as Record<string, unknown>;
  return { ...current, [HISTORY_MARKER_KEY]: marker };
}

/**
 * Open the Settings Dialog by pushing a new history entry.
 *
 * - Uses `history.pushState` so Back/Forward can close the dialog.
 * - Passes `location.href` as the URL so pathname / search / hash are
 *   preserved verbatim.
 * - Preserves all other `history.state` fields.
 * - Does NOT fire `popstate`; the Provider must update local React state
 *   directly after this call.
 */
export function openSettingsDialogHistory(section: SettingsSection): void {
  const marker: SettingsDialogMarker = {
    version: HISTORY_MARKER_VERSION,
    section,
  };
  const nextState = mergeStateWithMarker(marker);
  window.history.pushState(nextState, "", window.location.href);
}

/**
 * Switch section in place without accumulating history entries.
 *
 * - Uses `history.replaceState` (no new entry, Back does not step through
 *   every section change).
 * - No-op when the current entry does not own a marker — safe degradation
 *   that does not silently inject a marker into host page history.
 * - Does NOT fire `popstate`; the Provider must update local React state
 *   directly after this call.
 */
export function replaceSettingsDialogSection(
  section: SettingsSection,
): void {
  if (!isOwnedBySettingsDialog()) {
    return;
  }
  const marker: SettingsDialogMarker = {
    version: HISTORY_MARKER_VERSION,
    section,
  };
  const nextState = mergeStateWithMarker(marker);
  window.history.replaceState(nextState, "", window.location.href);
}

/**
 * Outcome of {@link closeSettingsDialogHistory}.
 *   - `'owned-back'`: the current entry was created by us; `history.back()`
 *     was called. The Provider should NOT also clear local React state
 *     immediately — the resulting `popstate` event will sync state.
 *   - `'local-only'`: the current entry was not created by us. The
 *     Provider closes local UI directly and does not touch history.
 */
export type CloseSettingsDialogHistoryResult = "owned-back" | "local-only";

/**
 * Close the Settings Dialog history entry.
 *
 * - When the current entry owns a Settings Dialog marker, calls
 *   `history.back()` so the host page's previous entry is restored
 *   naturally. Returns `'owned-back'`.
 * - When the current entry does not own a marker (e.g. the user navigated
 *   into the dialog's host page directly, or host code already replaced
 *   state), returns `'local-only'` and does NOT call `history.back()` —
 *   the Provider must close local UI without disturbing host page history.
 *
 * This function never throws and never changes pathname / search / hash.
 */
export function closeSettingsDialogHistory(): CloseSettingsDialogHistoryResult {
  if (!isOwnedBySettingsDialog()) {
    return "local-only";
  }
  window.history.back();
  return "owned-back";
}
