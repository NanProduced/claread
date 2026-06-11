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
  const contentColumnClassName = "w-full max-w-[38rem]";

  return (
    <Message from="assistant" className={cn("max-w-full gap-3", className)}>
      {reasoning ? <div className={contentColumnClassName}>{reasoning}</div> : null}
      {process ? <div className={contentColumnClassName}>{process}</div> : null}
      {answer ? <MessageContent className={contentColumnClassName}>{answer}</MessageContent> : null}
      {children ? (
        <div className={cn(contentColumnClassName, "min-w-0 space-y-2.5")}>
          {children ? <div>{children}</div> : null}
        </div>
      ) : null}
      {footer ? (
        <MessageToolbar className={cn(contentColumnClassName, "mt-0.5 justify-start")}>
          <div className="shrink-0">{footer}</div>
        </MessageToolbar>
      ) : null}
    </Message>
  );
}
