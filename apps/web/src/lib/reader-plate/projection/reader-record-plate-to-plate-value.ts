/**
 * Reader Record Plate → Plate Value projection
 *
 * 把 ReaderRecordPlateDocument（V2 标准 Plate blocks）转换为 Plate editor
 * 可直接消费的 Descendant[]。每种 block 映射为自定义 element type：
 * - paragraph  → reader_paragraph
 * - blockquote → reader_blockquote
 * - callout    → reader_callout
 *
 * text leaf 的 marks 转换为 text node 属性，供 leaf plugin 渲染。
 * 仅在 mark.startsHere 时携带 mark data，避免跨 leaf 重复携带。
 */
import type { Descendant } from "platejs";

import type {
  ReaderRecordPlateBlock,
  ReaderRecordPlateBlockquoteBlock,
  ReaderRecordPlateCalloutBlock,
  ReaderRecordPlateDocument,
  ReaderRecordPlateGrammarMark,
  ReaderRecordPlateMark,
  ReaderRecordPlateParagraphBlock,
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

// --- Mark key constants ---

export const READER_VOCABULARY_MARK_KEY = "vocabulary" as const;
export const READER_GRAMMAR_MARK_KEY = "grammar" as const;
export const READER_USER_HIGHLIGHT_MARK_KEY = "user_highlight" as const;
export const READER_USER_NOTE_MARK_KEY = "user_note" as const;

// --- Element node types ---

export interface ReaderParagraphElement {
  type: typeof READER_PARAGRAPH_TYPE;
  children: PlateTextNode[];
  data: ReaderRecordPlateParagraphBlock["data"];
}

export interface ReaderBlockquoteElement {
  type: typeof READER_BLOCKQUOTE_TYPE;
  children: PlateTextNode[];
  data: ReaderRecordPlateBlockquoteBlock["data"];
}

export interface ReaderCalloutElement {
  type: typeof READER_CALLOUT_TYPE;
  children: Descendant[];
  data: ReaderRecordPlateCalloutBlock["data"];
  variant: ReaderRecordPlateCalloutBlock["variant"];
  icon: string;
}

export type ReaderPlateElement =
  | ReaderParagraphElement
  | ReaderBlockquoteElement
  | ReaderCalloutElement;

// --- Text node type ---

export interface PlateTextNode {
  text: string;
  vocabulary?: boolean;
  vocabulary_data?: ReaderRecordPlateVocabularyMark;
  grammar?: boolean;
  grammar_data?: ReaderRecordPlateGrammarMark;
  user_highlight?: boolean;
  user_highlight_data?: ReaderRecordPlateUserHighlightMark;
  user_note?: boolean;
  user_note_data?: ReaderRecordPlateUserNoteMark;
  translation_owner?: string;
  translation_sourceRole?: string;
  /** 段落文本 leaf 所属的 anchor segment id（用于选区锚点定位） */
  anchor_segment_id?: string;
  /** 该 leaf 在 anchor segment 内的 UTF-16 起始偏移 */
  segment_start_utf16?: number;
  /** 该 leaf 在 anchor segment 内的 UTF-16 结束偏移 */
  segment_end_utf16?: number;
}

// --- Mark → Plate text node props ---

function isVocabularyMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateVocabularyMark {
  return (
    mark.kind !== "grammar_note" &&
    mark.kind !== "user_highlight" &&
    mark.kind !== "user_note"
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

function isUserNoteMark(
  mark: ReaderRecordPlateMark,
): mark is ReaderRecordPlateUserNoteMark {
  return mark.kind === "user_note";
}

/**
 * 把 ReaderRecordPlateMark[] 转换为 Plate text node 属性。
 *
 * 每个 mark 设置对应的 boolean flag（`vocabulary: true` 等），
 * 仅在 mark.startsHere 时携带完整 mark data（`vocabulary_data` 等），
 * 避免跨 leaf 重复携带 data。
 */
export function marksToPlateProps(
  marks: ReaderRecordPlateMark[],
): Partial<Pick<PlateTextNode, "vocabulary" | "vocabulary_data" | "grammar" | "grammar_data" | "user_highlight" | "user_highlight_data" | "user_note" | "user_note_data">> {
  const props: Record<string, unknown> = {};

  for (const mark of marks) {
    if (isVocabularyMark(mark)) {
      props[READER_VOCABULARY_MARK_KEY] = true;
      if (mark.startsHere) {
        props[`${READER_VOCABULARY_MARK_KEY}_data`] = mark;
      }
      continue;
    }
    if (isGrammarMark(mark)) {
      props[READER_GRAMMAR_MARK_KEY] = true;
      if (mark.startsHere) {
        props[`${READER_GRAMMAR_MARK_KEY}_data`] = mark;
      }
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
      props[`${READER_USER_NOTE_MARK_KEY}_data`] = mark;
    }
  }

  return props;
}

/**
 * ReaderRecordPlateTextLeaf → Plate text node。
 *
 * 无 marks 的 leaf（separator）仅返回 `{ text }`。
 * 携带 anchor segment id 和 segment range，供 renderLeaf 输出选区锚点 data 属性。
 */
export function textLeafToPlateTextNode(
  leaf: ReaderRecordPlateTextLeaf,
): PlateTextNode {
  return {
    text: leaf.text,
    ...marksToPlateProps(leaf.marks),
    anchor_segment_id: leaf.anchorSegmentId,
    segment_start_utf16: leaf.segmentRange.startUtf16,
    segment_end_utf16: leaf.segmentRange.endUtf16,
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
    children,
    data: block.data,
  };
}

function calloutBlockToElement(
  block: ReaderRecordPlateCalloutBlock,
): ReaderCalloutElement {
  return {
    type: READER_CALLOUT_TYPE,
    children: block.children,
    data: block.data,
    variant: block.variant,
    icon: block.icon,
  };
}

/**
 * 把 ReaderRecordPlateDocument 转换为 Plate editor 可消费的 Descendant[]。
 *
 * 空 children 的 block 会填充空文本节点，保证 Plate value 合法。
 * callout 的 children 已经是 Descendant[]（由 projection 层 deserializeMarkdownToBlocks 生成），直接传递。
 */
export function projectReaderRecordPlateToPlateValue(
  document: ReaderRecordPlateDocument,
): Descendant[] {
  return document.children.map((block: ReaderRecordPlateBlock) => {
    switch (block.type) {
      case "paragraph":
        return paragraphBlockToElement(block) as unknown as Descendant;
      case "blockquote":
        return blockquoteBlockToElement(block) as unknown as Descendant;
      case "callout":
        return calloutBlockToElement(block) as unknown as Descendant;
    }
  });
}
