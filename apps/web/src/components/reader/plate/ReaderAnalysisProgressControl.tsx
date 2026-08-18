"use client";

import { useEffect, useRef, useState } from "react";
import { TextSearch } from "lucide-react";

import { Button } from "@/components/primitives/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/primitives/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/primitives/tooltip";
import { readerTopBarAction } from "@/components/reader/interaction";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/cn";
import type {
  ReaderAnalysisActivePhase,
  ReaderAnalysisCapabilityStatus,
  ReaderAnalysisOverallStatus,
  ReaderAnalysisProgressDto,
  ReaderAnalysisSectionProgressDto,
  ReaderAnalysisSectionRequestOutcome,
} from "@/types/api/reader-plate";

export interface ReaderAnalysisProgressControlProps {
  recordId: string;
  progress: ReaderAnalysisProgressDto;
  onRequestSnapshotReload?: () => void | Promise<void>;
}

const OVERALL_LABELS: Record<ReaderAnalysisOverallStatus, string> = {
  queued: "等待解析",
  processing: "解析中",
  waiting_user: "可继续解析",
  completed: "解析完成",
  partial: "部分完成",
  failed: "需要处理",
  paused_quota: "解析已暂停",
};

const CAPABILITY_LABELS: Record<ReaderAnalysisCapabilityStatus, string> = {
  not_started: "未开始",
  queued: "排队中",
  processing: "解析中",
  completed: "已完成",
  partial: "部分完成",
  failed: "暂时未完成",
  paused_quota: "已暂停",
};

const REJECTED_REASON_LABELS: Record<string, string> = {
  analysis_mode_not_segmented: "当前文章会自动完成解析，无需手动开始",
  analysis_section_not_found: "文章内容已更新，请刷新后重试",
  analysis_section_not_runnable: "这一部分当前暂时无法开始",
};

const OUTCOME_LABELS: Record<ReaderAnalysisSectionRequestOutcome, string> = {
  started: "已开始解析",
  already_active: "正在解析中",
  already_complete: "这部分已经完成",
  paused_quota: "当前积分不足，解析已暂停",
  rejected: "当前暂时无法开始解析，请刷新后重试",
};

function collapsedStatusLabel(progress: ReaderAnalysisProgressDto): string {
  const status = progress.overall_status;
  if (status === "queued" || status === "processing") {
    if (progress.active_phase === "translation") {
      return "准备译文";
    }
    return status === "queued" ? "等待解析" : "解析中";
  }
  return OVERALL_LABELS[status] ?? "文章解析";
}

function capabilityLabel(status: string): string | null {
  return status in CAPABILITY_LABELS
    ? CAPABILITY_LABELS[status as ReaderAnalysisCapabilityStatus]
    : null;
}

function sectionTitle(section: ReaderAnalysisSectionProgressDto, index: number): string {
  const label = typeof section.label === "string" ? section.label.trim() : "";
  if (label.length > 0) {
    return label;
  }
  const order =
    typeof section.order_index === "number" && Number.isInteger(section.order_index) && section.order_index >= 0
      ? section.order_index + 1
      : index + 1;
  return `第 ${order} 部分`;
}

function phaseDescription(
  activePhase: ReaderAnalysisActivePhase | null,
  translationStatus: string,
): string | null {
  if (activePhase === "translation" || translationStatus === "processing" || translationStatus === "queued") {
    return "正在准备全文译文。";
  }
  if (activePhase === "analysis") {
    return "正在解析词汇与语法。";
  }
  const translationLabel = capabilityLabel(translationStatus);
  if (translationLabel && translationStatus !== "not_started") {
    return `译文${translationLabel}。`;
  }
  return null;
}

