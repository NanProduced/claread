/**
 * Reader Blocks Kit — 注册 Reader Plate 自定义 element plugin
 *
 * element plugins 对应 ReaderRecordPlateDocument 的 block：
 * - ReaderParagraphPlugin  (type: "reader_paragraph")  — 原文段落
 * - ReaderBlockquotePlugin (type: "reader_blockquote")   — 译文引用块
 * - ReaderCalloutPlugin    (type: "reader_callout")     — 增强 callout
 * - ReaderSentenceAnalysisPlugin (type: "reader_sentence_analysis") — 句子拆解块
 *
 * MarkdownPlugin 反序列化出的基础节点也在这里注册为薄 Plate element/leaf plugins。
 * 使用 Plate attributes（ref / data-slate-node 等）保证 Plate 选区和渲染正常工作。
 */
import * as React from "react";
import { createContext, useContext } from "react";
import {
  BookOpenText,
  Check,
  ChevronDown,
  Copy,
  Flag,
  MessageCircleQuestion,
  MessageSquareQuote,
  SquarePen,
  TextSearch,
  WandSparkles,
} from "lucide-react";
import {
  createPlatePlugin,
  type PlateElementProps,
  type PlateLeafProps,
} from "platejs/react";

import type {
  ReaderBlockquoteElement,
  ReaderCalloutElement,
  ReaderCodeBlockElement,
  ReaderHeadingElement,
  ReaderHrElement,
  ReaderImageElement,
  ReaderListItemElement,
  ReaderListElement,
  ReaderMarkdownBlockquoteElement,
  ReaderMathInlineElement,
  ReaderParagraphElement,
  ReaderSentenceAnalysisChunkElement,
  ReaderSourceCalloutElement,
  ReaderSentenceAnalysisElement,
  ReaderTableCellElement,
  ReaderTableElement,
  ReaderTableRowElement,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import {
  READER_BLOCKQUOTE_TYPE,
  READER_CALLOUT_TYPE,
  READER_CODE_BLOCK_TYPE,
  READER_HEADING_TYPE,
  READER_HR_TYPE,
  READER_IMAGE_BLOCK_TYPE,
  READER_IMAGE_TYPE,
  READER_LIST_ITEM_TYPE,
  READER_LIST_TYPE,
  READER_MARKDOWN_BLOCKQUOTE_TYPE,
  READER_MATH_BLOCK_TYPE,
  READER_MATH_INLINE_TYPE,
  READER_PARAGRAPH_TYPE,
  READER_SENTENCE_ANALYSIS_CHUNK_TYPE,
  READER_SENTENCE_ANALYSIS_CHUNKS_TYPE,
  READER_SENTENCE_ANALYSIS_TYPE,
  READER_SOURCE_CALLOUT_TYPE,
  READER_TABLE_CELL_TYPE,
  READER_TABLE_ROW_TYPE,
  READER_TABLE_TYPE,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import katex from "katex";
import { isLoadableImageUrl } from "@/components/editor/plugins/input-markdown-image-kit";
import {
  readerCodeTokenClassName,
  useReaderCodeHighlight,
} from "@/components/editor/plugins/reader-code-highlight";
import { readerRecordNavigableNodeAttrs } from "@/lib/reader-plate/reader-record-dom-contract";
import { SourceCalloutPlugin } from "@/components/editor/plugins/source-callout-kit";
import { isSafeCalloutEmoji } from "@/lib/source-callout/source-callout-display-icon";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/primitives/tooltip";
import { primitiveFocusRing } from "@/components/primitives/shared";

export const READER_CALLOUT_GROUP_TYPE = "reader_callout_group" as const;

function classNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export interface ReaderSentenceAnalysisInteractionValue {
  activeChunkId: string | null;
  setActiveChunkId: (chunkId: string | null) => void;
}

export const ReaderSentenceAnalysisInteractionContext =
  createContext<ReaderSentenceAnalysisInteractionValue>({
    activeChunkId: null,
    setActiveChunkId: () => {},
  });

function useReaderSentenceAnalysisInteraction() {
  return useContext(ReaderSentenceAnalysisInteractionContext);
}

export interface ReaderGrammarInteractionValue {
  activeGrammarItemId: string | null;
  expandGrammarItemRequest: { itemId: string; requestId: number } | null;
  setActiveGrammarItemId: (itemId: string | null) => void;
  /**
   * Hover-intent 版联动（进入 ~120ms / 退出 ~180ms 延迟）：鼠标扫过卡片
   * 不再瞬开瞬关。键盘 focus/click 仍走 setActiveGrammarItemId 即时通道。
   */
  hoverGrammarItemId: (itemId: string | null) => void;
  pulseGrammarItemId: (itemId: string) => void;
  requestExpandGrammarItem: (itemId: string) => void;
}

export const ReaderGrammarInteractionContext =
  createContext<ReaderGrammarInteractionValue>({
    activeGrammarItemId: null,
    expandGrammarItemRequest: null,
    setActiveGrammarItemId: () => {},
    hoverGrammarItemId: () => {},
    pulseGrammarItemId: () => {},
    requestExpandGrammarItem: () => {},
  });

function useReaderGrammarInteraction() {
  return useContext(ReaderGrammarInteractionContext);
}

// ---------------------------------------------------------------------------
// Grammar expansion state keyed by stable itemId.
//
// Standalone grammar callouts (not inside a ReaderCalloutGroupComponent)
// previously stored expanded/collapsed state in a local useState inside
// ReaderCalloutComponent. When `editor.tf.replaceNodes` targets the callout
// element, React remounts the component and the local state is lost.
//
// This context lifts the expansion state OUT of the component instance and
// into a provider that survives targeted replacements. The state is keyed
// by stable `grammarItemId` (from `data.itemId`), not by Slate path, array
// index, or DOM instance.
//
// The provider accepts an optional `controlRef` — a mutable ref object
// whose `.current` is set to a control handle with:
//   - `clear()`: drop ALL expansion state (used before full reload and on
//     generation change).
//   - `forgetItem(itemId)`: drop expansion state for a single itemId
//     (used when a targeted remove op deletes a grammar callout, so the
//     same itemId reappearing in the same generation defaults to collapsed
//     instead of inheriting stale expanded state).
//   - `getExpandedItemIds()`: return the current `expandedItemIds` snapshot
//     (used by ReaderRecordPlateSurface to capture expansion state before
//     `editor.tf.setValue` so it can selectively forget only items that no
//     longer exist in the new DOM on same-source-identity full reload).
// ---------------------------------------------------------------------------

export interface ReaderGrammarExpansionValue {
  expandedItemIds: ReadonlySet<string>;
  expandItem: (itemId: string) => void;
  collapseItem: (itemId: string) => void;
  toggleItem: (itemId: string) => void;
}

export const ReaderGrammarExpansionContext =
  createContext<ReaderGrammarExpansionValue>({
    expandedItemIds: new Set(),
    expandItem: () => {},
    collapseItem: () => {},
    toggleItem: () => {},
  });

export interface ReaderGrammarExpansionControl {
  clear: () => void;
  forgetItem: (itemId: string) => void;
  getExpandedItemIds: () => ReadonlySet<string>;
}

export type ReaderGrammarExpansionControlRef = {
  current: ReaderGrammarExpansionControl | null;
};

/**
 * Expansion key namespace for sentence-analysis cards inside the shared
 * keyed expansion state (no second expansion context). The sentence
 * analysis block id is `sentence_analysis:{analysisId}`, so the expansion
 * key equals the block id by construction.
 */
export const READER_SENTENCE_ANALYSIS_EXPANSION_KEY_PREFIX =
  "sentence_analysis:";

export function sentenceAnalysisExpansionKey(analysisId: string): string {
  return `${READER_SENTENCE_ANALYSIS_EXPANSION_KEY_PREFIX}${analysisId}`;
}

export function ReaderGrammarExpansionProvider({
  children,
  controlRef,
}: {
  children: React.ReactNode;
  controlRef?: ReaderGrammarExpansionControlRef;
}) {
  const [expandedItemIds, setExpandedItemIds] = React.useState<
    ReadonlySet<string>
  >(() => new Set());

  const expandItem = React.useCallback((itemId: string) => {
    setExpandedItemIds((current) => {
      if (current.has(itemId)) return current;
      const next = new Set(current);
      next.add(itemId);
      return next;
    });
  }, []);

  const collapseItem = React.useCallback((itemId: string) => {
    setExpandedItemIds((current) => {
      if (!current.has(itemId)) return current;
      const next = new Set(current);
      next.delete(itemId);
      return next;
    });
  }, []);

  const toggleItem = React.useCallback((itemId: string) => {
    setExpandedItemIds((current) => {
      const next = new Set(current);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  }, []);

  const clearExpanded = React.useCallback(() => {
    setExpandedItemIds((current) =>
      current.size === 0 ? current : new Set(),
    );
  }, []);

  const forgetItem = React.useCallback((itemId: string) => {
    setExpandedItemIds((current) => {
      if (!current.has(itemId)) return current;
      const next = new Set(current);
      next.delete(itemId);
      return next;
    });
  }, []);

  React.useEffect(() => {
    if (!controlRef) return;
    controlRef.current = {
      clear: clearExpanded,
      forgetItem,
      getExpandedItemIds: () => expandedItemIds,
    };
    return () => {
      controlRef.current = null;
    };
  }, [clearExpanded, forgetItem, controlRef, expandedItemIds]);

  const value = React.useMemo(
    () => ({ expandedItemIds, expandItem, collapseItem, toggleItem }),
    [collapseItem, expandItem, expandedItemIds, toggleItem],
  );

  return (
    <ReaderGrammarExpansionContext.Provider value={value}>
      {children}
    </ReaderGrammarExpansionContext.Provider>
  );
}

export interface ReaderCalloutActionTarget {
  kind: "grammar" | "sentence_analysis";
  blockId: string;
  anchorSegmentId: string;
  unitId: string;
  layerId: string;
  itemId?: string;
  analysisId?: string;
  title: string;
  preview: string;
  text: string;
}

export interface ReaderCalloutActionValue {
  onAskFromCallout?: (
    target: ReaderCalloutActionTarget,
    anchor: HTMLElement,
  ) => void;
  onFeedbackFromCallout?: (
    target: ReaderCalloutActionTarget,
    anchor: HTMLElement,
  ) => void;
}

export const ReaderCalloutActionContext =
  createContext<ReaderCalloutActionValue>({});

function useReaderCalloutActions() {
  return useContext(ReaderCalloutActionContext);
}

interface ReaderGrammarCalloutGroupElement {
  type: typeof READER_CALLOUT_GROUP_TYPE;
  id: string;
  children: ReaderCalloutElement[];
}

interface ReaderGrammarCalloutGroupValue {
  expandedItemIds: ReadonlySet<string>;
  expandItem: (itemId: string) => void;
  toggleItem: (itemId: string) => void;
}

const ReaderGrammarCalloutGroupContext =
  createContext<ReaderGrammarCalloutGroupValue | null>(null);

const copyExcludeProps = {
  contentEditable: false,
  draggable: false,
  "data-reader-record-copy-exclude": "true",
} as const;

// Frozen image URL override — minimal context, no media framework
export interface ReaderFrozenImageOverrideLocator {
  blockId: string;
  inlineOrdinal: number | null;
}
export interface ReaderFrozenImageOverrideContextValue {
  canEdit: boolean;
  stableDocumentId: string | null;
  upsert: (
    locator: ReaderFrozenImageOverrideLocator,
    rawUrl: string,
  ) => Promise<{ ok: boolean; message?: string }>;
  remove: (locator: ReaderFrozenImageOverrideLocator) => Promise<{ ok: boolean; message?: string }>;
}
export const ReaderFrozenImageOverrideContext =
  createContext<ReaderFrozenImageOverrideContextValue | null>(null);

// ---------------------------------------------------------------------------
// G3b Reader image (standalone + inline) — single image data shape, reuse
// isLoadableImageUrl, native <img> + Clipboard API, no media framework.
//
// R2 状态语言：loading / loaded / load_failed / unsafe 四态共用同一文案与
// chrome 语法；成功态 chrome 是右上角绝对定位的紧凑中性 toolbar（icon-only
// + Tooltip primitive，hover / focus-within 才出现，不占正文高度）；失败态
// 的恢复操作常显；unsafe 态 fail-closed，普通表面不显示 raw URL。
// ---------------------------------------------------------------------------

const READER_IMAGE_PLACEHOLDER_BASE_CLASS =
  "min-h-[4.5rem] flex-col gap-1.5 rounded-[8px] border border-hairline/70 bg-surface-raised/55 px-3 py-2.5 text-sm text-ink-soft";
// standalone 状态盒占满正文块宽度并预留稳定块级高度，避免加载时明显跳动
const READER_IMAGE_PLACEHOLDER_STANDALONE_CLASS = `flex w-full items-start ${READER_IMAGE_PLACEHOLDER_BASE_CLASS}`;
// inline 状态盒保持紧凑，不被 standalone 的全宽样式扩大
const READER_IMAGE_PLACEHOLDER_INLINE_CLASS = `inline-flex max-w-full items-start align-top ${READER_IMAGE_PLACEHOLDER_BASE_CLASS}`;
const READER_IMAGE_BUTTON_CLASS =
  "rounded border border-hairline px-2 py-0.5 text-xs text-lens-blue hover:bg-surface-raised";
// 成功态紧凑中性 chrome：右上角绝对定位，hover / focus-within 才出现，
// 不占正文高度（键盘可达：按钮 focus 时经 group-focus-within 显示）
const READER_IMAGE_TOOLBAR_CLASS =
  "absolute right-1.5 top-1.5 z-10 flex items-center gap-0.5 rounded-[6px] border border-hairline bg-surface/95 p-0.5 opacity-0 shadow-sm transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100";
const READER_IMAGE_TOOLBAR_BUTTON_CLASS = `flex size-6 cursor-pointer items-center justify-center rounded-[4px] text-ink-soft transition-colors hover:bg-surface-raised hover:text-ink focus-visible:opacity-100 ${primitiveFocusRing}`;

function ReaderImageInlineComponent({ attributes, children, element }: PlateElementProps) {
  const node = element as unknown as ReaderImageElement;
  const data = (node.data ?? {}) as unknown as {
    sourceUrl?: unknown;
    effectiveUrl?: unknown;
    altText?: unknown;
    title?: unknown;
    positionKind?: unknown;
    stableBlockId?: unknown;
    parentStableBlockId?: unknown;
    overrideUrl?: unknown;
    inlineOrdinal?: unknown;
    beforeUtf16?: unknown;
  };
  const sourceUrl = typeof data.sourceUrl === "string" ? data.sourceUrl : "";
  const effectiveUrl = data.effectiveUrl;
  const altText = typeof data.altText === "string" ? data.altText : "";
  // R2 契约：alt 只用于 img alt；显式 Markdown title 才作为可见 caption
  const title =
    typeof data.title === "string" && data.title.length > 0
      ? data.title
      : undefined;
  const positionKind = data.positionKind === "inline" ? "inline" : data.positionKind === "standalone" ? "standalone" : "inline";
  const overrideUrlRaw = data.overrideUrl;
  const hasOverride = typeof overrideUrlRaw === "string";
  const overrideUrl = hasOverride ? (overrideUrlRaw as string) : undefined;
  const stableBlockId = typeof data.stableBlockId === "string" ? data.stableBlockId : "";
  // standalone has no inlineOrdinal; inline has ordinal.
  const locatorInlineOrdinal =
    positionKind === "inline" && typeof data.inlineOrdinal === "number" && Number.isInteger(data.inlineOrdinal)
      ? (data.inlineOrdinal as number)
      : null;
  const resolvedInlineOrdinal = positionKind === "inline" ? locatorInlineOrdinal : null;
  const isStandalone = positionKind === "standalone";
  const placeholderClass = isStandalone
    ? READER_IMAGE_PLACEHOLDER_STANDALONE_CLASS
    : READER_IMAGE_PLACEHOLDER_INLINE_CLASS;

  const safe = typeof effectiveUrl === "string" && isLoadableImageUrl(effectiveUrl);
  const ctx = useContext(ReaderFrozenImageOverrideContext);
  const canEdit = Boolean(ctx?.canEdit && stableBlockId && ctx?.stableDocumentId);
  const [loadState, setLoadState] = React.useState<"loading" | "loaded" | "failed">("loading");
  const [prevUrl, setPrevUrl] = React.useState<unknown>(effectiveUrl);
  if (prevUrl !== effectiveUrl) {
    setPrevUrl(effectiveUrl);
    setLoadState("loading");
  }
  const [isEditing, setIsEditing] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);

  const copyLink = React.useCallback(() => {
    if (!safe || typeof effectiveUrl !== "string") return;
    try {
      void navigator.clipboard?.writeText(effectiveUrl).catch(() => {});
    } catch {}
  }, [effectiveUrl, safe]);

  const handleEdit = React.useCallback(() => {
    setDraft(hasOverride ? (overrideUrl as string) : "");
    setError(null);
    setSaving(false);
    setIsEditing(true);
  }, [hasOverride, overrideUrl]);

  const handleCancel = React.useCallback(() => {
    setIsEditing(false);
    setError(null);
    setSaving(false);
  }, []);

  const handleSave = React.useCallback(async () => {
    if (!ctx || !stableBlockId) return;
    setSaving(true);
    setError(null);
    const locator = { blockId: stableBlockId, inlineOrdinal: resolvedInlineOrdinal };
    const result = await ctx.upsert(locator, draft);
    setSaving(false);
    if (result.ok) {
      setIsEditing(false);
      setError(null);
    } else {
      setError(result.message ?? "保存失败，请重试。");
    }
  }, [ctx, stableBlockId, resolvedInlineOrdinal, draft]);

  const handleRestore = React.useCallback(async () => {
    if (!ctx || !stableBlockId) return;
    setSaving(true);
    setError(null);
    const locator = { blockId: stableBlockId, inlineOrdinal: resolvedInlineOrdinal };
    const result = await ctx.remove(locator);
    setSaving(false);
    if (result.ok) {
      setIsEditing(false);
      setError(null);
    } else {
      setError(result.message ?? "恢复失败，请重试。");
    }
  }, [ctx, stableBlockId, resolvedInlineOrdinal]);

  // R2：重试重新挂载同一安全 URL（fail 分支不渲染 img，回到 loading 分支
  // 即重新 mount 一个新 img 节点），不改写 URL。
  const handleRetry = React.useCallback(() => {
    setLoadState("loading");
  }, []);

  const editingPanel = isEditing ? (
    <span {...copyExcludeProps} className="mt-2 flex max-w-full flex-col gap-1.5 rounded border border-hairline bg-surface-raised/70 p-2 text-xs">
      <span className="break-all">
        <span className="font-medium">原始地址：</span>
        <span className="font-mono text-xs">{sourceUrl}</span>
      </span>
      <input
        aria-label="图片覆盖地址"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        className="w-full rounded border border-hairline bg-surface px-2 py-1 text-xs font-mono"
        placeholder="输入图片链接"
      />
      {error ? <span className="text-rose-600">{error}</span> : null}
      <span className="flex flex-wrap gap-1">
        <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={handleSave} disabled={saving}>
          {saving ? "保存中…" : "保存"}
        </button>
        <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={handleCancel} disabled={saving}>
          取消
        </button>
        {hasOverride ? (
          <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={handleRestore} disabled={saving}>
            恢复原始地址
          </button>
        ) : null}
      </span>
    </span>
  ) : null;

  // 成功态（loading / loaded）紧凑 toolbar：icon-only + Tooltip primitive
  const toolbar = (
    <TooltipProvider>
      <span {...copyExcludeProps} data-reader-image-toolbar="true" className={READER_IMAGE_TOOLBAR_CLASS}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="复制链接"
              className={READER_IMAGE_TOOLBAR_BUTTON_CLASS}
              onClick={copyLink}
            >
              <Copy aria-hidden="true" size={14} strokeWidth={1.9} />
            </button>
          </TooltipTrigger>
          <TooltipContent>复制链接</TooltipContent>
        </Tooltip>
        {canEdit && !isEditing ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label="修改链接"
                className={READER_IMAGE_TOOLBAR_BUTTON_CLASS}
                onClick={handleEdit}
              >
                <SquarePen aria-hidden="true" size={14} strokeWidth={1.9} />
              </button>
            </TooltipTrigger>
            <TooltipContent>修改链接</TooltipContent>
          </Tooltip>
        ) : null}
      </span>
    </TooltipProvider>
  );

  // unsafe / null effectiveUrl — fail-closed：普通表面只显示友好说明与修改
  // 入口，不显示 raw source/effective URL（显式编辑面板除外）
  if (!safe) {
    return (
      <span
        {...attributes}
        data-reader-image="true"
        data-reader-image-kind={positionKind}
        data-image-state="unsafe"
        className={isStandalone ? "group block w-full" : "group inline-block max-w-full align-top"}
      >
        <span {...copyExcludeProps} data-image-state="unsafe" className={placeholderClass}>
          <span className="font-medium text-ink">链接不安全</span>
          <span className="text-xs leading-snug">该图片链接不安全，图片未加载。</span>
          {canEdit && !isEditing ? (
            <span className="flex flex-wrap items-center gap-1">
              <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={handleEdit}>
                修改链接
              </button>
            </span>
          ) : null}
        </span>
        {editingPanel}
        {children}
      </span>
    );
  }

  // safe: loading / loaded / load_failed — share same edit handling
  if (loadState === "failed") {
    return (
      <span
        {...attributes}
        data-reader-image="true"
        data-reader-image-kind={positionKind}
        data-image-state="load_failed"
        className={isStandalone ? "group block w-full" : "group inline-block max-w-full align-top"}
      >
        <span {...copyExcludeProps} data-image-state="load_failed" className={placeholderClass}>
          <span className="font-medium text-ink">图片无法加载</span>
          {altText ? (
            <span className="break-all text-xs leading-snug">{altText}</span>
          ) : (
            <span className="text-xs leading-snug">图片加载失败，可重新加载或检查图片链接。</span>
          )}
          <span className="flex flex-wrap items-center gap-1">
            <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={handleRetry}>
              重新加载
            </button>
            <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={copyLink}>
              复制链接
            </button>
            {canEdit && !isEditing ? (
              <button type="button" className={READER_IMAGE_BUTTON_CLASS} onClick={handleEdit}>
                修改链接
              </button>
            ) : null}
          </span>
        </span>
        {editingPanel}
        {children}
      </span>
    );
  }

  return (
    <span
      {...attributes}
      data-reader-image="true"
      data-reader-image-kind={positionKind}
      data-image-state={loadState === "loaded" ? "loaded" : "loading"}
      className={isStandalone ? "group flex w-full justify-center" : "group inline-block max-w-full align-top"}
    >
      <span
        {...copyExcludeProps}
        className={
          isStandalone
            ? loadState === "loaded"
              ? "relative inline-flex max-w-full flex-col items-start gap-1"
              : "relative flex w-full flex-col items-start"
            : "relative inline-flex max-w-full flex-col items-start gap-1 align-top"
        }
      >
        {loadState === "loading" ? (
          <span
            data-image-state="loading"
            className={
              isStandalone
                ? "flex w-full min-h-[4.5rem] items-center justify-center rounded-[8px] border border-hairline/70 bg-surface-raised/55 px-3 py-2.5 text-sm text-ink-soft"
                : READER_IMAGE_PLACEHOLDER_INLINE_CLASS
            }
          >
            图片加载中…
          </span>
        ) : null}
        <img
          draggable={false}
          data-image-state={loadState === "loaded" ? "loaded" : undefined}
          src={effectiveUrl as string}
          alt={altText}
          decoding="async"
          referrerPolicy="no-referrer"
          onLoad={() => setLoadState("loaded")}
          onError={() => setLoadState("failed")}
          className={loadState === "loaded" ? "max-w-full rounded-[8px]" : "hidden max-w-full rounded-[8px]"}
        />
        {toolbar}
        {loadState === "loaded" && title ? (
          <span data-reader-image-caption="true" className="text-xs leading-snug text-ink-soft">
            {title}
          </span>
        ) : null}
        {editingPanel}
      </span>
      {children}
    </span>
  );
}

export const ReaderImagePlugin = createPlatePlugin({
  key: READER_IMAGE_TYPE,
  node: {
    isElement: true,
    isInline: true,
    isVoid: true,
    component: ReaderImageInlineComponent,
  },
});

function ReaderImageBlockComponent({ attributes, children }: PlateElementProps) {
  return (
    <div
      {...attributes}
      data-reader-image-block="true"
      className="reader-record-plate-image-block my-3 flex justify-center"
    >
      {children}
    </div>
  );
}

export const ReaderImageBlockPlugin = createPlatePlugin({
  key: READER_IMAGE_BLOCK_TYPE,
  node: { isElement: true, component: ReaderImageBlockComponent },
});

// ---------------------------------------------------------------------------
// Reader math (inline + block) — KaTeX, fail-closed, chrome excluded
// Mirrors the reader image surface: math rendering product is void inline + block
// wrapper, excluded from copy/selection/word count via same contracts.
// ---------------------------------------------------------------------------

const READER_MATH_FALLBACK_CLASS =
  "inline-flex max-w-full items-center rounded border border-hairline/60 bg-surface-raised/40 px-1.5 py-0.5 font-mono text-xs text-ink-soft break-all";
const READER_MATH_DISPLAY_WRAPPER_CLASS = "my-3 block w-full overflow-x-auto text-center";

function ReaderMathInlineComponent({ attributes, children, element }: PlateElementProps) {
  const node = element as unknown as ReaderMathInlineElement;
  const data = (node.data ?? {}) as unknown as { latex?: unknown; display?: unknown };
  const latex = typeof data.latex === "string" ? data.latex : "";
  const display = data.display === true;
  const katexRef = React.useRef<HTMLSpanElement>(null);
  let html: string | null = null;
  let isError = false;
  try {
    html = katex.renderToString(latex, {
      displayMode: display,
      throwOnError: true,
      strict: false,
      trust: false,
      output: "html",
    });
  } catch {
    isError = true;
    html = null;
  }
  React.useLayoutEffect(() => {
    if (isError || !html || !katexRef.current) return;
    katexRef.current.innerHTML = html;
  }, [html, isError]);
  const wrapperClass = display ? READER_MATH_DISPLAY_WRAPPER_CLASS : "inline-flex align-baseline";
  if (isError || !html) {
    // fail-closed: show raw latex source, never throw, never silently drop
    return (
      <span
        {...attributes}
        data-reader-math="true"
        data-math-display={display ? "true" : "false"}
        data-math-state="error"
        className={display ? "inline-block max-w-full align-top" : "inline-block align-baseline"}
      >
        <span {...copyExcludeProps} className={READER_MATH_FALLBACK_CLASS} data-reader-math-fallback="true">
          {latex}
        </span>
        {children}
      </span>
    );
  }
  return (
    <span
      {...attributes}
      data-reader-math="true"
      data-math-display={display ? "true" : "false"}
      data-math-state="ok"
      className={display ? "inline-block max-w-full align-top" : "inline-block align-baseline"}
    >
      <span
        {...copyExcludeProps}
        ref={katexRef}
        className={wrapperClass}
        data-reader-math-content="true"
      />
      {children}
    </span>
  );
}

export const ReaderMathInlinePlugin = createPlatePlugin({
  key: READER_MATH_INLINE_TYPE,
  node: { isElement: true, isInline: true, isVoid: true, component: ReaderMathInlineComponent },
});

function ReaderMathBlockComponent({ attributes, children }: PlateElementProps) {
  return (
    <div
      {...attributes}
      data-reader-math-block="true"
      className="reader-record-plate-math-block my-3 flex w-full justify-center overflow-x-auto"
    >
      {children}
    </div>
  );
}

export const ReaderMathBlockPlugin = createPlatePlugin({
  key: READER_MATH_BLOCK_TYPE,
  node: { isElement: true, component: ReaderMathBlockComponent },
});

function stopReaderCalloutControlEvent(
  event: React.SyntheticEvent<HTMLElement>,
) {
  event.preventDefault();
  event.stopPropagation();
}

function sentenceChunkDomId(chunk: {
  order: number;
  label: string;
  sourceMatch?: { markId: string };
}): string {
  return chunk.sourceMatch?.markId ?? `unmatched:${chunk.order}:${chunk.label}`;
}

function calloutTypeLabel(variant: ReaderCalloutElement["variant"]): string {
  return variant === "grammar" ? "语法解析" : "Ask 补充";
}

function domIdFromBlockId(prefix: string, id: string): string {
  return `${prefix}-${id.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}

function textFromNode(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(textFromNode).join(" ");
  }
  if (value && typeof value === "object") {
    const record = value as { text?: unknown; children?: unknown };
    if (typeof record.text === "string") {
      return record.text;
    }
    return textFromNode(record.children);
  }
  return "";
}

function stripMarkdownPreviewSyntax(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[>#]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compactPreviewFromText(value: string, maxLength = 96): string {
  const normalized = stripMarkdownPreviewSyntax(value);
  if (!normalized) {
    return "";
  }
  const sentenceBoundary = normalized.search(/[.!?。！？；;]/);
  const firstSentence =
    sentenceBoundary >= 12 && sentenceBoundary < maxLength
      ? normalized.slice(0, sentenceBoundary + 1)
      : normalized;
  if (firstSentence.length <= maxLength) {
    return firstSentence;
  }
  return `${firstSentence.slice(0, maxLength - 1).trimEnd()}…`;
}

function compactPreviewFromMarkdown(value: string, maxLength = 96): string {
  const paragraphs = value
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean)
    .filter((part) => !part.startsWith("```"))
    .filter((part) => !/^#{1,6}\s+/.test(part));
  return compactPreviewFromText(paragraphs[0] ?? value, maxLength);
}

function previewCandidateTexts(value: unknown, output: string[] = []): string[] {
  if (!value || typeof value !== "object") {
    return output;
  }
  if (Array.isArray(value)) {
    value.forEach((child) => previewCandidateTexts(child, output));
    return output;
  }
  const record = value as { type?: unknown; children?: unknown; text?: unknown };
  if (typeof record.text === "string") {
    return output;
  }
  const type = typeof record.type === "string" ? record.type : "";
  const normalized = textFromNode(record.children).replace(/\s+/g, " ").trim();
  if (
    normalized &&
    (type.includes("paragraph") ||
      type === "p" ||
      type.includes("blockquote") ||
      type.includes("li"))
  ) {
    output.push(normalized);
  }
  previewCandidateTexts(record.children, output);
  return output;
}

function compactPreviewFromChildren(children: unknown, maxLength = 96): string {
  const firstBlock = previewCandidateTexts(children)[0];
  const normalized = firstBlock
    ? compactPreviewFromText(firstBlock, maxLength)
    : compactPreviewFromText(textFromNode(children), maxLength);
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
}

function ReaderCalloutActionButtons({
  target,
  askDisabled = false,
  feedbackDisabled = false,
}: {
  target: ReaderCalloutActionTarget;
  askDisabled?: boolean;
  feedbackDisabled?: boolean;
}) {
  const { onAskFromCallout, onFeedbackFromCallout } = useReaderCalloutActions();
  const askAvailable = Boolean(!askDisabled && target.text.trim() && onAskFromCallout);
  const feedbackAvailable = Boolean(!feedbackDisabled && onFeedbackFromCallout);

  if (!askAvailable && !feedbackAvailable) {
    return null;
  }

  return (
    <div
      className="reader-record-plate-callout-actions"
      data-reader-record-callout-actions={target.kind}
      {...copyExcludeProps}
    >
      {askAvailable ? (
        <button
          type="button"
          className="reader-record-plate-callout-icon-button"
          aria-label="带这条解析提问"
          data-reader-record-callout-action="ask"
          {...copyExcludeProps}
          onPointerDown={stopReaderCalloutControlEvent}
          onMouseDown={stopReaderCalloutControlEvent}
          onClick={(event) => {
            stopReaderCalloutControlEvent(event);
            onAskFromCallout?.(target, event.currentTarget);
          }}
        >
          <MessageCircleQuestion aria-hidden="true" size={15} strokeWidth={1.9} />
        </button>
      ) : null}
      {feedbackAvailable ? (
        <button
          type="button"
          className="reader-record-plate-callout-icon-button"
          aria-label="反馈解析"
          data-reader-record-callout-action="feedback"
          {...copyExcludeProps}
          onPointerDown={stopReaderCalloutControlEvent}
          onMouseDown={stopReaderCalloutControlEvent}
          onClick={(event) => {
            stopReaderCalloutControlEvent(event);
            onFeedbackFromCallout?.(target, event.currentTarget);
          }}
        >
          <Flag aria-hidden="true" size={15} strokeWidth={1.9} />
        </button>
      ) : null}
    </div>
  );
}

// --- Paragraph element ---

function ReaderParagraphComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderParagraphElement).data;

  return (
    <p
      {...attributes}
      className={`reader-record-plate-paragraph text-pretty text-ink ${attributes?.className ?? ""}`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "paragraph",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderParagraphElement).id}
      data-reader-record-stable-block-type="paragraph"
      data-sentence-id={data?.sentenceId}
    >
      {children}
    </p>
  );
}

