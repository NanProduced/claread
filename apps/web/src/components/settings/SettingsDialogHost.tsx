"use client";

import * as React from "react";

import { SettingsDialogContentClient } from "@/components/settings/SettingsDialogContentClient";
import {
  SettingsDialogShell,
  type SettingsSection as ShellSection,
} from "@/components/settings/SettingsDialogShell";
import { useSettingsDialog } from "@/components/settings/SettingsDialogProvider";
import type { SettingsDialogData } from "@/lib/settings-dialog-data";

/**
 * SettingsDialogHost
 *
 * Rendered once by the SettingsDialogProvider. Owns the lazy data fetch
 * lifecycle for the AppShell Settings Dialog.
 *
 * Contract:
 *   - The Host never fetches on mount. It only fetches when the
 *     Provider's `isOpen` transitions to `true` (or when `isOpen` is
 *     true on initial render — initial-restore case from the Provider).
 *   - Every open re-fetches for data freshness — there is no cross-page
 *     global cache. Simple and correct over clever.
 *   - An `AbortController` per fetch guards against stale responses
 *     overwriting state after close, unmount, or retry.
 *   - Loading / error / retry are first-class states. Errors render a
 *     fixed Chinese fallback message — upstream internal messages are
 *     never surfaced to the user.
 *   - Only the `{ ok: true, data }` envelope is accepted, AND `data`
 *     must contain non-null object `accountData` and `preferencesData`.
 *     Anything else (non-2xx, malformed JSON, missing `data`,
 *     `ok: false`, missing sub-objects) is treated as an error.
 *
 * Implementation notes:
 *   - The visible (effective) state is derived: when `isOpen` is false,
 *     effective state is `idle` regardless of stale fetch state. This
 *     avoids `setState` inside the effect body for the close-reset case
 *     (which would trip `react-hooks/set-state-in-effect`).
 *   - The "just opened" transition is detected with the React-blessed
 *     "Adjusting state when props change" pattern
 *     (https://react.dev/reference/react/useState#storing-information-from-previous-renders):
 *     a `useState`-tracked `prevIsOpen` is compared to the live prop
 *     during render. When a false→true transition is detected, the
 *     state is set to `loading` immediately so the user sees the
 *     loading indicator before the fetch effect fires. React queues
 *     the update and re-renders without committing the stale render.
 *
 * The Host does NOT handle focus restoration directly. The Provider
 * passes an `onCloseAutoFocus` callback that the Host forwards to the
 * Shell → DialogContent chain. Radix fires this callback when the dialog
 * closes and focus is about to return.
 */

const SETTINGS_DIALOG_ENDPOINT = "/api/web/settings-dialog";
const SAFE_ERROR_MESSAGE = "设置信息加载失败，请稍后重试。";

type FetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "success"; data: SettingsDialogData };

const INITIAL_STATE: FetchState = { kind: "idle" };
const LOADING_STATE: FetchState = { kind: "loading" };

export interface SettingsDialogHostProps {
  /**
   * Forwarded to Radix DialogContent's `onCloseAutoFocus` via the Shell.
   *
   * Radix fires this when the dialog closes and focus is about to return.
   * The Provider uses this callback (not a useEffect) to restore focus
   * to the opener / mobile trigger / desktop trigger — eliminating races
   * between effect timing and Radix's internal focus management.
   */
  onCloseAutoFocus?: (event: Event) => void;
}

