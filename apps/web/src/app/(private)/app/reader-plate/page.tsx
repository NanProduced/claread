"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ReaderPlateSnapshotSurface } from "@/components/reader/plate/ReaderPlateSnapshotSurface";
import { useReaderPlatePolling } from "@/lib/reader-plate-snapshot/polling";
import type {
  ReaderPlateSnapshotDto,
  ReaderPlateValueDto,
} from "@/types/api/reader-plate";

type SubmitState =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "error"; message: string };

type SnapshotState =
  | { kind: "idle" }
  | { kind: "loaded"; recordId: string; snapshot: ReaderPlateSnapshotDto }
  | { kind: "error"; recordId: string; message: string };

type SubmitResponse =
  | ({ ok: true } & {
      record_id: string;
      base_id: string;
      article_ready_sequence: number;
      snapshot: ReaderPlateSnapshotDto;
    })
  | { ok: false; status: number; code: string; message: string };

type SnapshotResponse =
  | ({ ok: true } & ReaderPlateSnapshotDto)
  | { ok: false; status: number; code: string; message: string };

const SAMPLE_TEXT = `The future of reading is not about consuming more words. It is about understanding each sentence deeply, tracing the writer's logic, and building a mental model that lasts.

A scarce few can turn their passion into a stable income. Most settle for comfort and call it wisdom.`;

