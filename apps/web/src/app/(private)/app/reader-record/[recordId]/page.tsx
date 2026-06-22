"use client";

import { useEffect, useState } from "react";

import { ReaderRecordWorkbenchSurface } from "@/components/reader/ReaderRecordWorkbenchSurface";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

type SnapshotState =
  | { kind: "loading"; recordId: string }
  | { kind: "loaded"; recordId: string; snapshot: ReaderPlateSnapshotDto }
  | { kind: "error"; recordId: string; message: string };

type SnapshotResponse =
  | ({ ok: true } & ReaderPlateSnapshotDto)
  | { ok: false; status: number; code: string; message: string };

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

      setSnapshotState({ kind: "loading", recordId });

      try {
        const response = await fetch(
          `/api/web/reader-plate/${encodeURIComponent(recordId)}/snapshot`,
          { method: "GET", headers: { accept: "application/json" } },
        );
        const payload = (await response.json()) as SnapshotResponse;
        if (cancelled) {
          return;
        }

        if (!response.ok || !payload.ok) {
          setSnapshotState({
            kind: "error",
            recordId,
            message:
              payload.ok === false ? payload.message : "阅读内容加载失败，请稍后重试。",
          });
          return;
        }

        const { ok: _ok, ...snapshot } = payload;
        void _ok;
        setSnapshotState({
          kind: "loaded",
          recordId,
          snapshot,
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

  if (snapshotState.kind === "loaded") {
    return <ReaderRecordWorkbenchSurface snapshot={snapshotState.snapshot} />;
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
