/**
 * Pure scroll helpers for Ask Claread conversation.
 *
 * StickToBottom always auto-follows via `targetScrollTop`. Explicit "jump to
 * latest" must reach the natural content bottom, so user-question anchoring is
 * applied only for automatic follow, never for explicit jumps.
 */

export type ScrollRectLike = {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
};

export type ScrollElementsLike = {
  scrollElement: {
    scrollTop: number;
    getBoundingClientRect: () => ScrollRectLike;
  };
  contentElement: {
    querySelectorAll: (
      selector: string,
    ) =>
      | ArrayLike<{ getBoundingClientRect: () => ScrollRectLike }>
      | {
          length: number;
          item: (
            index: number,
          ) => { getBoundingClientRect: () => ScrollRectLike } | null;
        };
  };
};

/**
 * Prefer keeping the latest user question near the top of the viewport when
 * content grows (activity / answer). Never scroll past the natural bottom.
 */
export function computeUserQuestionAnchoredScrollTop(
  defaultTarget: number,
  { scrollElement, contentElement }: ScrollElementsLike,
  options?: { topMarginPx?: number; userSelector?: string },
): number {
  const topMarginPx = options?.topMarginPx ?? 16;
  const userSelector = options?.userSelector ?? '[data-testid="ask-user-message"]';
  const userNodes = contentElement.querySelectorAll(userSelector);
  const length = userNodes.length;
  if (length <= 0) {
    return defaultTarget;
  }
  const lastUser =
    "item" in userNodes && typeof userNodes.item === "function"
      ? userNodes.item(length - 1)
      : (userNodes as ArrayLike<{ getBoundingClientRect: () => ScrollRectLike }>)[
          length - 1
        ];
  if (!lastUser) {
    return defaultTarget;
  }
  const containerRect = scrollElement.getBoundingClientRect();
  const userRect = lastUser.getBoundingClientRect();
  const userTop = userRect.top - containerRect.top + scrollElement.scrollTop;
  const preferred = Math.max(0, userTop - topMarginPx);
  return Math.min(preferred, defaultTarget);
}

/** Explicit jump target: always the natural bottom. */
export function computeNaturalBottomScrollTop(defaultTarget: number): number {
  return defaultTarget;
}

export type NaturalConversationScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

/**
 * Natural bottom is independent from StickToBottom's customizable target.
 * A small tolerance avoids button flicker from sub-pixel browser rounding.
 */
export function isAtNaturalConversationBottom(
  metrics: NaturalConversationScrollMetrics,
  tolerancePx = 2,
): boolean {
  const naturalBottom = Math.max(0, metrics.scrollHeight - metrics.clientHeight);
  return naturalBottom - metrics.scrollTop <= tolerancePx;
}
