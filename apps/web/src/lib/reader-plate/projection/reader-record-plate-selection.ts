/**
 * Plate editor.selection → Reading Record 锚点草稿桥接。
 *
 * 替代 reader-record-dom-selection.ts 的 window.getSelection() 路径：
 * - 选区模型：editor.selection（Slate Range: path+offset），由 Plate 维护
 * - 锚点映射：从 PlateTextNode.anchor_segment_id / segment_start_utf16 读取
 * - DOM rect：editor.api.toDOMRange(selection).getBoundingClientRect()，全程 Plate 原生
 *
 * 输出契约与 readReaderRecordSelectionAnchorDrafts 相同的
 * ReaderRecordSelectionAnchorBridgeResult，后端锚点逻辑无需改动。
 */
import { computeUtf16FNV1a } from "@claread/contracts";
import {
  RangeApi,
  TextApi,
  type TRange,
  type TText,
} from "platejs";
import type { PlateEditor } from "platejs/react";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import {
  anchorDraftsForSelection,
  type ReaderRecordAnchorDraftSelectionSegment,
} from "./reader-record-anchor-draft";
import {
  READER_BLOCKQUOTE_TYPE,
  READER_CALLOUT_TYPE,
  READER_CODE_BLOCK_TYPE,
  READER_HEADING_TYPE,
  READER_LIST_TYPE,
  READER_MARKDOWN_BLOCKQUOTE_TYPE,
  READER_PARAGRAPH_TYPE,
  READER_SENTENCE_ANALYSIS_TYPE,
  READER_SOURCE_CALLOUT_TYPE,
  READER_TABLE_TYPE,
  type PlateTextNode,
  type ReaderBlockquoteElement,
  type ReaderCalloutElement,
  type ReaderCodeBlockElement,
  type ReaderHeadingElement,
  type ReaderListElement,
  type ReaderMarkdownBlockquoteElement,
  type ReaderParagraphElement,
  type ReaderSentenceAnalysisElement,
  type ReaderSourceCalloutElement,
  type ReaderTableElement,
} from "./reader-record-plate-to-plate-value";
import type {
  ReaderRecordSelectionAnchorBridgeResult,
  ReaderRecordSelectionBlockContext,
  ReaderRecordSelectionSourceContext,
} from "./reader-record-dom-selection";

const READER_CALLOUT_GROUP_TYPE = "reader_callout_group" as const;

/**
 * Top-level Plate block types that carry stable source text (Markdown
 * original). Selections whose top-level block is one of these are eligible
 * to become actionable source anchors — as opposed to translation /
 * callout / sentence-analysis surfaces which are non-source.
 *
 * `reader_list` / `reader_table` are wrappers: `selectionBlockForPath`
 * returns the wrapper (not the inner list_item / table_cell) for nested
 * text leaves, so they must be allowlisted here.
 */
const STABLE_SOURCE_BLOCK_TYPES: ReadonlySet<string> = new Set([
  READER_PARAGRAPH_TYPE,
  READER_HEADING_TYPE,
  READER_MARKDOWN_BLOCKQUOTE_TYPE,
  READER_LIST_TYPE,
  READER_TABLE_TYPE,
  READER_CODE_BLOCK_TYPE,
  READER_SOURCE_CALLOUT_TYPE,
]);

function pathsEqual(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((v, i) => v === b[i]);
}

interface LeafSlice {
  /** 该 text node 的完整文本 */
  text: string;
  /** 在该 text node 内的选中起始（字符偏移） */
  localStart: number;
  /** 在该 text node 内的选中结束（字符偏移） */
  localEnd: number;
  /** 该 leaf 在 anchor segment 内的 UTF-16 起始偏移 */
  segmentStartUtf16: number;
}

interface SelectedTextSlice {
  text: string;
  localStart: number;
  localEnd: number;
}

type ReaderTopLevelPlateElement =
  | ReaderParagraphElement
  | ReaderBlockquoteElement
  | ReaderCalloutElement
  | ReaderSentenceAnalysisElement
  | ReaderHeadingElement
  | ReaderListElement
  | ReaderCodeBlockElement
  | ReaderMarkdownBlockquoteElement
  | ReaderTableElement
  | ReaderSourceCalloutElement
  | {
      type: typeof READER_CALLOUT_GROUP_TYPE;
      id: string;
      children: ReaderCalloutElement[];
    };

type ReaderSelectionBlockElement =
  | ReaderParagraphElement
  | ReaderBlockquoteElement
  | ReaderCalloutElement
  | ReaderSentenceAnalysisElement
  | ReaderHeadingElement
  | ReaderListElement
  | ReaderCodeBlockElement
  | ReaderMarkdownBlockquoteElement
  | ReaderTableElement
  | ReaderSourceCalloutElement;

