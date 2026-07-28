"use client";

/**
 * 输入页 UI 状态（L2/L3 三段统一）。
 *
 * - Hero 弱化/收起：编辑器有内容（或进入 Content Check）后，Hero 从
 *   首屏大标题收起为单行眉题，编辑器成为首屏主任务。状态由
 *   AnalyzeSubmitForm 通过 `useReadPageUi().setHasContent` 上报。
 * - 动效合同：收起/展开 200ms（阶段 2 动效窗口 160-240ms），
 *   prefers-reduced-motion 下关闭过渡即时切换。
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

/**
 * 可收起的输入页 Hero。`hasContent` 时压缩为单行眉题（高度/透明度
 * 200ms 过渡），把首屏让位给编辑器。
 */
export function ReadPageHero() {
  const { hasContent } = useReadPageUi();
  return (
    <div
      data-testid="read-page-hero"
      data-collapsed={hasContent ? "true" : "false"}
      className={cn(
        "max-w-[58rem] overflow-hidden transition-all duration-200 ease-out motion-reduce:transition-none",
        hasContent ? "mb-2 opacity-90" : "mb-0",
      )}
    >
      <span
        className={cn(
          "inline-block text-[0.72rem] font-bold tracking-[0.14em] text-lens-blue transition-all duration-200 motion-reduce:transition-none",
          hasContent ? "mb-0" : "mb-3",
        )}
      >
        Paste to Begin
      </span>
      <h1
        className={cn(
          "font-headline font-semibold tracking-[-0.035em] text-ink transition-all duration-200 ease-out motion-reduce:transition-none",
          hasContent
            ? "text-[1.05rem] leading-[1.2] tracking-[-0.01em]"
            : "text-[clamp(2.5rem,4.3vw,4.5rem)] leading-[0.94]",
        )}
      >
        {hasContent ? (
          <span className="block">Bring it to Claread.</span>
        ) : (
          <>
            <span className="block">Bring it to Claread.</span>
            <span className="mt-1 block">Read It Deeply.</span>
          </>
        )}
      </h1>
      <p
        className={cn(
          "max-w-[28rem] overflow-hidden font-reading text-[1.08rem] leading-[1.65] text-muted-foreground transition-all duration-200 ease-out motion-reduce:transition-none sm:text-[1.12rem]",
          hasContent ? "mt-0 max-h-0 opacity-0" : "mt-4 max-h-24 opacity-100",
        )}
      >
        从粘贴开始，进入深度阅读。
      </p>
    </div>
  );
}