export default function ReaderPlatePage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [text, setText] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>({ kind: "idle" });
  const [snapshotState, setSnapshotState] = useState<SnapshotState>({ kind: "idle" });
  const [isReloading, setIsReloading] = useState(false);
  const autoLoadedRecordIdRef = useRef<string | null>(null);

  const requestedRecordIdParam =
    searchParams.get("record_id") ?? searchParams.get("recordId");
  const requestedRecordId =
    typeof requestedRecordIdParam === "string" && requestedRecordIdParam.trim().length > 0
      ? requestedRecordIdParam.trim()
      : null;

  const recordId =
    snapshotState.kind === "loaded" || snapshotState.kind === "error"
      ? snapshotState.recordId
      : null;
  const snapshot =
    snapshotState.kind === "loaded" ? snapshotState.snapshot : null;
  const initialCursor = snapshot?.last_event_sequence ?? 0;

  const reloadSnapshot = useCallback(
    async (reason: string) => {
      if (!recordId) return;
      setIsReloading(true);
      try {
        const response = await fetch(
          `/api/web/reader-plate/${encodeURIComponent(recordId)}/snapshot`,
          { method: "GET", headers: { accept: "application/json" } },
        );
        const payload = (await response.json()) as SnapshotResponse;
        if (!response.ok || !payload.ok) {
          setSnapshotState({
            kind: "error",
            recordId,
            message: payload.ok === false ? payload.message : "文章解析内容重新加载失败。",
          });
          return;
        }
        const { ok: _ok, ...snapshotData } = payload;
        void _ok;
        void reason;
        setSnapshotState({
          kind: "loaded",
          recordId,
          snapshot: snapshotData,
        });
      } catch (err) {
        setSnapshotState({
          kind: "error",
          recordId,
          message: err instanceof Error ? err.message : "文章解析内容重新加载发生未知错误。",
        });
      } finally {
        setIsReloading(false);
      }
    },
    [recordId],
  );

  const loadSnapshotForRecord = useCallback(async (targetRecordId: string) => {
    try {
      const response = await fetch(
        `/api/web/reader-plate/${encodeURIComponent(targetRecordId)}/snapshot`,
        { method: "GET", headers: { accept: "application/json" } },
      );
      const payload = (await response.json()) as SnapshotResponse;
      if (!response.ok || !payload.ok) {
        setSnapshotState({
          kind: "error",
          recordId: targetRecordId,
          message:
            payload.ok === false ? payload.message : "文章解析内容加载失败，请稍后重试。",
        });
        return;
      }

      const { ok: _ok, ...snapshotData } = payload;
      void _ok;
      setSubmitState({ kind: "idle" });
      setSnapshotState({
        kind: "loaded",
        recordId: targetRecordId,
        snapshot: snapshotData,
      });
    } catch (err) {
      setSnapshotState({
        kind: "error",
        recordId: targetRecordId,
        message: err instanceof Error ? err.message : "文章解析内容加载发生未知错误。",
      });
    }
  }, []);

  useEffect(() => {
    if (!requestedRecordId) {
      autoLoadedRecordIdRef.current = null;
      return;
    }

    const currentRecordId =
      snapshotState.kind === "loaded" || snapshotState.kind === "error"
        ? snapshotState.recordId
        : null;
    if (currentRecordId === requestedRecordId) {
      autoLoadedRecordIdRef.current = requestedRecordId;
      return;
    }
    if (autoLoadedRecordIdRef.current === requestedRecordId) {
      return;
    }

    autoLoadedRecordIdRef.current = requestedRecordId;
    void loadSnapshotForRecord(requestedRecordId);
  }, [loadSnapshotForRecord, requestedRecordId, snapshotState]);

  const polling = useReaderPlatePolling({
    recordId: recordId ?? "",
    initialCursor,
    enabled: recordId !== null && snapshotState.kind === "loaded",
    onReloadRequired: reloadSnapshot,
  });

  async function handleSubmit() {
    if (submitState.kind === "pending") return;
    const plainText = text.trim();
    if (!plainText) {
      setSubmitState({ kind: "error", message: "请先粘贴需要透读的英文内容。" });
      return;
    }

    setSubmitState({ kind: "pending" });
    setSnapshotState({ kind: "idle" });

    try {
      const response = await fetch("/api/web/reader-plate/submit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ plainText }),
      });
      const payload = (await response.json()) as SubmitResponse;

      if (!response.ok || !payload.ok) {
        setSubmitState({
          kind: "error",
          message:
            payload.ok === false
              ? payload.message
              : "提交失败，请稍后重试。",
        });
        return;
      }

      setSubmitState({ kind: "idle" });
      setSnapshotState({
        kind: "loaded",
        recordId: payload.record_id,
        snapshot: payload.snapshot,
      });
      autoLoadedRecordIdRef.current = payload.record_id;
      router.replace(`/app/reader-plate?record_id=${encodeURIComponent(payload.record_id)}`);
    } catch (err) {
      setSubmitState({
        kind: "error",
        message: err instanceof Error ? err.message : "提交失败，请稍后重试。",
      });
    }
  }

  const snapshotValue: ReaderPlateValueDto = snapshot?.value ?? [];
  const isDirectRecordLoading =
    requestedRecordId !== null &&
    snapshotState.kind === "idle" &&
    submitState.kind !== "pending";

  return (
    <main className="paper-grain min-h-screen text-ink">
      <div className="mx-auto max-w-[72ch] px-5 py-10 sm:px-8 lg:py-14">
        <header className="mb-8">
          <p className="text-xs font-semibold tracking-[0.12em] text-lens-blue">
            Claread Reader
          </p>
          <h1 className="mt-3 font-headline text-2xl font-semibold text-ink sm:text-3xl">
            透读新文章
          </h1>
        </header>

        {submitState.kind === "pending" ? (
          <section className="rounded-note border border-hairline bg-surface p-10 shadow-surface-quiet">
            <div className="flex items-center gap-3 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-lens-blue" />
              正在解析文章结构，请稍候
            </div>
          </section>
        ) : null}

        {isDirectRecordLoading ? (
          <section className="rounded-note border border-hairline bg-surface p-10 shadow-surface-quiet">
            <div className="flex items-center gap-3 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-lens-blue" />
              正在加载阅读快照，请稍候
            </div>
          </section>
        ) : null}

        {snapshotState.kind === "idle" &&
        submitState.kind !== "pending" &&
        requestedRecordId === null ? (
          <section className="rounded-note border border-hairline bg-surface p-6 shadow-surface-quiet">
            <label htmlFor="reader-plate-text" className="sr-only">
              粘贴英文内容
            </label>
            <textarea
              id="reader-plate-text"
              className="min-h-[16rem] w-full resize-y rounded-lg border border-hairline/70 bg-reader-paper/40 p-4 font-reading text-[1.05rem] leading-[1.8] text-ink outline-none focus:border-lens-blue/40"
              placeholder="Paste an English article here"
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                className="text-xs font-medium text-muted underline-offset-4 hover:text-ink hover:underline"
                onClick={() => setText(SAMPLE_TEXT)}
              >
                填入示例文本
              </button>
              <button
                type="button"
                className="inline-flex h-10 items-center justify-center rounded-[10px] bg-ink px-5 font-sans text-sm font-semibold text-white transition-colors hover:bg-ink/90 disabled:opacity-50"
                disabled={text.trim().length === 0}
                onClick={() => void handleSubmit()}
              >
                开始解析
              </button>
            </div>

            {submitState.kind === "error" ? (
              <p className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {submitState.message}
              </p>
            ) : null}
          </section>
        ) : null}

        {snapshotState.kind === "loaded" ? (
          <section>
            {isReloading ? (
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-hairline/70 bg-surface/70 px-3 py-1.5 text-xs font-medium text-lens-blue">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lens-blue" />
                正在刷新译文
              </div>
            ) : polling.isPolling ? (
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-hairline/70 bg-surface/70 px-3 py-1.5 text-xs font-medium text-lens-blue">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lens-blue" />
                批注生成中
              </div>
            ) : null}

            {polling.error ? (
              <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                批注更新暂时中断：{polling.error}
              </p>
            ) : null}

            <ReaderPlateSnapshotSurface value={snapshotValue} />

            <div className="mt-8 flex justify-center">
              <button
                type="button"
                className="inline-flex h-9 items-center justify-center rounded-[8px] border border-hairline bg-surface px-4 font-sans text-xs font-medium text-muted transition-colors hover:text-ink"
                onClick={() => {
                  autoLoadedRecordIdRef.current = null;
                  setText("");
                  setSubmitState({ kind: "idle" });
                  setSnapshotState({ kind: "idle" });
                  router.replace("/app/reader-plate");
                }}
              >
                提交新内容
              </button>
            </div>
          </section>
        ) : null}

        {snapshotState.kind === "error" ? (
          <section className="rounded-note border border-red-200 bg-red-50 p-6">
            <h2 className="font-headline text-lg font-semibold text-red-800">
              文章解析内容加载失败
            </h2>
            <p className="mt-2 text-sm text-red-700">{snapshotState.message}</p>
            <button
              type="button"
              className="mt-4 inline-flex h-9 items-center justify-center rounded-[8px] border border-red-300 bg-white px-4 font-sans text-xs font-medium text-red-700 hover:bg-red-50"
              onClick={() => void reloadSnapshot("manual_retry")}
            >
              重新加载
            </button>
          </section>
        ) : null}
      </div>
    </main>
  );
}
