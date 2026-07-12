"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";

import { ReaderRecordWorkbenchSurface } from "@/components/reader/ReaderRecordWorkbenchSurface";
import { ReaderRecordPlateSurface } from "@/components/reader/plate";
import { notify } from "@/components/primitives/notification-center";
import { useReaderPlatePolling } from "@/lib/reader-plate-snapshot/polling";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import { getReaderRecordSurfaceMode } from "./reader-record-surface-mode";
import { CandidateConfirmCallout } from "./CandidateConfirmCallout";

const READER_POLLING_TOAST_ID = "reader-record-polling-interrupted";

type SnapshotState =
  | { kind: "loading"; recordId: string }
  | { kind: "loaded"; recordId: string; snapshot: ReaderPlateSnapshotDto }
  | { kind: "error"; recordId: string; message: string };

type SnapshotResponse =
  | ({ ok: true } & ReaderPlateSnapshotDto)
  | { ok: false; status: number; code: string; message: string };

type SnapshotLoadResult =
  | { ok: true; snapshot: ReaderPlateSnapshotDto }
  | { ok: false; message: string };

type ReadingRecordRouteParams = { recordId: string };
type ReadingRecordRouteParamsInput =
  | ReadingRecordRouteParams
  | Promise<ReadingRecordRouteParams>;

function useReadingRecordRouteParams(params: ReadingRecordRouteParamsInput) {
  if (typeof (params as PromiseLike<ReadingRecordRouteParams>).then === "function") {
    return use(params as Promise<ReadingRecordRouteParams>);
  }

  return params as ReadingRecordRouteParams;
}

async function loadSnapshotForRecord(
  recordId: string,
  fallbackMessage: string,
): Promise<SnapshotLoadResult> {
  const response = await fetch(
    `/api/web/reader-plate/${encodeURIComponent(recordId)}/snapshot`,
    { method: "GET", headers: { accept: "application/json" } },
  );
  const payload = (await response.json()) as SnapshotResponse;

  if (!response.ok || !payload.ok) {
    return {
      ok: false,
      message: payload.ok === false ? payload.message : fallbackMessage,
    };
  }

  const { ok: _ok, ...snapshot } = payload;
  void _ok;
  return { ok: true, snapshot };
}

function reloadStatusLabel(reason: string | null): string {
  switch (reason) {
    case "layer_published":
      return "检测到新的增强层，正在刷新阅读内容。";
    case "record_product_state_updated":
      return "检测到阅读记录状态变化，正在刷新阅读内容。";
    case "projection_reset_required":
      return "检测到阅读投影重置请求，正在刷新阅读内容。";
    case "user_asset_written":
      return "已保存阅读标注，正在刷新阅读内容。";
    default:
      return "正在刷新阅读内容。";
  }
}

/**
 * Manages the top-center connection alert for polling/reload interruptions.
 *
 * The toast is non-blocking: it overlays the workspace without occupying
 * document flow, so it never shifts the Reader Header, sidebar, Outline rail,
 * or Ask panel geometry.
 *
 * Dedup strategy:
 * - A stable `toastId` ensures repeated polling rerenders with the same error
 *   content update the existing toast instead of stacking.
 * - `lastShownErrorRef` tracks the error content that is currently displayed.
 *   When the user dismisses the toast, Sonner removes it but our state still
 *   records what was shown; we only re-show when the error content changes or
 *   a fresh retry failure produces a different message.
 * - On recovery (error becomes null), route change, or unmount, the toast is
 *   dismissed and the ref is cleared, allowing the next downgrade cycle to
 *   show a new alert.
 */
function useReaderPollingConnectionToast(
  connectionError: string | null,
  onRetry: () => void,
) {
  const lastShownErrorRef = useRef<string | null>(null);

  useEffect(() => {
    if (connectionError) {
      if (lastShownErrorRef.current === connectionError) {
        // Same error content already displayed (or user-dismissed with no
        // content change). Do not re-pop.
        return;
      }
      lastShownErrorRef.current = connectionError;
      notify.alert({
        id: READER_POLLING_TOAST_ID,
        tone: "warning",
        title: "自动刷新已暂停",
        description: connectionError,
        action: {
          label: "重试",
          onClick: onRetry,
        },
      });
    } else if (lastShownErrorRef.current !== null) {
      // Error cleared — resolve the alert and reset so the next cycle can
      // show again.
      notify.resolveAlert(READER_POLLING_TOAST_ID);
      lastShownErrorRef.current = null;
    }
  }, [connectionError, onRetry]);

  // Dismiss on unmount / route change so the alert never outlives the page.
  useEffect(() => {
    return () => {
      notify.resolveAlert(READER_POLLING_TOAST_ID);
    };
  }, []);
}

