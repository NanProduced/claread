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

export function ReasoningPanel({
  reasoningMd,
  reasoningStatus,
  className,
}: ReasoningPanelProps) {
  const reasoningText = reasoningMd ?? "";
  const hasReasoningContent = reasoningText.trim().length > 0;
  const isStreaming = reasoningStatus === "streaming";
  const isCompleted = reasoningStatus === "completed";
  const shouldRender = isStreaming || isCompleted || hasReasoningContent;

  if (!shouldRender) {
    return null;
  }

  // Empty + completed: trigger collapsed with placeholder inside content.
  if (!hasReasoningContent && isCompleted) {
    return (
      <div data-slot="reasoning" className={cn(className)}>
        <Reasoning>
          <ReasoningTrigger
            className="gap-1.5 text-[12px] font-medium text-muted-foreground"
            data-slot="reasoning-trigger"
            getThinkingMessage={() => <span>思考过程</span>}
          />
          <ReasoningContent
            className="mt-2 pl-5"
            data-slot="reasoning-content"
          >
            本轮模型未返回可展示的思考内容。
          </ReasoningContent>
        </Reasoning>
      </div>
    );
  }

  // Empty + streaming: trigger open with shimmer placeholder text in content.
  if (!hasReasoningContent && isStreaming) {
    return (
      <div data-slot="reasoning" className={cn(className)}>
        <Reasoning isStreaming={isStreaming}>
          <ReasoningTrigger
            className="gap-1.5 text-[12px] font-medium text-muted-foreground"
            data-slot="reasoning-trigger"
            getThinkingMessage={() => <Shimmer as="span" duration={1}>思考中</Shimmer>}
          />
          <ReasoningContent
            className="mt-2 pl-5"
            data-slot="reasoning-content"
          >
            正在形成可展示的思路…
          </ReasoningContent>
        </Reasoning>
      </div>
    );
  }

  return (
    <div data-slot="reasoning" className={cn(className)}>
      <Reasoning isStreaming={isStreaming}>
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
