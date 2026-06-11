"use client";

import React from "react";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { cn } from "@/lib/cn";

type ConversationShellProps = {
  children: React.ReactNode;
  emptyState?: React.ReactNode;
  hasMessages: boolean;
  className?: string;
  contentClassName?: string;
};

export function ConversationShell({
  children,
  emptyState,
  hasMessages,
  className,
  contentClassName,
}: ConversationShellProps) {
  if (!hasMessages) {
    return (
      <div className={cn("flex min-h-0 flex-1 overflow-hidden", className)}>
        {emptyState}
      </div>
    );
  }

  return (
    <Conversation className={cn("ask-conversation relative h-full min-h-0 flex-1 overflow-hidden", className)}>
      <ConversationContent
        className={cn("ask-conversation-content min-h-full pb-5", contentClassName)}
        scrollClassName="ask-conversation-scroll"
        children={children as React.ComponentProps<typeof ConversationContent>["children"]}
      >
      </ConversationContent>
      <ConversationScrollButton aria-label="跳到最新消息" />
    </Conversation>
  );
}