export const ReaderParagraphPlugin = createPlatePlugin({
  key: READER_PARAGRAPH_TYPE,
  node: {
    isElement: true,
    component: ReaderParagraphComponent,
  },
});

// --- Blockquote element ---

function ReaderBlockquoteComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderBlockquoteElement).data;

  return (
    <blockquote
      {...attributes}
      aria-label="译文"
      className={`reader-record-plate-blockquote reader-record-plate-translation reader-record-plate-translation-lane reader-record-plate-translation-copy reader-font-sans border-l border-hairline/85 bg-transparent ${
        attributes?.className ?? ""
      }`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "blockquote",
        unitId: data?.unitId,
      })}
      data-reader-record-translation-lane="true"
      data-reader-record-block-id={(element as unknown as ReaderBlockquoteElement).id}
    >
      {children}
    </blockquote>
  );
}

export const ReaderBlockquotePlugin = createPlatePlugin({
  key: READER_BLOCKQUOTE_TYPE,
  node: {
    isElement: true,
    component: ReaderBlockquoteComponent,
  },
});

// --- Callout element ---

function ReaderCalloutGroupComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const node = element as unknown as ReaderGrammarCalloutGroupElement;
  const calloutCount = node.children.length;
  // R2.1E: delegate expansion state to the Surface-level
  // ReaderGrammarExpansionContext (above <Plate>) so that expansion survives
  // editor.tf.replaceNodes remounts of the callout-group block. Previously
  // local useState was used, which reset to empty on every remount.
  const expansionContext = useContext(ReaderGrammarExpansionContext);
  const expandItem = expansionContext.expandItem;
  const toggleItem = expansionContext.toggleItem;
  const expandedItemIds = expansionContext.expandedItemIds;
  const contextValue = React.useMemo(
    () => ({ expandedItemIds, expandItem, toggleItem }),
    [expandItem, expandedItemIds, toggleItem],
  );

  return (
    <section
      {...attributes}
      className={`reader-record-plate-callout-group reader-record-plate-callout-group--grammar font-sans text-ink-soft ${
        attributes?.className ?? ""
      }`.trim()}
      role="group"
      aria-label={calloutCount > 1 ? `语法解析 · ${calloutCount} 条` : "语法解析"}
      {...readerRecordNavigableNodeAttrs({ nodeKind: "callout-group" })}
      data-reader-record-callout-group="grammar"
      data-reader-record-callout-group-count={calloutCount}
      data-reader-record-block-id={node.id}
    >
      <div className="reader-record-plate-callout-group-header">
        <span
          className="reader-record-plate-callout-group-icon"
          aria-hidden="true"
          {...copyExcludeProps}
        >
          <WandSparkles size={16} strokeWidth={1.8} />
        </span>
        <span className="reader-record-plate-callout-group-label">
          {calloutCount > 1 ? `语法解析 · ${calloutCount} 条` : "语法解析"}
        </span>
      </div>
      <ReaderGrammarCalloutGroupContext.Provider value={contextValue}>
        <div className="reader-record-plate-callout-group-rows">{children}</div>
      </ReaderGrammarCalloutGroupContext.Provider>
    </section>
  );
}

