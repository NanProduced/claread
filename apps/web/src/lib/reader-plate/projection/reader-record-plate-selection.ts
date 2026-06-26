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
import type { PlateTextNode } from "./reader-record-plate-to-plate-value";
import type { ReaderRecordSelectionAnchorBridgeResult } from "./reader-record-dom-selection";

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

  // DOM rect：全程 Plate 原生，用 editor.api.toDOMRange 转换
  let rect: DOMRect | null = null;
  try {
    const domRange = editor.api.toDOMRange(selection);
    if (domRange) {
      rect = domRange.getBoundingClientRect();
    }
  } catch {
    // toDOMRange 在极端情况下可能抛错（DOM 未挂载），降级为 null
    rect = null;
  }

  return {
    drafts,
    selectedText,
    anchorType: segments.length === 1 ? "text_range" : "multi_text",
    segments,
    sentenceId: segments[0]?.sentenceId ?? "",
    contextSentence,
    supportedSingleRange: drafts.length === 1,
    rect,
  };
}
