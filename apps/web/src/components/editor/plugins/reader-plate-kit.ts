/**
 * Reader Plate Kit — Reader Plate surface 的统一 plugin 入口
 *
 * 聚合：
 * - markdown-kit: callout children 的 markdown deserialize 后渲染（阶段一 P0）
 * - reader-blocks-kit: reader_paragraph / reader_blockquote / reader_callout element plugin（阶段二 V2 Phase 1）
 * - reader-leaf-kit: vocabulary / grammar / user_highlight / user_note leaf plugin（阶段二 V2 Phase 1）
 * - floating-toolbar-kit: Plate FloatingToolbar plugin，只读模式下选区浮动工具栏（阶段二 V2 Phase 2）
 * - comment-kit: Plate CommentPlugin + CommentLeaf，inline comment mark 渲染（阶段二 V2 Phase 3）
 * - cursor-overlay-kit: Plate CursorOverlayPlugin，rail 获焦时维持选区高亮（G1 选区保持）
 */
import { CommentKit } from "./comment-kit";
import { CursorOverlayKit } from "./cursor-overlay-kit";
import { FloatingToolbarKit } from "./floating-toolbar-kit";
import { MarkdownKit } from "./markdown-kit";
import { ReaderBlocksKit } from "./reader-blocks-kit";
import { ReaderLeafKit } from "./reader-leaf-kit";

export const ReaderPlateKit = [
  ...MarkdownKit,
  ...ReaderBlocksKit,
  ...ReaderLeafKit,
  ...FloatingToolbarKit,
  ...CommentKit,
  ...CursorOverlayKit,
];
