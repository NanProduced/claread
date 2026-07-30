"use client";

/**
 * ASK-COT — Ask Claread turn process disclosure.
 *
 * The single typed, collapsible, turn-scoped process surface for agentic
 * (reader_record_ask_agentic_v2) turns. Converges the two former
 * surfaces — the reasoning disclosure (ReasoningPanel) and the streaming
 * activity row (AssistantStreamingIndicator's agentic branch) — into one
 * Chain of Thought built on the generic ai-elements/chain-of-thought
 * family.
 *
 * Contracts honored here:
 * - Collapsed by default, always; the running header carries a low-weight
 *   shimmer over the fixed typed step label (never a fabricated pipeline
 *   and never server warning/error summary copy).
 * - Only server-projected inputs are rendered: `reasoning_md` is the
 *   reasoning_projection_v1 sanitized text (hot≡cold byte-identical when
 *   reasoning.completed was accepted); steps come from the
 *   reducer-sanitized activity state / snapshot with fixed typed labels.
 *   No raw reasoning, tool args, queries, URLs, evh handles, fingerprints,
 *   or terminal_reason ever reach this DOM subtree.
 * - Warning/error copy is NEVER rendered here: unavailable tools and
 *   terminal explanations stay the sole property of the turn-scoped
 *   Prompt Kit SystemMessage. Degraded/failed steps only change glyph.
 * - Web domains are compact NON-interactive chips (hostname only); the
 *   interactive citation/navigation truth remains AgenticWebSources.
 * - Wording: settled copy says "思考过程" (reasoning present) or
 *   "处理过程" (steps only) — never implying full internal reasoning.
 * - No reasoning AND no activity ⇒ renders nothing (no empty shell).
 * - One-shot auto-close is owned by ChainOfThought: default collapsed,
 *   no auto-open; if the user expands during streaming, settle ~1s later
 *   collapses once; subsequent user expands stick.
 *
 * Cold history renders reasoning-only: activity steps are same-session
 * memory only (snapshot never persists across reload — known gap G3;
 * cold process steps are not yet persisted).
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
import { sanitizeMarkdownForStreamdown } from "@/components/ai-elements/streamdown";
import { cn } from "@/lib/cn";
import { cjk } from "@streamdown/cjk";
import { code } from "@streamdown/code";
import { math } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import {
  CheckIcon,
  CircleSlashIcon,
  Globe2Icon,
  XIcon,
} from "lucide-react";
import { Streamdown } from "streamdown";

import {
  projectTurnProcess,
  type TurnProcessProjectionInput,
} from "../ask/agentic-process-projection";

export type TurnProcessDisclosureProps = TurnProcessProjectionInput & {
  className?: string;
};

const streamdownPlugins = { cjk, code, math, mermaid };

function RunningPulseGlyph() {
  return (
    <span
      aria-hidden="true"
      data-testid="ask-turn-process-pulse"
      className={cn(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-lens-blue/80",
        "motion-safe:animate-pulse",
        "motion-reduce:animate-none",
      )}
    />
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
  const settledCopy = `${view.header.titleHint === "thinking" ? "思考过程" : "处理过程"}${
    view.header.durationS != null ? ` · ${view.header.durationS}s` : ""
  }`;

  const settledGlyph =
    sourceStatus === "failed" ? (
      <XIcon className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden="true" />
    ) : sourceStatus === "cancelled" ? (
      <CircleSlashIcon
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
        className="gap-1.5 text-[12px] font-medium text-muted-foreground"
        glyph={isRunning ? <RunningPulseGlyph /> : settledGlyph}
        aria-label={view.ariaLabel || undefined}
        {...(isRunning && projectionInput.activity
          ? {
              // r2 activity contract: the running header row keeps the
              // legacy test hooks (visibility / phase / sequence). The
              // trigger stays a button (no role override); aria-live
              // announces live-summary changes like the old activity row.
              "data-testid": "ask-agentic-activity",
              "data-activity-status": projectionInput.activity.status,
              "data-activity-phase":
                projectionInput.activity.currentPhase ?? "",
              "data-activity-sequence": String(
                projectionInput.activity.lastSequence,
              ),
              "aria-live": "polite" as const,
              "aria-atomic": "true",
            }
          : {})}
      >
        {isRunning ? (
          <Shimmer as="span" duration={1}>
            {view.header.liveSummary ?? "Ask Claread 正在工作"}
          </Shimmer>
        ) : (
          <span>{settledCopy}</span>
        )}
      </ChainOfThoughtHeader>
      <ChainOfThoughtContent className="mt-2 pl-5" data-slot="chain-of-thought-content">
        {view.reasoning ? (
          <div data-testid="ask-turn-process-reasoning">
            <div className="border-l border-border/60 pl-3">
              <Streamdown
                className="ask-reasoning-response"
                plugins={streamdownPlugins}
              >
                {sanitizeMarkdownForStreamdown(view.reasoning.text)}
              </Streamdown>
            </div>
            {view.reasoning.truncated ? (
              <div
                data-slot="reasoning-truncated"
                data-testid="ask-reasoning-truncated"
                role="status"
                aria-label="推理已达到展示上限"
                className="mt-1 text-[11px] leading-4 text-muted-foreground"
              >
                已达到展示上限，仅显示部分推理内容。
              </div>
            ) : null}
          </div>
        ) : null}
        {view.steps.length > 0 ? (
          <div className={view.reasoning ? "mt-2" : "mt-1"}>
            {view.steps.map((step) => (
              <ChainOfThoughtStep
                key={step.id}
                status={step.status}
                label={step.label}
                durationMs={step.durationMs}
                description={step.attempts ?? undefined}
              >
                {step.domains.length > 0 ? (
                  <ChainOfThoughtSearchResults>
                    {step.domains.map((domain) => (
                      <SearchResult
                        key={domain}
                        domain={domain}
                        icon={
                          <Globe2Icon className="size-3 shrink-0" aria-hidden="true" />
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