export default function ReadingRecordPage({
  params,
}: {
  params: ReadingRecordRouteParamsInput;
}) {
  const routeParams = useReadingRecordRouteParams(params);
  const recordId = routeParams.recordId.trim();
  const [snapshotState, setSnapshotState] = useState<SnapshotState>({
    kind: "loading",
    recordId,
  });
  const [isReloading, setIsReloading] = useState(false);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [activeReloadReason, setActiveReloadReason] = useState<string | null>(null);
  const [surfaceMode] = useState(getReaderRecordSurfaceMode);

  // T2.1: ref-based re-entry guard. `isReloading` (state) cannot prevent
  // same-tick re-entry because React batches and the boolean only commits
  // after the render. The polling hook + the in-flight guard in polling.ts
  // already debounce reloads, but user-triggered reloads (e.g. manual refresh
  // button) and polling-triggered reloads can still overlap. The ref lets us
  // decline a second concurrent reload synchronously.
  const reloadInFlightRef = useRef(false);

  const snapshot = snapshotState.kind === "loaded" ? snapshotState.snapshot : null;
  const initialCursor = snapshot?.last_event_sequence ?? 0;

  // T2.1 contract: returns `true` only when a fresh snapshot was actually
  // applied (so the polling hook can advance its cursor). Returns `false`
  // when skipped (in-flight / not loaded) or when the fetch failed — in
  // those cases the polling hook keeps the original cursor and the next
  // tick re-asks with the same `after_sequence`, so reload-required events
  // are not silently consumed.
  const reloadSnapshot = useCallback(
    async (reason: string): Promise<boolean> => {
      if (!recordId || snapshotState.kind !== "loaded") {
        return false;
      }

      // T2.1: a reload is already in flight — decline the second caller and
      // return false so the polling hook does NOT advance its cursor. The
      // in-flight reload will push a fresh snapshot on success (which resets
      // the cursor via the prevResetKey effect), or fail and leave the
      // cursor untouched so the next tick retries the same events.
      if (reloadInFlightRef.current) {
        return false;
      }

      reloadInFlightRef.current = true;
      setIsReloading(true);
      setReloadError(null);
      setActiveReloadReason(reason);

      try {
        const result = await loadSnapshotForRecord(
          recordId,
          "阅读内容刷新失败，请稍后重试。",
        );

        if (!result.ok) {
          setReloadError(result.message);
          return false;
        }

        setSnapshotState({
          kind: "loaded",
          recordId,
          snapshot: result.snapshot,
        });
        return true;
      } catch (err) {
        setReloadError(
          err instanceof Error ? err.message : "阅读内容刷新发生未知错误。",
        );
        return false;
      } finally {
        reloadInFlightRef.current = false;
        setIsReloading(false);
        setActiveReloadReason(null);
      }
    },
    [recordId, snapshotState.kind],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadSnapshot() {
      if (!recordId) {
        setSnapshotState({
          kind: "error",
          recordId: "",
          message: "缺少可加载的阅读记录。",
        });
        return;
      }

      setIsReloading(false);
      setReloadError(null);
      setSnapshotState({ kind: "loading", recordId });

      try {
        const result = await loadSnapshotForRecord(
          recordId,
          "阅读内容加载失败，请稍后重试。",
        );
        if (cancelled) {
          return;
        }

        if (!result.ok) {
          setSnapshotState({
            kind: "error",
            recordId,
            message: result.message,
          });
          return;
        }

        setSnapshotState({
          kind: "loaded",
          recordId,
          snapshot: result.snapshot,
        });
      } catch (err) {
        if (cancelled) {
          return;
        }

        setSnapshotState({
          kind: "error",
          recordId,
          message: err instanceof Error ? err.message : "阅读内容加载发生未知错误。",
        });
      }
    }

    void loadSnapshot();

    return () => {
      cancelled = true;
    };
  }, [recordId]);

  const polling = useReaderPlatePolling({
    recordId,
    initialCursor,
    enabled: snapshotState.kind === "loaded" && recordId.length > 0,
    onReloadRequired: reloadSnapshot,
  });

  // Non-conditional hook: must run before the `if (snapshotState.kind === "loaded")`
  // early return so the toast lifecycle (show/dismiss/unmount) is always
  // consistent regardless of snapshot state transitions.
  const connectionError =
    snapshotState.kind === "loaded" ? reloadError ?? polling.error : null;
  useReaderPollingConnectionToast(connectionError, () => {
    void reloadSnapshot("user_asset_written");
  });

  if (snapshotState.kind === "loaded") {
    return (
      <>
        <CandidateConfirmCallout recordId={recordId} />

        {surfaceMode === "plate" ? (
          <ReaderRecordPlateSurface
            snapshot={snapshotState.snapshot}
            onRequestSnapshotReload={() => {
              void reloadSnapshot("user_asset_written");
            }}
          />
        ) : (
          <ReaderRecordWorkbenchSurface snapshot={snapshotState.snapshot} />
        )}
      </>
    );
  }

  return (
    <main className="paper-grain min-h-screen text-ink">
      <div className="mx-auto max-w-[72ch] px-5 py-10 sm:px-8 lg:py-14">
        <header className="mb-8">
          <p className="text-xs font-semibold tracking-[0.12em] text-lens-blue">
            Claread Reader
          </p>
          <h1 className="mt-3 font-headline text-2xl font-semibold text-ink sm:text-3xl">
            阅读记录
          </h1>
        </header>

        {snapshotState.kind === "loading" ? (
          <section className="rounded-note border border-hairline bg-surface p-10 shadow-surface-quiet">
            <div className="flex items-center gap-3 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-lens-blue" />
              正在加载阅读内容，请稍候
            </div>
          </section>
        ) : null}

        {snapshotState.kind === "error" ? (
          <section className="rounded-note border border-danger/30 bg-danger/5 p-6 shadow-surface-quiet">
            <p className="text-sm font-medium text-danger">加载失败</p>
            <p className="mt-2 text-sm text-danger/90">{snapshotState.message}</p>
          </section>
        ) : null}

      </div>
    </main>
  );
}