function selectionBlockForPath(
  editor: PlateEditor,
  path: number[],
): ReaderSelectionBlockElement | null {
  const topLevelIndex = path[0];
  if (!Number.isInteger(topLevelIndex)) {
    return null;
  }
  const topLevelBlock = editor.children[topLevelIndex] as unknown as
    | ReaderTopLevelPlateElement
    | undefined;
  if (!topLevelBlock) {
    return null;
  }
  if (topLevelBlock.type === READER_CALLOUT_GROUP_TYPE) {
    const childIndex = path[1];
    if (!Number.isInteger(childIndex)) {
      return null;
    }
    return topLevelBlock.children[childIndex] ?? null;
  }
  return topLevelBlock;
}

function selectedSliceForTextNode(
  node: TText,
  path: number[],
  start: TRange["anchor"],
  end: TRange["focus"],
): SelectedTextSlice | null {
  const text = (node as unknown as PlateTextNode).text ?? "";
  const isStartNode = pathsEqual(path, start.path);
  const isEndNode = pathsEqual(path, end.path);

  let localStart = 0;
  let localEnd = text.length;

  if (isStartNode && isEndNode) {
    localStart = start.offset;
    localEnd = end.offset;
  } else if (isStartNode) {
    localStart = start.offset;
    localEnd = text.length;
  } else if (isEndNode) {
    localStart = 0;
    localEnd = end.offset;
  }

  if (localEnd <= localStart) {
    return null;
  }
  return { text, localStart, localEnd };
}

function sourceTextForAnchorSegment(
  snapshot: ReaderPlateSnapshotDto,
  anchorSegmentId: string,
): string {
  for (const unit of snapshot.value) {
    for (const child of unit.children) {
      if (child.type !== "reader_source_block") {
        continue;
      }
      for (const sourceChild of child.children) {
        if (
          "type" in sourceChild &&
          sourceChild.type === "reader_anchor_segment" &&
          sourceChild.anchor_segment_id === anchorSegmentId
        ) {
          return sourceChild.children.map((leaf) => leaf.text).join("");
        }
      }
    }
  }
  return "";
}

function sourceSegmentsForUnit(
  snapshot: ReaderPlateSnapshotDto,
  unitId: string,
): NonNullable<ReaderRecordSelectionSourceContext["sourceSegments"]> {
  return snapshot.anchor_segments
    .filter((segment) => segment.unit_id === unitId)
    .sort(
      (a, b) =>
        a.unit_order_index - b.unit_order_index ||
        a.order_index - b.order_index,
    )
    .map((segment) => ({
      anchorSegmentId: segment.anchor_segment_id,
      sentenceId: segment.sentence_id,
      unitStart: segment.unit_start_utf16,
      unitEnd: segment.unit_end_utf16,
      sourceText: sourceTextForAnchorSegment(
        snapshot,
        segment.anchor_segment_id,
      ),
      textHash: segment.text_hash,
    }));
}

function sourceContextForSelectionBlock(
  snapshot: ReaderPlateSnapshotDto,
  options: { anchorSegmentId?: string; unitId?: string },
): ReaderRecordSelectionSourceContext | undefined {
  const unitSourceSegments = options.unitId
    ? sourceSegmentsForUnit(snapshot, options.unitId)
    : [];
  const unitSourceText =
    unitSourceSegments.length > 0
      ? unitSourceSegments.map((segment) => segment.sourceText).join("")
      : undefined;
  const anchorSegment =
    options.anchorSegmentId
      ? snapshot.anchor_segments.find(
          (segment) => segment.anchor_segment_id === options.anchorSegmentId,
        )
      : undefined;

  if (!anchorSegment) {
    return options.unitId
      ? {
          unitId: options.unitId,
          unitSourceText,
          sourceSegments: unitSourceSegments,
        }
      : undefined;
  }

  return {
    anchorSegmentId: anchorSegment.anchor_segment_id,
    unitId: anchorSegment.unit_id,
    sentenceId: anchorSegment.sentence_id,
    sourceText: sourceTextForAnchorSegment(
      snapshot,
      anchorSegment.anchor_segment_id,
    ),
    textHash: anchorSegment.text_hash,
    unitSourceText,
    sourceSegments: unitSourceSegments,
  };
}

function domRectForSelection(
  editor: PlateEditor,
  selection: TRange,
): DOMRect | null {
  try {
    const domRange = editor.api.toDOMRange(selection);
    return domRange?.getBoundingClientRect() ?? null;
  } catch {
    return null;
  }
}

