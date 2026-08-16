"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";

import {
  userFacingErrorMessage,
  userFacingPayloadMessage,
} from "@/lib/user-facing-error";
import { ReaderRecordPlateSurface } from "@/components/reader/plate";
import { useRecentReading } from "@/components/layout/recent-reading-context";
import { notify } from "@/components/primitives/notification-center";
import { useReaderPlatePolling, type ReloadContext } from "@/lib/reader-plate-snapshot/polling";
import {
  applySnapshotReload,
  createInitialProgressiveState,
  formatProgressiveStatusLine,
  type ProgressiveClientState,
  type ProgressivePhase,
} from "@/lib/reader-plate-snapshot/progressive-transition";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

import { CandidateConfirmCallout } from "./CandidateConfirmCallout";
import { ReaderOpenedBeacon } from "./ReaderOpenedBeacon";

function deriveSnapshotStateKind(
  kind: SnapshotState["kind"],
): "idle" | "loading" | "loaded" | "error" {
  switch (kind) {
    case "loaded":
      return "loaded";
    case "loading":
      return "loading";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

const READER_POLLING_TOAST_ID = "reader-record-polling-interrupted";

type SnapshotState =
  | { kind: "loading"; recordId: string }
  | { kind: "loaded"; recordId: string; snapshot: ReaderPlateSnapshotDto }
  | { kind: "not-ready"; recordId: string; message: string }
  | { kind: "error"; recordId: string; message: string };

type SnapshotResponse =
  | ({ ok: true } & ReaderPlateSnapshotDto)
  | { ok: false; status: number; code: string; message: string };

type SnapshotLoadResult =
  | { ok: true; snapshot: ReaderPlateSnapshotDto }
  | { ok: false; code: string; message: string };

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
    `/api/web/reader/records/${encodeURIComponent(recordId)}/snapshot`,
    { method: "GET", headers: { accept: "application/json" } },
  );
  const payload = (await response.json()) as SnapshotResponse;

  if (!response.ok || !payload.ok) {
    return {
      ok: false,
      code: payload.ok === false ? payload.code : "upstream_error",
      // 用户可读闸口：干净中文的 BFF message 放行，其余按 code/status
      // 映射为固定文案；原始上游 detail / 英文串不透传。
      message: userFacingPayloadMessage(
        payload.ok === false ? payload : { status: response.status },
        fallbackMessage,
      ),
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

/**
 * Minimal progressive status strip: no full-page overlay, no layout shift
 * of the article body. Driven by T4.2a-PUX phase projection.
 */
function ReaderProgressiveStatusStrip(props: {
  phase: ProgressivePhase;
  statusLine: string;
  visibleLayers: readonly string[];
  lastRejected: boolean;
  rejectReason: string | null;
  isReloading: boolean;
  activeReloadReason: string | null;
}) {
  const {
    phase,
    statusLine,
    visibleLayers,
    lastRejected,
    rejectReason,
    isReloading,
    activeReloadReason,
  } = props;

  if (!statusLine && !isReloading) {
    return null;
  }

  return (
    <div
      data-testid="reader-record-progressive-status"
      data-phase={phase}
      data-visible-layers={visibleLayers.join(",")}
      data-last-rejected={lastRejected ? "true" : "false"}
      data-reject-reason={rejectReason ?? ""}
      data-reloading={isReloading ? "true" : "false"}
      // T5.1e-PUX-Rail-R1: normal phases must not occlude the reading body
      // with a fixed bottom floating toast. Keep the semantics and data
      // attributes for tests/screen readers via a visually hidden region.
      className="sr-only"
      role="status"
      aria-live="polite"
    >
      {isReloading ? (
        <span data-testid="reader-record-progressive-reloading">
          {reloadStatusLabel(activeReloadReason)}
        </span>
      ) : (
        <span data-testid="reader-record-progressive-status-line">
          {statusLine}
        </span>
      )}
    </div>
  );
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
  const [initialLoadAttempt, setInitialLoadAttempt] = useState(0);
  const [isReloading, setIsReloading] = useState(false);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [activeReloadReason, setActiveReloadReason] = useState<string | null>(null);
  // T4.2a-PUX-R4-R2: latest ReloadContext delivered to the Surface for
  // incremental projection merge. The Surface consumes this in its value
  // swap effect: when triggerEvents are present the merger may produce a
  // targeted_apply (replaceNodes batch) instead of a full setValue.
  // Cleared by the Surface after consumption; null on initial mount and
  // after fallback so the next render's setValue path runs untouched.
  const [pendingReloadContext, setPendingReloadContext] =
    useState<ReloadContext | null>(null);

  // T4.2a-PUX-R2: progressive gate state for UI. The authoritative last-accepted
  // snapshot for monotonic checks lives in progressiveStateRef so reload does
  // not race with React batching. Polling cursor remains sole source of truth
  // in useReaderPlatePolling (initialCursor from accepted snapshot only).
  const progressiveStateRef = useRef<ProgressiveClientState>(
    createInitialProgressiveState(),
  );
  const [progressivePhase, setProgressivePhase] =
    useState<ProgressivePhase>("loading");
  const [progressiveStatusLine, setProgressiveStatusLine] = useState("");
  const [progressiveVisibleLayers, setProgressiveVisibleLayers] = useState<
    readonly string[]
  >([]);
  const [progressiveLastRejected, setProgressiveLastRejected] = useState(false);
  const [progressiveRejectReason, setProgressiveRejectReason] = useState<
    string | null
  >(null);

  // T2.1: ref-based re-entry guard. `isReloading` (state) cannot prevent
  // same-tick re-entry because React batches and the boolean only commits
  // after the render. The polling hook + the in-flight guard in polling.ts
  // already debounce reloads, but user-triggered reloads (e.g. manual refresh
  // button) and polling-triggered reloads can still overlap. The ref lets us
  // decline a second concurrent reload synchronously.
  const reloadInFlightRef = useRef(false);

  const snapshot = snapshotState.kind === "loaded" ? snapshotState.snapshot : null;

  // Sidebar sync: title generation completes asynchronously after the record
  // is created. When the accepted snapshot first carries a generated display
  // title, refresh the recent-reading list once so the sidebar stops showing
  // the import placeholder (e.g. "粘贴文本") without a full page navigation.
  const { refetch: refetchRecentReading } = useRecentReading();
  const lastSyncedDisplayTitleRef = useRef<string | null>(null);
  const displayTitleZh = snapshot?.record.display_title_zh?.trim() ?? "";
  useEffect(() => {
    if (!displayTitleZh) return;
    if (lastSyncedDisplayTitleRef.current === displayTitleZh) return;
    lastSyncedDisplayTitleRef.current = displayTitleZh;
    void refetchRecentReading();
  }, [displayTitleZh, refetchRecentReading]);
  // Single cursor path: polling reads last_event_sequence from the *accepted*
  // snapshot only. Rejected reloads never update snapshotState, so cursor holds.
  const initialCursor = snapshot?.last_event_sequence ?? 0;
  // T4.2a-O4-R2-D: snapshot fence for the payload-aware classifier. Derived
  // from the accepted snapshot so representation events from a stale base are
  // detected as reload_or_reset instead of silently consumed as cursor-only.
  const snapshotFence =
    snapshot !== null
      ? { generation: snapshot.record.generation, baseId: snapshot.base.base_id }
      : null;

  const publishProgressiveUi = useCallback((state: ProgressiveClientState) => {
    setProgressivePhase(state.phase);
    setProgressiveStatusLine(
      formatProgressiveStatusLine(state.phase, state.visibleLayerTypes),
    );
    setProgressiveVisibleLayers(state.visibleLayerTypes);
    setProgressiveLastRejected(state.lastRejected);
    setProgressiveRejectReason(state.rejectReason);
  }, []);

  /**
   * T4.2a-PUX-R2: attempt to accept a snapshot through progressive monotonic
   * validation. On success, updates snapshotState + progressive ref/UI.
   * On reject (stale / layer regression), leaves UI snapshot untouched and
   * returns false so polling holds its cursor.
   */
  const tryAcceptSnapshot = useCallback(
    (
      nextSnapshot: ReaderPlateSnapshotDto,
      reloadReason: string | null,
    ): boolean => {
      const applied = applySnapshotReload(
        progressiveStateRef.current,
        nextSnapshot,
        { reloadReason },
      );

      if (!applied.ok) {
        // Keep progressiveStateRef at the prior accepted state for the next
        // comparison, but surface reject diagnostics for tests / aria.
        progressiveStateRef.current = applied.state;
        publishProgressiveUi(applied.state);
        return false;
      }

      progressiveStateRef.current = applied.state;
      publishProgressiveUi(applied.state);
      setSnapshotState({
        kind: "loaded",
        recordId: nextSnapshot.record_id,
        snapshot: nextSnapshot,
      });
      return true;
    },
    [publishProgressiveUi],
  );

  // T2.1 contract: returns `true` only when a fresh snapshot was actually
  // applied (so the polling hook can advance its cursor). Returns `false`
  // when skipped (in-flight / not loaded), fetch failed, or progressive
  // monotonic validation rejected the snapshot — in those cases the polling
  // hook keeps the original cursor and the next tick re-asks with the same
  // `after_sequence`, so reload-required events are not silently consumed.
  //
  // T4.2a-PUX-R4-R2: receives a full ReloadContext (not just a reason
  // string) so the Surface can pass trigger events + fence to the
  // incremental projection merger. Manual reloads (toast retry, Surface
  // onRequestSnapshotReload) build a synthetic ReloadContext with empty
  // events — the merger returns fallback_full_reload → existing setValue.
  const reloadSnapshot = useCallback(
    async (context: ReloadContext): Promise<boolean> => {
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
      setActiveReloadReason(context.reason);
      // Surface reads pendingReloadContext in its value swap effect when
      // the new snapshot prop arrives. The merger sees context.events
      // (possibly empty for manual reloads) and decides targeted_apply vs
      // fallback_full_reload.
      setPendingReloadContext(context);

      try {
        const result = await loadSnapshotForRecord(
          recordId,
          "阅读内容刷新失败，请稍后重试。",
        );

        if (!result.ok) {
          setReloadError(result.message);
          return false;
        }

        const accepted = tryAcceptSnapshot(result.snapshot, context.reason);
        if (!accepted) {
          // Progressive gate rejected (stale sequence / layer regression).
          // Do not setReloadError toast spam — hold cursor for retry; status
          // strip exposes data-last-rejected for diagnostics.
          return false;
        }
        return true;
      } catch (err) {
        setReloadError(
          userFacingErrorMessage(err, "阅读内容刷新失败，请稍后重试。"),
        );
        return false;
      } finally {
        reloadInFlightRef.current = false;
        setIsReloading(false);
        setActiveReloadReason(null);
      }
    },
    [recordId, snapshotState.kind, tryAcceptSnapshot],
  );

  /**
   * T4.2a-PUX-R4-R2: build a synthetic ReloadContext for manual reloads
   * (toast retry button, Surface onRequestSnapshotReload). Empty events
   * forces the incremental projection merger to fallback_full_reload with
   * reason `no_trigger_events` — preserves existing setValue behavior.
   */
  const buildManualReloadContext = useCallback((): ReloadContext => {
    return {
      cursor: initialCursor,
      events: [],
      triggerClassification: {
        kind: "reload_snapshot",
        reason: "user_asset_written",
      },
      acceptedSnapshotFence: snapshotFence,
      reason: "user_asset_written",
    };
  }, [initialCursor, snapshotFence]);

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
      // Reset progressive gate when the route record changes.
      progressiveStateRef.current = createInitialProgressiveState();
      publishProgressiveUi(progressiveStateRef.current);

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
            kind: result.code === "record_not_ready" ? "not-ready" : "error",
            recordId,
            message: result.message,
          });
          return;
        }

        const accepted = tryAcceptSnapshot(result.snapshot, "initial_load");
        if (!accepted) {
          // Initial load should not be rejected by monotonic gates (empty
          // prior state). Treat as hard error if it somehow fails.
          setSnapshotState({
            kind: "error",
            recordId,
            message: "阅读内容校验失败，请稍后重试。",
          });
        }
      } catch (err) {
        if (cancelled) {
          return;
        }

        setSnapshotState({
          kind: "error",
          recordId,
          // 闸口：SyntaxError（打到 HTML 错误页）/网络失败映射为固定文案。
          message: userFacingErrorMessage(err, "阅读内容加载失败，请稍后重试。"),
        });
      }
    }

    void loadSnapshot();

    return () => {
      cancelled = true;
    };
  }, [initialLoadAttempt, recordId, publishProgressiveUi, tryAcceptSnapshot]);

  const polling = useReaderPlatePolling({
    recordId,
    initialCursor,
    enabled: snapshotState.kind === "loaded" && recordId.length > 0,
    snapshotFence,
    onReloadRequired: reloadSnapshot,
  });

  // Non-conditional hook: must run before the `if (snapshotState.kind === "loaded")`
  // early return so the toast lifecycle (show/dismiss/unmount) is always
  // consistent regardless of snapshot state transitions.
  const connectionError =
    snapshotState.kind === "loaded" ? reloadError ?? polling.error : null;
  useReaderPollingConnectionToast(connectionError, () => {
    void reloadSnapshot(buildManualReloadContext());
  });

  if (snapshotState.kind === "loaded") {
    return (
      <>
        <ReaderOpenedBeacon
          recordId={recordId}
          snapshotStateKind={deriveSnapshotStateKind(snapshotState.kind)}
        />
        <CandidateConfirmCallout recordId={recordId} />

        <ReaderProgressiveStatusStrip
          phase={progressivePhase}
          statusLine={progressiveStatusLine}
          visibleLayers={progressiveVisibleLayers}
          lastRejected={progressiveLastRejected}
          rejectReason={progressiveRejectReason}
          isReloading={isReloading}
          activeReloadReason={activeReloadReason}
        />

        <ReaderRecordPlateSurface
          snapshot={snapshotState.snapshot}
          pendingReloadContext={pendingReloadContext}
          onReloadContextConsumed={() => setPendingReloadContext(null)}
          onRequestSnapshotReload={() => {
            void reloadSnapshot(buildManualReloadContext());
          }}
        />
      </>
    );
  }

  return (
    <main className="min-h-screen text-ink">
      <ReaderOpenedBeacon
        recordId={recordId}
        snapshotStateKind={deriveSnapshotStateKind(snapshotState.kind)}
      />
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
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <span className="h-2 w-2 animate-pulse rounded-full bg-lens-blue" />
              正在加载阅读内容，请稍候
            </div>
          </section>
        ) : null}

        {snapshotState.kind === "error" ? (
          <section className="rounded-note border border-danger/30 bg-danger/5 p-6 shadow-surface-quiet">
            <p className="text-sm font-medium text-danger">加载失败</p>
            <p className="mt-2 text-sm text-danger/90">{snapshotState.message}</p>
            <button
              type="button"
              className="mt-4 rounded-md border border-danger/30 bg-surface px-3 py-2 text-sm font-medium text-ink hover:bg-danger/10"
              onClick={() => setInitialLoadAttempt((attempt) => attempt + 1)}
            >
              重新加载
            </button>
          </section>
        ) : null}

        {snapshotState.kind === "not-ready" ? (
          <section className="rounded-note border border-hairline bg-surface p-6 shadow-surface-quiet">
            <p className="text-sm font-medium text-ink">文档仍在解析</p>
            <p className="mt-2 text-sm text-muted-foreground">{snapshotState.message}</p>
            <button
              type="button"
              className="mt-4 rounded-md border border-hairline px-3 py-2 text-sm font-medium text-ink hover:bg-muted/50"
              onClick={() => setInitialLoadAttempt((attempt) => attempt + 1)}
            >
              重新检查
            </button>
          </section>
        ) : null}

      </div>
    </main>
  );
}
