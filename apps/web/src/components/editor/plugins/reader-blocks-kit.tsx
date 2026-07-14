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
  ChevronDown,
  ChevronUp,
  Flag,
  MessageCircleQuestion,
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
  ReaderParagraphElement,
  ReaderSentenceAnalysisChunkElement,
  ReaderSentenceAnalysisElement,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import {
  READER_BLOCKQUOTE_TYPE,
  READER_CALLOUT_TYPE,
  READER_PARAGRAPH_TYPE,
  READER_SENTENCE_ANALYSIS_CHUNK_TYPE,
  READER_SENTENCE_ANALYSIS_CHUNKS_TYPE,
  READER_SENTENCE_ANALYSIS_TYPE,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";

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
  pulseGrammarItemId: (itemId: string) => void;
  requestExpandGrammarItem: (itemId: string) => void;
}

export const ReaderGrammarInteractionContext =
  createContext<ReaderGrammarInteractionValue>({
    activeGrammarItemId: null,
    expandGrammarItemRequest: null,
    setActiveGrammarItemId: () => {},
    pulseGrammarItemId: () => {},
    requestExpandGrammarItem: () => {},
  });

function useReaderGrammarInteraction() {
  return useContext(ReaderGrammarInteractionContext);
}

// ---------------------------------------------------------------------------
// T4.2a-PUX-R4-R2.1C: Grammar expansion state keyed by stable itemId.
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
}

export type ReaderGrammarExpansionControlRef = {
  current: ReaderGrammarExpansionControl | null;
};

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
    controlRef.current = { clear: clearExpanded, forgetItem };
    return () => {
      controlRef.current = null;
    };
  }, [clearExpanded, forgetItem, controlRef]);

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
  return variant === "grammar" ? "语法解析" : "补充说明";
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
      data-reader-record-node="paragraph"
      data-reader-record-block-id={(element as unknown as ReaderParagraphElement).id}
      data-anchor-segment-id={data?.anchorSegmentId}
      data-sentence-id={data?.sentenceId}
      data-unit-id={data?.unitId}
      data-reader-record-unit-start={data?.isUnitStart ? "true" : undefined}
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
      data-reader-record-node="blockquote"
      data-reader-record-translation-lane="true"
      data-reader-record-block-id={(element as unknown as ReaderBlockquoteElement).id}
      data-unit-id={data?.unitId}
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
      aria-label={`语法解析 · ${calloutCount} 条`}
      data-reader-record-node="callout-group"
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
          <WandSparkles size={15} strokeWidth={1.8} />
        </span>
        <span className="reader-record-plate-callout-group-label">
          语法解析 · {calloutCount} 条
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
  const icon = node.icon;
  const {
    activeGrammarItemId,
    expandGrammarItemRequest,
    setActiveGrammarItemId,
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
  const containerClass = [
    "reader-record-plate-callout rounded-[8px] border font-sans text-ink-soft shadow-none",
    isGroupedGrammar
      ? "reader-record-plate-callout--grammar-row"
      : isGrammar
      ? "reader-record-plate-callout--grammar border-grammar-violet/18 bg-ink/[0.035]"
      : "",
    grammarActive ? "reader-record-plate-callout--grammar-active" : "",
    isSupplement
      ? "reader-record-plate-callout--supplement border-vocab-amber/18 bg-vocab-amber/[0.045]"
      : "",
    attributes?.className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  const eyebrowClass = isGrammar
    ? "text-grammar-violet/80"
    : "text-vocab-amber/90";
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
    } else if (isStandaloneGrammar) {
      // T4.2a-PUX-R4-R2.1C: lift expand state into itemId-keyed context so
      // it survives targeted replaceNodes on the same callout element.
      expansionContext.expandItem(grammarItemId);
    } else {
      setLocalExpanded(true);
    }
  }, [
    expandGrammarItemRequest?.itemId,
    expandGrammarItemRequest?.requestId,
    expansionContext,
    grammarItemId,
    groupContext,
    isGroupedGrammar,
    isStandaloneGrammar,
    isGrammar,
  ]);

  return (
    <div
      {...attributes}
      className={containerClass}
      role="note"
      data-reader-record-node="callout"
      data-reader-record-callout="true"
      data-callout-variant={variant}
      data-reader-record-block-id={node.id}
      data-anchor-segment-id={data?.anchorSegmentId}
      data-unit-id={data?.unitId}
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
          setActiveGrammarItemId(grammarItemId);
        }
      }}
      onMouseLeave={() => {
        if (grammarItemId) {
          setActiveGrammarItemId(null);
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
            {icon}
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
                {expanded ? (
                  <ChevronUp aria-hidden="true" size={15} strokeWidth={1.9} />
                ) : (
                  <ChevronDown aria-hidden="true" size={15} strokeWidth={1.9} />
                )}
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
  const [expanded, setExpanded] = React.useState(false);
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
      className={`reader-record-plate-sentence-analysis reader-record-plate-callout--analysis rounded-[8px] border border-context-blue/20 bg-context-blue/[0.04] font-sans text-ink-soft shadow-none ${
        attributes?.className ?? ""
      }`.trim()}
      role="note"
      data-reader-record-node="sentence-analysis"
      data-reader-record-sentence-analysis-block="true"
      data-reader-record-sentence-analysis-element={READER_SENTENCE_ANALYSIS_TYPE}
      data-reader-record-sentence-analysis-label="长句拆析"
      data-reader-record-block-id={node.id}
      data-anchor-segment-id={data?.anchorSegmentId}
      data-unit-id={data?.unitId}
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
                {node.icon}
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
                setExpanded((current) => !current);
              }}
            >
              {expanded ? (
                <ChevronUp aria-hidden="true" size={15} strokeWidth={1.9} />
              ) : (
                <ChevronDown aria-hidden="true" size={15} strokeWidth={1.9} />
              )}
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
      className={`reader-record-plate-markdown-blockquote border-l-2 border-current/30 italic text-ink-soft ${
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
      className={`reader-record-plate-markdown-code-block overflow-x-auto rounded bg-muted/40 ${
        attributes?.className ?? ""
      }`.trim()}
      data-reader-record-markdown-node={type}
    >
      <code>{children}</code>
    </pre>
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
  return (
    <span
      {...attributes}
      className={`line-through ${attributes?.className ?? ""}`.trim()}
      data-reader-record-markdown-mark="strikethrough"
    >
      {children}
    </span>
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
  createPlatePlugin({
    key: "hr",
    node: { isElement: true, component: ReaderMarkdownHrComponent },
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
  ...markdownElementPlugins,
  ...markdownLeafPlugins,
];
