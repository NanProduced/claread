"use client";

import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { cn } from "@/lib/cn";
import type { ReaderAskMessageDto } from "@/types/api/reader-ask";

type ReasoningPanelProps = {
  reasoningMd: string | null | undefined;
  reasoningStatus: ReaderAskMessageDto["reasoning_status"];
  /**
   * ASK-TURN-LIFECYCLE R3 — when `true`, the visible `reasoningMd` was
   * truncated by the server-side projection char cap. The panel surfaces
   * an explicit "达到展示上限" indicator below the reasoning body. The
   * body itself never carries a truncation marker.
   */
  reasoningTruncated?: boolean;
  className?: string;
  // markdownComponents?: Partial<Components>; // TODO: ai-elements/reasoning
  // uses Streamdown internally; once it exposes markdownComponents we can
  // re-surface ASK_MARKDOWN_COMPONENTS from AiWorkspacePanel.
};

/**
 * Ask reasoning surface (legacy reasoning.* + agentic.reasoning.* share it).
 *
 * ASK-REASONING-R1 contract:
 * - collapsed by default (`defaultOpen={false}` — no auto-open while
 *   streaming); the trigger carries a low-weight shimmer while running;
 * - expanded view appends projected text live as deltas arrive;
 * - completed/interrupted stay collapsed and re-expandable;
 * - no fabricated content: when there is no actual reasoning (provider
 *   returned none, or the turn never produced a projection), nothing
 *   renders — never an empty "model returned no reasoning" placeholder.
 *
 * ASK-TURN-LIFECYCLE R3:
 * - `reasoningTruncated` toggles an explicit "达到展示上限" indicator
 *   below the body; the body itself never contains a marker.
 */
export function ReasoningPanel({
  reasoningMd,
  reasoningStatus,
  reasoningTruncated,
  className,
}: ReasoningPanelProps) {
  const reasoningText = reasoningMd ?? "";
  const hasReasoningContent = reasoningText.trim().length > 0;
  const isStreaming = reasoningStatus === "streaming";

  // Render only when there is real projected content or an active stream.
  // Empty + completed/idle/interrupted ⇒ nothing to show ⇒ no element.
  if (!isStreaming && !hasReasoningContent) {
    return null;
  }

  return (
    <div data-slot="reasoning" className={cn(className)}>
      <Reasoning isStreaming={isStreaming} defaultOpen={false}>
        <ReasoningTrigger
          className="gap-1.5 text-[12px] font-medium text-muted-foreground"
          data-slot="reasoning-trigger"
          getThinkingMessage={(streaming, duration) =>
            streaming ? (
              <Shimmer as="span" duration={1}>思考中</Shimmer>
            ) : duration !== undefined ? (
              <span>思考过程 · {duration}s</span>
            ) : (
              <span>思考过程</span>
            )
          }
        />
        <ReasoningContent
          className="mt-2 pl-5"
          data-slot="reasoning-content"
        >
          {reasoningText}
        </ReasoningContent>
      </Reasoning>
      {reasoningTruncated ? (
        <div
          data-slot="reasoning-truncated"
          data-testid="ask-reasoning-truncated"
          role="status"
          aria-label="推理已达到展示上限"
          className="mt-1 pl-5 text-[11px] leading-4 text-muted-foreground"
        >
          已达到展示上限，仅显示部分推理内容。
        </div>
      ) : null}
    </div>
  );
}