export const ReaderCalloutGroupPlugin = createPlatePlugin({
  key: READER_CALLOUT_GROUP_TYPE,
  node: {
    isElement: true,
    component: ReaderCalloutGroupComponent,
  },
});

function ReaderCalloutComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const node = element as unknown as ReaderCalloutElement;
  const data = node.data;
  const variant = node.variant;
  const {
    activeGrammarItemId,
    expandGrammarItemRequest,
    setActiveGrammarItemId,
    hoverGrammarItemId,
    pulseGrammarItemId,
  } = useReaderGrammarInteraction();
  const groupContext = useContext(ReaderGrammarCalloutGroupContext);
  const expansionContext = useContext(ReaderGrammarExpansionContext);

  const isGrammar = variant === "grammar";
  const isSupplement = variant === "supplement";
  const grammarItemId = isGrammar ? data?.itemId : undefined;
  const isGroupedGrammar = isGrammar && groupContext !== null;
  // P2 fix: only treat as standalone-grammar (itemId-keyed context path)
  // when grammarItemId is actually present. When a grammar callout lacks
  // itemId (legacy/edge data), fall back to localExpanded behavior so the
  // callout can still be expanded/collapsed by the user.
  const isStandaloneGrammar =
    isGrammar && groupContext === null && grammarItemId !== undefined;
  const grammarActive =
    isGrammar && grammarItemId ? activeGrammarItemId === grammarItemId : false;
  const label = calloutTypeLabel(variant);
  // Unified filled-card family: visual values live in semantic classes
  // (globals.css), never inline. Group rows stay flat inside the group
  // card; standalone grammar/supplement cards get the family fill.
  const containerClass = [
    "reader-record-plate-callout font-sans text-ink-soft",
    isGroupedGrammar
      ? "reader-record-plate-callout--grammar-row"
      : isGrammar
      ? "reader-record-plate-callout--grammar"
      : "",
    grammarActive ? "reader-record-plate-callout--grammar-active" : "",
    isSupplement ? "reader-record-plate-callout--supplement" : "",
    attributes?.className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  const eyebrowClass = "text-grammar-violet/80";
  const title = isGrammar
    ? data?.grammarPoint ?? ""
    : data?.supplementTitle ?? "";
  const [localExpanded, setLocalExpanded] = React.useState(!isGrammar);
  const contentId = domIdFromBlockId("reader-record-callout-content", node.id);
  const preview =
    isGrammar && data?.note
      ? compactPreviewFromMarkdown(data.note)
      : compactPreviewFromChildren(node.children);
  const fullText = textFromNode(node.children).replace(/\s+/g, " ").trim();
  const expanded = isGroupedGrammar
    ? grammarItemId !== undefined && groupContext.expandedItemIds.has(grammarItemId)
    : isStandaloneGrammar
      ? grammarItemId !== undefined && expansionContext.expandedItemIds.has(grammarItemId)
      : localExpanded;
  const toggleExpanded = React.useCallback(() => {
    if (isGroupedGrammar && grammarItemId) {
      groupContext.toggleItem(grammarItemId);
      return;
    }
    if (isStandaloneGrammar && grammarItemId) {
      expansionContext.toggleItem(grammarItemId);
      return;
    }
    setLocalExpanded((current) => !current);
  }, [expansionContext, grammarItemId, groupContext, isGroupedGrammar, isStandaloneGrammar]);
  const actionTarget: ReaderCalloutActionTarget | null = isGrammar
    ? {
        kind: "grammar",
        blockId: node.id,
        anchorSegmentId: data?.anchorSegmentId ?? "",
        unitId: data?.unitId ?? "",
        layerId: data?.layerId ?? "",
        itemId: grammarItemId,
        analysisId: data?.analysisId,
        title: title || label,
        preview,
        text: fullText,
      }
    : null;
  const handledExpandGrammarRequestIdRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (!isGrammar || !grammarItemId || !expandGrammarItemRequest) {
      return;
    }
    if (expandGrammarItemRequest.itemId !== grammarItemId) {
      return;
    }
    if (
      handledExpandGrammarRequestIdRef.current ===
      expandGrammarItemRequest.requestId
    ) {
      return;
    }
    handledExpandGrammarRequestIdRef.current = expandGrammarItemRequest.requestId;

    if (isGroupedGrammar) {
      groupContext.expandItem(grammarItemId);
    } else {
      // T4.2a-PUX-R4-R2.1C: lift expand state into itemId-keyed context so
      // it survives targeted replaceNodes on the same callout element.
      // Grammar callouts WITHOUT itemId never reach this branch: the
      // guard above requires grammarItemId, and with one present the
      // only remaining distinction is grouped vs standalone context.
      expansionContext.expandItem(grammarItemId);
    }
  }, [
    expandGrammarItemRequest?.itemId,
    expandGrammarItemRequest?.requestId,
    expansionContext,
    grammarItemId,
    groupContext,
    isGroupedGrammar,
    isGrammar,
  ]);

  return (
    <div
      {...attributes}
      className={containerClass}
      role="note"
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "callout",
        unitId: data?.unitId,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-callout="true"
      data-callout-variant={variant}
      data-reader-record-block-id={node.id}
      data-layer-id={data?.layerId}
      data-supplement-id={data?.supplementId}
      data-reader-record-grammar-item-id={grammarItemId}
      data-reader-record-grammar-active={grammarActive ? "true" : undefined}
      data-reader-record-callout-label={label}
      data-reader-record-callout-collapsed={expanded ? "false" : "true"}
      data-reader-record-callout-row={isGroupedGrammar ? "grammar" : undefined}
      tabIndex={isGrammar ? -1 : undefined}
      onMouseEnter={() => {
        if (grammarItemId) {
          hoverGrammarItemId(grammarItemId);
        }
      }}
      onMouseLeave={() => {
        if (grammarItemId) {
          hoverGrammarItemId(null);
        }
      }}
      onFocus={() => {
        if (grammarItemId) {
          setActiveGrammarItemId(grammarItemId);
        }
      }}
      onBlur={() => {
        if (grammarItemId) {
          setActiveGrammarItemId(null);
        }
      }}
      onClick={() => {
        if (grammarItemId) {
          pulseGrammarItemId(grammarItemId);
        }
      }}
    >
      <div className="min-w-0">
        <div className="reader-record-plate-callout-header mb-1.5 flex min-w-0 items-start gap-2">
          <span
            className={`reader-record-plate-note-icon mt-0.5 leading-none ${eyebrowClass}`}
            aria-hidden="true"
            {...copyExcludeProps}
          >
            {isGrammar ? (
              <BookOpenText size={16} strokeWidth={1.8} />
            ) : (
              <MessageSquareQuote size={16} strokeWidth={1.8} />
            )}
          </span>
          <div className="reader-record-plate-callout-heading min-w-0">
            <div className={`reader-record-plate-label ${eyebrowClass}`}>
              {label}
            </div>
            <div className="reader-record-plate-callout-title-line">
              {title ? (
                <div
                  className="reader-record-plate-note-title reader-record-plate-callout-title mt-0.5 min-w-0 text-ink/90"
                  data-reader-record-callout-title={
                    isGrammar ? "grammar" : variant
                  }
                >
                  {title}
                </div>
              ) : null}
              {isGrammar && data?.pattern ? (
                <span
                  className="reader-record-plate-callout-pattern-chip"
                  data-reader-record-callout-pattern="grammar"
                >
                  {data.pattern}
                </span>
              ) : null}
            </div>
          </div>
          {isGrammar ? (
            <div
              className="reader-record-plate-callout-row-controls"
              data-reader-record-callout-controls="grammar"
              {...copyExcludeProps}
            >
              <button
                type="button"
                className="reader-record-plate-callout-toggle"
                aria-expanded={expanded}
                aria-controls={contentId}
                aria-label={expanded ? "收起语法解析" : "展开语法解析"}
                data-reader-record-callout-toggle="grammar"
                {...copyExcludeProps}
                onPointerDown={stopReaderCalloutControlEvent}
                onMouseDown={stopReaderCalloutControlEvent}
                onClick={(event) => {
                  stopReaderCalloutControlEvent(event);
                  toggleExpanded();
                }}
              >
                <ChevronDown
                  aria-hidden="true"
                  size={15}
                  strokeWidth={1.9}
                  className="reader-record-plate-callout-toggle-icon"
                />
              </button>
              {actionTarget ? (
                <ReaderCalloutActionButtons
                  target={actionTarget}
                  askDisabled
                  feedbackDisabled={!actionTarget.analysisId}
                />
              ) : null}
            </div>
          ) : null}
        </div>
        <div
          id={contentId}
          className="reader-record-plate-markdown reader-record-plate-note-prose mt-1.5 text-ink-soft"
          data-reader-record-markdown-content="plate"
          hidden={isGrammar && !expanded}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

export const ReaderCalloutPlugin = createPlatePlugin({
  key: READER_CALLOUT_TYPE,
  node: {
    isElement: true,
    component: ReaderCalloutComponent,
  },
});

// --- Sentence analysis element ---

function ReaderSentenceAnalysisComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const node = element as unknown as ReaderSentenceAnalysisElement;
  const data = node.data;
  // Expansion state lives in the shared keyed expansion context (above
  // <Plate>), so it survives editor.tf.replaceNodes remounts of this
  // block. Cards without an analysisId (legacy/edge data) fall back to
  // local state, mirroring the grammar callout fallback.
  const expansionContext = useContext(ReaderGrammarExpansionContext);
  const expansionKey = data?.analysisId
    ? sentenceAnalysisExpansionKey(data.analysisId)
    : null;
  const [localExpanded, setLocalExpanded] = React.useState(false);
  const expanded = expansionKey
    ? expansionContext.expandedItemIds.has(expansionKey)
    : localExpanded;
  const toggleExpanded = React.useCallback(() => {
    if (expansionKey) {
      expansionContext.toggleItem(expansionKey);
      return;
    }
    setLocalExpanded((current) => !current);
  }, [expansionContext, expansionKey, setLocalExpanded]);
  const contentId = domIdFromBlockId("reader-record-sentence-analysis-content", node.id);
  const chunkCount = data?.chunks?.length ?? 0;
  const summary = chunkCount > 0 ? `${chunkCount} 个片段` : "结构说明";
  const fullText = textFromNode(node.children).replace(/\s+/g, " ").trim();
  const actionTarget: ReaderCalloutActionTarget = {
    kind: "sentence_analysis",
    blockId: node.id,
    anchorSegmentId: data?.anchorSegmentId ?? "",
    unitId: data?.unitId ?? "",
    layerId: data?.layerId ?? "",
    analysisId: data?.analysisId,
    title: data?.label ?? "长句拆析",
    preview: summary,
    text: fullText,
  };

  return (
    <section
      {...attributes}
      className={`reader-record-plate-sentence-analysis font-sans text-ink-soft ${
        attributes?.className ?? ""
      }`.trim()}
      role="note"
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "sentence-analysis",
        unitId: data?.unitId,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-sentence-analysis-block="true"
      data-reader-record-sentence-analysis-element={READER_SENTENCE_ANALYSIS_TYPE}
      data-reader-record-sentence-analysis-label="长句拆析"
      data-reader-record-block-id={node.id}
      data-layer-id={data?.layerId}
      data-analysis-id={data?.analysisId}
      data-reader-record-sentence-analysis-collapsed={expanded ? "false" : "true"}
    >
      <div className="min-w-0">
        <div className="reader-record-plate-callout-header reader-record-plate-sentence-analysis-header mb-1.5 flex min-w-0 items-start gap-2">
          <div className="reader-record-plate-callout-heading min-w-0">
            <div className="reader-record-plate-label reader-record-plate-sentence-analysis-eyebrow text-context-blue/85">
              <span
                className="reader-record-plate-note-icon leading-none text-context-blue/85"
                aria-hidden="true"
                {...copyExcludeProps}
              >
                <TextSearch size={16} strokeWidth={1.8} />
              </span>
              <span>长句拆析</span>
            </div>
            <div className="reader-record-plate-sentence-analysis-title-row">
              {data?.label ? (
                <div
                  className="reader-record-plate-note-title reader-record-plate-callout-title mt-0.5 min-w-0 text-ink/88"
                  data-reader-record-callout-title="sentence-analysis"
                >
                  {data.label}
                </div>
              ) : null}
            </div>
          </div>
          <div
            className="reader-record-plate-callout-row-controls"
            data-reader-record-callout-controls="sentence-analysis"
            {...copyExcludeProps}
          >
            <button
              type="button"
              className="reader-record-plate-callout-toggle"
              aria-expanded={expanded}
              aria-controls={contentId}
              aria-label={expanded ? "收起长句拆析" : "展开长句拆析"}
              data-reader-record-callout-toggle="sentence-analysis"
              {...copyExcludeProps}
              onPointerDown={stopReaderCalloutControlEvent}
              onMouseDown={stopReaderCalloutControlEvent}
              onClick={(event) => {
                stopReaderCalloutControlEvent(event);
                toggleExpanded();
              }}
            >
              <ChevronDown
                aria-hidden="true"
                size={15}
                strokeWidth={1.9}
                className="reader-record-plate-callout-toggle-icon"
              />
            </button>
            <ReaderCalloutActionButtons
              target={actionTarget}
              feedbackDisabled={!data?.analysisId}
            />
          </div>
        </div>
        <div
          id={contentId}
          className={classNames(
            "reader-record-plate-markdown reader-record-plate-note-prose",
            data?.chunks?.length ? "mt-2" : "mt-1",
          )}
          data-reader-record-markdown-content="plate"
          hidden={!expanded}
        >
          {children}
        </div>
      </div>
    </section>
  );
}