export function SettingsDialogHost({
  onCloseAutoFocus,
}: SettingsDialogHostProps) {
  const controller = useSettingsDialog();
  const [state, setState] = React.useState<FetchState>(INITIAL_STATE);

  // retryCount lets the Retry button force a re-fetch without waiting
  // for isOpen to toggle. Bumping it re-runs the fetch effect.
  const [retryCount, setRetryCount] = React.useState(0);

  // Track the in-flight AbortController so close / unmount / retry can
  // cancel the previous request. Stored in a ref because the value is
  // imperative and read by the cleanup function.
  const abortRef = React.useRef<AbortController | null>(null);

  // Adjusting state when props change (React-blessed pattern):
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders
  //
  // When `controller.isOpen` transitions false→true, immediately switch
  // the visible state to `loading` so the user sees the loading indicator
  // before the fetch effect fires (avoids stale-content flash on
  // re-open). React queues the update and re-renders without committing
  // the stale render to the DOM.
  //
  // On the true→false (close) transition, we do NOT reset state here.
  // The effectiveState derivation below returns INITIAL_STATE when
  // closed, so the visible content resets naturally without triggering
  // the `react-hooks/set-state-in-effect` lint rule.
  //
  // Initial-restore case: when the Provider mounts with `isOpen=true`
  // (because the history marker was already present), `prevIsOpen`
  // starts as `true` so this branch is skipped on first render. The
  // fetch effect below fires on mount because `controller.isOpen` is
  // already `true` — that's the desired behavior.
  const [prevIsOpen, setPrevIsOpen] = React.useState(controller.isOpen);
  if (controller.isOpen !== prevIsOpen) {
    setPrevIsOpen(controller.isOpen);
    if (controller.isOpen) {
      setState(LOADING_STATE);
    }
  }

  React.useEffect(() => {
    if (!controller.isOpen) {
      // Closed: abort any in-flight request. No setState here — the
      // effective state is derived as INITIAL_STATE when isOpen is
      // false, so the visible content resets naturally without
      // cascading renders.
      abortRef.current?.abort();
      abortRef.current = null;
      return;
    }

    // Open (or retry while open): start a fresh fetch.
    const ac = new AbortController();
    abortRef.current = ac;

    let cancelled = false;

    (async () => {
      try {
        const res = await fetch(SETTINGS_DIALOG_ENDPOINT, {
          signal: ac.signal,
          headers: { accept: "application/json" },
        });

        // Parse JSON defensively; an empty or non-JSON body must not
        // throw uncaught.
        const json: unknown = await res.json().catch(() => null);

        if (cancelled || ac.signal.aborted) return;

        // Accept ONLY the success envelope from Task 1: { ok: true, data }.
        // Anything else — including ok:false error envelopes, malformed
        // JSON, or missing data — falls back to the safe error message.
        if (
          !res.ok ||
          json === null ||
          typeof json !== "object" ||
          (json as { ok?: unknown }).ok !== true
        ) {
          setState({ kind: "error", message: SAFE_ERROR_MESSAGE });
          return;
        }

        const data = (json as { data?: unknown }).data;
        // Strict shape check: data must be an object with non-null
        // object `accountData` and `preferencesData`. Anything else
        // is treated as an error — we never fake partial data.
        if (
          !data ||
          typeof data !== "object" ||
          !isObjectWithKey(data, "accountData") ||
          !isObjectWithKey(data, "preferencesData")
        ) {
          setState({ kind: "error", message: SAFE_ERROR_MESSAGE });
          return;
        }

        setState({ kind: "success", data: data as SettingsDialogData });
      } catch (err) {
        // AbortError is expected when close/unmount/retry cancels us.
        if (cancelled || ac.signal.aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        // Network / parse / unknown error: show the safe message.
        setState({ kind: "error", message: SAFE_ERROR_MESSAGE });
      }
    })();

    return () => {
      // Cleanup: mark cancelled so the async closure stops, and abort
      // the in-flight request so the network drops.
      cancelled = true;
      ac.abort();
    };
  }, [controller.isOpen, retryCount]);

  // Derive effective state to avoid setState in effect body:
  //   - When closed: idle (no stale content rendered — Radix hides
  //     DialogContent when open=false anyway).
  //   - When open: actual fetch state. The "just opened" loading
  //     transition is handled above by setState-during-render.
  const effectiveState: FetchState = !controller.isOpen
    ? INITIAL_STATE
    : state;

  const handleOpenChange = React.useCallback(
    (open: boolean) => {
      if (!open) {
        controller.closeSettings();
      }
    },
    [controller],
  );

  // Shell's `SettingsSection` is a structural type alias for the same
  // `"account" | "preferences" | "usage" | "support"` union declared
  // in `settings-dialog-history.ts`. TypeScript accepts the assignment
  // without a cast; we avoid the unsafe `as SettingsSection` here so
  // any future drift between the two types surfaces as a compile error
  // at the call site instead of being silently overridden.
  const handleSectionChange = React.useCallback(
    (section: ShellSection) => {
      controller.setActiveSection(section);
    },
    [controller],
  );

  const handleRetry = React.useCallback(() => {
    // Show loading immediately (event handler — safe to setState here),
    // then bump retryCount to re-run the fetch effect.
    setState(LOADING_STATE);
    setRetryCount((n) => n + 1);
  }, []);

  return (
    <SettingsDialogShell
      open={controller.isOpen}
      onOpenChange={handleOpenChange}
      activeSection={controller.activeSection}
      onSectionChange={handleSectionChange}
      onCloseAutoFocus={onCloseAutoFocus}
    >
      {renderContent(effectiveState, controller.activeSection, handleRetry)}
    </SettingsDialogShell>
  );
}

/**
 * Type guard for "value is an object (not null) that owns the given key
 * as a non-null object". Used to validate `data.accountData` and
 * `data.preferencesData` before treating the envelope as a success.
 */
function isObjectWithKey<
  K extends string,
>(value: object, key: K): boolean {
  const v = (value as Record<string, unknown>)[key];
  return v !== null && typeof v === "object";
}

function renderContent(
  state: FetchState,
  section: ShellSection,
  onRetry: () => void,
): React.ReactNode {
  if (state.kind === "success") {
    return (
      <SettingsDialogContentClient data={state.data} section={section} />
    );
  }

  if (state.kind === "error") {
    return (
      <div
        role="alert"
        className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center"
      >
        <p className="text-sm text-ink">{state.message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-11 items-center justify-center rounded-[var(--cl-radius-control-sm)] border border-hairline bg-transparent px-4 text-sm text-ink transition-colors hover:bg-[var(--interactive-quiet-hover)]"
        >
          重试
        </button>
      </div>
    );
  }

  // idle (brief moment before the fetch effect fires) or loading.
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-0 flex-1 items-center justify-center p-6"
    >
      <span className="text-sm text-muted-foreground">加载中…</span>
    </div>
  );
}
