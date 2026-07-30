/** @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest";

import { createArticleStartScrollRestorer } from "./paste-scroll-restore";

/**
 * 粘贴后滚动保持合同（helper 级；完整 ClipboardEvent → onPaste → 纠正
 * 链路在 jsdom 不可驱动——Slate legacy 路径不调用组件 onPaste——由真实
 * Chromium 验收覆盖）。
 */

const immediateRaf = (cb: () => void) => cb();

function makeEnv(initial: { scrollTop: number; windowScrollY: number }) {
  const el = document.createElement("div");
  el.scrollTop = initial.scrollTop;
  const state = { windowScrollY: initial.windowScrollY };
  const restoreWindowScroll = vi.fn((top: number) => {
    state.windowScrollY = top;
  });
  return {
    el,
    state,
    restoreWindowScroll,
    env: {
      getScrollElement: () => el,
      getWindowScrollY: () => state.windowScrollY,
      restoreWindowScroll,
      raf: immediateRaf,
    },
  };
}

describe("createArticleStartScrollRestorer", () => {
  it("scrolls the content element back to the top and restores the pre-paste window position", () => {
    // 模拟 reveal caret 之后：正文滚到底（800）、window 被拉到文末（5000），
    // 粘贴前 window 停在 300。
    const { el, state, restoreWindowScroll, env } = makeEnv({
      scrollTop: 800,
      windowScrollY: 5000,
    });
    const schedule = createArticleStartScrollRestorer(env);

    schedule(300);

    expect(el.scrollTop).toBe(0);
    expect(restoreWindowScroll).toHaveBeenCalledWith(300);
    expect(state.windowScrollY).toBe(300);
  });

  it("does not touch window scroll when the browser did not move it", () => {
    const { el, restoreWindowScroll, env } = makeEnv({
      scrollTop: 0,
      windowScrollY: 300,
    });
    const schedule = createArticleStartScrollRestorer(env);

    schedule(300);

    expect(el.scrollTop).toBe(0);
    expect(restoreWindowScroll).not.toHaveBeenCalled();
  });

  it("tolerates a missing scroll element and still restores the window", () => {
    const restoreWindowScroll = vi.fn();
    const schedule = createArticleStartScrollRestorer({
      getScrollElement: () => null,
      getWindowScrollY: () => 5000,
      restoreWindowScroll,
      raf: immediateRaf,
    });

    schedule(300);

    expect(restoreWindowScroll).toHaveBeenCalledWith(300);
  });

  it("runs the correction two frames out, not synchronously", () => {
    const order: string[] = [];
    const raf = (cb: () => void) => {
      order.push("frame");
      cb();
    };
    const el = document.createElement("div");
    el.scrollTop = 10;
    const schedule = createArticleStartScrollRestorer({
      getScrollElement: () => {
        order.push("correct");
        return el;
      },
      getWindowScrollY: () => 0,
      restoreWindowScroll: () => {},
      raf,
    });

    schedule(0);

    expect(order).toEqual(["frame", "frame", "correct"]);
  });

  it("restores scrollable ancestors (e.g. page main) to their pre-paste positions", () => {
    // 高度链失守场景：reveal caret 滚的是祖先 main（8400），正文容器也
    // 到底（600）；粘贴前 main 停在 0、window 未动。
    const el = document.createElement("div");
    el.scrollTop = 600;
    const main = document.createElement("main");
    main.scrollTop = 8400;
    const other = document.createElement("div");
    other.scrollTop = 120;
    const schedule = createArticleStartScrollRestorer({
      getScrollElement: () => el,
      getScrollableAncestors: () => [main, other],
      getWindowScrollY: () => 0,
      restoreWindowScroll: () => {},
      raf: immediateRaf,
    });

    schedule(0, [0, 120]);

    expect(el.scrollTop).toBe(0);
    expect(main.scrollTop).toBe(0);
    // 位置未偏离的祖先不动（120 == 120，保持原样）。
    expect(other.scrollTop).toBe(120);
  });

  it("skips ancestor correction when no snapshot was captured", () => {
    const main = document.createElement("main");
    main.scrollTop = 8400;
    const schedule = createArticleStartScrollRestorer({
      getScrollElement: () => null,
      getScrollableAncestors: () => [main],
      getWindowScrollY: () => 0,
      restoreWindowScroll: () => {},
      raf: immediateRaf,
    });

    schedule(0);

    expect(main.scrollTop).toBe(8400);
  });
});
