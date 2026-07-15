/** @vitest-environment node */
import { describe, expect, it } from "vitest";
import {
  computeNaturalBottomScrollTop,
  computeUserQuestionAnchoredScrollTop,
  isAtNaturalConversationBottom,
} from "./conversation-scroll";

function rect(top: number, height = 40): {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
} {
  return { top, bottom: top + height, left: 0, right: 100, width: 100, height };
}

describe("conversation-scroll helpers", () => {
  it("anchors automatic follow near the last user message", () => {
    const nodes = [
      { getBoundingClientRect: () => rect(200) },
      { getBoundingClientRect: () => rect(400) },
    ];
    const target = computeUserQuestionAnchoredScrollTop(
      900,
      {
        scrollElement: {
          scrollTop: 0,
          getBoundingClientRect: () => rect(0, 500),
        },
        contentElement: {
          querySelectorAll: () => ({
            length: nodes.length,
            item: (index: number) => nodes[index] ?? null,
          }),
        },
      },
    );
    // last user top 400 - margin 16 = 384, below natural bottom 900
    expect(target).toBe(384);
    expect(target).toBeLessThan(900);
  });

  it("never exceeds the natural bottom target", () => {
    const nodes = [{ getBoundingClientRect: () => rect(1200) }];
    const target = computeUserQuestionAnchoredScrollTop(
      500,
      {
        scrollElement: {
          scrollTop: 0,
          getBoundingClientRect: () => rect(0, 500),
        },
        contentElement: {
          querySelectorAll: () => ({
            length: nodes.length,
            item: (index: number) => nodes[index] ?? null,
          }),
        },
      },
    );
    expect(target).toBe(500);
  });

  it("falls back to natural bottom when no user message exists", () => {
    const target = computeUserQuestionAnchoredScrollTop(777, {
      scrollElement: {
        scrollTop: 0,
        getBoundingClientRect: () => rect(0, 500),
      },
      contentElement: {
        querySelectorAll: () => ({
          length: 0,
          item: () => null,
        }),
      },
    });
    expect(target).toBe(777);
  });

  it("explicit jump always uses the natural bottom", () => {
    expect(computeNaturalBottomScrollTop(1234)).toBe(1234);
  });

  it("distinguishes the real content bottom from a question anchor", () => {
    expect(
      isAtNaturalConversationBottom({
        scrollTop: 384,
        scrollHeight: 1400,
        clientHeight: 500,
      }),
    ).toBe(false);
    expect(
      isAtNaturalConversationBottom({
        scrollTop: 899,
        scrollHeight: 1400,
        clientHeight: 500,
      }),
    ).toBe(true);
  });
});
