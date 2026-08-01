/**
 * Reader Record Plate → Plate Value projection
 *
 * 把 ReaderRecordPlateDocument（V2 标准 Plate blocks）转换为 Plate editor
 * 可直接消费的 Descendant[]。每种 block 映射为自定义 element type：
 * - paragraph  → reader_paragraph
 * - blockquote → reader_blockquote (译文引用块)
 * - callout    → reader_callout
 * - sentence_analysis → reader_sentence_analysis
 *
 * B2: Markdown stable-block-derived blocks 也映射为 reader_* element types：
 * - heading             → reader_heading (carries level)
 * - list                → reader_list (carries ordered)
 * - list_item           → reader_list_item
 * - code_block          → reader_code_block (carries language)
 * - markdown_blockquote → reader_markdown_blockquote
 * - table               → reader_table
 * - table_row           → reader_table_row
 * - table_cell          → reader_table_cell (carries columnIndex/alignment/isHeader)
 * - hr                  → reader_hr
 *
 * text leaf 的 marks 转换为 text node 属性，供 leaf plugin 渲染。
 * vocabulary / grammar continuation leaf 也携带 mark data，保证跨 leaf 标注完整渲染。
 */
import type { Descendant } from "platejs";

import type {
  ReaderRecordPlateBlock,
  ReaderRecordPlateBlockquoteBlock,
  ReaderRecordPlateCalloutBlock,
  ReaderRecordPlateCodeBlockBlock,
  ReaderRecordPlateDocument,
  ReaderRecordPlateGrammarMark,
  ReaderRecordPlateHeadingBlock,
  ReaderRecordPlateHrBlock,
  ReaderRecordPlateInlineMark,
  ReaderRecordPlateListBlock,
  ReaderRecordPlateListItemBlock,
  ReaderRecordPlateMarkdownBlockquoteBlock,
  ReaderRecordPlateMark,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateSentenceChunkMark,
  ReaderRecordPlateSentenceAnalysisBlock,
  ReaderRecordPlateSourceCalloutBlock,
  ReaderRecordPlateTableBlock,
  ReaderRecordPlateTableCellBlock,
  ReaderRecordPlateTableRowBlock,
  ReaderRecordPlateTextLeaf,
  ReaderRecordPlateTranslationTextLeaf,
  ReaderRecordPlateUserHighlightMark,
  ReaderRecordPlateUserNoteMark,
  ReaderRecordPlateVocabularyMark,
} from "./reader-record-plate-document";

// --- Block type constants ---

export const READER_PARAGRAPH_TYPE = "reader_paragraph" as const;
export const READER_BLOCKQUOTE_TYPE = "reader_blockquote" as const;
export const READER_CALLOUT_TYPE = "reader_callout" as const;
export const READER_SENTENCE_ANALYSIS_TYPE = "reader_sentence_analysis" as const;
export const READER_SENTENCE_ANALYSIS_CHUNKS_TYPE =
  "reader_sentence_analysis_chunks" as const;
export const READER_SENTENCE_ANALYSIS_CHUNK_TYPE =
  "reader_sentence_analysis_chunk" as const;

// B2: Markdown stable-block-derived element types.
// Prefixed with `reader_` to avoid colliding with Plate's built-in Markdown
// plugin node types (`heading`, `ul`, `ol`, `li`, `code_block`, `blockquote`,
// `table`, `tr`, `td`/`th`, `hr`). Reader-owned elements let us attach
// anchor-segment / stable-block metadata without polluting the standard
// Markdown plugin contract.
export const READER_HEADING_TYPE = "reader_heading" as const;
export const READER_LIST_TYPE = "reader_list" as const;
export const READER_LIST_ITEM_TYPE = "reader_list_item" as const;
export const READER_CODE_BLOCK_TYPE = "reader_code_block" as const;
export const READER_MARKDOWN_BLOCKQUOTE_TYPE = "reader_markdown_blockquote" as const;
export const READER_TABLE_TYPE = "reader_table" as const;
export const READER_TABLE_ROW_TYPE = "reader_table_row" as const;
export const READER_TABLE_CELL_TYPE = "reader_table_cell" as const;
export const READER_HR_TYPE = "reader_hr" as const;
export const READER_SOURCE_CALLOUT_TYPE = "reader_source_callout" as const;

// --- Mark key constants ---

