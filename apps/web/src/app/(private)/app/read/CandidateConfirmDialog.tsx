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
import type { ReaderCandidateDocumentOutlineItem, ReaderCandidateDocumentRiskItem } from "@/types/api/reader-plate";

interface CandidateConfirmDialogProps {
  candidate: PendingCandidate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirmed: (candidate: PendingCandidate) => void;
  onRestart: (candidate: PendingCandidate) => void;
  onRefresh?: () => void;
  mode?: "submit" | "resume";
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

const RISK_KIND_LABEL: Record<string, string> = {
  low_confidence_ocr: "OCR 置信度偏低",
  short_content: "正文过短",
  language_mixed: "中英文混杂",
  encoding_warning: "编码告警",
  structure_fragmented: "结构碎片化",
  other: "提示",
};

function describeRiskKind(kind: ReaderCandidateDocumentRiskItem["risk_kind"]): string {
  return RISK_KIND_LABEL[kind] ?? "提示";
}

function hasOutlineOrRisk(candidate: PendingCandidate | null): boolean {
  if (!candidate) return false;
  if (candidate.previewMode === "outline_only") return true;
  if (candidate.documentOutline && candidate.documentOutline.length > 0) return true;
  if (candidate.riskItems && candidate.riskItems.length > 0) return true;
  return false;
}

function getPreviewPresentation(candidate: PendingCandidate | null): { title: string; notice: string | null } {
  const count = candidate?.totalCharCount;
  const countSuffix = typeof count === "number" && count > 0
    ? `（约 ${count.toLocaleString("zh-CN")} 字）`
    : "";
  if (candidate?.previewMode === "truncated_preview") return { title: "内容节选", notice: `内容较长，以下为节选${countSuffix}。` };
  if (candidate?.previewMode === "outline_only") return { title: "内容结构", notice: `内容较长，以下为结构概览${countSuffix}。` };
  return { title: "正文预览", notice: null };
}

export function CandidateConfirmDialog({
  candidate,
  open,
  onOpenChange,
  onConfirmed,
  onRestart,
  onRefresh,
  mode,
}: CandidateConfirmDialogProps) {
  const effectiveMode = mode ?? "submit";
  const isResume = effectiveMode === "resume";
  const [confirmState, setConfirmState] = useState<ConfirmState>({ kind: "idle" });
  const previewText = useMemo(() => getPreviewText(candidate), [candidate]);
  const previewPresentation = useMemo(() => getPreviewPresentation(candidate), [candidate]);

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

      // localStorage owns the submit-origin candidate. In resume mode the
      // BFF is the source of truth and localStorage may carry a different
      // (submit-origin) candidate that must NOT be destroyed by a resume
      // confirm. Only clear when the user is acting on a submit candidate.
      if (!isResume) {
        clearPendingCandidate();
      }
      onConfirmed(candidate);
    } catch (error: unknown) {
      setConfirmState({
        kind: "error",
        message: error instanceof Error ? error.message : "确认失败，请稍后重试。",
      });
    }
  }

  function handleRestart() {
    if (!candidate || isResume) {
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
      ? "这份候选正文的状态已经变化。你可以重试确认。"
      : isResume
        ? "待确认的内容已就绪，确认后即可开始阅读。"
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
              <p className="mt-3 truncate font-sans text-[0.76rem] font-medium text-muted-foreground">
                {getSourceLabel(candidate)}
              </p>
            </div>
          </div>
        </DialogHeader>

        <div className="min-h-0 overflow-hidden px-6 py-5 sm:px-8">
          <div className="flex h-full min-h-[22rem] flex-col rounded-[10px] border border-hairline/70 bg-reader-paper/54">
            <div className="flex items-center justify-between gap-4 border-b border-hairline/60 px-4 py-3 font-sans">
              <p className="text-[0.78rem] font-semibold tracking-[0.08em] text-muted-foreground">
                {previewPresentation.title}
              </p>
              {previewPresentation.notice ? (
                <p className="text-right text-[0.72rem] font-medium text-subtle">
                  {previewPresentation.notice}
                </p>
              ) : null}
            </div>
            <div
              data-testid="candidate-confirm-preview"
              className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap px-5 py-5 font-reading text-[1.02rem] leading-[1.9] text-ink/88 sm:text-[1.08rem]"
            >
              {previewText ? (
                previewText
              ) : (
                <span className="font-sans text-[0.86rem] text-muted-foreground">
                  暂无可展示的正文预览。确认后，Claread 会使用已提取的正文进入透读。
                </span>
              )}
            </div>
            {hasOutlineOrRisk(candidate) ? (
              <div
                data-testid="candidate-confirm-outline-risk"
                className="border-t border-hairline/60 px-5 py-3 font-sans"
              >
                {candidate?.documentOutline && candidate.documentOutline.length > 0 ? (
                  <div className="mb-2">
                    <p className="mb-1 text-[0.72rem] font-semibold tracking-[0.08em] text-muted-foreground">
                      内容结构
                    </p>
                    <ul
                      data-testid="candidate-confirm-outline-list"
                      className="list-disc space-y-0.5 pl-4 text-[0.78rem] leading-snug text-muted-foreground"
                    >
                      {candidate.documentOutline.map((item, index) => (
                        <li
                          key={`${item.order_index}-${index}`}
                          className="marker:text-muted-foreground/60"
                        >
                          <span className="font-medium text-ink/82">
                            {item.block_type_label}
                          </span>
                          {item.heading_text ? (
                            <span className="text-muted-foreground">
                              {" · "}
                              {item.heading_text}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {candidate?.riskItems && candidate.riskItems.length > 0 ? (
                  <div>
                    <p className="mb-1 text-[0.72rem] font-semibold tracking-[0.08em] text-amber-700">
                      阅读提示
                    </p>
                    <ul
                      data-testid="candidate-confirm-risk-list"
                      className="list-disc space-y-0.5 pl-4 text-[0.78rem] leading-snug text-amber-800"
                    >
                      {candidate.riskItems.map((item, index) => (
                        <li key={`${item.risk_kind}-${index}`}>
                          <span className="font-medium">
                            {describeRiskKind(item.risk_kind)}
                          </span>
                          {item.user_message ? (
                            <span className="text-amber-900/80">
                              {" · "}
                              {item.user_message}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
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
              {!isResume && (
                <>
                  {onRefresh ? (
                    <Button type="button" variant="secondary" size="sm" onClick={onRefresh}>
                      刷新页面
                    </Button>
                  ) : null}
                  <Button type="button" variant="secondary" size="sm" onClick={handleRestart}>
                    重新提交
                  </Button>
                </>
              )}
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
            </>
          ) : confirmState.kind === "error" ? (
            <>
              <Button type="button" variant="secondary" size="sm" onClick={() => setConfirmState({ kind: "idle" })}>
                返回确认
              </Button>
              {!isResume && (
                <Button type="button" variant="secondary" size="sm" onClick={handleRestart}>
                  重新提交
                </Button>
              )}
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
              {!isResume && (
                <Button type="button" variant="secondary" size="sm" onClick={handleRestart} disabled={isConfirming}>
                  重新提交
                </Button>
              )}
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
