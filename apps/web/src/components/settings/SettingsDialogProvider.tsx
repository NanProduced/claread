"use client";

import * as React from "react";

import { SettingsDialogHost } from "@/components/settings/SettingsDialogHost";
import {
  closeSettingsDialogHistory,
  openSettingsDialogHistory,
  parseSettingsSection,
  readSettingsDialogMarker,
  replaceSettingsDialogSection,
  type SettingsSection,
} from "@/components/settings/settings-dialog-history";
import {
  captureSettingsDialogOpener,
  restoreSettingsDialogFocus,
} from "@/components/settings/settings-dialog-focus";

/**
 * Public controller exposed via React Context.
 *
 * The Settings Dialog's open/close/section state is owned by the
 * Provider. State is synchronized with the browser history stack so
 * Back/Forward works without changing pathname / search / hash.
 */
export interface SettingsDialogController {
  openSettings: (section?: SettingsSection) => void;
  closeSettings: () => void;
  setActiveSection: (section: SettingsSection) => void;
  activeSection: SettingsSection;
  isOpen: boolean;
}

const SettingsDialogContext = React.createContext<SettingsDialogController | null>(
  null,
);

interface SettingsDialogProviderState {
  isOpen: boolean;
  activeSection: SettingsSection;
}

const CLOSED_INITIAL_STATE: SettingsDialogProviderState = {
  isOpen: false,
  activeSection: "preferences",
};

/**
 * Lazy initializer for the Provider's useState.
 *
 * If the current history entry already owns a valid Settings Dialog
 * marker (e.g. the user reloaded the page while the dialog was open,
 * or landed here via Back/Forward), the Provider opens directly with
 * the marker's section. This is the only initial-restore entry point —
 * there is no sessionStorage, URL query, or module-global state.
 *
 * Invalid markers (wrong version, malformed shape, invalid section)
 * fall through to closed — never partially restore.
 *
 * SSR safety: when `window` is undefined the initializer returns the
 * closed state. The Host's lazy fetch effect will fire on the client
 * when `isOpen` is true on first render.
 */
function getInitialProviderState(): SettingsDialogProviderState {
  if (typeof window === "undefined") {
    return CLOSED_INITIAL_STATE;
  }
  const marker = readSettingsDialogMarker();
  if (marker) {
    return { isOpen: true, activeSection: marker.section };
  }
  return CLOSED_INITIAL_STATE;
}

/**
 * Provider for the AppShell Settings Dialog.
 *
 * State machine:
 *   - Provider mounts by reading the current history marker via
 *     `getInitialProviderState`. If a valid marker exists, the dialog
 *     opens directly with the marker's section; otherwise it starts
 *     closed. Initial mount does NOT fetch — only the Host's lazy
 *     fetch effect fires when `isOpen === true`.
 *   - `openSettings(section?)`:
 *       * If closed: captures opener, `pushState` with namespaced
 *         marker, opens with the section.
 *       * If already open: `replaceState` only (no new history entry,
 *         no second opener capture) — switches section in place.
 *   - `setActiveSection(section)`: `replaceState` (no new entry).
 *   - `closeSettings()`:
 *       * Reads the result of `closeSettingsDialogHistory()`.
 *       * `'owned-back'` → `history.back()` was called. DO NOT setState
 *         here — wait for the resulting `popstate` to sync state to
 *         closed. This preserves the user's mental model: "Back closes
 *         the dialog" and avoids double-close races.
 *       * `'local-only'` → no marker was owned. setState closed
 *         immediately so the user sees the dialog disappear.
 *   - `popstate` is the ONLY Back/Forward sync entry point: if the
 *     landed entry has a valid marker, the Provider opens/restores
 *     the corresponding section; otherwise it closes the dialog.
 *
 * All actions preserve `pathname` / `search` / `hash` and any
 * pre-existing `history.state` fields (Next internal key, host page
 * state). No module-global mutable state, no sessionStorage, no URL
 * query string is used as a state source.
 *
 * Focus restoration:
 *   - The opener is captured on first open (when transitioning from
 *     closed to open). Subsequent `openSettings(section)` calls while
 *     already open do NOT re-capture the opener.
 *   - On close, Radix Dialog fires `onCloseAutoFocus` when its content
 *     unmounts. The Provider passes a callback to the Host → Shell →
 *     DialogContent chain. The callback tries, in order:
 *       1. opener (if still connected)
 *       2. visible mobile user-menu trigger
 *       3. visible desktop user-menu trigger
 *     On success: `event.preventDefault()` so Radix skips its default
 *     body-focus behavior. On no target: do NOT preventDefault — let
 *     Radix handle focus as it sees fit.
 *   - There is no useEffect-based focus restoration. The Radix callback
 *     is the single, deterministic focus-move point, eliminating races
 *     between effect timing and Radix's internal focus management.
 */
