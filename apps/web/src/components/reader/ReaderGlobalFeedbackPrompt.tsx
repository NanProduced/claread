"use client";

import type { ReactNode } from "react";
import { MessageSquareText, ThumbsDown, ThumbsUp } from "lucide-react";

import { cn } from "@/lib/cn";
import { readerInlineFocusRing, readerTransitionFast } from "./interaction";

interface ReaderGlobalFeedbackPromptProps {
  className?: string;
  onHelpful: () => void;
  onIssue: () => void;
  onSuggestion: () => void;
}

function PromptButton({
  children,
  icon,
  intent,
  onClick,
}: {
  children: ReactNode;
  icon: ReactNode;
  intent: "helpful" | "issue" | "suggestion";
  onClick: () => void;
}) {
  const intentClassName = {
    helpful: "hover:bg-structure-green/10 hover:text-structure-green focus-visible:text-structure-green",
    issue: "hover:bg-error-red/10 hover:text-error-red focus-visible:text-error-red",
    suggestion: "hover:bg-lens-blue/10 hover:text-lens-blue focus-visible:text-lens-blue",
  }[intent];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group inline-flex h-11 min-w-[6.5rem] items-center justify-center gap-2 bg-surface/72 px-3.5 text-[13px] font-semibold text-ink-soft",
        readerInlineFocusRing,
        readerTransitionFast,
        "active:bg-surface",
        intentClassName,
      )}
    >
      <span className="inline-flex size-6 items-center justify-center rounded-[7px] border border-hairline/70 bg-background/72 text-current transition-colors group-hover:border-current/20">
        {icon}
      </span>
      <span>{children}</span>
    </button>
  );
}

export function ReaderGlobalFeedbackPrompt({
  className,
  onHelpful,
  onIssue,
  onSuggestion,
}: ReaderGlobalFeedbackPromptProps) {
  return (
    <section
      aria-label="阅读反馈"
      className={cn("mt-12 border-t border-hairline/60 pt-7 pb-16", className)}
    >
      <div className="mx-auto flex w-full max-w-[48rem] flex-col items-stretch justify-between gap-3 rounded-[16px] border border-hairline/70 bg-surface px-3 py-3 shadow-[var(--app-panel-shadow-quiet)] sm:flex-row sm:items-center sm:pl-4 sm:pr-3">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink">这次解读有帮助吗？</p>
        </div>

        <div className="grid grid-cols-3 gap-px overflow-hidden rounded-[12px] border border-hairline/75 bg-hairline/75">
          <PromptButton
            intent="helpful"
            icon={<ThumbsUp className="size-4" aria-hidden="true" />}
            onClick={onHelpful}
          >
            有帮助
          </PromptButton>
          <PromptButton
            intent="issue"
            icon={<ThumbsDown className="size-4" aria-hidden="true" />}
            onClick={onIssue}
          >
            有问题
          </PromptButton>
          <PromptButton
            intent="suggestion"
            icon={<MessageSquareText className="size-4" aria-hidden="true" />}
            onClick={onSuggestion}
          >
            写建议
          </PromptButton>
        </div>
      </div>
    </section>
  );
}
