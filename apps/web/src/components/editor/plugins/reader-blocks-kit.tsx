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
      className={`reader-record-plate-paragraph my-2.5 text-pretty leading-[1.88] text-ink ${attributes?.className ?? ""}`.trim()}
      data-reader-record-node="paragraph"
      data-reader-record-block-id={(element as unknown as ReaderParagraphElement).id}
      data-anchor-segment-id={data?.anchorSegmentId}
      data-sentence-id={data?.sentenceId}
      data-unit-id={data?.unitId}
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
      className={`reader-record-plate-blockquote reader-record-plate-translation reader-record-plate-translation-lane my-1.5 border-l-2 border-border/70 bg-transparent py-0.5 pl-3 pr-1 font-sans text-[0.84rem] leading-6 text-muted/85 ${attributes?.className ?? ""}`.trim()}
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

  const isGrammar = variant === "grammar";
  const isSupplement = variant === "supplement";
  const containerClass = [
    "reader-record-plate-callout my-2.5 bg-transparent py-1 pl-3 pr-2 text-sm leading-6 text-ink-soft",
    isGrammar
      ? "reader-record-plate-callout--grammar border-l-2 border-emerald-300/75"
      : "",
    isSupplement
      ? "reader-record-plate-callout--supplement border-l-2 border-amber-300/75"
      : "",
    attributes?.className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  const eyebrowClass = isGrammar
    ? "text-emerald-700/75"
    : "text-amber-700/75";
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
    >
      <div className="min-w-0">
        <div className="mb-1 flex min-w-0 items-baseline gap-2">
          <span
            className={`font-mono text-[0.68rem] font-medium leading-none ${eyebrowClass}`}
            aria-hidden="true"
          >
            {icon}
          </span>
          {title ? (
            <span className="reader-serif min-w-0 text-[0.93rem] font-semibold leading-snug text-ink/90">
              {title}
            </span>
          ) : null}
        </div>
        <div
          className="reader-record-plate-markdown mt-1"
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
      className={`reader-record-plate-sentence-analysis reader-record-plate-callout--analysis my-3 border-l-2 border-sky-300/75 bg-transparent py-1 pl-3 pr-2 text-sm leading-6 text-ink-soft ${attributes?.className ?? ""}`.trim()}
      role="note"
      data-reader-record-node="sentence-analysis"
      data-reader-record-sentence-analysis-block="true"
      data-reader-record-sentence-analysis-element={READER_SENTENCE_ANALYSIS_TYPE}
      data-reader-record-block-id={node.id}
      data-anchor-segment-id={data?.anchorSegmentId}
      data-unit-id={data?.unitId}
      data-layer-id={data?.layerId}
      data-analysis-id={data?.analysisId}
    >
      <div className="min-w-0">
        <div className="mb-1 flex min-w-0 items-baseline gap-2">
          <span
            className="font-mono text-[0.68rem] font-medium leading-none text-sky-700/75"
            aria-hidden="true"
          >
            {node.icon}
          </span>
          {data?.label ? (
            <span className="reader-serif min-w-0 text-[0.93rem] font-semibold leading-snug text-ink/90">
              {data.label}
            </span>
          ) : null}
        </div>
        <div
          className={classNames(
            "reader-record-plate-markdown",
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
      className={`mb-2 mt-2 space-y-1.5 ${attributes?.className ?? ""}`.trim()}
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

  return (
    <div
      {...attributes}
      className={`grid grid-cols-[minmax(5.5rem,auto)_minmax(0,1fr)] gap-x-3 text-[0.82rem] leading-6 ${attributes?.className ?? ""}`.trim()}
      data-reader-record-sentence-analysis-chunk={chunk.label}
      data-reader-record-sentence-analysis-chunk-order={chunk.order}
    >
      <dt className="min-w-0 truncate font-medium text-sky-800/75">
        {chunk.label}
      </dt>
      <dd className="min-w-0 text-muted/90">{children}</dd>
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
      className={`my-1 leading-6 ${attributes?.className ?? ""}`.trim()}
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
        <h1 {...commonProps} className={className("my-2 text-lg font-semibold leading-snug")}>
          {children}
        </h1>
      );
    case "h2":
      return (
        <h2 {...commonProps} className={className("my-2 text-base font-semibold leading-snug")}>
          {children}
        </h2>
      );
    case "h3":
      return (
        <h3 {...commonProps} className={className("my-2 text-[0.95rem] font-semibold leading-snug")}>
          {children}
        </h3>
      );
    case "h4":
      return (
        <h4 {...commonProps} className={className("my-2 text-[0.9rem] font-semibold leading-snug")}>
          {children}
        </h4>
      );
    case "h5":
      return (
        <h5 {...commonProps} className={className("my-2 text-[0.9rem] font-semibold leading-snug")}>
          {children}
        </h5>
      );
    case "h6":
    default:
      return (
        <h6 {...commonProps} className={className("my-2 text-[0.9rem] font-semibold leading-snug")}>
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
      className={`my-1 border-l-2 border-current/30 pl-3 italic text-ink-soft ${attributes?.className ?? ""}`.trim()}
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
      className={`my-1 list-disc pl-5 leading-6 ${attributes?.className ?? ""}`.trim()}
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
      className={`my-1 list-decimal pl-5 leading-6 ${attributes?.className ?? ""}`.trim()}
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
      className={`my-1 overflow-x-auto rounded bg-muted/40 p-2 text-xs leading-5 ${attributes?.className ?? ""}`.trim()}
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
      className={`rounded bg-muted/50 px-1 py-0.5 font-mono text-[0.85em] ${attributes?.className ?? ""}`.trim()}
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
