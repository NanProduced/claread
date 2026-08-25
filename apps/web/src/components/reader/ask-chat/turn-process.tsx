"use client";

/**
 * The single learner-facing process disclosure for one assistant turn.
 *
 * ChainOfThought owns the one collapsible surface. This host component owns
 * fixed labels, semantic icons, the provider-reasoning stream and its
 * auto expand/collapse lifecycle (per attempt), plus the small set of dynamic
 * metadata allowed by the process contract. It never renders server
 * summaries, tool names, queries, URLs, ids, handles, or errors.
 */

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
  SearchResult,
} from "@/components/ai-elements/chain-of-thought";
import { primitiveFocusRing } from "@/components/primitives/shared";
import { cn } from "@/lib/cn";
import {
  BrainIcon,
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
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  projectTurnProcess,
  type ProcessStepView,
  type TurnProcessProjectionInput,
  type TurnProcessStepId,
} from "../ask/agentic-process-projection";
import { PROCESS_STEP_ISSUE_MESSAGES } from "../ask/ask-error-messages";

/** Neutral explanation surfaced when safety filtering hides reasoning. */
const BLOCKED_REASONING_EXPLANATION =
  "为避免展示可能包含敏感信息的内容，部分思考已隐藏。";

/** Distance from the reasoning bottom that keeps auto-follow engaged. */
const REASONING_FOLLOW_THRESHOLD_PX = 24;

export type TurnProcessDisclosureProps = TurnProcessProjectionInput & {
  className?: string;
  /**
   * Server-projected visibility of the provider reasoning (`complete`,
   * `truncated`, or `blocked`). Drives the quiet in-container notes.
   */
  reasoningVisibilityStatus?: "complete" | "truncated" | "blocked" | null;
  /**
   * True once the first formal answer delta of the current attempt has
   * landed. Derived by the caller from the canonical answer preview slot.
   */
  answerStarted?: boolean;
  /** Canonical assistant turn status; drives stopped / failed titles. */
  turnStatus?:
    | "streaming"
    | "pending"
    | "completed"
    | "interrupted"
    | "failed"
    | null;
};

type DisclosureMode = "auto" | "manual";

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
  const issue =
    step.status === "failed" || step.status === "degraded"
      ? PROCESS_STEP_ISSUE_MESSAGES[step.id]?.[step.status]
      : null;
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
      {issue ?? detail ? <span>{issue ?? detail}</span> : null}
      {(issue ?? detail) && step.attempts ? <span aria-hidden="true">·</span> : null}
      {step.attempts ? <span>{step.attempts}</span> : null}
    </span>
  );
}

/**
 * Preserve authored line breaks while collapsing runs of blank lines so a
 * long provider reasoning projection never renders as a wall of gaps.
 */
function normalizeReasoningText(value: string): string {
  const lines = value.split("\n");
  const kept: string[] = [];
  let blankRun = 0;
  for (const line of lines) {
    if (line.trim() === "") {
      blankRun += 1;
      if (blankRun > 1) {
        continue;
      }
      kept.push("");
    } else {
      blankRun = 0;
      kept.push(line);
    }
  }
  return kept.join("\n").replace(/\n+$/, "");
}

