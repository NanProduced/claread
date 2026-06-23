"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ReaderEventPollResponseDto,
  ReaderEventType,
} from "@/types/api/reader-plate";

/**
 * Polling decision produced by {@link decidePollingAction}.
 *
 * - `reload`: snapshot must be reloaded (server flagged `reload_required`,
 *   a `layer_published` / `record_product_state_updated` /
 *   `projection_reset_required` event arrived, or the cursor drifted ahead of
 *   the server).
 * - `advance`: events were consumed without a reload trigger; the cursor
 *   should move to `next_after_sequence`. `hasMore` suggests an immediate
 *   follow-up poll.
 * - `caught_up`: no new events and the cursor is aligned with the server's
 *   high-water mark. This is NOT an error — the caller should just wait.
 */
export type PollingDecision =
  | { kind: "reload"; reason: string }
  | { kind: "advance"; cursor: number; hasMore: boolean }
  | { kind: "caught_up"; cursor: number };

/** Event types that force a snapshot reload in the D4 polling-first slice. */
export const RELOAD_TRIGGER_EVENT_TYPES: ReadonlySet<ReaderEventType> = new Set([
  "layer_published",
  "record_product_state_updated",
  "projection_reset_required",
]);

/**
 * Pure decision function: given the cursor sent and the poll response,
 * decide what the polling loop should do next.
 *
 * Contract reference: `docs/initiatives/reader-agentic-orchestration/modules/streaming-and-projection.md`
 * - `after_sequence == last_event_sequence` with empty events → caught up, no reload.
 * - `reload_required` → reload.
 * - `layer_published` / `record_product_state_updated` / `projection_reset_required` → reload.
 * - `after_sequence > last_event_sequence` → cursor drift, reload.
 */
export function decidePollingAction(input: {
  afterSequence: number;
  response: ReaderEventPollResponseDto;
}): PollingDecision {
  const { afterSequence, response } = input;

  if (response.reload_required) {
    return {
      kind: "reload",
      reason: response.reload_reason ?? "reload_required",
    };
  }

  const triggerEvent = response.events.find((event) =>
    RELOAD_TRIGGER_EVENT_TYPES.has(event.event_type),
  );
  if (triggerEvent) {
    return { kind: "reload", reason: triggerEvent.event_type };
  }

  if (afterSequence > response.last_event_sequence) {
    return { kind: "reload", reason: "cursor_ahead_of_server" };
  }

  if (response.events.length === 0) {
    return { kind: "caught_up", cursor: response.next_after_sequence };
  }

  return {
    kind: "advance",
    cursor: response.next_after_sequence,
    hasMore: response.has_more,
  };
}

export interface UseReaderPlatePollingOptions {
  recordId: string;
  initialCursor: number;
  enabled: boolean;
  pollIntervalMs?: number;
  pollLimit?: number;
  onReloadRequired: (reason: string) => Promise<void>;
  onCursorChange?: (cursor: number) => void;
}

export interface UseReaderPlatePollingResult {
  cursor: number;
  isPolling: boolean;
  error: string | null;
  lastReloadReason: string | null;
}

const DEFAULT_POLL_INTERVAL_MS = 3000;
const DEFAULT_POLL_LIMIT = 100;

/**
 * Polling hook for the D4 readOnly Reader Plate slice.
 *
 * Flow:
 * 1. Poll `GET /api/web/reader-plate/{recordId}/events?after_sequence={cursor}`.
 * 2. Feed the response to {@link decidePollingAction}.
 * 3. On `reload` → call `onReloadRequired` (the parent reloads the snapshot
 *    and pushes a new `initialCursor`).
 * 4. On `advance` → update the local cursor; if `hasMore`, poll again sooner.
 * 5. On `caught_up` → wait for the next interval tick (no error).
 *
 * Errors from the BFF are surfaced via `error` but do not stop the loop;
 * the next tick retries. This keeps transient polling failures visible
 * without bricking the reader.
 */
