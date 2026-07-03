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
  setActiveGrammarItemId: (itemId: string | null) => void;
  pulseGrammarItemId: (itemId: string) => void;
}

export const ReaderGrammarInteractionContext =
  createContext<ReaderGrammarInteractionValue>({
    activeGrammarItemId: null,
    setActiveGrammarItemId: () => {},
    pulseGrammarItemId: () => {},
  });

function useReaderGrammarInteraction() {
  return useContext(ReaderGrammarInteractionContext);
}

function sentenceChunkDomId(chunk: {
  order: number;
  label: string;
  sourceMatch?: { markId: string };
}): string {
  return chunk.sourceMatch?.markId ?? `unmatched:${chunk.order}:${chunk.label}`;
}

function sentenceChunkDescription(label: string): string {
  const normalized = label.trim().toLowerCase();
  if (normalized.includes("subject") || normalized.includes("主语")) {
    return "主语 / 话题核心";
  }
  if (normalized.includes("predicate") || normalized.includes("verb") || normalized.includes("谓语")) {
    return "谓语 / 动作核心";
  }
  if (normalized.includes("object") || normalized.includes("宾语")) {
    return "宾语 / 承接对象";
  }
  if (normalized.includes("modifier") || normalized.includes("修饰")) {
    return "修饰 / 补充限定";
  }
  if (normalized.includes("clause") || normalized.includes("从句")) {
    return "从句 / 信息层";
  }
  if (normalized.includes("condition") || normalized.includes("条件")) {
    return "条件 / 前提信息";
  }
  if (normalized.includes("reason") || normalized.includes("cause") || normalized.includes("原因")) {
    return "原因 / 解释关系";
  }
  return "结构片段";
}

function calloutTypeLabel(variant: ReaderCalloutElement["variant"]): string {
  return variant === "grammar" ? "语法解析" : "补充说明";
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
    setActiveGrammarItemId,
    pulseGrammarItemId,
  } = useReaderGrammarInteraction();

  const isGrammar = variant === "grammar";
  const isSupplement = variant === "supplement";
  const grammarItemId = isGrammar ? data?.itemId : undefined;
  const grammarActive =
    isGrammar && grammarItemId ? activeGrammarItemId === grammarItemId : false;
  const label = calloutTypeLabel(variant);
  const containerClass = [
    "reader-record-plate-callout rounded-[8px] border font-sans text-ink-soft shadow-none",
    isGrammar
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
        <div className="mb-1.5 flex min-w-0 items-start gap-2">
          <span
            className={`reader-record-plate-note-icon mt-0.5 leading-none ${eyebrowClass}`}
            aria-hidden="true"
          >
            {icon}
          </span>
          <div className="min-w-0">
            <div className={`reader-record-plate-label ${eyebrowClass}`}>
              {label}
            </div>
            {title ? (
              <div className="reader-record-plate-note-title mt-0.5 min-w-0 text-ink/90">
                {title}
              </div>
            ) : null}
          </div>
        </div>
        <div
          className="reader-record-plate-markdown reader-record-plate-note-prose mt-1.5 text-ink-soft"
          data-reader-record-markdown-content="plate"
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
    >
      <div className="min-w-0">
        <div className="mb-1.5 flex min-w-0 items-start gap-2">
          <span
            className="reader-record-plate-note-icon mt-0.5 leading-none text-context-blue/85"
            aria-hidden="true"
          >
            {node.icon}
          </span>
          <div className="min-w-0">
            <div className="reader-record-plate-label text-context-blue/85">
              长句拆析
            </div>
            {data?.label ? (
              <div className="reader-record-plate-note-title mt-0.5 min-w-0 text-ink/88">
                {data.label}
              </div>
            ) : null}
          </div>
        </div>
        <div
          className={classNames(
            "reader-record-plate-markdown reader-record-plate-note-prose",
            data?.chunks?.length ? "mt-2" : "mt-1",
          )}
          data-reader-record-markdown-content="plate"
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
      tabIndex={hasSourceMatch ? 0 : undefined}
      onMouseEnter={() => {
        if (hasSourceMatch) setActiveChunkId(chunkId);
      }}
      onMouseLeave={() => {
        if (hasSourceMatch) setActiveChunkId(null);
      }}
      onFocus={() => {
        if (hasSourceMatch) setActiveChunkId(chunkId);
      }}
      onBlur={() => {
        if (hasSourceMatch) setActiveChunkId(null);
      }}
      onPointerDown={() => {
        if (hasSourceMatch) setActiveChunkId(chunkId);
      }}
      onClick={() => {
        if (hasSourceMatch) setActiveChunkId(chunkId);
      }}
    >
      <dt className="min-w-0">
        <span className="reader-record-plate-sentence-analysis-chunk-label block truncate text-context-blue/90">
          {chunk.label}
        </span>
        <span className="reader-record-plate-sentence-analysis-chunk-kind mt-0.5 block truncate font-sans text-muted/75">
          {sentenceChunkDescription(chunk.label)}
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
  ReaderCalloutPlugin,
  ReaderSentenceAnalysisPlugin,
  ReaderSentenceAnalysisChunksPlugin,
  ReaderSentenceAnalysisChunkPlugin,
  ...markdownElementPlugins,
  ...markdownLeafPlugins,
];
