/**
 * Floating Toolbar Kit — 注册 Plate FloatingToolbar plugin
 *
 * 通过 `render.afterEditable` 在 Plate editor 之外渲染 FloatingToolbar。
 * `showWhenReadOnly: true` 确保只读模式下选中文本时工具栏仍显示。
 *
 * ReaderFloatingToolbarButtons 通过 ReaderToolbarActionsContext 获取回调，
 * 由 ReaderRecordPlateSurface 在渲染时通过 Provider 注入。
 */
import { createPlatePlugin } from "platejs/react";

import { FloatingToolbar } from "@/components/ui/floating-toolbar";
import { ReaderFloatingToolbarButtons } from "./reader-floating-toolbar-buttons";

export const FloatingToolbarKit = [
  createPlatePlugin({
    key: "reader-floating-toolbar",
    render: {
      afterEditable: () => (
        <FloatingToolbar state={{ showWhenReadOnly: true }}>
          <ReaderFloatingToolbarButtons />
        </FloatingToolbar>
      ),
    },
  }),
];
