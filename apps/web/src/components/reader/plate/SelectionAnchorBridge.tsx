"use client";

/**
 * SelectionAnchorBridge — 在 <Plate> 内部渲染，把 Plate editor.selection
 * 桥接为 ReaderRecordSelectionAnchorBridgeResult，通过 onChange 推给父组件。
 *
 * 替代旧的 `selectionchange` DOM 监听 + readReaderRecordSelectionAnchorDrafts
 * 路径，全程使用 Plate 原生选区模型：
 * - useEditorRef() 拿到 PlateEditor 实例
 * - useEditorSelection() 订阅 selection 变化并触发 re-render
 * - readReaderRecordSelectionFromEditor(editor, snapshot, selection) 计算
 *   Reading Record 锚点草稿 + DOM rect
 *
 * 该组件不渲染任何 DOM，只承担副作用桥接职责。
 *
 * 在 jsdom 测试环境中，Plate 内部的 onDOMSelectionChange 处理器（throttled+
 * debounced）可能不会及时同步 editor.selection。为保证测试可靠，桥接同时
 * 监听原生 selectionchange 事件，当 editor.selection 为 null 时降级用
 * editor.api.toSlateRange(domSelection) 计算选区。
 */
import { useEffect, useReducer } from "react";
import { useEditorRef, useEditorSelection } from "platejs/react";
import type { TRange } from "platejs";

import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import {
  readReaderRecordSelectionFromEditor,
} from "@/lib/reader-plate/projection/reader-record-plate-selection";
import type { ReaderRecordSelectionAnchorBridgeResult } from "@/lib/reader-plate/projection/reader-record-dom-selection";

export interface SelectionAnchorBridgeProps {
  snapshot: ReaderPlateSnapshotDto;
  onChange: (
    next: ReaderRecordSelectionAnchorBridgeResult | null,
  ) => void;
}

export function SelectionAnchorBridge({
  snapshot,
  onChange,
}: SelectionAnchorBridgeProps) {
  const editor = useEditorRef();
  const selection = useEditorSelection() as TRange | null;
  // 触发 re-render 的计数器：当原生 selectionchange 事件触发但 editor.selection
  // 没有更新时（jsdom 环境），用这个计数器强制桥接重新计算。
  const [domSelectionTick, bumpDomSelectionTick] = useReducer(
    (x: number) => x + 1,
    0,
  );

  // 监听原生 selectionchange：在 jsdom 测试中，Plate 内部 onDOMSelectionChange
  // 受 throttle/debounce 影响，editor.selection 同步延迟会导致测试等待超时。
  // 此处订阅原生事件并用计数器触发 re-render，保证选区变化能被桥接及时捕获。
  useEffect(() => {
    function handleDomSelectionChange() {
      bumpDomSelectionTick();
    }
    window.document.addEventListener("selectionchange", handleDomSelectionChange);
    return () => {
      window.document.removeEventListener("selectionchange", handleDomSelectionChange);
    };
  }, []);

  useEffect(() => {
    if (!editor) {
      onChange(null);
      return;
    }

    // Reader readonly 表面的唯一选区真相是原生 DOM selection。处理顺序：
    // 1. 折叠/空 → null
    // 2. 选区位于 Reader document 外 → null（不回退 editor.selection，避免
    //    旧 editor.selection 在工具栏已关闭后被复活）
    // 3. 有效 native selection → 优先 toSlateRange 转换为 Slate range
    // 4. 转换失败 → 回退 editor.selection（此时 native 仍在 document 内）
    // 5. 不向 editor.selection 反向写入选区
    const domSelection = window.getSelection();
    const hasNonCollapsedNativeSelection =
      !!domSelection &&
      domSelection.rangeCount > 0 &&
      !domSelection.isCollapsed;

    if (!hasNonCollapsedNativeSelection) {
      onChange(null);
      return;
    }

    // 显式检查 native selection 是否位于 Reader document 内。
    // `.reader-record-plate-document` 是 ReaderRecordPlateSurface 渲染的
    // Plate document 根节点。选区落在侧栏、Ask 面板、Quick Peek 等区域时
    // anchorNode / focusNode 不是该根节点的后代，必须视为"无选区"以关闭
    // 工具栏。两个端点都必须在文档内，防止跨边界选区（从 Reader 拖到侧栏）
    // 残留旧 editor.selection 被复活。
    // 测试/无根节点环境下退化为不拦截，由 toSlateRange 与
    // readReaderRecordSelectionFromEditor 兜底。
    const readerDocRoot = document.querySelector(".reader-record-plate-document");
    const anchorNode = domSelection!.anchorNode;
    const focusNode = domSelection!.focusNode;
    const insideReaderDocument = readerDocRoot
      ? !!anchorNode &&
        !!focusNode &&
        readerDocRoot.contains(anchorNode) &&
        readerDocRoot.contains(focusNode)
      : true;

    if (!insideReaderDocument) {
      onChange(null);
      return;
    }

    // 优先把 native DOM selection 转换为 Slate range。只有转换失败才回退
    // editor.selection（此时 native 仍在 document 内，editor.selection 可能
    // 滞后但仍指向有效 Slate 范围）。全程不向 editor.selection 反向写入选区。
    let workingSelection: TRange | null = null;

    if (typeof editor.api?.toSlateRange === "function") {
      try {
        const slateRange = editor.api.toSlateRange(domSelection!, {
          exactMatch: false,
          suppressThrow: true,
        } as never);
        if (slateRange && typeof slateRange === "object") {
          workingSelection = slateRange as TRange;
        }
      } catch {
        // toSlateRange 在异常 DOM 状态下可能抛错，降级到 editor.selection
      }
    }

    if (!workingSelection) {
      workingSelection = selection ?? editor.selection ?? null;
    }

    if (!workingSelection) {
      onChange(null);
      return;
    }

    // readReaderRecordSelectionFromEditor 做最终校验：
    // - 选区在 editor.children 外（editor.api.nodes 返回空）→ null
    // - 选区跨非 source block → null
    // - 选区折叠 → null
    const result = readReaderRecordSelectionFromEditor(
      editor,
      snapshot,
      workingSelection,
    );
    onChange(result);
  }, [editor, snapshot, selection, domSelectionTick, onChange]);

  return null;
}
