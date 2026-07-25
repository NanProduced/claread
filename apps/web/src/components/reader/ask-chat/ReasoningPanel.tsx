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
 */
export function ReasoningPanel({
  reasoningMd,
  reasoningStatus,
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
    </div>
  );
}