export const READER_VOCABULARY_MARK_KEY = "vocabulary" as const;
export const READER_GRAMMAR_MARK_KEY = "grammar" as const;
export const READER_USER_HIGHLIGHT_MARK_KEY = "user_highlight" as const;
export const READER_USER_NOTE_MARK_KEY = "user_note" as const;
export const READER_SENTENCE_CHUNK_MARK_KEY = "sentence_chunk" as const;

// --- Element node types ---

export interface ReaderParagraphElement {
  type: typeof READER_PARAGRAPH_TYPE;
  id: ReaderRecordPlateParagraphBlock["id"];
  children: PlateTextNode[];
  data: ReaderRecordPlateParagraphBlock["data"];
}

export interface ReaderBlockquoteElement {
  type: typeof READER_BLOCKQUOTE_TYPE;
  id: ReaderRecordPlateBlockquoteBlock["id"];
  children: PlateTextNode[];
  data: ReaderRecordPlateBlockquoteBlock["data"];
}

export interface ReaderCalloutElement {
  type: typeof READER_CALLOUT_TYPE;
  id: ReaderRecordPlateCalloutBlock["id"];
  children: Descendant[];
  data: ReaderRecordPlateCalloutBlock["data"];
  variant: ReaderRecordPlateCalloutBlock["variant"];
  icon: string;
}

export interface ReaderSentenceAnalysisElement {
  type: typeof READER_SENTENCE_ANALYSIS_TYPE;
  id: ReaderRecordPlateSentenceAnalysisBlock["id"];
  children: Descendant[];
  data: ReaderRecordPlateSentenceAnalysisBlock["data"];
  icon: string;
}

export interface ReaderSentenceAnalysisChunksElement {
  type: typeof READER_SENTENCE_ANALYSIS_CHUNKS_TYPE;
  children: ReaderSentenceAnalysisChunkElement[];
}

export interface ReaderSentenceAnalysisChunkElement {
  type: typeof READER_SENTENCE_ANALYSIS_CHUNK_TYPE;
  children: PlateTextNode[];
  data: ReaderRecordPlateSentenceAnalysisBlock["data"]["chunks"][number];
}

// B2: Markdown stable-block-derived element types.

export interface ReaderHeadingElement {
  type: typeof READER_HEADING_TYPE;
  id: ReaderRecordPlateHeadingBlock["id"];
  /** 1-based heading level (clamped to 1-6). */
  level: ReaderRecordPlateHeadingBlock["level"];
  children: PlateTextNode[];
  data: ReaderRecordPlateHeadingBlock["data"];
}

export interface ReaderListElement {
  type: typeof READER_LIST_TYPE;
  id: ReaderRecordPlateListBlock["id"];
  /** True for ordered lists, false for bullet lists. */
  ordered: ReaderRecordPlateListBlock["ordered"];
  children: ReaderListItemElement[];
  data: ReaderRecordPlateListBlock["data"];
}

export interface ReaderListItemElement {
  type: typeof READER_LIST_ITEM_TYPE;
  id: ReaderRecordPlateListItemBlock["id"];
  children: Array<PlateTextNode | ReaderListElement>;
  data: ReaderRecordPlateListItemBlock["data"];
}

export interface ReaderCodeBlockElement {
  type: typeof READER_CODE_BLOCK_TYPE;
  id: ReaderRecordPlateCodeBlockBlock["id"];
  children: PlateTextNode[];
  data: ReaderRecordPlateCodeBlockBlock["data"];
}

export interface ReaderMarkdownBlockquoteElement {
  type: typeof READER_MARKDOWN_BLOCKQUOTE_TYPE;
  id: ReaderRecordPlateMarkdownBlockquoteBlock["id"];
  children: PlateTextNode[];
  data: ReaderRecordPlateMarkdownBlockquoteBlock["data"];
}

export interface ReaderTableElement {
  type: typeof READER_TABLE_TYPE;
  id: ReaderRecordPlateTableBlock["id"];
  children: ReaderTableRowElement[];
  data: ReaderRecordPlateTableBlock["data"];
}

export interface ReaderTableRowElement {
  type: typeof READER_TABLE_ROW_TYPE;
  id: ReaderRecordPlateTableRowBlock["id"];
  children: ReaderTableCellElement[];
  data: ReaderRecordPlateTableRowBlock["data"];
}

