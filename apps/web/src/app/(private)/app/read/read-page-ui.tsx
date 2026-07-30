"use client";

/**
 * 输入页 UI 状态（L2/L3 三段统一）。
 *
 * - Hero 形态收缩：编辑器有内容（或进入 Content Check）后，Hero 从首屏
 *   大标题收缩为同一品牌身份的紧凑形态（"Paste to Begin" + "Bring it
 *   to Claread."），不再切换成另一个标题。状态由 AnalyzeSubmitForm 通过
 *   `useReadPageUi().setHasContent` 上报。
 * - 动效合同：height（grid-rows）/ font-size / opacity / translate 联合
 *   过渡 250ms（设计窗口 220-280ms），prefers-reduced-motion 下全部关闭、
 *   即时切换。
 * - Provider 缺省时 useReadPageUi 返回 no-op（单测/其他页面直接渲染
 *   AnalyzeSubmitForm 不需要包 Provider）。
 */

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/cn";

interface ReadPageUiState {
  hasContent: boolean;
  setHasContent: (value: boolean) => void;
}

const ReadPageUiContext = createContext<ReadPageUiState | null>(null);

const NOOP_UI_STATE: ReadPageUiState = {
  hasContent: false,
  setHasContent: () => {},
};

export function useReadPageUi(): ReadPageUiState {
  return useContext(ReadPageUiContext) ?? NOOP_UI_STATE;
}

export function ReadPageUiProvider({ children }: { children: ReactNode }) {
  const [hasContent, setHasContent] = useState(false);
  const value = useMemo(
    () => ({ hasContent, setHasContent }),
    [hasContent],
  );
  return (
    <ReadPageUiContext.Provider value={value}>
      {children}
    </ReadPageUiContext.Provider>
  );
}

const HERO_TRANSITION_MS = "duration-[250ms]";

/**
 * 同一个 Hero 的两种形态：完整（空状态，报刊感大标题两行）与紧凑（有
 * 内容，主标题缩小、副标题与辅助文案收起）。两形态共享 "Paste to Begin"
 * 眉题与 "Bring it to Claread." 主标题，收缩是同一身份的形变而非替换。
 */
export function ReadPageHero() {
  const { hasContent } = useReadPageUi();
  return (
    <div
      data-testid="read-page-hero"
      data-collapsed={hasContent ? "true" : "false"}
      className="max-w-[56rem]"
    >
      <span className="inline-block text-xs font-bold tracking-[0.14em] text-lens-blue">
        Paste to Begin
      </span>
      <h1
        data-testid="read-page-hero-title"
        className={cn(
          "font-headline font-semibold text-ink",
          "transition-[font-size,line-height,letter-spacing,margin] ease-out motion-reduce:transition-none",
          HERO_TRANSITION_MS,
          hasContent
            ? "mt-2 text-xl leading-tight tracking-[-0.018em] sm:text-2xl xl:text-[1.75rem]"
            : "mt-3 text-5xl leading-[0.96] tracking-[-0.035em] sm:text-6xl xl:text-7xl",
        )}
      >
        Bring it to Claread.
      </h1>

      <div
        className={cn(
          "grid transition-[grid-template-rows] ease-out motion-reduce:transition-none",
          HERO_TRANSITION_MS,
          hasContent ? "grid-rows-[0fr]" : "grid-rows-[1fr]",
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div
            data-testid="read-page-hero-detail"
            aria-hidden={hasContent}
            className={cn(
              "transition-[opacity,transform] ease-out motion-reduce:transition-none motion-reduce:transform-none",
              HERO_TRANSITION_MS,
              hasContent
                ? "pointer-events-none -translate-y-2 opacity-0"
                : "translate-y-0 opacity-100",
            )}
          >
            <span className="mt-1 block font-headline text-5xl font-semibold leading-[0.96] tracking-[-0.035em] text-ink sm:text-6xl xl:text-7xl">
              Read It Deeply.
            </span>
            <p className="mt-4 max-w-[28rem] pb-1 font-sans text-[0.98rem] leading-[1.65] text-muted-foreground">
              从粘贴开始，进入深度阅读。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
