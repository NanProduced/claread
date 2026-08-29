"use client";

import { Check, FileText } from "lucide-react";
import { cn } from "@/lib/cn";

export type IntakeWaitingPhase = "upload" | "extract" | "check" | "prepare";

export interface IntakePhaseItem {
  id: IntakeWaitingPhase;
  label: string;
  headline: string;
  subtitle?: string;
}

export const INTAKE_WAITING_PHASES: readonly IntakePhaseItem[] = [
  { id: "upload", label: "上传文件", headline: "正在上传文件…" },
  {
    id: "extract",
    label: "提取正文",
    headline: "正在提取正文…",
    subtitle: "离开本页不会影响透读，完成后会保存到阅读记录",
  },
  {
    id: "check",
    label: "检查内容",
    headline: "正在检查内容与排版…",
    subtitle: "离开本页不会影响透读，完成后会保存到阅读记录",
  },
  { id: "prepare", label: "准备阅读", headline: "正在准备阅读环境…" },
] as const;

export interface ReadIntakeWaitingStageProps {
  /** 当前真实等待阶段 */
  phase: IntakeWaitingPhase;
  /** 来源文件名（如有） */
  filename?: string | null;
  /** 来源文件格式短标签（如 PDF / Markdown / TXT / 图片） */
  formatLabel?: string | null;
  /** 来源文件字节大小（如有） */
  fileSize?: number | null;
  /** 是否已由后端 Worker 接管处理（允许离开页面） */
  canLeave?: boolean;
  className?: string;
}

/**
 * 格式化文件大小显示（B / KB / MB）
 */
function formatFileSizeShort(bytes?: number | null): string | null {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) {
    return null;
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/**
 * 输入与等待表面：四真实阶段推进工作台。
 *
 * 遵循极简与安静原则（Notion-like Pragmatic Minimalism）：
 * - 仅呈现 4 个真实后台流水线阶段
 * - 无虚假百分比、无倒计时、无大篇插画与日志式堆叠
 * - 克制呼吸点指示当前阶段，已完成与未开始状态明确
 * - 明确告知用户是否可安全离开页面
 */
export function ReadIntakeWaitingStage({
  phase,
  filename,
  formatLabel,
  fileSize,
  canLeave = false,
  className,
}: ReadIntakeWaitingStageProps) {
  const currentStepIndex = INTAKE_WAITING_PHASES.findIndex(
    (p) => p.id === phase,
  );
  const activePhase =
    INTAKE_WAITING_PHASES[currentStepIndex] ?? INTAKE_WAITING_PHASES[0];

  const formattedSize = formatFileSizeShort(fileSize);

  return (
    <div
      data-testid="read-intake-waiting-stage"
      role="status"
      aria-live="polite"
      className={cn(
        "relative z-10 flex min-h-[22rem] flex-1 flex-col items-center justify-center px-4 py-8 sm:px-8",
        className,
      )}
    >
      <div className="flex w-full max-w-[34rem] flex-col items-center text-center">
        {filename ? (
          <div
            data-testid="waiting-stage-file-chip"
            className="mb-8 inline-flex max-w-[90vw] items-center gap-2 truncate rounded-full border border-hairline/70 bg-surface/75 px-3.5 py-1.5 font-sans text-xs text-muted-foreground shadow-sm"
          >
            <FileText aria-hidden className="h-3.5 w-3.5 shrink-0 text-ink/70" />
            <span className="truncate font-medium text-ink/90">{filename}</span>
            {formatLabel ? (
              <span className="shrink-0 text-subtle">· {formatLabel}</span>
            ) : null}
            {formattedSize ? (
              <span className="shrink-0 text-subtle">· {formattedSize}</span>
            ) : null}
          </div>
        ) : null}

        {/* 4 真实阶段进展轨道 */}
        <div
          data-testid="waiting-stage-phases"
          className="flex w-full items-center justify-between gap-1 sm:gap-2"
        >
          {INTAKE_WAITING_PHASES.map((item, index) => {
            const isCompleted = index < currentStepIndex;
            const isCurrent = index === currentStepIndex;

            const state = isCompleted
              ? "completed"
              : isCurrent
                ? "current"
                : "upcoming";

            return (
              <div
                key={item.id}
                data-testid={`waiting-phase-step-${item.id}`}
                data-state={state}
                className="flex flex-1 items-center"
              >
                <div className="flex flex-col items-center gap-2 text-center w-full">
                  <div className="flex h-5 w-5 items-center justify-center">
                    {isCompleted ? (
                      <span className="inline-flex h-4.5 w-4.5 items-center justify-center rounded-full bg-lens-blue/15 text-lens-blue">
                        <Check aria-hidden className="h-3 w-3 stroke-[2.5]" />
                      </span>
                    ) : isCurrent ? (
                      <span className="relative flex h-4 w-4 items-center justify-center">
                        <span className="absolute inline-flex h-full w-full rounded-full bg-lens-blue/30 opacity-75 motion-safe:animate-ping motion-reduce:animate-none" />
                        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-lens-blue motion-safe:animate-pulse motion-reduce:animate-none" />
                      </span>
                    ) : (
                      <span className="inline-flex h-2 w-2 rounded-full bg-hairline/80" />
                    )}
                  </div>
                  <span
                    className={cn(
                      "font-sans text-[0.72rem] leading-tight sm:text-xs transition-colors duration-150 motion-reduce:transition-none",
                      isCurrent
                        ? "font-semibold text-ink"
                        : isCompleted
                          ? "font-medium text-subtle"
                          : "font-normal text-muted-foreground/60",
                    )}
                  >
                    {item.label}
                  </span>
                </div>

                {index < INTAKE_WAITING_PHASES.length - 1 ? (
                  <div
                    aria-hidden="true"
                    className={cn(
                      "h-px flex-1 shrink-0 -translate-y-3.5 transition-colors duration-150 motion-reduce:transition-none",
                      index < currentStepIndex
                        ? "bg-lens-blue/35"
                        : "bg-hairline/60",
                    )}
                  />
                ) : null}
              </div>
            );
          })}
        </div>

        {/* 当前阶段主指示标题 */}
        <h2
          data-testid="waiting-stage-headline"
          className="mt-8 font-headline text-lg sm:text-xl font-semibold leading-snug text-ink"
        >
          {activePhase.headline}
        </h2>

        {/* 用户安心提示（离开承诺：后端接管后且阶段支持离开时展示） */}
        {canLeave && activePhase.subtitle ? (
          <p
            data-testid="waiting-stage-subtitle"
            className="mt-2.5 max-w-[24rem] font-sans text-xs leading-relaxed text-muted-foreground"
          >
            {activePhase.subtitle}
          </p>
        ) : null}
      </div>
    </div>
  );
}