export interface ReaderTableCellElement {
  type: typeof READER_TABLE_CELL_TYPE;
  id: ReaderRecordPlateTableCellBlock["id"];
  children: PlateTextNode[];
  data: ReaderRecordPlateTableCellBlock["data"];
}

export interface ReaderHrElement {
  type: typeof READER_HR_TYPE;
  id: ReaderRecordPlateHrBlock["id"];
  children: [];
  data: ReaderRecordPlateHrBlock["data"];
}

export interface ReaderSourceCalloutElement {
  type: typeof READER_SOURCE_CALLOUT_TYPE;
  id: ReaderRecordPlateSourceCalloutBlock["id"];
  children: Array<PlateTextNode | ReaderPlateElement>;
  data: ReaderRecordPlateSourceCalloutBlock["data"];
}

export type ReaderPlateElement =
  | ReaderParagraphElement
  | ReaderBlockquoteElement
  | ReaderCalloutElement
  | ReaderSentenceAnalysisElement
  | ReaderSentenceAnalysisChunksElement
  | ReaderSentenceAnalysisChunkElement
  // B2: Markdown stable-block-derived elements.
  | ReaderHeadingElement
  | ReaderListElement
  | ReaderListItemElement
  | ReaderCodeBlockElement
  | ReaderMarkdownBlockquoteElement
  | ReaderTableElement
  | ReaderTableRowElement
  | ReaderTableCellElement
  | ReaderHrElement
  | ReaderSourceCalloutElement;

// --- Text node type ---

export interface PlateTextNode {
  text: string;
  vocabulary?: boolean;
  vocabulary_data?: ReaderRecordPlateVocabularyMark;
  grammar?: boolean;
  grammar_data?: ReaderRecordPlateGrammarMark;
  sentence_chunk?: boolean;
  sentence_chunk_data?: ReaderRecordPlateSentenceChunkMark;
  user_highlight?: boolean;
  user_highlight_data?: ReaderRecordPlateUserHighlightMark;
  user_note?: boolean;
  user_note_data?: ReaderRecordPlateUserNoteMark | ReaderRecordPlateUserNoteMark[];
  translation_owner?: string;
  translation_sourceRole?: string;
  /** 段落文本 leaf 所属的 anchor segment id（用于选区锚点定位） */
  anchor_segment_id?: string;
  /** 该 leaf 在 anchor segment 内的 UTF-16 起始偏移 */
  segment_start_utf16?: number;
  /** 该 leaf 在 anchor segment 内的 UTF-16 结束偏移 */
  segment_end_utf16?: number;
  /**
   * B3: Inline marks from Markdown parser. Plate leaf plugins with the
   * matching key (`bold` / `italic` / `strikethrough` / `code` / `link`)
   * render these as `<strong>` / `<em>` / `<s>` / `<code>` / `<a>`.
   */
  bold?: boolean;
  italic?: boolean;
  strikethrough?: boolean;
  code?: boolean;
  link?: boolean;
  /** Safe href for `link === true` (whitelist-filtered by parser). */
  link_href?: string;
}

// --- Mark → Plate text node props ---

function isVocabularyMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateVocabularyMark {
  return (
    mark.kind === "vocab_highlight" ||
    mark.kind === "phrase_gloss" ||
    mark.kind === "context_gloss"
  );
}

function isGrammarMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateGrammarMark {
  return mark.kind === "grammar_note";
}

function isUserHighlightMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateUserHighlightMark {
  return mark.kind === "user_highlight";
}

function isSentenceChunkMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateSentenceChunkMark {
  return mark.kind === "sentence_analysis_chunk";
}

function isUserNoteMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateUserNoteMark {
  return mark.kind === "user_note";
}

/**
 * 把 ReaderRecordPlateMark[] 转换为 Plate text node 属性。
 *
 * 每个 mark 设置对应的 boolean flag（`vocabulary: true` 等），
 * vocabulary / grammar mark 会被 overlapping marks 切成多个 leaf；
 * continuation leaf 也必须携带 data，leaf plugin 才能恢复完整视觉样式。
 * 交互能力由 leaf plugin 按 startsHere 决定，不在 projection 阶段丢弃 data。
 */
