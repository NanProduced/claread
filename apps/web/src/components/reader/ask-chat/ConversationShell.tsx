"use client";

import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
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

type FollowMode = "question-anchor" | "natural-bottom" | "detached";

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
  const [followMode, setFollowMode] = useState<FollowMode>("question-anchor");
  const followModeRef = useRef<FollowMode>("question-anchor");
  const lastUserIdRef = useRef<string | null>(null);
  const enterNaturalBottom = useCallback(() => {
    followModeRef.current = "natural-bottom";
    setFollowMode("natural-bottom");
  }, []);
  const detachFromFollow = useCallback(() => {
    followModeRef.current = "detached";
    setFollowMode("detached");
  }, []);
  const isFollowingNaturalBottom = useCallback(
    () => followModeRef.current === "natural-bottom",
    [],
  );

  useEffect(() => {
    if (latestUserMessageId == null) {
      lastUserIdRef.current = null;
      return;
    }
    if (lastUserIdRef.current === latestUserMessageId) {
      return;
    }
    lastUserIdRef.current = latestUserMessageId;
    // A brand-new user turn always re-enters question-anchor mode.
    followModeRef.current = "question-anchor";
    setFollowMode("question-anchor");
  }, [latestUserMessageId]);

  const targetScrollTop = useCallback(
    (
      defaultTarget: number,
      elements: {
        scrollElement: HTMLElement;
        contentElement: HTMLElement;
      },
    ) => {
      const currentFollowMode = followModeRef.current;
      if (currentFollowMode === "natural-bottom" || latestUserMessageId == null) {
        return computeNaturalBottomScrollTop(defaultTarget);
      }
      if (currentFollowMode === "detached") {
        return elements.scrollElement.scrollTop;
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
        "ask-conversation relative h-full min-h-0 flex-1 overflow-hidden motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-150 motion-reduce:animate-none",
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
      <FollowModeController
        followMode={followMode}
        latestUserMessageId={latestUserMessageId}
        isFollowingNaturalBottom={isFollowingNaturalBottom}
        onDetach={detachFromFollow}
      />
      <AskConversationJumpToLatestButton onJumpToNaturalBottom={enterNaturalBottom} />
    </Conversation>
  );
}

function readNaturalBottom(scrollElement: HTMLElement): number {
  return Math.max(0, scrollElement.scrollHeight - scrollElement.clientHeight);
}

/**
 * Programmatic scroll via the library's scrollTop setter.
 * Direct DOM assignment is treated as a user escape; this path sets
 * ignoreScrollToTop so follow mode stays locked.
 */
function pinLibraryScrollTop(
  state: { scrollTop: number },
  value: number,
): void {
  // use-stick-to-bottom exposes scrollTop as a writable facade (not React state).
  state.scrollTop = value;
}

/**
 * use-stick-to-bottom only animates downward toward its calculated target, and
 * that target is only reactive after React commits. For mode transitions we
 * write scrollTop imperatively so the viewport moves on the same frame the
 * mode changes — including upward re-anchors for a new user turn.
 */
function FollowModeController({
  followMode,
  latestUserMessageId,
  isFollowingNaturalBottom,
  onDetach,
}: {
  followMode: FollowMode;
  latestUserMessageId: string | null;
  isFollowingNaturalBottom: () => boolean;
  onDetach: () => void;
}) {
  const { scrollRef, contentRef, scrollToBottom, state } =
    useStickToBottomContext();
  const previousModeRef = useRef<FollowMode>(followMode);
  const previousUserIdRef = useRef<string | null>(null);

  useEffect(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    const handleScroll = () => {
      if (!isFollowingNaturalBottom()) return;
      if (
        !isAtNaturalConversationBottom({
          scrollTop: scrollElement.scrollTop,
          scrollHeight: scrollElement.scrollHeight,
          clientHeight: scrollElement.clientHeight,
        })
      ) {
        onDetach();
      }
    };
    scrollElement.addEventListener("scroll", handleScroll, { passive: true });
    return () => scrollElement.removeEventListener("scroll", handleScroll);
  }, [isFollowingNaturalBottom, onDetach, scrollRef]);

  const pinNaturalBottom = useCallback(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) {
      return;
    }
    pinLibraryScrollTop(state, readNaturalBottom(scrollElement));
    try {
      const result = scrollToBottom({ animation: "instant" });
      if (result && typeof (result as Promise<boolean>).catch === "function") {
        void (result as Promise<boolean>).catch(() => undefined);
      }
    } catch {
      // Conversation remains usable if a browser scroll call fails.
    }
  }, [scrollRef, scrollToBottom, state]);

  const pinQuestionAnchor = useCallback(() => {
    const scrollElement = scrollRef.current;
    const contentElement = contentRef.current;
    if (!scrollElement || !contentElement) {
      return;
    }
    const defaultTarget = Math.max(
      0,
      scrollElement.scrollHeight - 1 - scrollElement.clientHeight,
    );
    const target = computeUserQuestionAnchoredScrollTop(defaultTarget, {
      scrollElement,
      contentElement,
    });
    pinLibraryScrollTop(state, target);
    try {
      const result = scrollToBottom({ animation: "instant" });
      if (result && typeof (result as Promise<boolean>).catch === "function") {
        void (result as Promise<boolean>).catch(() => undefined);
      }
    } catch {
      // Conversation remains usable if a browser scroll call fails.
    }
  }, [contentRef, scrollRef, scrollToBottom, state]);

  useLayoutEffect(() => {
    const previousMode = previousModeRef.current;
    const previousUserId = previousUserIdRef.current;
    previousModeRef.current = followMode;
    previousUserIdRef.current = latestUserMessageId;

    const enteredQuestionAnchor =
      previousMode !== "question-anchor" && followMode === "question-anchor";
    const newUserTurn =
      latestUserMessageId != null &&
      previousUserId !== latestUserMessageId;
    const enteredNaturalBottom =
      previousMode !== "natural-bottom" && followMode === "natural-bottom";

    if (followMode === "question-anchor" && (enteredQuestionAnchor || newUserTurn)) {
      // Wait one frame so the new user bubble is measurable in the content tree.
      requestAnimationFrame(() => {
        pinQuestionAnchor();
      });
      return;
    }

    if (enteredNaturalBottom) {
      requestAnimationFrame(() => {
        pinNaturalBottom();
      });
    }
  }, [
    followMode,
    latestUserMessageId,
    pinNaturalBottom,
    pinQuestionAnchor,
  ]);

  // Keep natural-bottom pin attached across large answer growth.
  useEffect(() => {
    if (followMode !== "natural-bottom") {
      return;
    }
    const contentElement = contentRef.current;
    if (!contentElement || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => {
      if (!isFollowingNaturalBottom()) {
        return;
      }
      pinNaturalBottom();
    });
    observer.observe(contentElement);
    return () => observer.disconnect();
  }, [contentRef, followMode, isFollowingNaturalBottom, pinNaturalBottom]);

  return null;
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
  const { scrollRef, scrollToBottom, state } = useStickToBottomContext();
  const isAtNaturalBottom = useIsAtNaturalConversationBottom();

  const handleClick = useCallback(() => {
    // Pin the real natural bottom immediately. Do not wait for React to commit
    // the follow-mode state change — otherwise scrollToBottom still uses the
    // previous question-anchor targetScrollTop and undershoots.
    const scrollElement = scrollRef.current;
    if (scrollElement) {
      pinLibraryScrollTop(state, readNaturalBottom(scrollElement));
    }
    onJumpToNaturalBottom();
    try {
      const result = scrollToBottom({ animation: "instant" });
      if (result && typeof (result as Promise<boolean>).catch === "function") {
        void (result as Promise<boolean>).catch(() => undefined);
      }
    } catch {
      // The conversation remains usable even if a browser scroll call fails.
    }
  }, [onJumpToNaturalBottom, scrollRef, scrollToBottom, state]);

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
        "absolute bottom-3 left-[50%] z-10 h-9 w-9 translate-x-[-50%] rounded-full border border-border/70 bg-background/86 text-muted-foreground shadow-[var(--app-panel-shadow-quiet)] backdrop-blur supports-[backdrop-filter]:bg-background/72 hover:border-border hover:bg-background hover:text-foreground",
      )}
      onClick={handleClick}
    >
      <ArrowDownIcon className="size-3.5" />
    </Button>
  );
}
