"use client";

/**
 * Learner-facing “思路摘要” surface (ASK-LEARNER-REASONING-PROJECTOR-R1.1).
 *
 * Sibling of ChainOfThought. Pure text — no Markdown/Streamdown.
 * First valid snapshot only; live auto-expand; settle one-shot auto-close;
 * user re-expand is not forced closed again. No new scroll owner.
 */

import { Shimmer } from "@/components/ai-elements/shimmer";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/cn";
import { useControllableState } from "@radix-ui/react-use-controllable-state";
import { BrainIcon, ChevronDownIcon } from "lucide-react";
import { memo, useCallback, useEffect, useRef, useState } from "react";

export type LearnerReasoningPanelProps = {
  text: string | null | undefined;
  status: "streaming" | "completed" | null | undefined;
  className?: string;
};

const TITLE = "思路摘要";
const AUTO_CLOSE_DELAY = 1000;

export const LearnerReasoningPanel = memo(function LearnerReasoningPanel({
  text,
  status,
  className,
}: LearnerReasoningPanelProps) {
  const body = (text ?? "").trim();
  if (!body) {
    return null;
  }

  return (
    <LearnerReasoningCollapsible
      body={body}
      isStreaming={status === "streaming"}
      className={className}
    />
  );
});

function LearnerReasoningCollapsible({
  body,
  isStreaming,
  className,
}: {
  body: string;
  isStreaming: boolean;
  className?: string;
}) {
  const [isOpen, setIsOpen] = useControllableState<boolean>({
    defaultProp: isStreaming,
  });
  const hasEverStreamedRef = useRef(isStreaming);
  const [hasAutoClosed, setHasAutoClosed] = useState(false);

  useEffect(() => {
    if (isStreaming) {
      hasEverStreamedRef.current = true;
      if (!isOpen) {
        setIsOpen(true);
      }
    }
  }, [isStreaming, isOpen, setIsOpen]);

  useEffect(() => {
    if (
      hasEverStreamedRef.current &&
      !isStreaming &&
      isOpen &&
      !hasAutoClosed
    ) {
      const timer = setTimeout(() => {
        setIsOpen(false);
        setHasAutoClosed(true);
      }, AUTO_CLOSE_DELAY);
      return () => clearTimeout(timer);
    }
  }, [isStreaming, isOpen, setIsOpen, hasAutoClosed]);

  const handleOpenChange = useCallback(
    (open: boolean) => {
      setIsOpen(open);
    },
    [setIsOpen]
  );

  return (
    <div
      data-slot="learner-reasoning"
      data-testid="ask-learner-reasoning"
      className={cn("not-prose mb-4", className)}
    >
      <Collapsible open={isOpen} onOpenChange={handleOpenChange}>
        <CollapsibleTrigger
          className="flex w-full items-center gap-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
          data-slot="learner-reasoning-trigger"
          data-testid="ask-learner-reasoning-trigger"
        >
          <BrainIcon className="size-4 shrink-0" aria-hidden="true" />
          {isStreaming ? (
            <Shimmer as="span" duration={1}>
              {TITLE}
            </Shimmer>
          ) : (
            <span>{TITLE}</span>
          )}
          <ChevronDownIcon
            className={cn(
              "size-4 transition-transform",
              isOpen ? "rotate-180" : "rotate-0"
            )}
            aria-hidden="true"
          />
        </CollapsibleTrigger>
        <CollapsibleContent
          className="mt-2 text-sm text-muted-foreground"
          data-slot="learner-reasoning-content"
          data-testid="ask-learner-reasoning-content"
        >
          <div className="border-l border-border/60 pl-3 whitespace-pre-wrap break-words">
            {body}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
