"use client";

import { useEffect, useRef, useState } from "react";

import type {
  ReaderArticleRagIndexEnsureSafeDto,
  ReaderArticleRagIndexStatusSafeDto,
} from "@/lib/reader-orchestration/status-mapper";

interface ArticleRagStatusPanelProps {
  recordId: string;
  generation: number;
}

type StatusApiOk = { ok: true } & ReaderArticleRagIndexStatusSafeDto;
type StatusApiErr = { ok: false; status: number; code?: string; message?: string };
type StatusApiResponse = StatusApiOk | StatusApiErr;

type EnsureApiOk = { ok: true } & ReaderArticleRagIndexEnsureSafeDto;
type EnsureApiErr = { ok: false; status: number; code?: string; message?: string };
type EnsureApiResponse = EnsureApiOk | EnsureApiErr;

type UiStatus =
  | "ready"
  | "preparing"
  | "unavailable"
  | "ensuring";

// Lifecycle status strings come from the BFF safe DTO. We do NOT accept
// ad-hoc strings (e.g. "ready" / "claimed" / "running") that are not part
// of the DTO contract. Only these two sets are mapped to UI states; every
// other lifecycle value (including `not_ready`, `not_indexed`, `failed`,
// `superseded_or_stale`, `unavailable`, and any unknown string) is treated
// as quiet unavailability so debug codes never reach the DOM.
const READY_LIFECYCLE = new Set<string>(["indexed"]);
const PREPARING_LIFECYCLE = new Set<string>(["queued", "indexing"]);

function mapLifecycleToUi(
  status: string | undefined,
): Exclude<UiStatus, "ensuring"> {
  if (typeof status !== "string") return "unavailable";
  if (READY_LIFECYCLE.has(status)) return "ready";
  if (PREPARING_LIFECYCLE.has(status)) return "preparing";
  return "unavailable";
}

function mapEnsureToUi(status: string | undefined): Exclude<UiStatus, "ensuring"> {
  if (status === "enqueued") return "preparing";
  // `idempotent_noop` and all error branches (`not_ready`, `no_active_base`,
  // `generation_mismatch`, `record_not_found`, `plan_hash_mismatch`,
  // `bootstrap_inconsistent`, `error`) → quiet unavailability. The user can
  // re-trigger ensure from there.
  return "unavailable";
}

interface PanelState {
  status: UiStatus;
  chunkCount?: number;
}

function deriveInitialState(): PanelState {
  return { status: "unavailable" };
}

