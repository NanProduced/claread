"use client";

/**
 * Cursor Overlay Kit — 注册 Plate CursorOverlayPlugin
 *
 * 通过 `render.afterEditable` 在 Plate editor 之外渲染 CursorOverlay。
 * 当编辑器失焦（如词典/Ask rail 获焦）时，CursorOverlayPlugin 维持选区高亮，
 * 保证 rail 操作期间中心文档选区可见且不丢失。
 *
 * 关键约束：
 * - 不持久化 Plate path / Slate path
 * - 只读模式下保留选区视觉，不开启编辑能力
 * - 需配合 `data-plate-focus="true"` 让 rail/toolbar 元素不触发选区清除
 *
 * 来源：Plate 官方 CursorOverlayKit 模式（@platejs/selection/react）。
 */
import { CursorOverlayPlugin } from "@platejs/selection/react";

import { CursorOverlay } from "@/components/ui/cursor-overlay";

export const CursorOverlayKit = [
  CursorOverlayPlugin.configure({
    render: {
      afterEditable: () => <CursorOverlay />,
    },
  }),
];
