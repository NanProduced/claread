"use client";

import React, { useCallback, useRef, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import { cn } from "@/lib/cn";
import { ArrowDownIcon } from "lucide-react";
import { useStickToBottomContext } from "use-stick-to-bottom";
import {
  computeNaturalBottomScrollTop,
  computeUserQuestionAnchoredScrollTop,
  isAtNaturalConversationBottom,
} from "@/components/reader/ask/conversation-scroll";

type ConversationShellProps = {
  children: React.ReactNode;
  emptyState?: React.ReactNode;
  hasMessages: boolean;
  latestUserMessageId?: string | null;
  className?: string;
  contentClassName?: string;
};

/**
 * Each new user turn starts in question-anchor mode so the prompt remains
 * visible while the answer grows. An explicit jump switches that turn to the
 * real natural bottom and keeps following it until another user message starts.
 */
export function ConversationShell({
  children,
  emptyState,
  hasMessages,
  latestUserMessageId = null,
  className,
  contentClassName,
}: ConversationShellProps) {
  const naturalBottomUserIdRef = useRef<string | null>(null);

  const targetScrollTop = useCallback(
    (
      defaultTarget: number,
      elements: {
        scrollElement: HTMLElement;
        contentElement: HTMLElement;
      },
    ) => {
      const followsNaturalBottom =
        latestUserMessageId == null ||
        naturalBottomUserIdRef.current === latestUserMessageId;
      if (followsNaturalBottom) {
        return computeNaturalBottomScrollTop(defaultTarget);
      }
      return computeUserQuestionAnchoredScrollTop(defaultTarget, elements);
    },
    [latestUserMessageId],
  );

  if (!hasMessages) {
    return (
      <div className={cn("flex min-h-0 flex-1 overflow-hidden", className)}>
        {emptyState}
      </div>
    );
  }

  return (
    <Conversation
      className={cn(
        "ask-conversation relative h-full min-h-0 flex-1 overflow-hidden",
        className,
      )}
      initial="smooth"
      resize="smooth"
      targetScrollTop={targetScrollTop}
    >
      <ConversationContent
        className={cn("ask-conversation-content min-h-full pb-5", contentClassName)}
        scrollClassName="ask-conversation-scroll"
      >
        {/* Cast needed: use-stick-to-bottom resolves a different @types/react
            version, making project React.ReactNode incompatible with the
            library's children prop type. */}
        {
          children as unknown as React.ComponentProps<
            typeof ConversationContent
          >["children"]
        }
      </ConversationContent>
      <AskConversationJumpToLatestButton
        onJumpToNaturalBottom={() => {
          naturalBottomUserIdRef.current = latestUserMessageId;
        }}
      />
    </Conversation>
  );
}

function useIsAtNaturalConversationBottom(): boolean {
  const { scrollRef, contentRef } = useStickToBottomContext();

  const getSnapshot = useCallback(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) {
      return true;
    }
    return isAtNaturalConversationBottom({
      scrollTop: scrollElement.scrollTop,
      scrollHeight: scrollElement.scrollHeight,
      clientHeight: scrollElement.clientHeight,
    });
  }, [scrollRef]);

  const subscribe = useCallback(
    (notify: () => void) => {
      const scrollElement = scrollRef.current;
      const contentElement = contentRef.current;
      if (!scrollElement) {
        return () => undefined;
      }
      scrollElement.addEventListener("scroll", notify, { passive: true });
      const observer =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(() => notify());
      observer?.observe(scrollElement);
      if (contentElement) {
        observer?.observe(contentElement);
      }
      return () => {
        scrollElement.removeEventListener("scroll", notify);
        observer?.disconnect();
      };
    },
    [contentRef, scrollRef],
  );

  return useSyncExternalStore(subscribe, getSnapshot, () => true);
}

function AskConversationJumpToLatestButton({
  onJumpToNaturalBottom,
}: {
  onJumpToNaturalBottom: () => void;
}) {
  const { scrollToBottom } = useStickToBottomContext();
  const isAtNaturalBottom = useIsAtNaturalConversationBottom();

  const handleClick = useCallback(() => {
    onJumpToNaturalBottom();
    try {
      const result = scrollToBottom();
      if (result && typeof (result as Promise<boolean>).catch === "function") {
        void (result as Promise<boolean>).catch(() => undefined);
      }
    } catch {
      // The conversation remains usable even if a browser scroll call fails.
    }
  }, [onJumpToNaturalBottom, scrollToBottom]);

  if (isAtNaturalBottom) {
    return null;
  }

  return (
    <Button
      type="button"
      size="icon"
      variant="outline"
      aria-label="跳到最新消息"
      data-testid="ask-jump-to-latest"
      className={cn(
        "absolute bottom-3 left-[50%] z-10 h-9 w-9 translate-x-[-50%] rounded-full border border-border/70 bg-background/86 text-muted-foreground shadow-[0_10px_28px_rgba(24,24,20,0.12)] backdrop-blur supports-[backdrop-filter]:bg-background/72 hover:border-border hover:bg-background hover:text-foreground dark:bg-background/86 dark:hover:bg-muted",
      )}
      onClick={handleClick}
    >
      <ArrowDownIcon className="size-3.5" />
    </Button>
  );
}