export const ReaderSentenceAnalysisPlugin = createPlatePlugin({
  key: READER_SENTENCE_ANALYSIS_TYPE,
  node: {
    isElement: true,
    component: ReaderSentenceAnalysisComponent,
  },
});

function ReaderSentenceAnalysisChunksComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <dl
      {...attributes}
      className={`reader-record-plate-sentence-analysis-chunks ${
        attributes?.className ?? ""
      }`.trim()}
      data-reader-record-sentence-analysis-chunks="plate"
    >
      {children}
    </dl>
  );
}

function ReaderSentenceAnalysisChunkComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const chunk = (element as unknown as ReaderSentenceAnalysisChunkElement).data;
  const { activeChunkId, setActiveChunkId } =
    useReaderSentenceAnalysisInteraction();
  const chunkId = sentenceChunkDomId(chunk);
  const hasSourceMatch = Boolean(chunk.sourceMatch);
  const active = hasSourceMatch && activeChunkId === chunkId;
  const activateChunk = React.useCallback(() => {
    if (hasSourceMatch) {
      setActiveChunkId(chunkId);
    }
  }, [chunkId, hasSourceMatch, setActiveChunkId]);

  return (
    <div
      {...attributes}
      className={`reader-record-plate-sentence-analysis-chunk grid grid-cols-[minmax(6rem,0.4fr)_minmax(0,1fr)] rounded-[6px] border border-hairline/55 bg-surface/35 ${
        hasSourceMatch
          ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-context-blue/30"
          : ""
      } ${attributes?.className ?? ""}`.trim()}
      data-reader-record-sentence-analysis-chunk={chunk.label}
      data-reader-record-sentence-analysis-chunk-order={chunk.order}
      data-reader-record-sentence-analysis-chunk-match={hasSourceMatch ? "true" : "false"}
      data-reader-record-sentence-analysis-chunk-source-mark-id={chunk.sourceMatch?.markId}
      data-reader-record-sentence-analysis-chunk-source-start={
        chunk.sourceMatch ? String(chunk.sourceMatch.startOffset) : undefined
      }
      data-reader-record-sentence-analysis-chunk-source-end={
        chunk.sourceMatch ? String(chunk.sourceMatch.endOffset) : undefined
      }
      data-reader-record-sentence-analysis-chunk-active={active ? "true" : "false"}
      role={hasSourceMatch ? "button" : undefined}
      aria-label={
        hasSourceMatch
          ? `定位原文片段：${chunk.label}`
          : undefined
      }
      tabIndex={hasSourceMatch ? 0 : undefined}
      onMouseEnter={() => {
        activateChunk();
      }}
      onMouseLeave={() => {
        if (hasSourceMatch) setActiveChunkId(null);
      }}
      onFocus={() => {
        activateChunk();
      }}
      onBlur={() => {
        if (hasSourceMatch) setActiveChunkId(null);
      }}
      onPointerDown={() => {
        activateChunk();
      }}
      onClick={() => {
        activateChunk();
      }}
      onKeyDown={(event) => {
        if (!hasSourceMatch) {
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activateChunk();
        }
      }}
    >
      <dt className="min-w-0">
        <span className="reader-record-plate-sentence-analysis-chunk-label block text-context-blue/90">
          {chunk.label}
        </span>
      </dt>
      <dd className="reader-record-plate-sentence-analysis-chunk-body min-w-0 font-sans text-ink-soft/92">
        {children}
      </dd>
    </div>
  );
}

