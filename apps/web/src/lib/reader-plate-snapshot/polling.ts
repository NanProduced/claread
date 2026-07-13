"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  classifyReaderEvent,
  RELIABLE_RELOAD_EVENT_TYPES,
  type SnapshotFenceContext,
} from "@/lib/reader-plate-snapshot/representation-event-classifier";
import type { ReaderEventPollResponseDto } from "@/types/api/reader-plate";

/**
 * Polling decision produced by {@link decidePollingAction}.
 *
 * - `reload`: snapshot must be reloaded (server flagged `reload_required`,
 *   a payload-aware classifier flagged `reload_snapshot` /
 *   `reload_or_reset` for a representation event, a reliable reload event
 *   arrived, or the cursor drifted ahead of the server).
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

/**
 * Event types that unconditionally force a snapshot reload, regardless of
 * payload. Re-exported from the payload-aware classifier so existing imports
 * keep working; the classifier is the single source of truth.
 *
 * @deprecated Import {@link RELIABLE_RELOAD_EVENT_TYPES} from
 *   `representation-event-classifier` directly. This alias is kept only for
 *   backward compatibility during the O4-R2-D rollout.
 */
export const RELOAD_TRIGGER_EVENT_TYPES = RELIABLE_RELOAD_EVENT_TYPES;

/**
 * Pure decision function: given the cursor sent and the poll response,
 * decide what the polling loop should do next.
 *
 * Contract reference: `docs/initiatives/reader-agentic-orchestration/modules/streaming-and-projection.md`
 * - `after_sequence == last_event_sequence` with empty events → caught up, no reload.
 * - `reload_required` → reload.
 * - Payload-aware classifier (T4.2a-O4-R2-D): for each event, call
 *   {@link classifyReaderEvent}. The first `reload_snapshot` or
 *   `reload_or_reset` classification forces a reload; the classifier's
 *   reason is preserved for traceability. `cursor_only` events are advanced.
 * - `after_sequence > last_event_sequence` → cursor drift, reload.
 *
 * `snapshotFence` carries the generation/base_id of the currently accepted
 * snapshot so representation events from a stale base are detected as
 * `reload_or_reset` instead of silently consumed.
 */
export function decidePollingAction(input: {
  afterSequence: number;
  response: ReaderEventPollResponseDto;
  snapshotFence?: SnapshotFenceContext | null;
}): PollingDecision {
  const { afterSequence, response, snapshotFence = null } = input;

  if (response.reload_required) {
    return {
      kind: "reload",
      reason: response.reload_reason ?? "reload_required",
    };
  }

  for (const event of response.events) {
    const classification = classifyReaderEvent(event, snapshotFence);
    if (classification.kind === "reload_snapshot") {
      return { kind: "reload", reason: classification.reason };
    }
    if (classification.kind === "reload_or_reset") {
      return { kind: "reload", reason: classification.reason };
    }
    // cursor_only: continue scanning the remaining events.
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
  /**
   * Snapshot fence for the payload-aware classifier (T4.2a-O4-R2-D).
   *
   * Pass the generation/base_id of the currently accepted snapshot so
   * representation events from a stale base are detected as
   * `reload_or_reset` instead of silently consumed as cursor-only. When
   * omitted or `null`, the fence check is skipped — representation events
   * still trigger a reload (fail-safe).
   */
  snapshotFence?: SnapshotFenceContext | null;
  /**
   * Called when the polling hook decides the snapshot must be reloaded.
   *
   * T2.1 contract: the callback MUST resolve to `true` only when a fresh
   * snapshot was actually applied (parent pushed a new `initialCursor` via
   * props). Resolve to `false` when the reload was skipped (e.g. an
   * in-flight reload is already running in the parent), rejected, or the
   * fetch returned an error — in all those cases the polling hook keeps the
   * original cursor so the next tick re-asks with the same `after_sequence`
   * and the reload-required events are not silently consumed.
   *
   * Rejecting the promise is treated identically to returning `false`:
   * cursor stays put and the error is surfaced via `error`.
   */
  onReloadRequired: (reason: string) => Promise<boolean>;
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
 * 3. On `reload` → call `onReloadRequired`. The cursor is advanced to
 *    `next_after_sequence` ONLY when the callback resolves to `true` (a fresh
 *    snapshot was applied). On `false` (skip / fetch error) or rejection the
 *    cursor stays put so the next tick re-asks with the same `after_sequence`
 *    and the reload-required events are not silently consumed. While a reload
 *    is in flight, subsequent reload decisions skip without advancing the
 *    cursor.
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
    snapshotFence = null,
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
  // T4.2a-O4-R2-D: keep the snapshot fence current in a ref so the polling
  // tick always classifies against the latest accepted snapshot without
  // restarting the effect (which would reset the timer).
  const snapshotFenceRef = useRef<SnapshotFenceContext | null>(snapshotFence);

  // T2.1: in-flight guard prevents stacking concurrent reloads. While a
  // reload is awaiting the parent's snapshot fetch, additional reload
  // decisions from subsequent polls skip WITHOUT advancing the cursor —
  // the reload-required events must stay visible so the next tick can
  // retry them if the in-flight reload fails (returns false / rejects).
  // The parent pushes a fresh initialCursor from the snapshot on success,
  // which resets this cursor via the prevResetKey effect.
  const reloadInFlightRef = useRef(false);

  useEffect(() => {
    onReloadRequiredRef.current = onReloadRequired;
  }, [onReloadRequired]);

  useEffect(() => {
    onCursorChangeRef.current = onCursorChange;
  }, [onCursorChange]);

  // T4.2a-O4-R2-D: sync the snapshot fence ref so the polling tick reads the
  // latest accepted snapshot's generation/base_id without re-subscribing.
  useEffect(() => {
    snapshotFenceRef.current = snapshotFence;
  }, [snapshotFence]);

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
          snapshotFence: snapshotFenceRef.current,
        });

        if (decision.kind === "reload") {
          // T2.1: in-flight skip — DO NOT advance the cursor. A previous
          // reload is still pending in the parent. If we advanced here, the
          // reload-required events (layer_published etc.) would be silently
          // consumed even though no snapshot was applied. Keeping the cursor
          // lets the next tick re-see the same events and retry once the
          // in-flight reload completes and pushes a fresh initialCursor.
          if (reloadInFlightRef.current) {
            setLastReloadReason(decision.reason);
            return;
          }

          reloadInFlightRef.current = true;
          setLastReloadReason(decision.reason);
          let reloadSucceeded = false;
          try {
            const result = await onReloadRequiredRef.current?.(decision.reason);
            // T2.1 contract: `true` means a fresh snapshot was applied and
            // the parent will push a new initialCursor. `false` (or a
            // rejected promise) means the reload was skipped/failed — keep
            // the cursor so the next tick retries the same events.
            reloadSucceeded = result === true;
          } catch (reloadErr) {
            // Reload rejected: surface the error but keep the cursor. The
            // next tick will re-ask with the same after_sequence and retry.
            if (!cancelled) {
              setError(
                reloadErr instanceof Error
                  ? reloadErr.message
                  : "阅读内容刷新发生未知错误。",
              );
            }
          } finally {
            reloadInFlightRef.current = false;
          }

          if (reloadSucceeded) {
            // Only advance the cursor when the reload actually applied a new
            // snapshot. The parent pushes a fresh initialCursor via props,
            // but advancing here also prevents the next tick (which may fire
            // before the new initialCursor propagates) from re-seeing the
            // same reload-required events and stacking another reload.
            setCursorBoth(payload.next_after_sequence);
          }
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
