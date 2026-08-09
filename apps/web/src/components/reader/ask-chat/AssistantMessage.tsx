"use client";

import React from "react";
import {
  Message,
  MessageContent,
  MessageToolbar,
} from "@/components/ai-elements/message";
import { cn } from "@/lib/cn";

type AssistantMessageProps = {
  reasoning?: React.ReactNode;
  process?: React.ReactNode;
  answer?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
};

export function AssistantMessage({
  reasoning,
  process,
  answer,
  footer,
  children,
  className,
}: AssistantMessageProps) {
  // P1 — vertical rhythm: 4/8 spacing between process → answer → sources →
  // actions. The content column stays narrow for readability; the assistant
  // surface is frameless (no border, no bg), letting the answer text own
  // the visual weight.
  const contentColumnClassName = "w-full max-w-[38rem]";

  return (
    <Message from="assistant" className={cn("max-w-full gap-2", className)}>
      {reasoning}
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