export function useReaderPlatePolling(
  options: UseReaderPlatePollingOptions,
): UseReaderPlatePollingResult {
  const {
    recordId,
    initialCursor,
    enabled,
    pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
    pollLimit = DEFAULT_POLL_LIMIT,
    onReloadRequired,
    onCursorChange,
  } = options;

  const [cursor, setCursor] = useState<number>(initialCursor);
  const [error, setError] = useState<string | null>(null);
  const [lastReloadReason, setLastReloadReason] = useState<string | null>(null);

  // Keep latest callbacks/props in refs so the polling effect can stay stable.
  const onReloadRequiredRef = useRef(onReloadRequired);
  const onCursorChangeRef = useRef(onCursorChange);
  const cursorRef = useRef(cursor);

  useEffect(() => {
    onReloadRequiredRef.current = onReloadRequired;
  }, [onReloadRequired]);

  useEffect(() => {
    onCursorChangeRef.current = onCursorChange;
  }, [onCursorChange]);

  // Reset cursor when the record or the initial cursor (from a fresh snapshot) changes.
  // Using the "adjust state during render" pattern avoids setState-in-effect.
  const [prevResetKey, setPrevResetKey] = useState({ recordId, initialCursor });
  if (prevResetKey.recordId !== recordId || prevResetKey.initialCursor !== initialCursor) {
    setPrevResetKey({ recordId, initialCursor });
    setCursor(initialCursor);
    setError(null);
    setLastReloadReason(null);
  }

  // Keep cursorRef in sync with cursor state. This runs as an effect so we
  // never mutate the ref during render. The polling tick (a macrotask via
  // setTimeout) always reads a ref that has been synced after the last commit.
  useEffect(() => {
    cursorRef.current = cursor;
  }, [cursor]);

  const setCursorBoth = useCallback((next: number) => {
    cursorRef.current = next;
    setCursor(next);
    onCursorChangeRef.current?.(next);
  }, []);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (cancelled) return;

      const currentCursor = cursorRef.current;
      let nextDelay = pollIntervalMs;

      try {
        const url = new URL(
          `/api/web/reader-plate/${encodeURIComponent(recordId)}/events`,
          window.location.origin,
        );
        url.searchParams.set("after_sequence", String(currentCursor));
        url.searchParams.set("limit", String(pollLimit));

        const response = await fetch(url.toString(), {
          method: "GET",
          headers: { accept: "application/json" },
        });

        if (cancelled) return;

        const payload = (await response.json()) as
          | ({ ok: true } & ReaderEventPollResponseDto)
          | { ok: false; message: string };

        if (!response.ok || !payload.ok) {
          const message =
            !payload.ok && payload.message
              ? payload.message
              : "批注更新请求失败。";
          setError(message);
          return;
        }

        setError(null);

        const decision = decidePollingAction({
          afterSequence: currentCursor,
          response: payload,
        });

        if (decision.kind === "reload") {
          setLastReloadReason(decision.reason);
          await onReloadRequiredRef.current?.(decision.reason);
          // The parent will push a new initialCursor via props after reload.
          return;
        }

        if (decision.kind === "advance") {
          setCursorBoth(decision.cursor);
          if (decision.hasMore) {
            nextDelay = Math.min(pollIntervalMs, 800);
          }
          return;
        }

        // caught_up: keep the cursor in sync with the server echo.
        setCursorBoth(decision.cursor);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "批注更新发生未知错误。");
        }
      } finally {
        if (!cancelled) {
          timer = setTimeout(tick, nextDelay);
        }
      }
    };

    timer = setTimeout(tick, pollIntervalMs);

    return () => {
      cancelled = true;
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    };
  }, [enabled, recordId, pollIntervalMs, pollLimit, setCursorBoth]);

  return { cursor, isPolling: enabled, error, lastReloadReason };
}
