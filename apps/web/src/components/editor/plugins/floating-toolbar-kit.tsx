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
import {
  ReaderFloatingToolbarButtons,
  useReaderToolbarActions,
} from "./reader-floating-toolbar-buttons";

function ReaderRecordFloatingToolbar() {
  const actions = useReaderToolbarActions();
  const state = actions?.suppressToolbar
    ? { showWhenReadOnly: true, hideToolbar: true }
    : { showWhenReadOnly: true };

  return (
    <FloatingToolbar
      className="reader-record-floating-toolbar rounded-[10px] p-1 !shadow-[0_8px_20px_rgba(28,24,18,0.08),0_1px_2px_rgba(17,17,17,0.04)] [&_[data-slot=separator][data-orientation=vertical]]:h-6 [&_[data-slot=separator][data-orientation=vertical]]:bg-border/80"
      data-reader-record-floating-toolbar="plate"
      state={state}
    >
      <ReaderFloatingToolbarButtons />
    </FloatingToolbar>
  );
}

export const FloatingToolbarKit = [
  createPlatePlugin({
    key: "reader-floating-toolbar",
    render: {
      afterEditable: () => <ReaderRecordFloatingToolbar />,
    },
  }),
];
