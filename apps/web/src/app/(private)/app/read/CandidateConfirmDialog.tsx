"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowRight, FileText, RefreshCw } from "lucide-react";
import { Button } from "@/components/primitives/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/primitives/dialog";
import { clearPendingCandidate, type PendingCandidate } from "./pending-candidate";

interface CandidateConfirmDialogProps {
  candidate: PendingCandidate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirmed: (candidate: PendingCandidate) => void;
  onRestart: (candidate: PendingCandidate) => void;
  onRefresh?: () => void;
}

type ConfirmState =
  | { kind: "idle" }
  | { kind: "confirming" }
  | { kind: "conflict" }
  | { kind: "error"; message: string };

interface ConfirmApiOk {
  ok: true;
  [key: string]: unknown;
}

interface ConfirmApiErr {
  ok: false;
  status: number;
  code?: string;
  message?: string;
}

type ConfirmApiResponse = ConfirmApiOk | ConfirmApiErr;

function getPreviewText(candidate: PendingCandidate | null): string {
  return (
    candidate?.canonicalTextPreview?.trim() ||
    candidate?.inputSnapshot?.trim() ||
    ""
  );
}

function getSourceLabel(candidate: PendingCandidate | null): string {
  if (candidate?.filename?.trim()) {
    return `来源文件：${candidate.filename.trim()}`;
  }
  return "来源：粘贴文本";
}

export function CandidateConfirmDialog({
  candidate,
  open,
  onOpenChange,
  onConfirmed,
  onRestart,
  onRefresh,
}: CandidateConfirmDialogProps) {
  const [confirmState, setConfirmState] = useState<ConfirmState>({ kind: "idle" });
  const previewText = useMemo(() => getPreviewText(candidate), [candidate]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const timer = window.setTimeout(() => {
      setConfirmState({ kind: "idle" });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [candidate?.candidateDocumentId, open]);

  async function postConfirm() {
    if (!candidate || confirmState.kind === "confirming") {
      return;
    }

    setConfirmState({ kind: "confirming" });
    try {
      const response = await fetch(
        `/api/web/reader-plate/records/${encodeURIComponent(candidate.readingRecordId)}/candidate-documents/${encodeURIComponent(candidate.candidateDocumentId)}/confirm`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ language: "en" }),
        },
      );
      const payload = (await response.json()) as ConfirmApiResponse;
      if (!payload.ok) {
        if (payload.code === "candidate_conflict" || payload.status === 409) {
          setConfirmState({ kind: "conflict" });
          return;
        }
        setConfirmState({
          kind: "error",
          message: payload.message || "确认失败，请稍后重试。",
        });
        return;
      }

      clearPendingCandidate();
      onConfirmed(candidate);
    } catch (error: unknown) {
      setConfirmState({
        kind: "error",
        message: error instanceof Error ? error.message : "确认失败，请稍后重试。",
      });
    }
  }

  function handleRestart() {
    if (!candidate) {
      return;
    }
    clearPendingCandidate();
    onRestart(candidate);
  }

  const isConfirming = confirmState.kind === "confirming";
  const title =
    confirmState.kind === "conflict"
      ? "提取结果需要重新确认"
      : "确认提取出的英文文章";
  const description =
    confirmState.kind === "conflict"
      ? "这份候选正文的状态已经变化。你可以重试确认，或重新提交来源内容。"
      : "请检查正文是否完整。确认后，Claread 会进入透读。";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="lg"
        data-testid="candidate-confirm-dialog"
        className="max-h-[86dvh] max-w-[min(72rem,calc(100vw-2rem))] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden p-0"
      >
        <DialogHeader className="border-b border-hairline/70 px-6 pb-4 pt-6 sm:px-8">
          <div className="flex items-start gap-3 pr-9">
            <span className="mt-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-hairline/70 bg-reader-paper/70 text-ink">
              <FileText aria-hidden className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <DialogTitle className="text-[1.85rem] leading-tight">
                {title}
              </DialogTitle>
              <DialogDescription className="mt-2 max-w-[48rem] font-sans text-[0.86rem] leading-6">
                {description}
              </DialogDescription>
              <p className="mt-3 truncate font-sans text-[0.76rem] font-medium text-muted">
                {getSourceLabel(candidate)}
              </p>
            </div>
          </div>
        </DialogHeader>

        <div className="min-h-0 overflow-hidden px-6 py-5 sm:px-8">
          <div className="flex h-full min-h-[22rem] flex-col rounded-[10px] border border-hairline/70 bg-reader-paper/54">
            <div className="flex items-center justify-between gap-4 border-b border-hairline/60 px-4 py-3 font-sans">
              <p className="text-[0.78rem] font-semibold tracking-[0.08em] text-muted">
                EXTRACTED ARTICLE
              </p>
              <p className="text-[0.72rem] font-medium text-subtle">
                纯文本预览
              </p>
            </div>
            <div
              data-testid="candidate-confirm-preview"
              className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap px-5 py-5 font-reading text-[1.02rem] leading-[1.9] text-ink/88 sm:text-[1.08rem]"
            >
              {previewText ? (
                previewText
              ) : (
                <span className="font-sans text-[0.86rem] text-muted">
                  暂无可展示的正文预览。确认后，Claread 会使用已提取的正文进入透读。
                </span>
              )}
            </div>
          </div>

          {confirmState.kind === "error" ? (
            <p
              data-testid="candidate-confirm-error"
              className="mt-3 font-sans text-[0.8rem] font-medium text-red-700"
            >
              {confirmState.message}
            </p>
          ) : null}
        </div>

        <DialogFooter className="border-t border-hairline/70 px-6 py-4 sm:px-8">
          {confirmState.kind === "conflict" ? (
            <>
              {onRefresh ? (
                <Button type="button" variant="secondary" size="sm" onClick={onRefresh}>
                  刷新页面
                </Button>
              ) : null}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  void postConfirm();
                }}
                data-testid="candidate-retry-confirm-button"
              >
                重试确认
                <RefreshCw aria-hidden className="ml-1 h-3.5 w-3.5" />
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleRestart}>
                重新提交
              </Button>
            </>
          ) : confirmState.kind === "error" ? (
            <>
              <Button type="button" variant="secondary" size="sm" onClick={() => setConfirmState({ kind: "idle" })}>
                返回确认
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleRestart}>
                重新提交
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onOpenChange(false)}
                data-testid="candidate-defer-button"
                disabled={isConfirming}
              >
                稍后处理
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleRestart} disabled={isConfirming}>
                重新提交
              </Button>
              <Button
                type="button"
                variant="primary-ink"
                size="sm"
                onClick={() => {
                  void postConfirm();
                }}
                data-testid="candidate-confirm-button"
                disabled={isConfirming}
              >
                {isConfirming ? "确认中..." : "确认并开始透读"}
                {!isConfirming ? <ArrowRight aria-hidden className="ml-1 h-3.5 w-3.5" /> : null}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
