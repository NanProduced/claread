"use client";

/**
 * The single agentic v2 learner-facing Answer Process disclosure.
 *
 * ChainOfThought owns the one collapsible surface. This host component owns
 * only fixed labels, semantic icons, and the small set of dynamic metadata
 * allowed by the Answer Process contract. It never renders reasoning text,
 * server summaries, tool names, queries, URLs, ids, handles, or errors.
 */

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
  SearchResult,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { cn } from "@/lib/cn";
import {
  BrainIcon,
  CheckIcon,
  CircleSlashIcon,
  CircleXIcon,
  FileSearchIcon,
  Globe2Icon,
  PencilLineIcon,
  SearchXIcon,
  ShieldCheckIcon,
  TriangleAlertIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";

import {
  projectTurnProcess,
  type ProcessStepView,
  type TurnProcessProjectionInput,
  type TurnProcessStepId,
} from "../ask/agentic-process-projection";

export type TurnProcessDisclosureProps = TurnProcessProjectionInput & {
  className?: string;
};

function RunningPulseGlyph() {
  return (
    <span
      aria-hidden="true"
      data-testid="ask-turn-process-pulse"
      className={cn(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-lens-blue/70",
        "motion-safe:animate-pulse",
        "motion-reduce:animate-none",
      )}
    />
  );
}

const TURN_PROCESS_STEP_ICONS: Record<TurnProcessStepId, LucideIcon> = {
  analysis: BrainIcon,
  "article-evidence": FileSearchIcon,
  "web-evidence": Globe2Icon,
  answering: PencilLineIcon,
  "citation-check": ShieldCheckIcon,
};

function TurnProcessStepGlyph({ step }: { step: ProcessStepView }) {
  const Icon =
    step.outcome === "empty"
      ? SearchXIcon
      : step.outcome === "degraded"
        ? TriangleAlertIcon
        : step.outcome === "failed"
          ? CircleXIcon
          : step.outcome === "interrupted"
            ? CircleSlashIcon
            : TURN_PROCESS_STEP_ICONS[step.id];

  return (
    <Icon
      aria-hidden="true"
      className={cn(
        "size-3.5 shrink-0",
        step.status === "active"
          ? "text-foreground motion-safe:animate-pulse"
          : "text-muted-foreground/80",
      )}
      data-step-icon={step.id}
      data-step-icon-status={step.status}
      data-step-icon-outcome={step.outcome ?? ""}
    />
  );
}

function stepMetadata(step: ProcessStepView) {
  const detail =
    step.detail === "no_results"
      ? step.id === "article-evidence"
        ? "未找到相关文章依据"
        : "未找到相关网页结果"
      : step.detail === "degraded"
        ? "部分可用信息"
        : null;
  const status =
    step.outcome === "empty"
      ? "未找到结果"
      : step.outcome === "degraded"
        ? "部分不可用"
        : step.outcome === "failed" || step.status === "failed"
          ? "失败"
          : step.outcome === "interrupted" || step.status === "interrupted"
            ? "已中断"
            : step.status === "active"
              ? "进行中"
              : "已完成";
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="sr-only" data-step-accessible-status="true">
        {status}
      </span>
      {detail ? <span>{detail}</span> : null}
      {detail && step.attempts ? <span aria-hidden="true">·</span> : null}
      {step.attempts ? <span>{step.attempts}</span> : null}
    </span>
  );
}

export function TurnProcessDisclosure({
  className,
  ...projectionInput
}: TurnProcessDisclosureProps) {
  const view = projectTurnProcess(projectionInput);
  if (!view.visible) {
    return null;
  }

  const sourceStatus =
    projectionInput.activity?.status ??
    projectionInput.snapshot?.status ??
    "idle";
  const isRunning = view.header.state === "running";
  const settledGlyph =
    sourceStatus === "failed" ? (
      <XIcon className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden="true" />
    ) : sourceStatus === "cancelled" ? (
      <CircleSlashIcon
        className="size-3.5 shrink-0 text-muted-foreground/70"
        aria-hidden="true"
      />
    ) : sourceStatus === "degraded" ? (
      <TriangleAlertIcon
        className="size-3.5 shrink-0 text-muted-foreground/70"
        aria-hidden="true"
      />
    ) : (
      <CheckIcon
        className="size-3.5 shrink-0 text-muted-foreground/70"
        aria-hidden="true"
      />
    );

  return (
    <ChainOfThought
      isStreaming={isRunning}
      className={cn("mb-0.5", className)}
      data-testid="ask-turn-process"
      data-turn-process-state={view.header.state}
    >
      <ChainOfThoughtHeader
        className="min-h-11 gap-1.5 text-xs font-medium text-muted-foreground sm:min-h-0"
        glyph={isRunning ? <RunningPulseGlyph /> : settledGlyph}
        aria-label={view.ariaLabel || undefined}
        {...(isRunning
          ? {
              "data-testid": "ask-agentic-activity",
              "data-activity-status": projectionInput.activity?.status ?? "idle",
              "data-activity-phase": projectionInput.activity?.currentPhase ?? "",
              "data-activity-sequence": String(
                projectionInput.activity?.lastSequence ?? 0,
              ),
              "aria-live": "polite" as const,
              "aria-atomic": "true",
            }
          : {})}
      >
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <span className="shrink-0">回答过程</span>
          {isRunning && view.header.liveSummary ? (
            <Shimmer as="span" duration={1} className="truncate">
              {`· ${view.header.liveSummary}`}
            </Shimmer>
          ) : !isRunning && view.header.settledCopy ? (
            <span className="truncate">· {view.header.settledCopy}</span>
          ) : null}
        </span>
      </ChainOfThoughtHeader>
      {view.steps.length > 0 ? (
        <ChainOfThoughtContent
          className="mt-2 pl-5"
          data-slot="chain-of-thought-content"
        >
          <div className="relative mt-1">
            {view.steps.length > 1 ? (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute bottom-1 left-[6px] top-2 w-px bg-hairline/50 motion-reduce:hidden"
                data-testid="ask-turn-process-rail"
              />
            ) : null}
            {view.steps.map((step) => (
              <ChainOfThoughtStep
                key={step.id}
                status={step.status}
                label={step.label}
                durationMs={step.durationMs}
                description={stepMetadata(step)}
                icon={<TurnProcessStepGlyph step={step} />}
                data-step-id={step.id}
                data-step-lifecycle={step.lifecycle}
                data-step-outcome={step.outcome ?? ""}
              >
                {step.domains.length > 0 ? (
                  <ChainOfThoughtSearchResults>
                    {step.domains.map((domain) => (
                      <SearchResult
                        key={domain}
                        domain={domain}
                        icon={
                          <Globe2Icon
                            className="size-3 shrink-0"
                            aria-hidden="true"
                          />
                        }
                      />
                    ))}
                  </ChainOfThoughtSearchResults>
                ) : null}
              </ChainOfThoughtStep>
            ))}
          </div>
        </ChainOfThoughtContent>
      ) : null}
    </ChainOfThought>
  );
}