export function ArticleRagStatusPanel({ recordId, generation }: ArticleRagStatusPanelProps) {
  const [state, setState] = useState<PanelState>(() => deriveInitialState());
  const inFlightRef = useRef(false);

  // Initial status fetch + re-fetch on generation change.
  useEffect(() => {
    let cancelled = false;
    const url = `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/article-rag-index/status`;

    async function loadStatus() {
      try {
        const response = await fetch(url);
        const payload = (await response.json()) as StatusApiResponse;
        if (cancelled) return;
        if (!payload.ok) {
          // BFF error → quiet unavailability; never surface reason_code /
          // message to the DOM.
          setState({ status: "unavailable" });
          return;
        }
        setState((prev: PanelState) => ({
          ...prev,
          status: mapLifecycleToUi(payload.status),
          chunkCount:
            typeof payload.chunk_count === "number" ? payload.chunk_count : undefined,
        }));
      } catch {
        if (!cancelled) setState({ status: "unavailable" });
      }
    }
    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, [recordId, generation]);

  async function handleEnsure() {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setState((prev: PanelState) => ({ ...prev, status: "ensuring" }));
    try {
      const response = await fetch(
        `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/article-rag-index/ensure`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            expectedGeneration: generation,
          }),
        },
      );
      const payload = (await response.json()) as EnsureApiResponse;
      // idempotent_noop: the BFF has a current index, so stay quiet on the
      // status returned (never show a synthetic "queued" UI). We re-fetch the
      // current status to land in the right UI state.
      if (payload.ok && payload.status === "idempotent_noop") {
        await refetchStatus();
        return;
      }
      if (!payload.ok) {
        setState((prev: PanelState) => ({
          ...prev,
          status: "unavailable",
        }));
        return;
      }
      setState((prev: PanelState) => ({
        ...prev,
        status: mapEnsureToUi(payload.status),
      }));
    } catch {
      setState({ status: "unavailable" });
    } finally {
      inFlightRef.current = false;
    }
  }

  async function refetchStatus() {
    try {
      const response = await fetch(
        `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/article-rag-index/status`,
      );
      const payload = (await response.json()) as StatusApiResponse;
      setState((prev: PanelState) => ({
        ...prev,
        status: payload.ok ? mapLifecycleToUi(payload.status) : "unavailable",
        chunkCount:
          payload.ok && typeof payload.chunk_count === "number"
            ? payload.chunk_count
            : undefined,
      }));
    } catch {
      setState({ status: "unavailable" });
    }
  }

  return (
    <aside
      data-testid="article-rag-status-panel"
      data-rag-status={state.status}
      className="pointer-events-auto mx-3 my-2 flex flex-wrap items-center gap-2 rounded-lg border border-hairline/60 bg-surface/55 px-3 py-2 text-[0.78rem] text-muted-foreground sm:mx-4 lg:mx-5"
    >
      {state.status === "ready" ? (
        <>
          <span
            data-testid="article-rag-status-label"
            className="font-sans font-medium text-emerald-700"
          >
            可用于文章引用问答
          </span>
          {typeof state.chunkCount === "number" ? (
            <span data-testid="article-rag-status-meta" className="text-muted-foreground">
              · 已索引 {state.chunkCount} 块
            </span>
          ) : null}
          <button
            type="button"
            disabled
            data-testid="article-rag-status-refresh"
            className="ml-auto inline-flex items-center gap-1 rounded-full border border-hairline/70 bg-surface/60 px-2 py-0.5 text-[0.72rem] font-medium text-muted-foreground opacity-50"
          >
            已就绪
          </button>
        </>
      ) : null}

      {state.status === "preparing" ? (
        <>
          <span
            data-testid="article-rag-status-label"
            className="font-sans font-medium text-ink"
          >
            后台准备文章引用中
          </span>
          <span className="text-muted-foreground">· 不影响当前阅读</span>
          <button
            type="button"
            disabled
            data-testid="article-rag-status-refresh"
            className="ml-auto inline-flex items-center gap-1 rounded-full border border-hairline/70 bg-surface/60 px-2 py-0.5 text-[0.72rem] font-medium text-muted-foreground opacity-50"
          >
            准备中
          </button>
        </>
      ) : null}

      {state.status === "ensuring" ? (
        <>
          <span
            data-testid="article-rag-status-label"
            className="font-sans font-medium text-ink"
          >
            正在准备文章引用
          </span>
          <button
            type="button"
            disabled
            data-testid="article-rag-status-refresh"
            className="ml-auto inline-flex items-center gap-1 rounded-full border border-hairline/70 bg-surface/60 px-2 py-0.5 text-[0.72rem] font-medium text-muted-foreground opacity-50"
          >
            处理中
          </button>
        </>
      ) : null}

      {state.status === "unavailable" ? (
        <>
          <span
            data-testid="article-rag-status-label"
            className="font-sans font-medium text-muted-foreground"
          >
            文章引用问答暂未准备
          </span>
          <button
            type="button"
            onClick={handleEnsure}
            data-testid="article-rag-status-ensure"
            className="ml-auto inline-flex items-center gap-1 rounded-full border border-ink/15 bg-ink/95 px-2 py-0.5 text-[0.72rem] font-medium text-white hover:bg-ink-soft"
          >
            准备引用问答
          </button>
        </>
      ) : null}
    </aside>
  );
}