export function marksToPlateProps(
  marks: ReaderRecordPlateMark[],
): Partial<Pick<PlateTextNode, "vocabulary" | "vocabulary_data" | "grammar" | "grammar_data" | "sentence_chunk" | "sentence_chunk_data" | "user_highlight" | "user_highlight_data" | "user_note" | "user_note_data">> {
  const props: Record<string, unknown> = {};
  const noteMarks: ReaderRecordPlateUserNoteMark[] = [];

  for (const mark of marks) {
    if (isVocabularyMark(mark)) {
      props[READER_VOCABULARY_MARK_KEY] = true;
      props[`${READER_VOCABULARY_MARK_KEY}_data`] = mark;
      continue;
    }
    if (isGrammarMark(mark)) {
      props[READER_GRAMMAR_MARK_KEY] = true;
      props[`${READER_GRAMMAR_MARK_KEY}_data`] = mark;
      continue;
    }
    if (isSentenceChunkMark(mark)) {
      props[READER_SENTENCE_CHUNK_MARK_KEY] = true;
      props[`${READER_SENTENCE_CHUNK_MARK_KEY}_data`] = mark;
      continue;
    }
    if (isUserHighlightMark(mark)) {
      props[READER_USER_HIGHLIGHT_MARK_KEY] = true;
      // user_highlight/user_note marks have no startsHere field (no span splitting);
      // always carry the data.
      props[`${READER_USER_HIGHLIGHT_MARK_KEY}_data`] = mark;
      continue;
    }
    if (isUserNoteMark(mark)) {
      props[READER_USER_NOTE_MARK_KEY] = true;
      noteMarks.push(mark);
    }
  }

  if (noteMarks.length === 1) {
    props[`${READER_USER_NOTE_MARK_KEY}_data`] = noteMarks[0];
  } else if (noteMarks.length > 1) {
    props[`${READER_USER_NOTE_MARK_KEY}_data`] = noteMarks;
  }

  return props;
}

/**
 * B3: 把 ReaderRecordPlateInlineMark[] 转换为 Plate text node 属性。
 *
 * `splitLeafByInlineMarks` 已经按 mark 边界切分过 leaf，每个 sub-leaf
 * 携带的 inlineMarks 都是「完全覆盖该 sub-leaf」的标记。因此这里只需要
 * 把每个 mark 的 kind 映射到对应布尔属性，并把 link 的 href 单独写到
 * `link_href`。
 *
 * 注意：inline marks 是纯排版属性，与 `marks`（词汇 / 语法 / 用户标注）
 * 正交，可以同时存在于同一个 text node 上。
 */
export function inlineMarksToPlateProps(
  inlineMarks: ReaderRecordPlateInlineMark[] | undefined,
): Pick<
  PlateTextNode,
  "bold" | "italic" | "strikethrough" | "code" | "link" | "link_href"
> {
  if (!inlineMarks || inlineMarks.length === 0) {
    return {};
  }

  const props: Partial<
    Pick<
      PlateTextNode,
      "bold" | "italic" | "strikethrough" | "code" | "link" | "link_href"
    >
  > = {};

  for (const mark of inlineMarks) {
    switch (mark.kind) {
      case "strong":
        props.bold = true;
        break;
      case "em":
        props.italic = true;
        break;
      case "strikethrough":
        props.strikethrough = true;
        break;
      case "inline_code":
        props.code = true;
        break;
      case "link":
        props.link = true;
        // 后端已对 href 做白名单过滤（http/https/mailto），这里直接透传。
        if (typeof mark.href === "string" && mark.href.length > 0) {
          props.link_href = mark.href;
        }
        break;
    }
  }

  return props as Pick<
    PlateTextNode,
    "bold" | "italic" | "strikethrough" | "code" | "link" | "link_href"
  >;
}

/**
 * ReaderRecordPlateTextLeaf → Plate text node。
 *
 * 无 anchor metadata 的 leaf（例如 separator）不会生成选区锚点属性。
 * 携带 anchor segment id 和 segment range 的 source leaf 会继续输出选区锚点 data 属性。
 *
 * B3: 携带 inlineMarks 的 leaf（由 splitLeafByInlineMarks 切分得到）会
 * 额外输出 bold / italic / strikethrough / code / link / link_href 属性，
 * 供 reader-blocks-kit 中的 leaf plugin 渲染为 <strong> / <em> / <s> /
 * <code> / <a>。
 */
