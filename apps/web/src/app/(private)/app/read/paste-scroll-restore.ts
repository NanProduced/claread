/**
 * 整篇粘贴后的可视位置纠正（工作台滚动模型）。
 *
 * 粘贴长文后浏览器会 reveal 文末 caret：桌面端把正文滚动容器（PlateContent）
 * 滚到底，移动端把 window 拉到页面底部；工作台高度链失守时还可能滚动页面
 * 祖先容器（如 main）。产品合同要求粘贴完成优先展示文章开头，且 window
 * 不得跳底。
 *
 * 纠正策略：等两帧（Slate 的 selection reveal 在 effect/绘制前落定）后
 * - 正文滚动容器 scrollTop 归 0（用户先看到标题/引用/列表等结构）；
 * - 正文之外的可滚动祖先与 window.scrollY 偏离粘贴前位置时恢复。
 * Plate 的逻辑 selection 保持不动，只纠正可视滚动位置。
 *
 * 该 helper 与 DOM 环境解耦（注入 env），jsdom 可直接单测；组件侧的完整
 * 粘贴链路（ClipboardEvent → handlePaste → 纠正）由真实 Chromium 验收
 * 覆盖——jsdom 的 Slate legacy 路径不会调用组件 onPaste（已实证）。
 */

export interface ArticleStartScrollRestoreEnv {
  /** 正文滚动容器（桌面端 PlateContent；取不到时跳过容器纠正） */
  getScrollElement: () => HTMLElement | null;
  /**
   * 正文容器之外的可滚动祖先（从内到外，不含 window）。工作台高度链
   * 失守时 reveal caret 可能滚动的是祖先（如页面 main），一并恢复。
   */
  getScrollableAncestors?: () => HTMLElement[];
  getWindowScrollY: () => number;
  restoreWindowScroll: (top: number) => void;
  /**
   * 帧调度器。默认 requestAnimationFrame；jsdom 无 rAF 时退化为 16ms
   * 宏任务，浏览器行为不受影响。
   */
  raf?: (cb: () => void) => void;
}

const defaultRaf = (cb: () => void): void => {
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => cb());
    return;
  }
  setTimeout(cb, 16);
};

/**
 * 收集正文容器之外的可滚动祖先（从内到外，不含 window）。粘贴前与
 * 恢复时必须用同一次 walk 的顺序对齐 scrollTop 快照。
 */
export function collectScrollableAncestors(
  scrollEl: HTMLElement | null,
): HTMLElement[] {
  const ancestors: HTMLElement[] = [];
  for (let el = scrollEl?.parentElement ?? null; el; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") {
      ancestors.push(el);
    }
  }
  return ancestors;
}

/**
 * 返回一个调度函数：参数为粘贴前的 window.scrollY 与祖先滚动位置
 * （与 getScrollableAncestors 返回顺序一致）。两帧后把正文滚回开头，
 * 并把祖先与 window 恢复到粘贴前位置（偏离 > 1px 才动，避免无意义
 * scrollTo）。
 */
export function createArticleStartScrollRestorer(
  env: ArticleStartScrollRestoreEnv,
): (previousWindowScrollY: number, previousAncestorTops?: number[]) => void {
  const raf = env.raf ?? defaultRaf;
  return (previousWindowScrollY: number, previousAncestorTops: number[] = []) => {
    raf(() => {
      raf(() => {
        const el = env.getScrollElement();
        if (el && el.scrollTop > 0) {
          el.scrollTop = 0;
        }
        const ancestors = env.getScrollableAncestors?.() ?? [];
        ancestors.forEach((ancestor, index) => {
          const previous = previousAncestorTops[index];
          if (typeof previous === "number" && Math.abs(ancestor.scrollTop - previous) > 1) {
            ancestor.scrollTop = previous;
          }
        });
        if (Math.abs(env.getWindowScrollY() - previousWindowScrollY) > 1) {
          env.restoreWindowScroll(previousWindowScrollY);
        }
      });
    });
  };
}