function outcomeFeedback(
  outcome: string,
  reasonCode: string | null,
): string {
  if (outcome === "rejected") {
    if (reasonCode && reasonCode in REJECTED_REASON_LABELS) {
      return REJECTED_REASON_LABELS[reasonCode];
    }
    return "当前暂时无法开始解析，请刷新后重试";
  }
  if (outcome in OUTCOME_LABELS) {
    return OUTCOME_LABELS[outcome as ReaderAnalysisSectionRequestOutcome];
  }
  return "当前暂时无法开始解析，请刷新后重试";
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

const RECOVERY_EXPLANATION =
  "部分解析没有完成，但正文和已完成内容仍可阅读。重新尝试不会重复扣费。";
const RECOVERY_UNAVAILABLE_FEEDBACK =
  "服务暂时不可用，请稍后重试。正文和已完成内容不会丢失。";

type RecoveryOutcome = "recovery_started" | "nothing_to_recover";

type RecoveryRequestResult =
  | { ok: true; outcome: RecoveryOutcome }
  | { ok: false; status: number | null };

async function submitReaderRecordRecovery(
  recordId: string,
): Promise<RecoveryRequestResult> {
  const response = await fetch(
    `/api/web/reader/records/${encodeURIComponent(recordId)}/recovery`,
    { method: "POST" },
  );
  const payload = (await response.json().catch(() => null)) as
    | { ok?: unknown; outcome?: unknown }
    | null;
  // Never surface raw envelope text: feedback is fixed per status below.
  // Trust only a 2xx response whose body carries the ok flag and one of
  // the two known outcomes; anything else is an unavailable request.
  const outcome = payload?.outcome;
  if (
    response.ok &&
    payload?.ok === true &&
    (outcome === "recovery_started" || outcome === "nothing_to_recover")
  ) {
    return { ok: true, outcome };
  }
  return { ok: false, status: response.status };
}

function recoveryFailureFeedback(status: number | null): string {
  if (status === 401) {
    return "登录状态已失效，请重新登录后再试。";
  }
  if (status === 404) {
    return "没有找到这条阅读记录，请返回资料库确认后再试。";
  }
  if (status === 409) {
    return "当前状态暂时无法恢复。正文和已完成内容仍会保留，请稍后刷新。";
  }
  return RECOVERY_UNAVAILABLE_FEEDBACK;
}

function isKnownCapability(value: unknown): value is ReaderAnalysisCapabilityStatus {
  return typeof value === "string" && value in CAPABILITY_LABELS;
}

function isPlainSection(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isConsistentSegmentedProgress(progress: ReaderAnalysisProgressDto): boolean {
  const sections = progress.sections;
  if (!Array.isArray(sections)) {
    return false;
  }
  if (
    !isNonNegativeInteger(progress.completed_section_count) ||
    !isNonNegativeInteger(progress.total_section_count) ||
    progress.completed_section_count > progress.total_section_count ||
    progress.total_section_count !== sections.length
  ) {
    return false;
  }

  const sectionIds = new Set<string>();
  const orderIndexes = new Set<number>();
  for (const rawSection of sections) {
    if (!isPlainSection(rawSection)) {
      return false;
    }
    const sectionId = rawSection.section_id;
    if (typeof sectionId !== "string") {
      return false;
    }
    const normalizedId = sectionId.trim();
    if (normalizedId.length === 0 || normalizedId !== sectionId) {
      return false;
    }
    if (!isNonNegativeInteger(rawSection.order_index)) {
      return false;
    }
    if (sectionIds.has(normalizedId) || orderIndexes.has(rawSection.order_index)) {
      return false;
    }
    sectionIds.add(normalizedId);
    orderIndexes.add(rawSection.order_index);
    if (
      !isKnownCapability(rawSection.status) ||
      !isKnownCapability(rawSection.vocabulary_status) ||
      !isKnownCapability(rawSection.grammar_status) ||
      typeof rawSection.can_start !== "boolean"
    ) {
      return false;
    }
  }

  if (progress.active_section_id !== null) {
    return (
      typeof progress.active_section_id === "string" &&
      sectionIds.has(progress.active_section_id)
    );
  }
  return true;
}

function sortedSections(
  sections: ReaderAnalysisSectionProgressDto[],
): ReaderAnalysisSectionProgressDto[] {
  return [...sections].sort((a, b) => a.order_index - b.order_index);
}

type RequestResult =
  | {
      ok: true;
      outcome: string;
      reason_code: string | null;
    }
  | {
      ok: false;
      message: string;
    };

async function submitAnalysisSectionRequest(
  recordId: string,
  body: { scope: "single" | "remaining"; sectionId: string | null },
): Promise<RequestResult> {
  const response = await fetch(
    `/api/web/reader/records/${encodeURIComponent(recordId)}/analysis-sections/requests`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  const payload = (await response.json().catch(() => null)) as
    | {
        ok?: unknown;
        outcome?: unknown;
        reason_code?: unknown;
        message?: unknown;
      }
    | null;
  if (payload?.ok === true && typeof payload.outcome === "string") {
    return {
      ok: true,
      outcome: payload.outcome,
      reason_code: typeof payload.reason_code === "string" ? payload.reason_code : null,
    };
  }
  if (payload && payload.ok === false && typeof payload.message === "string" && payload.message.trim()) {
    return { ok: false, message: payload.message };
  }
  return { ok: false, message: "当前暂时无法开始解析，请刷新后重试。" };
}

export function ReaderAnalysisProgressControl({
  recordId,
  progress,
  onRequestSnapshotReload,
}: ReaderAnalysisProgressControlProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const observationKey = `${recordId}:${progress.plan_version}`;
  const sawMountRef = useRef(false);
  const prevStatusRef = useRef<ReaderAnalysisOverallStatus | null>(null);
  const autoOpenedKeyRef = useRef<string | null>(null);
  const programmaticOpenRef = useRef(false);
  const observedKeyRef = useRef(observationKey);

  const statusLabel = collapsedStatusLabel(progress);
  const showSpinner = progress.overall_status === "processing";
  const segmented = progress.mode === "segmented_on_demand";
  const automatic = progress.mode === "automatic";
  const detailsTrusted = segmented && isConsistentSegmentedProgress(progress);
  const sections = detailsTrusted ? sortedSections(progress.sections) : [];
  const startableSections = detailsTrusted
    ? sections.filter((section) => section.can_start === true)
    : [];
  const showRemaining = detailsTrusted && startableSections.length >= 2;
  const showRecoveryAction =
    progress.overall_status === "failed" &&
    (automatic || progress.translation_status === "failed");

  useEffect(() => {
    if (observedKeyRef.current === observationKey) {
      return;
    }
    observedKeyRef.current = observationKey;
    sawMountRef.current = false;
    prevStatusRef.current = null;
    autoOpenedKeyRef.current = null;
    programmaticOpenRef.current = false;
    setOpen(false);
    setFeedback(null);
    setPending(false);
  }, [observationKey]);

  useEffect(() => {
    if (!sawMountRef.current) {
      sawMountRef.current = true;
      prevStatusRef.current = progress.overall_status;
      return;
    }

    const previous = prevStatusRef.current;
    prevStatusRef.current = progress.overall_status;

    if (!detailsTrusted) {
      return;
    }
    if (previous === "waiting_user" || progress.overall_status !== "waiting_user") {
      return;
    }
    if (progress.completed_section_count < 1) {
      return;
    }
    if (startableSections.length === 0) {
      return;
    }
    if (autoOpenedKeyRef.current === observationKey) {
      return;
    }

    programmaticOpenRef.current = true;
    autoOpenedKeyRef.current = observationKey;
    setOpen(true);
  }, [
    detailsTrusted,
    observationKey,
    progress.completed_section_count,
    progress.overall_status,
    startableSections.length,
  ]);

  async function requestAnalysis(
    scope: "single" | "remaining",
    sectionId: string | null,
  ) {
    if (pending) {
      return;
    }
    setPending(true);
    setFeedback(null);
    try {
      const result = await submitAnalysisSectionRequest(recordId, { scope, sectionId });
      if (!result.ok) {
        setFeedback(result.message);
        return;
      }
      const outcomeText = outcomeFeedback(result.outcome, result.reason_code);
      setFeedback(outcomeText);
      const shouldReload =
        result.outcome === "started" ||
        result.outcome === "already_active" ||
        result.outcome === "already_complete" ||
        result.outcome === "paused_quota";
      if (!shouldReload) {
        return;
      }
      try {
        await onRequestSnapshotReload?.();
      } catch {
        setFeedback(`${outcomeText}，状态暂未刷新，请稍后再试。`);
      }
    } catch {
      setFeedback("当前暂时无法开始解析，请刷新后重试。");
    } finally {
      setPending(false);
    }
  }

  async function requestRecovery() {
    if (pending) {
      return;
    }
    setPending(true);
    setFeedback(null);
    try {
      const result = await submitReaderRecordRecovery(recordId);
      if (!result.ok) {
        setFeedback(recoveryFailureFeedback(result.status));
        return;
      }
      const successText =
        result.outcome === "recovery_started"
          ? "已重新开始解析，你可以继续阅读。"
          : "当前没有需要重试的解析，已刷新最新状态。";
      setFeedback(successText);
      try {
        await onRequestSnapshotReload?.();
      } catch {
        setFeedback(`${successText}状态暂未刷新，请稍后再试。`);
      }
    } catch {
      setFeedback(RECOVERY_UNAVAILABLE_FEEDBACK);
    } finally {
      setPending(false);
    }
  }

  return (
    <TooltipProvider>
      <Popover
        open={open}
        onOpenChange={(next) => {
          programmaticOpenRef.current = false;
          setOpen(next);
        }}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <button
                type="button"
                data-testid="reader-analysis-progress-trigger"
                aria-label={statusLabel}
                aria-expanded={open}
                className={cn(
                  readerTopBarAction,
                  "w-auto min-w-9 gap-1.5 px-2 text-muted-foreground/90 hover:text-ink max-md:w-9 max-md:px-0",
                )}
              >
                {showSpinner ? (
                  <Spinner
                    className="size-[15px] motion-reduce:animate-none"
                    aria-hidden="true"
                    aria-label={undefined}
                    role={undefined}
                  />
                ) : (
                  <TextSearch className="h-[18px] w-[18px]" strokeWidth={1.5} aria-hidden="true" />
                )}
                <span className="max-w-[7.5rem] truncate text-[0.78rem] font-medium tracking-[0.01em] max-md:sr-only">
                  {statusLabel}
                </span>
              </button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">{statusLabel}</TooltipContent>
        </Tooltip>
        <PopoverContent
          align="end"
          data-testid="reader-analysis-progress-popover"
          onOpenAutoFocus={(event) => {
            if (programmaticOpenRef.current) {
              event.preventDefault();
              programmaticOpenRef.current = false;
            }
          }}
          className="flex max-h-[min(28rem,calc(100dvh-5rem))] w-[min(22.5rem,calc(100vw-1rem))] max-w-[calc(100vw-1rem)] flex-col overflow-y-auto p-0 outline-none focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring/30"
        >
          <div className="border-b border-hairline px-4 py-3">
            <h2 className="text-sm font-semibold tracking-[0.01em] text-ink">文章解析</h2>
            <p className="mt-1 text-[0.78rem] leading-5 text-muted-foreground">
              {automatic
                ? "这篇文章会自动完成译文、词汇与语法解析。"
                : segmented
                  ? "全文译文会先准备；词汇与语法按部分生成，你可以按阅读需要继续。"
                  : "文章解析状态已更新。"}
            </p>
            {automatic && progress.overall_status === "completed" ? (
              <p className="mt-2 text-[0.78rem] leading-5 text-ink">
                译文、词汇与语法解析已完成。
              </p>
            ) : automatic && phaseDescription(progress.active_phase, progress.translation_status) ? (
              <p className="mt-2 text-[0.78rem] leading-5 text-ink">
                {phaseDescription(progress.active_phase, progress.translation_status)}
              </p>
            ) : null}
            {detailsTrusted ? (
              <p className="mt-2 text-[0.78rem] leading-5 text-ink">
                已完成 {progress.completed_section_count} / {progress.total_section_count} 部分
              </p>
            ) : null}
            {segmented && !detailsTrusted ? (
              <p
                data-testid="reader-analysis-progress-unavailable"
                className="mt-2 text-[0.78rem] leading-5 text-ink"
              >
                解析详情暂时无法更新，请稍后重试。
              </p>
            ) : null}
            {showRecoveryAction ? (
              <div
                data-testid="reader-analysis-recovery"
                className="mt-3 rounded-md border border-hairline px-3 py-2.5"
              >
                <p className="text-[0.78rem] leading-5 text-ink">
                  {RECOVERY_EXPLANATION}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  density="compact"
                  className="mt-2"
                  disabled={pending}
                  onClick={() => void requestRecovery()}
                >
                  {pending ? "正在重新尝试…" : "重新尝试解析"}
                </Button>
              </div>
            ) : null}
          </div>

          {detailsTrusted && sections.length > 0 ? (
            <ul className="divide-y divide-hairline">
              {sections.map((section, index) => {
                const statusText = capabilityLabel(section.status);
                const vocabText = capabilityLabel(section.vocabulary_status);
                const grammarText = capabilityLabel(section.grammar_status);
                const excerpt =
                  typeof section.excerpt === "string" ? section.excerpt.trim() : "";
                const failed = section.status === "failed";
                const startLabel = failed ? "重试这一部分" : "解析这一部分";
                return (
                  <li key={section.section_id || `section-${index}`} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-[0.82rem] font-medium text-ink">
                          {sectionTitle(section, index)}
                        </p>
                        {excerpt ? (
                          <p className="mt-1 line-clamp-2 text-[0.75rem] leading-5 text-muted-foreground">
                            {excerpt}
                          </p>
                        ) : null}
                        {statusText ? (
                          <p className="mt-1 text-[0.72rem] text-muted-foreground">{statusText}</p>
                        ) : null}
                        {vocabText || grammarText ? (
                          <p className="mt-0.5 text-[0.72rem] text-muted-foreground">
                            {[
                              vocabText ? `词汇 ${vocabText}` : null,
                              grammarText ? `语法 ${grammarText}` : null,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </p>
                        ) : null}
                      </div>
                      {section.can_start === true &&
                      typeof section.section_id === "string" &&
                      section.section_id.trim().length > 0 ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          density="compact"
                          disabled={pending}
                          onClick={() => void requestAnalysis("single", section.section_id)}
                        >
                          {startLabel}
                        </Button>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}

          {showRemaining ? (
            <div className="border-t border-hairline px-4 py-3">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                density="compact"
                className="w-full justify-center text-muted-foreground"
                disabled={pending}
                onClick={() => void requestAnalysis("remaining", null)}
              >
                解析全部剩余部分
              </Button>
            </div>
          ) : null}

          {feedback ? (
            <p
              data-testid="reader-analysis-progress-feedback"
              className="border-t border-hairline px-4 py-3 text-[0.78rem] leading-5 text-ink"
              aria-live="polite"
            >
              {feedback}
            </p>
          ) : null}
        </PopoverContent>
      </Popover>
    </TooltipProvider>
  );
}