export function TurnProcessDisclosure({
  className,
  reasoningVisibilityStatus,
  answerStarted = false,
  turnStatus = null,
  ...projectionInput
}: TurnProcessDisclosureProps) {
  const view = projectTurnProcess(projectionInput);

  const reasoningActive = projectionInput.reasoningStatus === "streaming";
  const reasoningText = normalizeReasoningText(projectionInput.reasoningMd ?? "");
  const hasReasoningText = reasoningText.trim().length > 0;
  const visibilityBlocked = reasoningVisibilityStatus === "blocked";
  const truncatedNote =
    !visibilityBlocked &&
    hasReasoningText &&
    (projectionInput.reasoningTruncated === true ||
      reasoningVisibilityStatus === "truncated");
  // Fully filtered reasoning: nothing visible survived, yet the disclosure
  // stays titled and explained instead of rendering an empty shell.
  const fullyBlocked = visibilityBlocked && !hasReasoningText;

  const sourceStatus =
    projectionInput.activity?.status ??
    projectionInput.snapshot?.status ??
    "idle";
  const isRunning = view.header.state === "running";
  // The working state: reasoning is streaming, or the run is live before any
  // reasoning lane exists (providers that never emit one included).
  const thinkingPhase =
    (reasoningActive || (isRunning && projectionInput.reasoningStatus == null)) &&
    !answerStarted &&
    turnStatus !== "interrupted" &&
    turnStatus !== "failed";

  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<DisclosureMode>("auto");
  const [announcement, setAnnouncement] = useState("");

  // Reasoning-phase duration measured from real event transitions; cold
  // history has no measurement and falls back to the run-level duration.
  const reasoningStartedAtRef = useRef<number | null>(null);
  const reasoningEndedAtRef = useRef<number | null>(null);
  const [, forceDurationTick] = useState(0);
  const reasoningScrollRef = useRef<HTMLDivElement | null>(null);
  const followBottomRef = useRef(true);

  // Per-attempt identity: a fresh live run id restarts the automatic
  // lifecycle, so a previous attempt's manual pick never leaks forward.
  const attemptEpoch =
    projectionInput.activity?.turnRunId ?? null;
  const prevEpochRef = useRef<string | null>(null);
  const transitionPrevRef = useRef({
    reasoningActive: false,
    answerStarted: false,
    stopped: false,
  });

  useEffect(() => {
    if (attemptEpoch === null) {
      return;
    }
    if (prevEpochRef.current !== null && attemptEpoch !== prevEpochRef.current) {
      setMode("auto");
    }
    prevEpochRef.current = attemptEpoch;
  }, [attemptEpoch]);

  useEffect(() => {
    const previous = transitionPrevRef.current;
    const stoppedNow = turnStatus === "interrupted" || turnStatus === "failed";
    transitionPrevRef.current = {
      reasoningActive,
      answerStarted,
      stopped: stoppedNow,
    };

    if (reasoningActive && !previous.reasoningActive) {
      followBottomRef.current = true;
      reasoningStartedAtRef.current = performance.now();
      reasoningEndedAtRef.current = null;
      forceDurationTick((tick) => tick + 1);
    }
    if (
      !reasoningActive &&
      previous.reasoningActive &&
      reasoningStartedAtRef.current != null &&
      reasoningEndedAtRef.current == null
    ) {
      reasoningEndedAtRef.current = performance.now();
      forceDurationTick((tick) => tick + 1);
    }

    // A single polite region announces fixed milestones only; reasoning
    // deltas themselves never enter the live region.
    let phrase: string | null = null;
    if (stoppedNow && !previous.stopped) {
      phrase = "思考已停止";
    } else if (reasoningActive && !previous.reasoningActive) {
      phrase = "开始思考";
    } else if (answerStarted && !previous.answerStarted) {
      phrase = "开始生成回答";
    } else if (!reasoningActive && previous.reasoningActive) {
      phrase = "思考完成";
    }
    if (phrase != null) {
      setAnnouncement(phrase);
    }
  });

  useEffect(() => {
    if (mode === "manual" || !reasoningActive || answerStarted || open) {
      return;
    }
    setOpen(true);
  }, [mode, reasoningActive, answerStarted, open]);

  useEffect(() => {
    if (mode === "manual" || !answerStarted || !open) {
      return;
    }
    setOpen(false);
  }, [mode, answerStarted, open]);

  const handleOpenChange = useCallback((nextOpen: boolean) => {
    // Any trigger interaction hands ownership to the user for this attempt.
    setMode("manual");
    setOpen(nextOpen);
  }, []);

  // Stick-to-bottom for the streaming reasoning body. Following pauses when
  // the reader scrolls away from the bottom and resumes once they return.
  const handleReasoningScroll = useCallback(() => {
    const element = reasoningScrollRef.current;
    if (!element) {
      return;
    }
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    followBottomRef.current = distance <= REASONING_FOLLOW_THRESHOLD_PX;
  }, []);
  useEffect(() => {
    const element = reasoningScrollRef.current;
    if (!element || !reasoningActive || !followBottomRef.current) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  });

  const measuredDurationS = (() => {
    const startedAt = reasoningStartedAtRef.current;
    const endedAt = reasoningEndedAtRef.current;
    if (startedAt == null || endedAt == null) {
      return null;
    }
    return Math.max(1, Math.round((endedAt - startedAt) / 1000));
  })();
  const durationS = measuredDurationS ?? view.header.durationS;

  const stepCount = view.steps.length;
  const titleParts: string[] = [];
  if (fullyBlocked) {
    titleParts.push("思考完毕 · 部分内容未展示");
  } else if (turnStatus === "failed") {
    titleParts.push("思考未完成");
  } else if (turnStatus === "interrupted") {
    titleParts.push("思考已停止");
  } else if (thinkingPhase) {
    titleParts.push("正在思考…");
  } else {
    const base = ["思考完毕"];
    if (durationS != null) {
      base.push(`${durationS}s`);
    }
    titleParts.push(base.join(" · "));
  }
  if (!thinkingPhase && stepCount > 0) {
    titleParts.push(`${stepCount} 个步骤`);
  }
  const title = titleParts.join(" · ");

  // Render only when real content exists: never an empty container.
  const hasReasoningSurface =
    reasoningActive || hasReasoningText || visibilityBlocked;
  if (!hasReasoningSurface && stepCount === 0) {
    return null;
  }

  const statusGlyph =
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
    ) : undefined;

  return (
    <ChainOfThought
      // This host owns the full open/close contract below; the primitive's
      // own settle timer stays off so it cannot fight the spec'd lifecycle.
      isStreaming={false}
      open={open}
      onOpenChange={handleOpenChange}
      className={cn("mb-0.5", className)}
      data-testid="ask-turn-process"
      data-turn-process-state={view.header.state}
    >
      <ChainOfThoughtHeader
        className={cn(
          "min-h-5 w-fit max-w-full gap-1 rounded-sm px-0 text-sm leading-5 font-normal max-md:min-h-11 motion-safe:transition-colors motion-safe:duration-[var(--cl-duration-fast)] hover:text-foreground",
          primitiveFocusRing,
        )}
        glyph={thinkingPhase ? <RunningPulseGlyph /> : statusGlyph}
        {...(thinkingPhase
          ? {
              "data-testid": "ask-agentic-activity",
              "data-activity-status": projectionInput.activity?.status ?? "idle",
              "data-activity-phase": projectionInput.activity?.currentPhase ?? "",
              "data-activity-sequence": String(
                projectionInput.activity?.lastSequence ?? 0,
              ),
            }
          : {})}
      >
        <span>{title}</span>
      </ChainOfThoughtHeader>
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid="ask-turn-process-announcement"
        className="sr-only"
      >
        {announcement}
      </div>
      <ChainOfThoughtContent
        className="mt-1.5 pl-0"
        data-slot="chain-of-thought-content"
      >
        {fullyBlocked ? (
          <p className="pr-1 text-[13px] leading-5 font-normal text-muted-foreground">
            {BLOCKED_REASONING_EXPLANATION}
          </p>
        ) : hasReasoningText ? (
          <div
            ref={reasoningScrollRef}
            role="region"
            tabIndex={0}
            onScroll={handleReasoningScroll}
            data-testid="ask-turn-process-reasoning"
            aria-label="思考内容"
            className={cn(
              "max-h-[min(176px,28dvh)] overflow-y-auto overflow-x-hidden pr-1",
              "whitespace-pre-wrap break-words [overflow-wrap:anywhere]",
              "text-sm font-normal leading-5 text-muted-foreground",
              primitiveFocusRing,
            )}
          >
            {reasoningText}
          </div>
        ) : null}
        {truncatedNote ? (
          <p
            data-testid="ask-turn-process-truncated-note"
            aria-label="内容较长，仅展示部分"
            className="mt-2 text-xs leading-4 text-muted-foreground"
          >
            内容较长，仅展示部分
          </p>
        ) : null}
        {visibilityBlocked && hasReasoningText ? (
          <p
            data-testid="ask-turn-process-blocked-note"
            title={BLOCKED_REASONING_EXPLANATION}
            aria-label={BLOCKED_REASONING_EXPLANATION}
            className="mt-2 text-xs leading-4 text-muted-foreground"
          >
            部分思考未展示
          </p>
        ) : null}
        {stepCount > 0 ? (
          <div className="relative mt-2">
            {stepCount > 1 ? (
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
        ) : null}
      </ChainOfThoughtContent>
    </ChainOfThought>
  );
}
