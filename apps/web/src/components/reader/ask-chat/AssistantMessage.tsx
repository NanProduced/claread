"use client";

import React from "react";
import {
  Message,
  MessageContent,
  MessageToolbar,
} from "@/components/ai-elements/message";
import { cn } from "@/lib/cn";

type AssistantMessageProps = {
  /**
   * The turn's single process disclosure — one owner for provider reasoning
   * and agentic steps. There is deliberately no second disclosure slot.
   */
  process?: React.ReactNode;
  answer?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
};

export function AssistantMessage({
  process,
  answer,
  footer,
  children,
  className,
}: AssistantMessageProps) {
  const contentColumnClassName = "w-full max-w-[38rem]";

  return (
    <Message from="assistant" className={cn("max-w-full gap-2", className)}>
      {process}
      {answer ? <MessageContent className={contentColumnClassName}>{answer}</MessageContent> : null}
      {children ? (
        <div className={cn(contentColumnClassName, "min-w-0 space-y-3")}>
          {children ? <div>{children}</div> : null}
        </div>
      ) : null}
      {footer ? (
        <MessageToolbar
          className={cn(
            contentColumnClassName,
            "mt-1 justify-start opacity-60 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 motion-reduce:transition-none",
          )}
        >
          <div className="shrink-0">{footer}</div>
        </MessageToolbar>
      ) : null}
    </Message>
  );
}