export const ReaderSentenceAnalysisChunksPlugin = createPlatePlugin({
  key: READER_SENTENCE_ANALYSIS_CHUNKS_TYPE,
  node: {
    isElement: true,
    component: ReaderSentenceAnalysisChunksComponent,
  },
});

export const ReaderSentenceAnalysisChunkPlugin = createPlatePlugin({
  key: READER_SENTENCE_ANALYSIS_CHUNK_TYPE,
  node: {
    isElement: true,
    component: ReaderSentenceAnalysisChunkComponent,
  },
});

// ---------------------------------------------------------------------------
// B2.5: Stable-block-derived element plugins (reader_heading / reader_list /
// reader_list_item / reader_code_block / reader_markdown_blockquote /
// reader_table / reader_table_row / reader_table_cell / reader_hr).
//
// These render Markdown stable blocks emitted by the backend (A5) projected
// through B2.4. They differ from the `markdownElementPlugins` below:
// - They carry `ReaderRecordPlateStableBlockData` (anchor segment / unit id /
//   base range / hash) so selection, vocabulary marks, grammar marks and the
//   navigation rail continue to work on Markdown-rendered blocks.
// - They emit `data-reader-record-node` + `data-unit-id` to join the shared
//   navigation contract (`readerRecordNavigableNodeAttrs`).
// - Heading level comes from `element.level` (not from `h1`/`h2`/... type).
// - List ordering comes from `element.ordered` (not from `ul`/`ol` type).
// ---------------------------------------------------------------------------

function ReaderStableHeadingComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderHeadingElement).data;
  const level = (element as unknown as ReaderHeadingElement).level ?? 1;
  const Tag = (`h${Math.min(Math.max(level, 1), 6)}` as unknown) as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";

  return (
    <Tag
      {...attributes}
      className={`reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h${level} ${
        attributes?.className ?? ""
      }`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "heading",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderHeadingElement).id}
      data-reader-record-stable-block-type="heading"
      data-reader-record-markdown-node={`h${level}`}
    >
      {children}
    </Tag>
  );
}

function ReaderStableListComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderListElement).data;
  const ordered = (element as unknown as ReaderListElement).ordered;
  const Tag = ordered ? "ol" : "ul";
  const listClass = ordered
    ? "reader-record-plate-markdown-list list-decimal"
    : "reader-record-plate-markdown-list list-disc";

  return (
    <Tag
      {...attributes}
      className={`${listClass} ${attributes?.className ?? ""}`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "list",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderListElement).id}
      data-reader-record-stable-block-type="list"
      data-reader-record-markdown-node={ordered ? "ol" : "ul"}
    >
      {children}
    </Tag>
  );
}

function ReaderStableListItemComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderListItemElement).data;
  const presentationOnly = data?.presentationOnly === true;

  return (
    <li
      {...attributes}
      className={`${attributes?.className ?? ""}`.trim()}
      {...(presentationOnly
        ? {}
        : readerRecordNavigableNodeAttrs({
            nodeKind: "list_item",
            unitId: data?.unitId,
            isUnitStart: data?.isUnitStart,
            anchorSegmentId: data?.anchorSegmentId,
          }))}
      data-reader-record-block-id={(element as unknown as ReaderListItemElement).id}
      data-reader-record-stable-block-type={presentationOnly ? undefined : "list_item"}
      data-reader-record-markdown-node="li"
    >
      {children}
    </li>
  );
}

// 代码块工具区（语言 badge + 复制按钮）hover / focus-visible
// 才显示（Notion 式 chrome 收敛，与图片块 READER_IMAGE_BUTTON_REVEAL_CLASS 同款）。
const READER_CODE_TOOLBAR_REVEAL_CLASS =
  "flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100";
const READER_CODE_COPY_FEEDBACK_MS = 1600;

/** 从 Plate element 的 text leaf 拼出代码原文（复制与高亮共用，不改文档模型）。 */
function readerCodeBlockPlainText(element: unknown): string {
  const children = (element as { children?: unknown } | null)?.children;
  if (!Array.isArray(children)) {
    return "";
  }
  return children
    .map((child) =>
      typeof (child as { text?: unknown })?.text === "string"
        ? (child as { text: string }).text
        : "",
    )
    .join("");
}

function ReaderStableCodeBlockComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderCodeBlockElement).data;
  const language = data?.language ?? null;
  const codeText = readerCodeBlockPlainText(element);
  const tokens = useReaderCodeHighlight(codeText, language);
  const [copyState, setCopyState] = React.useState<"idle" | "copied" | "error">(
    "idle",
  );
  const copyResetTimeoutRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    return () => {
      if (copyResetTimeoutRef.current !== null) {
        window.clearTimeout(copyResetTimeoutRef.current);
      }
    };
  }, []);

  const handleCopy = React.useCallback(() => {
    void (async () => {
      let next: "copied" | "error";
      try {
        await navigator.clipboard.writeText(codeText);
        next = "copied";
      } catch {
        next = "error";
      }
      setCopyState(next);
      if (copyResetTimeoutRef.current !== null) {
        window.clearTimeout(copyResetTimeoutRef.current);
      }
      copyResetTimeoutRef.current = window.setTimeout(() => {
        setCopyState("idle");
      }, READER_CODE_COPY_FEEDBACK_MS);
    })();
  }, [codeText]);

  return (
    <pre
      {...attributes}
      className={`reader-record-plate-markdown-code-block relative group overflow-x-auto ${
        attributes?.className ?? ""
      }`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "code_block",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderCodeBlockElement).id}
      data-reader-record-stable-block-type="code_block"
      data-reader-record-markdown-node="code_block"
      data-language={language ?? undefined}
    >
      <span
        data-testid="code-toolbar"
        className={`absolute right-3 top-2 font-sans text-[0.7rem] ${READER_CODE_TOOLBAR_REVEAL_CLASS}`}
        {...copyExcludeProps}
      >
        {language ? (
          <span
            data-testid="code-language-badge"
            className="font-medium uppercase tracking-wide text-muted-foreground/70"
            {...copyExcludeProps}
          >
            {language}
          </span>
        ) : null}
        <button
          type="button"
          data-testid="code-copy-button"
          aria-label="复制代码"
          title="复制代码"
          onClick={(event) => {
            event.stopPropagation();
            handleCopy();
          }}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 font-medium text-muted-foreground/80 transition-colors hover:bg-surface-raised hover:text-ink"
          {...copyExcludeProps}
        >
          {copyState === "copied" ? (
            <Check size={12} aria-hidden="true" />
          ) : (
            <Copy size={12} aria-hidden="true" />
          )}
          <span data-testid="code-copy-status" aria-live="polite">
            {copyState === "copied"
              ? "已复制"
              : copyState === "error"
                ? "复制失败"
                : "复制"}
          </span>
        </button>
      </span>
      <code className={language ? "block pt-6" : undefined}>
        {tokens ? (
          <>
            {tokens.map((line, lineIndex) => (
              <React.Fragment key={lineIndex}>
                {lineIndex > 0 ? "\n" : null}
                {line.map((token, tokenIndex) => (
                  <span
                    key={tokenIndex}
                    className={readerCodeTokenClassName(token.color)}
                  >
                    {token.content}
                  </span>
                ))}
              </React.Fragment>
            ))}
            {/* 对齐 slate 纯文本渲染的尾部行为：以 \n 结尾的代码块会多渲染
             * 一个换行，保持高亮前后 textContent 完全一致（selection/copy 不变）。 */}
            {codeText.endsWith("\n") ? "\n" : null}
          </>
        ) : (
          children
        )}
      </code>
    </pre>
  );
}

function ReaderStableMarkdownBlockquoteComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderMarkdownBlockquoteElement).data;

  return (
    <blockquote
      {...attributes}
      className={`reader-record-plate-markdown-blockquote text-ink-soft ${
        attributes?.className ?? ""
      }`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "markdown_blockquote",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={
        (element as unknown as ReaderMarkdownBlockquoteElement).id
      }
      data-reader-record-stable-block-type="blockquote"
      data-reader-record-markdown-node="blockquote"
    >
      {children}
    </blockquote>
  );
}

function ReaderStableTableComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderTableElement).data;

  return (
    <div
      {...attributes}
      className={`reader-record-plate-markdown-table-wrapper my-2 overflow-x-auto ${
        attributes?.className ?? ""
      }`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "table",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderTableElement).id}
      data-reader-record-stable-block-type="table"
      data-reader-record-markdown-node="table"
    >
      <table className="reader-record-plate-markdown-table w-full border-collapse">
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function ReaderStableTableRowComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderTableRowElement).data;
  const isHeader = data?.isHeader ?? false;

  return (
    <tr
      {...attributes}
      className={`reader-record-plate-markdown-table-row ${
        isHeader ? "reader-record-plate-markdown-table-row--header" : ""
      } ${attributes?.className ?? ""}`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "table_row",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderTableRowElement).id}
      data-reader-record-stable-block-type="table_row"
      data-reader-record-markdown-node="tr"
      data-header-row={isHeader ? "true" : undefined}
    >
      {children}
    </tr>
  );
}

function ReaderStableTableCellComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderTableCellElement).data;
  const isHeader = data?.isHeader ?? false;
  const alignment = data?.alignment ?? "default";
  const Tag = isHeader ? "th" : "td";
  const alignClass =
    alignment === "left"
      ? "text-left"
      : alignment === "center"
        ? "text-center"
        : alignment === "right"
          ? "text-right"
          : "";

  return (
    <Tag
      {...attributes}
      className={`reader-record-plate-markdown-table-cell border border-hairline/60 px-2 py-1 ${
        isHeader ? "bg-muted/40 font-semibold" : ""
      } ${alignClass} ${attributes?.className ?? ""}`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "table_cell",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderTableCellElement).id}
      data-reader-record-stable-block-type="table_cell"
      data-reader-record-markdown-node={isHeader ? "th" : "td"}
      data-alignment={alignment !== "default" ? alignment : undefined}
    >
      {children}
    </Tag>
  );
}

function ReaderStableHrComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderHrElement).data;

  return (
    <div
      {...attributes}
      className={`my-2 ${attributes?.className ?? ""}`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "hr",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={(element as unknown as ReaderHrElement).id}
      data-reader-record-stable-block-type="hr"
      data-reader-record-markdown-node="hr"
    >
      <hr className="border-current/15" />
      {children}
    </div>
  );
}

export const ReaderStableHeadingPlugin = createPlatePlugin({
  key: READER_HEADING_TYPE,
  node: { isElement: true, component: ReaderStableHeadingComponent },
});

export const ReaderStableListPlugin = createPlatePlugin({
  key: READER_LIST_TYPE,
  node: { isElement: true, component: ReaderStableListComponent },
});

export const ReaderStableListItemPlugin = createPlatePlugin({
  key: READER_LIST_ITEM_TYPE,
  node: { isElement: true, component: ReaderStableListItemComponent },
});

export const ReaderStableCodeBlockPlugin = createPlatePlugin({
  key: READER_CODE_BLOCK_TYPE,
  node: { isElement: true, component: ReaderStableCodeBlockComponent },
});

export const ReaderStableMarkdownBlockquotePlugin = createPlatePlugin({
  key: READER_MARKDOWN_BLOCKQUOTE_TYPE,
  node: { isElement: true, component: ReaderStableMarkdownBlockquoteComponent },
});

function ReaderStableSourceCalloutComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const data = (element as unknown as ReaderSourceCalloutElement).data;
  const calloutIcon =
    data?.calloutIcon && isSafeCalloutEmoji(data.calloutIcon)
      ? data.calloutIcon
      : "💡";

  return (
    <aside
      {...attributes}
      role="note"
      className={`reader-record-plate-source-callout flex gap-3 rounded-lg border border-amber-200/60 bg-amber-50/80 px-4 py-3 text-ink not-italic dark:border-amber-400/20 dark:bg-amber-950/30 ${
        attributes?.className ?? ""
      }`.trim()}
      {...readerRecordNavigableNodeAttrs({
        nodeKind: "source_callout",
        unitId: data?.unitId,
        isUnitStart: data?.isUnitStart,
        anchorSegmentId: data?.anchorSegmentId,
      })}
      data-reader-record-block-id={
        (element as unknown as ReaderSourceCalloutElement).id
      }
      data-reader-record-stable-block-type="source_callout"
      data-reader-record-markdown-node="aside"
    >
      <span aria-hidden="true" className="select-none text-base leading-relaxed text-amber-600 dark:text-amber-400">
        {calloutIcon}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </aside>
  );
}

export const ReaderStableSourceCalloutPlugin = createPlatePlugin({
  key: READER_SOURCE_CALLOUT_TYPE,
  node: { isElement: true, component: ReaderStableSourceCalloutComponent },
});

export const ReaderStableTablePlugin = createPlatePlugin({
  key: READER_TABLE_TYPE,
  node: { isElement: true, component: ReaderStableTableComponent },
});

export const ReaderStableTableRowPlugin = createPlatePlugin({
  key: READER_TABLE_ROW_TYPE,
  node: { isElement: true, component: ReaderStableTableRowComponent },
});

export const ReaderStableTableCellPlugin = createPlatePlugin({
  key: READER_TABLE_CELL_TYPE,
  node: { isElement: true, component: ReaderStableTableCellComponent },
});

export const ReaderStableHrPlugin = createPlatePlugin({
  key: READER_HR_TYPE,
  node: { isElement: true, component: ReaderStableHrComponent },
});

// --- Markdown element plugins used by enhancement children ---

function ReaderMarkdownParagraphComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <p
      {...attributes}
      className={`reader-record-plate-markdown-p ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-node="p"
    >
      {children}
    </p>
  );
}

function ReaderMarkdownHeadingComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const type = (element as { type?: string }).type;
  const commonProps = {
    ...attributes,
    "data-reader-record-markdown-node": type,
  };
  const className = (base: string) =>
    `${base} ${attributes?.className ?? ""}`.trim();

  switch (type) {
    case "h1":
      return (
        <h1
          {...commonProps}
          className={className(
            "reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h1",
          )}
        >
          {children}
        </h1>
      );
    case "h2":
      return (
        <h2
          {...commonProps}
          className={className(
            "reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h2",
          )}
        >
          {children}
        </h2>
      );
    case "h3":
      return (
        <h3
          {...commonProps}
          className={className(
            "reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h3",
          )}
        >
          {children}
        </h3>
      );
    case "h4":
      return (
        <h4
          {...commonProps}
          className={className(
            "reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h4",
          )}
        >
          {children}
        </h4>
      );
    case "h5":
      return (
        <h5
          {...commonProps}
          className={className(
            "reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h5",
          )}
        >
          {children}
        </h5>
      );
    case "h6":
    default:
      return (
        <h6
          {...commonProps}
          className={className(
            "reader-record-plate-markdown-heading reader-record-plate-markdown-heading--h6",
          )}
        >
          {children}
        </h6>
      );
  }
}

function ReaderMarkdownBlockquoteComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <blockquote
      {...attributes}
      className={`reader-record-plate-markdown-blockquote text-ink-soft ${
        attributes?.className ?? ""
      }`.trim()}
      data-reader-record-markdown-node="blockquote"
    >
      {children}
    </blockquote>
  );
}

function ReaderMarkdownUnorderedListComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <ul
      {...attributes}
      className={`reader-record-plate-markdown-list list-disc ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-node="ul"
    >
      {children}
    </ul>
  );
}

function ReaderMarkdownOrderedListComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <ol
      {...attributes}
      className={`reader-record-plate-markdown-list list-decimal ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-node="ol"
    >
      {children}
    </ol>
  );
}

function ReaderMarkdownListItemComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <li
      {...attributes}
      className={`${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-node="li"
    >
      {children}
    </li>
  );
}

function ReaderMarkdownListContentComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <span
      {...attributes}
      className={`${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-node="lic"
    >
      {children}
    </span>
  );
}

function ReaderMarkdownCodeBlockComponent({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const type = (element as { type?: string }).type ?? "code_block";
  return (
    <pre
      {...attributes}
      className={`reader-record-plate-markdown-code-block overflow-x-auto ${
        attributes?.className ?? ""
      }`.trim()}
      data-reader-record-markdown-node={type}
    >
      <code>{children}</code>
    </pre>
  );
}

// R2 Phase 1: code_line plugin for deserialized Markdown code blocks.
// MarkdownPlugin.deserialize() emits `code_block` with `code_line` children
// (one per line). Without this plugin, code_line nodes have no component
// and render as plain text, breaking code block structure in callout /
// sentence-analysis children that use `deserializeMarkdownToBlocks`.
// The stable-block path (ReaderStableCodeBlockComponent) does not need
// this — backend stores `text_content` as text nodes, not code_line elements.
//
// R2R Phase 4: HTML 语义修复。
// `code_block` 组件渲染 `<pre><code>{children}</code></pre>`，`code_line`
// 是 `<code>` 的直接子节点。`<code>` 仅接受 phrasing content，`<div>` 是
// flow content，`<pre><code><div>…</div></code></pre>` 无效。改用 `<span>`
// + `block` display 实现逐行换行，DOM 语义有效且不依赖 `<div>`。
function ReaderMarkdownCodeLineComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <span
      {...attributes}
      className={`reader-record-plate-markdown-code-line block ${
        attributes?.className ?? ""
      }`.trim()}
      data-reader-record-markdown-node="code_line"
    >
      {children}
    </span>
  );
}

function ReaderMarkdownHrComponent({
  children,
  attributes,
}: PlateElementProps) {
  return (
    <div
      {...attributes}
      className={`my-2 ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-node="hr"
    >
      <hr className="border-current/15" />
      {children}
    </div>
  );
}

function ReaderMarkdownBoldLeaf({ children, attributes }: PlateLeafProps) {
  return (
    <strong
      {...attributes}
      className={`font-semibold ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-mark="bold"
    >
      {children}
    </strong>
  );
}

function ReaderMarkdownItalicLeaf({ children, attributes }: PlateLeafProps) {
  return (
    <em
      {...attributes}
      className={`italic ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-mark="italic"
    >
      {children}
    </em>
  );
}

function ReaderMarkdownStrikethroughLeaf({
  children,
  attributes,
}: PlateLeafProps) {
  // P1: Use semantic <s> tag (GFM strikethrough) instead of <span> with
  // `line-through` class. Matches the input page (MarkdownStrikethroughLeaf
  // in MarkdownTextInput.tsx) and keeps accessibility/semantics consistent
  // across both rendering paths.
  return (
    <s
      {...attributes}
      className={`line-through ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-mark="strikethrough"
    >
      {children}
    </s>
  );
}

function ReaderMarkdownCodeLeaf({ children, attributes }: PlateLeafProps) {
  return (
    <code
      {...attributes}
      className={`reader-record-plate-inline-code ${
        attributes?.className ?? ""
      }`.trim()}
      data-reader-record-markdown-mark="code"
    >
      {children}
    </code>
  );
}

/**
 * B3: Markdown link leaf.
 *
 * `leaf.link_href` 由 `inlineMarksToPlateProps` 投影而来，后端已对 href
 * 做白名单过滤（仅允许 http / https / mailto），这里直接渲染为 <a>。
 * 缺失 href 时退化为 <span>，避免渲染出空的锚点。
 */
function ReaderMarkdownLinkLeaf({
  children,
  attributes,
  leaf,
}: PlateLeafProps) {
  const plateLeaf = leaf as unknown as { link_href?: string };
  const href =
    typeof plateLeaf?.link_href === "string" && plateLeaf.link_href.length > 0
      ? plateLeaf.link_href
      : undefined;

  if (!href) {
    return (
      <span
        {...attributes}
        data-reader-record-markdown-mark="link"
        data-reader-record-markdown-link-missing-href="true"
      >
        {children}
      </span>
    );
  }

  return (
    <a
      {...attributes}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`reader-record-plate-link ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-mark="link"
    >
      {children}
    </a>
  );
}

/**
 * P1: Markdown link element (deserialized `a` node).
 *
 * When `MarkdownPlugin.deserialize()` (used by callout / sentence-analysis
 * children via `deserializeMarkdownToBlocks`) encounters a Markdown link like
 * `[text](https://example.com)`, it emits a Plate element of type `"a"` with
 * `element.url` carrying the href. The B3 `ReaderMarkdownLinkLeaf` path is a
 * separate rendering: it projects `link` leaves from backend inline marks.
 *
 * This element plugin ensures deserialized Markdown links inside callouts /
 * sentence-analysis blocks render as real `<a>` elements with safe rel attrs.
 * Without it, `a` nodes would render as plain text (no plugin component).
 *
 * `element.url` is already remark-validated (URL shape). The parser-level
 * protocol whitelist is enforced in the backend `markdown_source_parser`
 * (http/https/mailto only), so by the time we reach here the href is safe.
 * Defensive fallback to `#` when `url` is missing — never render an empty href.
 */
function ReaderMarkdownLinkElement({
  children,
  element,
  attributes,
}: PlateElementProps) {
  const url =
    typeof (element as { url?: unknown }).url === "string"
      ? ((element as { url?: unknown }).url as string)
      : "";
  const href = url.length > 0 ? url : "#";
  return (
    <a
      {...attributes}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`reader-record-plate-link ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-mark="link"
      data-reader-record-markdown-link-source="deserialized"
    >
      {children}
    </a>
  );
}

const markdownElementPlugins = [
  createPlatePlugin({
    key: "p",
    node: { isElement: true, component: ReaderMarkdownParagraphComponent },
  }),
  ...(["h1", "h2", "h3", "h4", "h5", "h6"] as const).map((key) =>
    createPlatePlugin({
      key,
      node: { isElement: true, component: ReaderMarkdownHeadingComponent },
    }),
  ),
  createPlatePlugin({
    key: "blockquote",
    node: { isElement: true, component: ReaderMarkdownBlockquoteComponent },
  }),
  createPlatePlugin({
    key: "ul",
    node: { isElement: true, component: ReaderMarkdownUnorderedListComponent },
  }),
  createPlatePlugin({
    key: "ol",
    node: { isElement: true, component: ReaderMarkdownOrderedListComponent },
  }),
  createPlatePlugin({
    key: "li",
    node: { isElement: true, component: ReaderMarkdownListItemComponent },
  }),
  createPlatePlugin({
    key: "lic",
    node: { isElement: true, component: ReaderMarkdownListContentComponent },
  }),
  createPlatePlugin({
    key: "code_block",
    node: { isElement: true, component: ReaderMarkdownCodeBlockComponent },
  }),
  createPlatePlugin({
    key: "pre",
    node: { isElement: true, component: ReaderMarkdownCodeBlockComponent },
  }),
  // R2 Phase 1: code_line element plugin — needed for deserialized code blocks.
  // code_block's children are code_line nodes (one per line). Without this
  // plugin, code_line elements render as plain text, breaking code block
  // structure in callout / sentence-analysis children.
  createPlatePlugin({
    key: "code_line",
    node: { isElement: true, component: ReaderMarkdownCodeLineComponent },
  }),
  createPlatePlugin({
    key: "hr",
    node: { isElement: true, component: ReaderMarkdownHrComponent },
  }),
  // P1: `a` element plugin for deserialized Markdown links. Without this,
  // links inside callout/sentence-analysis children (produced by
  // `deserializeMarkdownToBlocks`) would render as plain text. The B3
  // `link` leaf plugin is separate — it serves the inline-marks projection
  // path. Both coexist without conflict (different node kinds: element vs
  // leaf, different keys: "a" vs "link").
  // NOTE: do NOT set `options.mode` here. Plate's LinkPlugin mode contract
  // is "" | "edit" | "insert"; a static "inline" value is illegal and
  // previously made the shared FloatingToolbar's link-open check (`!!mode`)
  // permanently hide the toolbar. The link-open check now tests for
  // "edit"/"insert" explicitly, and `usePluginOption` returning undefined
  // is handled gracefully (treated as closed).
  createPlatePlugin({
    key: "a",
    node: { isElement: true, component: ReaderMarkdownLinkElement },
  }),
];

const markdownLeafPlugins = [
  createPlatePlugin({
    key: "bold",
    node: { isLeaf: true, component: ReaderMarkdownBoldLeaf },
  }),
  createPlatePlugin({
    key: "italic",
    node: { isLeaf: true, component: ReaderMarkdownItalicLeaf },
  }),
  createPlatePlugin({
    key: "strikethrough",
    node: { isLeaf: true, component: ReaderMarkdownStrikethroughLeaf },
  }),
  createPlatePlugin({
    key: "code",
    node: { isLeaf: true, component: ReaderMarkdownCodeLeaf },
  }),
  // B3: link leaf. Plate's link plugin key is "link"; leaf propagates
  // `link_href` from the projected text node so the renderer can emit
  // a real <a> with a whitelisted href.
  createPlatePlugin({
    key: "link",
    node: { isLeaf: true, component: ReaderMarkdownLinkLeaf },
  }),
];

// --- Kit aggregation ---

export const ReaderBlocksKit = [
  ReaderParagraphPlugin,
  ReaderBlockquotePlugin,
  ReaderCalloutGroupPlugin,
  ReaderCalloutPlugin,
  ReaderSentenceAnalysisPlugin,
  ReaderSentenceAnalysisChunksPlugin,
  ReaderSentenceAnalysisChunkPlugin,
  // B2.5: Stable-block-derived plugins for Markdown rendering.
  ReaderStableHeadingPlugin,
  ReaderStableListPlugin,
  ReaderStableListItemPlugin,
  ReaderStableCodeBlockPlugin,
  ReaderStableMarkdownBlockquotePlugin,
  ReaderStableSourceCalloutPlugin,
  ReaderImagePlugin,
  ReaderImageBlockPlugin,
  ReaderMathInlinePlugin,
  ReaderMathBlockPlugin,
  // source_callout（非 stable-block 路径）：当 enhancement children markdown
  // 反序列化遇到 `<aside>` 或 GFM alert 时，SOURCE_CALLOUT_RULES 产出
  // `{type:"source_callout"}` element。此处注册 SourceCalloutPlugin 让该
  // type 有 component 可渲染，避免退化为默认渲染丢失 callout 视觉/语义。
  // 与 ReaderStableSourceCalloutPlugin（key=reader_source_callout）并存：
  // 后者服务 stable block projection，前者服务 markdown 直接反序列化。
  SourceCalloutPlugin,
  ReaderStableTablePlugin,
  ReaderStableTableRowPlugin,
  ReaderStableTableCellPlugin,
  ReaderStableHrPlugin,
  ...markdownElementPlugins,
  ...markdownLeafPlugins,
];