export function SettingsDialogProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [state, setState] = React.useState<SettingsDialogProviderState>(
    getInitialProviderState,
  );

  // Opener captured on first open (closed→open transition). Stored in
  // a ref because we never re-render on opener change — it is an
  // imperative handle read by the close-focus callback.
  const openerRef = React.useRef<HTMLElement | null>(null);

  // `openSettings` reads `state.isOpen` to decide between push (closed→open)
  // and replace (already-open section switch). We pass `state.isOpen` as a
  // dep so the callback always sees the fresh value; the controller memo
  // already includes `state.isOpen`, so this adds no extra churn.
  const openSettings = React.useCallback((section?: SettingsSection) => {
    const target = parseSettingsSection(section);

    if (state.isOpen) {
      // Already open: replace section in place, no new history entry.
      // Do NOT re-capture the opener — the original opener is still
      // the right restore target on close.
      replaceSettingsDialogSection(target);
      setState((prev) => ({ ...prev, activeSection: target }));
      return;
    }

    // Opening from closed: capture opener + push new history entry.
    openerRef.current = captureSettingsDialogOpener();
    openSettingsDialogHistory(target);
    setState({ isOpen: true, activeSection: target });
  }, [state.isOpen]);

  const setActiveSection = React.useCallback((section: SettingsSection) => {
    const target = parseSettingsSection(section);
    replaceSettingsDialogSection(target);
    setState((prev) =>
      prev.isOpen
        ? { ...prev, activeSection: target }
        : prev,
    );
  }, []);

  const closeSettings = React.useCallback(() => {
    const result = closeSettingsDialogHistory();
    if (result === "local-only") {
      // No marker was owned. Close local UI immediately so the user
      // sees the dialog disappear. `history.back()` was NOT called,
      // so host page history is undisturbed.
      setState((prev) => (prev.isOpen ? { ...prev, isOpen: false } : prev));
      return;
    }
    // 'owned-back': history.back() was called. Do NOT setState here.
    // Wait for the resulting popstate event to land on a host entry
    // without a marker — the popstate handler will setState closed.
    // This avoids double-close races and preserves the user's mental
    // model: "Back closes the dialog".
  }, []);

  // popstate listener — the ONLY Back/Forward sync entry point.
  React.useEffect(() => {
    function handlePopState(): void {
      const marker = readSettingsDialogMarker();
      if (marker) {
        setState({ isOpen: true, activeSection: marker.section });
      } else {
        setState((prev) =>
          prev.isOpen ? { ...prev, isOpen: false } : prev,
        );
      }
    }

    window.addEventListener("popstate", handlePopState);
    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, []);

  // Focus restoration callback invoked by Radix Dialog's
  // `onCloseAutoFocus` (forwarded through Host → Shell → DialogContent).
  // Tries opener → mobile trigger → desktop trigger. On success,
  // `event.preventDefault()` so Radix skips its default body-focus.
  // On no target, does NOT preventDefault — lets Radix handle focus.
  const handleCloseAutoFocus = React.useCallback((event: Event) => {
    const focused = restoreSettingsDialogFocus(openerRef.current);
    // Clear the opener so a subsequent open re-captures.
    openerRef.current = null;
    if (focused) {
      event.preventDefault();
    }
  }, []);

  const controller = React.useMemo<SettingsDialogController>(
    () => ({
      openSettings,
      closeSettings,
      setActiveSection,
      activeSection: state.activeSection,
      isOpen: state.isOpen,
    }),
    [
      openSettings,
      closeSettings,
      setActiveSection,
      state.activeSection,
      state.isOpen,
    ],
  );

  return (
    <SettingsDialogContext.Provider value={controller}>
      {children}
      <SettingsDialogHost onCloseAutoFocus={handleCloseAutoFocus} />
    </SettingsDialogContext.Provider>
  );
}

/**
 * Access the Settings Dialog controller.
 *
 * Throws when called outside a `SettingsDialogProvider` so misuse is
 * caught early rather than silently rendering a no-op dialog.
 */
export function useSettingsDialog(): SettingsDialogController {
  const controller = React.useContext(SettingsDialogContext);
  if (!controller) {
    throw new Error(
      "useSettingsDialog must be used inside a <SettingsDialogProvider>.",
    );
  }
  return controller;
}
