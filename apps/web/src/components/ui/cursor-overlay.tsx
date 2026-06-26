"use client";

/**
 * CursorOverlay — 渲染选区/插入符 overlay。
 *
 * 当编辑器失焦（如词典/Ask rail 获焦）时，由 CursorOverlayPlugin 维持选区高亮。
 * 组件通过 useCursorOverlay 读取 selectionRects / caretPosition，渲染绝对定位 div。
 *
 * 来源：Plate registry cursor-overlay，适配 Claread 只读阅读器视觉（calm 选区色）。
 * 关键约束：
 * - 不持久化 Plate path / Slate path
 * - 只负责渲染，不重新计算业务优先级
 * - 选区 overlay 透明度低于正在编辑的视觉抢眼度
 */
import { useCursorOverlay } from "@platejs/selection/react";

import { cn } from "@/lib/cn";

export function CursorOverlay() {
  const { cursors } = useCursorOverlay();

  return (
    <div className="pointer-events-none absolute inset-0 z-25">
      {cursors.map((cursor) => {
        const selectionStyle = cursor.data?.selectionStyle ?? {};
        const caretStyle = cursor.data?.style ?? {};

        return (
          <div key={cursor.id}>
            {/* Selection rects — 半透明覆盖，不抢正文可读性 */}
            {cursor.selectionRects.map((rect, index) => (
              <div
                key={`${cursor.id}-sel-${index}`}
                className={cn(
                  "absolute rounded-[1px] bg-brand/20",
                )}
                style={{
                  ...selectionStyle,
                  height: rect.height,
                  left: rect.left,
                  top: rect.top,
                  width: rect.width,
                }}
              />
            ))}
            {/* Caret — 失焦后的插入符位置 */}
            {cursor.caretPosition ? (
              <div
                className={cn(
                  "absolute w-0.5 bg-brand/70",
                )}
                style={{
                  ...caretStyle,
                  height: "1em",
                  left: cursor.caretPosition.left,
                  top: cursor.caretPosition.top,
                }}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
