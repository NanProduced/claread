"use client";

import { useCallback, useEffect, useState } from "react";

import { ReaderRecordWorkbenchSurface } from "@/components/reader/ReaderRecordWorkbenchSurface";
import { useReaderPlatePolling } from "@/lib/reader-plate-snapshot/polling";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

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
    default:
      return "正在刷新阅读内容。";
  }
}

export default function ReadingRecordPage({
  params,
}: {
  params: { recordId: string };
}) {
  const recordId = params.recordId.trim();
  const [snapshotState, setSnapshotState] = useState<SnapshotState>({
    kind: "loading",
    recordId,
  });
  const [isReloading, setIsReloading] = useState(false);
  const [reloadError, setReloadError] = useState<string | null>(null);

  const snapshot = snapshotState.kind === "loaded" ? snapshotState.snapshot : null;
  const initialCursor = snapshot?.last_event_sequence ?? 0;

  const reloadSnapshot = useCallback(
    async (reason: string) => {
      if (!recordId || snapshotState.kind !== "loaded") {
        return;
      }

      setIsReloading(true);
      setReloadError(null);

      try {
        const result = await loadSnapshotForRecord(
          recordId,
          "阅读内容刷新失败，请稍后重试。",
        );

        if (!result.ok) {
          setReloadError(result.message);
          return;
        }

        void reason;
        setSnapshotState({
          kind: "loaded",
          recordId,
          snapshot: result.snapshot,
        });
      } catch (err) {
        setReloadError(
          err instanceof Error ? err.message : "阅读内容刷新发生未知错误。",
        );
      } finally {
        setIsReloading(false);
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

  if (snapshotState.kind === "loaded") {
    const inlinePollingError = reloadError ?? polling.error;
    const showInlineStrip = isReloading || inlinePollingError !== null;

    return (
      <>
        {showInlineStrip ? (
          <div className="paper-grain border-b border-hairline/70 bg-background/90 backdrop-blur">
            <div
              aria-live="polite"
              className="mx-auto flex max-w-[82ch] flex-col gap-2 px-3 py-2.5 sm:px-4 lg:px-5"
            >
              {isReloading ? (
                <div
                  className="inline-flex items-center gap-2 text-xs font-medium text-lens-blue"
                  data-testid="reader-record-reload-status"
                >
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lens-blue" />
                  {reloadStatusLabel(polling.lastReloadReason)}
                </div>
              ) : null}

              {inlinePollingError ? (
                <p
                  className="text-xs font-medium text-amber-800"
                  data-testid="reader-record-polling-error"
                >
                  自动刷新暂时中断：{inlinePollingError}
                </p>
              ) : null}
            </div>
          </div>
        ) : null}

        <ReaderRecordWorkbenchSurface snapshot={snapshotState.snapshot} />
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
