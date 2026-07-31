"use client";

/**
 * ASK-UI-NOTION-R1-rework · ASK-UX-COT-COMPOSER-R3 — LearnerProcessView
 * disclosure.
 *
 * The single typed, collapsible, turn-scoped process surface for agentic
 * (reader_record_ask_agentic_v2) turns. Built on the generic
 * ai-elements/chain-of-thought family.
 *
 * Contracts honored here:
 * - Collapsed by default, always. The running header carries a low-weight
 *   shimmer over a fixed user-facing phase label (正在理解问题 /
 *   正在阅读本文 / 正在查询网页 / 正在整理回答). Settled header carries a
 *   fixed one-liner (已整理回答 · Ns / 已根据当前文章整理 · Ns /
 *   已查询网页 · N 个来源 · Ns / 未完成 / 已取消). Never a fabricated
 *   pipeline, never server warning/error summary copy, never
 *   internal-stage labels (分析问题 / 检索文章 / 核对依据).
 * - R3 learner steps: 理解问题 (host-provable for every accepted run) →
 *   阅读本文 (real reading_context only) → 网页查询 (real search_web
 *   only) → 整理回答 (composing row, or host-proved once the answer
 *   exists). 整理回答 stays visible after settle — complete on success,
 *   interrupted on failure / cancellation. Technical internals (retrieval,
 *   evidence validation, provider / tool names) never appear.
 * - R3 safe reasoning ("思考要点"): rendered INSIDE this disclosure —
 *   never a second competing process card. Consumes ONLY the server-side
 *   safe projection (reasoning_md / reasoning_status /
 *   reasoning_truncated — reasoning_projection_v1 redacted text) via the
 *   view model. Empty reasoning_md renders no shell; raw provider
 *   self-talk, system prompts, tool args, queries, URLs, evh handles,
 *   run/message ids, exception text and [引用] placeholders can never
 *   reach this subtree (the projection is leak-proof by construction and
 *   the markdown passes the shared Streamdown sanitizer).
 * - Hot, same-session settled, and cold history can all re-open the
 *   reasoning: cold history shows reasoning ONLY (no fabricated process
 *   steps) when the persisted safe projection exists; with neither a
 *   snapshot nor reasoning, nothing renders.
 * - Warning/error copy is NEVER rendered here: unavailable tools and
 *   terminal explanations stay the sole property of the turn-scoped
 *   Prompt Kit SystemMessage. Degraded/failed steps only change glyph.
 * - Web domains are compact NON-interactive chips (hostname only); the
 *   interactive citation/navigation truth remains AgenticWebSources.
 * - One-shot auto-close is owned by ChainOfThought: default collapsed,
 *   no auto-open; if the user expands during streaming, settle ~1s later
 *   collapses once; subsequent user expands stick (both streaming and
 *   settled states stay click-expandable).
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
  AlertTriangleIcon,
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

const streamdownPlugins = { cjk, code, math, mermaid };

export type TurnProcessDisclosureProps = TurnProcessProjectionInput & {
  className?: string;
};

function RunningPulseGlyph() {
  return (
    <span
      aria-hidden="true"
      data-testid="ask-turn-process-pulse"
      className={cn(
        // R1 — single soft active indicator: a small, low-opacity pulse
        // dot. Not a spinner (the active step already carries the spinner);
        // this is the header-level "working" hint, kept deliberately quiet.
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-lens-blue/70",
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

  const settledGlyph =
    sourceStatus === "failed" ? (
      <XIcon className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden="true" />
    ) : sourceStatus === "cancelled" ? (
      <CircleSlashIcon
        className="size-3.5 shrink-0 text-muted-foreground/70"
        aria-hidden="true"
      />
    ) : sourceStatus === "degraded" ? (
      <AlertTriangleIcon
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
          <span>{view.header.settledCopy ?? "已整理回答"}</span>
        )}
      </ChainOfThoughtHeader>
      {view.steps.length > 0 || view.reasoning ? (
        <ChainOfThoughtContent className="mt-2 pl-5" data-slot="chain-of-thought-content">
          {view.steps.length > 0 ? (
            <div className="relative mt-1">
              {/*
                R1 — light timeline rail: a single hairline-vertical connects
                the step icons down the left edge, creating the AI Elements
                "轻时间线" feel without a second border or card. The rail is
                purely decorative; it sits behind the icons (icon bg matches
                the reading surface) and never extends past the last step.
              */}
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
          {view.reasoning ? (
            /*
              R3 — 思考要点: the server-side SAFE reasoning projection,
              rendered inside the SAME disclosure as the process steps —
              never a second competing process card. The text is the
              reasoning_projection_v1 redacted markdown (no provider
              self-talk, system prompts, tool args, queries, URLs, evh,
              ids, exceptions, [引用] placeholders) and passes the shared
              Streamdown sanitizer before render. Empty text renders no
              body (never an empty shell); the label carries a quiet
              shimmer while the projection is still streaming.
            */
            <div
              data-testid="ask-turn-process-reasoning"
              className={cn(view.steps.length > 0 ? "mt-3" : "mt-1")}
            >
              {view.reasoning.streaming ? (
                <Shimmer
                  as="span"
                  duration={1}
                  className="text-[11px] font-medium text-muted-foreground"
                >
                  思考要点
                </Shimmer>
              ) : (
                <span className="text-[11px] font-medium text-muted-foreground">
                  思考要点
                </span>
              )}
              {view.reasoning.text.trim().length > 0 ? (
                <div className="mt-1 border-l border-border/60 pl-3">
                  <Streamdown
                    className="ask-reasoning-response"
                    plugins={streamdownPlugins}
                  >
                    {sanitizeMarkdownForStreamdown(view.reasoning.text)}
                  </Streamdown>
                </div>
              ) : null}
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
        </ChainOfThoughtContent>
      ) : null}
    </ChainOfThought>
  );
}
