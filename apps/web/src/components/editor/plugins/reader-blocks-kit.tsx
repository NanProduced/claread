/**
 * Reader Blocks Kit — 注册 Reader Plate 自定义 element plugin
 *
 * 三个 element plugin 对应 ReaderRecordPlateDocument 的三种 block：
 * - ReaderParagraphPlugin  (type: "reader_paragraph")  — 原文段落
 * - ReaderBlockquotePlugin (type: "reader_blockquote")   — 译文引用块
 * - ReaderCalloutPlugin    (type: "reader_callout")     — 增强 callout
 *
 * 渲染逻辑参考 ReaderRecordPlateSurface.tsx 中的 ParagraphBlock / BlockquoteBlock / CalloutBlock。
 * 使用 Plate attributes（ref / data-slate-node 等）保证 Plate 选区和渲染正常工作。
 */
import * as React from "react";
import { createPlatePlugin, type PlateElementProps } from "platejs/react";

import { CalloutMarkdownRenderer } from "@/components/reader/plate/CalloutMarkdownRenderer";
import type {
  ReaderBlockquoteElement,
  ReaderCalloutElement,
  ReaderParagraphElement,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import {
  READER_BLOCKQUOTE_TYPE,
  READER_CALLOUT_TYPE,
  READER_PARAGRAPH_TYPE,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";

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
      className={`reader-record-plate-paragraph ${attributes?.className ?? ""}`.trim()}
      data-reader-record-node="paragraph"
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
      className={`reader-record-plate-blockquote mt-3 border-l-2 border-emerald-300/60 bg-emerald-50/40 py-2 pl-4 pr-3 font-sans text-[0.95rem] leading-7 text-ink-soft ${attributes?.className ?? ""}`.trim()}
      data-reader-record-node="blockquote"
      data-unit-id={data?.unitId}
    >
      <span className="mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-emerald-700/80">
        译文
      </span>
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
  element,
  attributes,
}: PlateElementProps) {
  const node = element as unknown as ReaderCalloutElement;
  const data = node.data;
  const variant = node.variant;
  const icon = node.icon;

  const isGrammar = variant === "grammar";
  const isSupplement = variant === "supplement";
  const containerClass = isGrammar
    ? "reader-record-plate-callout reader-record-plate-callout--grammar mt-3 rounded-md border border-emerald-200/70 bg-emerald-50/60 px-4 py-3 text-sm leading-6 text-ink-soft"
    : isSupplement
      ? "reader-record-plate-callout reader-record-plate-callout--supplement mt-3 rounded-md border border-amber-200/70 bg-amber-50/60 px-4 py-3 text-sm leading-6 text-ink-soft"
      : "reader-record-plate-callout reader-record-plate-callout--analysis mt-3 rounded-md border border-sky-200/70 bg-sky-50/60 px-4 py-3 text-sm leading-6 text-ink-soft";
  const labelClass = isGrammar
    ? "mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-emerald-700/80"
    : isSupplement
      ? "mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-amber-700/80"
      : "mb-1 block text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-sky-700/80";
  const label = isGrammar
    ? "语法讲解"
    : isSupplement
      ? "AI 补充"
      : "句子结构";
  const title = isGrammar
    ? data?.grammarPoint ?? ""
    : isSupplement
      ? data?.supplementTitle ?? ""
      : data?.label ?? "";

  return (
    <div
      {...attributes}
      className={containerClass}
      data-reader-record-node="callout"
      data-callout-variant={variant}
      data-anchor-segment-id={data?.anchorSegmentId}
    >
      <div className="flex items-start gap-2">
        <span className="text-base leading-none" aria-hidden="true">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <span className={labelClass}>{label}</span>
          {title ? (
            <span className="reader-serif block text-[0.95rem] font-semibold leading-snug text-ink">
              {title}
            </span>
          ) : null}
          <div className="mt-1">
            <CalloutMarkdownRenderer nodes={node.children} />
          </div>
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

// --- Kit aggregation ---

export const ReaderBlocksKit = [
  ReaderParagraphPlugin,
  ReaderBlockquotePlugin,
  ReaderCalloutPlugin,
];
