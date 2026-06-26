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

    // 优先使用 Plate editor.selection（由 Plate 的 onDOMSelectionChange 同步）。
    // 在 jsdom 测试中，editor.selection 可能未及时同步，此时降级用
    // editor.api.toSlateRange(domSelection) 计算选区。
    let workingSelection: TRange | null = selection ?? editor.selection ?? null;

    if (!workingSelection) {
      const domSelection = window.getSelection();
      if (
        domSelection &&
        domSelection.rangeCount > 0 &&
        !domSelection.isCollapsed &&
        typeof editor.api?.toSlateRange === "function"
      ) {
        try {
          const slateRange = editor.api.toSlateRange(domSelection, {
            exactMatch: false,
            suppressThrow: true,
          } as never);
          if (slateRange && typeof slateRange === "object") {
            workingSelection = slateRange as TRange;
          }
        } catch {
          // toSlateRange 在异常 DOM 状态下可能抛错，降级为 null
        }
      }
    }

    const result = readReaderRecordSelectionFromEditor(
      editor,
      snapshot,
      workingSelection,
    );
    onChange(result);
  }, [editor, snapshot, selection, domSelectionTick, onChange]);

  return null;
}