function blockContextForNonSourceSelection(
  block: ReaderSelectionBlockElement,
  selectedText: string,
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordSelectionBlockContext | null {
  switch (block.type) {
    case READER_BLOCKQUOTE_TYPE: {
      const data = (block as ReaderBlockquoteElement).data;
      return {
        surfaceKind: "translation",
        blockType: block.type,
        blockId: block.id,
        selectedText,
        unitId: data.unitId,
        layerId: data.layerId,
        source: sourceContextForSelectionBlock(snapshot, {
          unitId: data.unitId,
        }),
      };
    }
    case READER_CALLOUT_TYPE: {
      const callout = block as ReaderCalloutElement;
      const data = callout.data;
      const surfaceKind =
        callout.variant === "grammar"
          ? "grammar_callout"
          : "supplement_callout";
      return {
        surfaceKind,
        blockType: block.type,
        blockId: block.id,
        selectedText,
        anchorSegmentId: data.anchorSegmentId,
        unitId: data.unitId,
        layerId: data.layerId,
        supplementId: data.supplementId,
        source: sourceContextForSelectionBlock(snapshot, {
          anchorSegmentId: data.anchorSegmentId,
          unitId: data.unitId,
        }),
      };
    }
    case READER_SENTENCE_ANALYSIS_TYPE: {
      const data = (block as ReaderSentenceAnalysisElement).data;
      return {
        surfaceKind: "sentence_analysis",
        blockType: block.type,
        blockId: block.id,
        selectedText,
        anchorSegmentId: data.anchorSegmentId,
        unitId: data.unitId,
        layerId: data.layerId,
        analysisId: data.analysisId,
        chunks: data.chunks,
        source: sourceContextForSelectionBlock(snapshot, {
          anchorSegmentId: data.anchorSegmentId,
          unitId: data.unitId,
        }),
      };
    }
    default:
      return null;
  }
}

/**
 * 从 Plate editor.selection 计算 Reading Record 锚点草稿。
 *
 * 算法：
 * 1. 用 editor.nodes({ at: selection, match: Text.isText }) 迭代选区内所有文本节点
 * 2. 按 anchor_segment_id 分组
 * 3. 每组用 RangeApi.edges 归一化的 start/end 点裁剪首尾 leaf 的选中范围
 * 4. 构建 ReaderRecordAnchorDraftSelectionSegment[]，调用 anchorDraftsForSelection
 *
 * 返回 null 表示选区无效（折叠、无锚点 leaf、hash 校验失败等）。
 */
export function readReaderRecordSelectionFromEditor(
  editor: PlateEditor,
  snapshot: ReaderPlateSnapshotDto,
  selection: TRange | null,
): ReaderRecordSelectionAnchorBridgeResult | null {
  if (!selection || RangeApi.isCollapsed(selection)) {
    return null;
  }

  // 1. 迭代选区范围内的文本节点（document order）
  const textEntries: Array<[TText, number[]]> = [];
  for (const entry of editor.api.nodes({
    at: selection,
    match: (node) => TextApi.isText(node),
  })) {
    // NodeEntry 是 [node, path] 二元组
    textEntries.push(entry as unknown as [TText, number[]]);
  }

  if (textEntries.length === 0) {
    return null;
  }

  // 2. 归一化选区起止点（RangeApi.edges 返回 [start, end] tuple）
  const [start, end] = RangeApi.edges(selection);

  const topLevelIndexes = new Set(textEntries.map(([, path]) => path[0]));
  const firstBlock = selectionBlockForPath(editor, textEntries[0][1]);

  // 3. 按 anchor_segment_id 分组，计算每组的选中片段
  const groups = new Map<string, LeafSlice[]>();

  for (const [node, path] of textEntries) {
    const textNode = node as unknown as PlateTextNode;
    const text = textNode.text ?? "";
    const segmentId = textNode.anchor_segment_id;
    const segmentStartUtf16 = textNode.segment_start_utf16 ?? 0;

    // 没有锚点信息的 leaf（callout 内文本、translation 等）跳过
    if (!segmentId) {
      continue;
    }

    // 计算该 text node 内的选中范围
    const isStartNode = pathsEqual(path, start.path);
    const isEndNode = pathsEqual(path, end.path);

    let localStart = 0;
    let localEnd = text.length;

    if (isStartNode && isEndNode) {
      localStart = start.offset;
      localEnd = end.offset;
    } else if (isStartNode) {
      localStart = start.offset;
      localEnd = text.length;
    } else if (isEndNode) {
      localStart = 0;
      localEnd = end.offset;
    }

    if (localEnd <= localStart) {
      continue;
    }

    const group = groups.get(segmentId) ?? [];
    group.push({ text, localStart, localEnd, segmentStartUtf16 });
    groups.set(segmentId, group);
  }

  if (groups.size === 0) {
    if (topLevelIndexes.size !== 1 || !firstBlock) {
      return null;
    }

    const blockContextSlices: string[] = [];
    for (const [node, path] of textEntries) {
      const block = selectionBlockForPath(editor, path);
      if (!block || block.id !== firstBlock.id) {
        return null;
      }
      const slice = selectedSliceForTextNode(node, path, start, end);
      if (slice) {
        blockContextSlices.push(
          slice.text.slice(slice.localStart, slice.localEnd),
        );
      }
    }

    const selectedText = blockContextSlices.join("").trim();
    if (!selectedText) {
      return null;
    }

    const blockContext = blockContextForNonSourceSelection(
      firstBlock,
      selectedText,
      snapshot,
    );
    if (!blockContext) {
      return null;
    }

    return {
      drafts: [],
      selectedText,
      anchorType: "text_range",
      segments: [],
      sentenceId: blockContext.source?.sentenceId ?? "",
      contextSentence:
        blockContext.source?.sourceText ??
        blockContext.source?.unitSourceText ??
        "",
      supportedSingleRange: false,
      rect: domRectForSelection(editor, selection),
      surfaceKind: blockContext.surfaceKind,
      blockType: blockContext.blockType,
      blockId: blockContext.blockId,
      blockContext,
    };
  }

  // A selection is a valid source anchor iff every touched top-level block
  // is a stable source surface (paragraph / heading / markdown_blockquote /
  // list / table / code_block / source_callout). Translation, callout and
  // sentence-analysis blocks are non-source and must not reach this branch
  // (they are handled by the groups.size === 0 path above via
  // blockContextForNonSourceSelection).
  const hasNonSourceBlockText = textEntries.some(([, path]) => {
    const block = selectionBlockForPath(editor, path);
    return !block || !STABLE_SOURCE_BLOCK_TYPES.has(block.type);
  });
  if (hasNonSourceBlockText) {
    return null;
  }

  // 4. 构建 segments
  const segments: ReaderRecordAnchorDraftSelectionSegment[] = [];
  let contextSentence = "";

  for (const [segmentId, leaves] of groups) {
    const anchorSegment = snapshot.anchor_segments.find(
      (s) => s.anchor_segment_id === segmentId,
    );
    if (!anchorSegment) continue;

    const selectedText = leaves
      .map((l) => l.text.slice(l.localStart, l.localEnd))
      .join("");

    if (!selectedText.trim()) continue;

    const firstLeaf = leaves[0];
    const lastLeaf = leaves[leaves.length - 1];
    const startOffset = firstLeaf.segmentStartUtf16 + firstLeaf.localStart;
    const endOffset = lastLeaf.segmentStartUtf16 + lastLeaf.localEnd;
    const textHash = computeUtf16FNV1a(selectedText);

    segments.push({
      paragraphId: anchorSegment.unit_id,
      sentenceId: anchorSegment.sentence_id,
      selectedText,
      startOffset,
      endOffset,
      textHash,
    });

    // contextSentence：第一个 segment 的所有选中 leaf 的完整文本（与 DOM 路径行为一致）
    if (segments.length === 1) {
      contextSentence = leaves.map((l) => l.text).join("");
    }
  }

  if (segments.length === 0) {
    return null;
  }

  const drafts = anchorDraftsForSelection(snapshot, { segments });
  if (drafts.length !== segments.length) {
    return null;
  }

  const selectedText = segments
    .map((s) => s.selectedText.trim())
    .filter(Boolean)
    .join(" ")
    .trim();

  if (!selectedText) {
    return null;
  }

  const primaryDraft = drafts[0] ?? null;
  const primarySegment = segments[0] ?? null;
  const blockId =
    firstBlock?.id ??
    `paragraph:${primaryDraft?.anchor_segment_id ?? primarySegment?.sentenceId ?? ""}`;
  const sourceContext: ReaderRecordSelectionSourceContext = {
    anchorSegmentId: primaryDraft?.anchor_segment_id,
    unitId: primaryDraft?.unit_id,
    sentenceId: primarySegment?.sentenceId,
    sourceText: contextSentence,
    textHash: primaryDraft?.text_hash,
  };

  return {
    drafts,
    selectedText,
    anchorType: segments.length === 1 ? "text_range" : "multi_text",
    segments,
    sentenceId: segments[0]?.sentenceId ?? "",
    contextSentence,
    supportedSingleRange: drafts.length === 1,
    rect: domRectForSelection(editor, selection),
    surfaceKind: "source",
    blockType: firstBlock?.type ?? READER_PARAGRAPH_TYPE,
    blockId,
    blockContext: {
      surfaceKind: "source",
      blockType: firstBlock?.type ?? READER_PARAGRAPH_TYPE,
      blockId,
      selectedText,
      anchorSegmentId: primaryDraft?.anchor_segment_id,
      unitId: primaryDraft?.unit_id,
      source: sourceContext,
    },
  };
}