export function textLeafToPlateTextNode(
  leaf: ReaderRecordPlateTextLeaf,
): PlateTextNode {
  return {
    text: leaf.text,
    ...marksToPlateProps(leaf.marks),
    ...inlineMarksToPlateProps(leaf.inlineMarks),
    ...(leaf.anchorSegmentId && leaf.segmentRange
      ? {
          anchor_segment_id: leaf.anchorSegmentId,
          segment_start_utf16: leaf.segmentRange.startUtf16,
          segment_end_utf16: leaf.segmentRange.endUtf16,
        }
      : {}),
  };
}

/**
 * ReaderRecordPlateTranslationTextLeaf → Plate text node。
 *
 * 携带 translation_owner 和 translation_sourceRole 属性。
 */
export function translationLeafToPlateTextNode(
  leaf: ReaderRecordPlateTranslationTextLeaf,
): PlateTextNode {
  return {
    text: leaf.text,
    translation_owner: leaf.owner,
    translation_sourceRole: leaf.sourceRole,
  };
}

// --- Block → Element converters ---

function paragraphBlockToElement(
  block: ReaderRecordPlateParagraphBlock,
): ReaderParagraphElement {
  const children: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(textLeafToPlateTextNode)
      : [{ text: "" }];

  return {
    type: READER_PARAGRAPH_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function blockquoteBlockToElement(
  block: ReaderRecordPlateBlockquoteBlock,
): ReaderBlockquoteElement {
  const children: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(translationLeafToPlateTextNode)
      : [{ text: "" }];

  return {
    type: READER_BLOCKQUOTE_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function calloutBlockToElement(
  block: ReaderRecordPlateCalloutBlock,
): ReaderCalloutElement {
  return {
    type: READER_CALLOUT_TYPE,
    id: block.id,
    children: block.children,
    data: block.data,
    variant: block.variant,
    icon: block.icon,
  };
}

function sentenceAnalysisBlockToElement(
  block: ReaderRecordPlateSentenceAnalysisBlock,
): ReaderSentenceAnalysisElement {
  const chunkChildren: Descendant[] =
    block.data.chunks.length > 0
      ? [
          {
            type: READER_SENTENCE_ANALYSIS_CHUNKS_TYPE,
            children: block.data.chunks.map((chunk) => ({
              type: READER_SENTENCE_ANALYSIS_CHUNK_TYPE,
              data: chunk,
              children: [{ text: chunk.text }],
            })),
          } as unknown as Descendant,
        ]
      : [];

  return {
    type: READER_SENTENCE_ANALYSIS_TYPE,
    id: block.id,
    children: [...chunkChildren, ...block.children],
    data: block.data,
    icon: block.icon,
  };
}

// --- B2: Markdown stable-block-derived converters ---

function headingBlockToElement(
  block: ReaderRecordPlateHeadingBlock,
): ReaderHeadingElement {
  const children: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(textLeafToPlateTextNode)
      : [{ text: "" }];

  return {
    type: READER_HEADING_TYPE,
    id: block.id,
    level: block.level,
    children,
    data: block.data,
  };
}

function listItemBlockToElement(
  block: ReaderRecordPlateListItemBlock,
): ReaderListItemElement {
  const textChildren: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(textLeafToPlateTextNode)
      : [{ text: "" }];
  const nestedChildren = (block.nestedChildren ?? []).map(listBlockToElement);

  return {
    type: READER_LIST_ITEM_TYPE,
    id: block.id,
    children: [...textChildren, ...nestedChildren],
    data: block.data,
  };
}

function listBlockToElement(block: ReaderRecordPlateListBlock): ReaderListElement {
  // Backend guarantees list blocks contain at least one list_item (B2.6
  // groups by parentStableBlockId). If a malformed empty list slips through,
  // we emit a single empty list_item to keep Plate value valid rather than
  // crashing the editor.
  const children: ReaderListItemElement[] =
    block.children.length > 0
      ? block.children.map(listItemBlockToElement)
      : [
          {
            type: READER_LIST_ITEM_TYPE,
            id: `${block.id}:empty`,
            children: [{ text: "" }],
            data: block.data,
          },
        ];

  return {
    type: READER_LIST_TYPE,
    id: block.id,
    ordered: block.ordered,
    children,
    data: block.data,
  };
}

function codeBlockBlockToElement(
  block: ReaderRecordPlateCodeBlockBlock,
): ReaderCodeBlockElement {
  const children: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(textLeafToPlateTextNode)
      : [{ text: "" }];

  return {
    type: READER_CODE_BLOCK_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function markdownBlockquoteBlockToElement(
  block: ReaderRecordPlateMarkdownBlockquoteBlock,
): ReaderMarkdownBlockquoteElement {
  const children: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(textLeafToPlateTextNode)
      : [{ text: "" }];

  return {
    type: READER_MARKDOWN_BLOCKQUOTE_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function tableCellBlockToElement(
  block: ReaderRecordPlateTableCellBlock,
): ReaderTableCellElement {
  const children: PlateTextNode[] =
    block.children.length > 0
      ? block.children.map(textLeafToPlateTextNode)
      : [{ text: "" }];

  return {
    type: READER_TABLE_CELL_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function tableRowBlockToElement(
  block: ReaderRecordPlateTableRowBlock,
): ReaderTableRowElement {
  const children: ReaderTableCellElement[] = block.children.map(
    tableCellBlockToElement,
  );

  return {
    type: READER_TABLE_ROW_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function tableBlockToElement(
  block: ReaderRecordPlateTableBlock,
): ReaderTableElement {
  const children: ReaderTableRowElement[] = block.children.map(
    tableRowBlockToElement,
  );

  return {
    type: READER_TABLE_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function hrBlockToElement(block: ReaderRecordPlateHrBlock): ReaderHrElement {
  return {
    type: READER_HR_TYPE,
    id: block.id,
    children: [],
    data: block.data,
  };
}

function sourceCalloutBlockToElement(
  block: ReaderRecordPlateSourceCalloutBlock,
): ReaderSourceCalloutElement {
  const firstChild = block.children[0];
  const children: Array<PlateTextNode | ReaderPlateElement> =
    firstChild === undefined
      ? [{ text: "" }]
      : "text" in firstChild
        ? (block.children as ReaderRecordPlateTextLeaf[]).map(
            textLeafToPlateTextNode,
          )
        : (block.children as ReaderRecordPlateBlock[]).map(
            readerBlockToPlateElement,
          );

  return {
    type: READER_SOURCE_CALLOUT_TYPE,
    id: block.id,
    children,
    data: block.data,
  };
}

function readerBlockToPlateElement(
  block: ReaderRecordPlateBlock,
): ReaderPlateElement {
  switch (block.type) {
    case "paragraph":
      return paragraphBlockToElement(block);
    case "blockquote":
      return blockquoteBlockToElement(block);
    case "callout":
      return calloutBlockToElement(block);
    case "sentence_analysis":
      return sentenceAnalysisBlockToElement(block);
    case "heading":
      return headingBlockToElement(block);
    case "list":
      return listBlockToElement(block);
    case "list_item":
      return listItemBlockToElement(block);
    case "code_block":
      return codeBlockBlockToElement(block);
    case "markdown_blockquote":
      return markdownBlockquoteBlockToElement(block);
    case "table":
      return tableBlockToElement(block);
    case "table_row":
      return tableRowBlockToElement(block);
    case "table_cell":
      return tableCellBlockToElement(block);
    case "hr":
      return hrBlockToElement(block);
    case "source_callout":
      return sourceCalloutBlockToElement(block);
  }
}

/**
 * 把 ReaderRecordPlateDocument 转换为 Plate editor 可消费的 Descendant[]。
 *
 * 空 children 的 block 会填充空文本节点，保证 Plate value 合法。
 * enhancement children 已经是 Descendant[]（由 projection 层 deserializeMarkdownToBlocks 生成），直接传递。
 *
 * B2: Markdown stable-block-derived blocks (heading/list/list_item/code_block/
 * markdown_blockquote/table/table_row/table_cell/hr) are converted to
 * reader_* element types. They carry `ReaderRecordPlateStableBlockData`
 * (anchor segment id, base range, hash, etc.) so selection / vocabulary /
 * grammar marks continue to work on Markdown-rendered blocks.
 */
export function projectReaderRecordPlateToPlateValue(
  document: ReaderRecordPlateDocument,
): Descendant[] {
  return document.children.map(
    (block) => readerBlockToPlateElement(block) as unknown as Descendant,
  );
}
