/**
 * Reader Plate Kit — Reader Plate surface 的统一 plugin 入口
 *
 * 聚合：
 * - markdown-kit: callout children 的 markdown deserialize 后渲染（阶段一 P0）
 * - reader-blocks-kit: reader_paragraph / reader_blockquote / reader_callout / reader_sentence_analysis element plugin（阶段二 V2 Phase 1）
 * - reader-leaf-kit: vocabulary / grammar / user_highlight / user_note leaf plugin（阶段二 V2 Phase 1）
 * - floating-toolbar-kit: Plate FloatingToolbar plugin，只读模式下选区浮动工具栏（阶段二 V2 Phase 2）
 * - comment-kit: Plate CommentPlugin + CommentLeaf，inline comment mark 渲染（阶段二 V2 Phase 3）
 * - cursor-overlay-kit: Plate CursorOverlayPlugin，rail 获焦时维持选区高亮（G1 选区保持）
 *
 * 注意：ReaderRecordPlateSurface 不再使用 ReaderPlateKit，改用 ReaderRecordPlateKit。
 * ReaderPlateKit 保留给其他可能仍依赖 leaf plugin 组件渲染的 surface。
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

/**
 * Reader Record Plate Kit — ReaderRecordPlateSurface 专用 plugin 列表
 *
 * 与 ReaderPlateKit 的区别：
 * - 不注册 ReaderLeafKit（vocabulary / grammar / user_highlight / user_note
 *   leaf plugin）。这些 mark 的视觉和交互全部由 ReaderRecordPlateSurface
 *   的 renderLeaf 在外层 span 统一承载，避免嵌套 mark-hit wrapper 干扰
 *   浏览器原生 selection 落点。
 * - 不注册 FloatingToolbarKit。Reader 选区工具栏改由
 *   ReaderRecordPlateSurface 内部通过 useReaderFloatingLayer +
 *   ReaderFloatingSurface + ReaderFloatingToolbarButtons 渲染，以
 *   activeSelection（SelectionAnchorBridge 产出）为唯一显示与定位真相，
 *   不再依赖 Plate editor.selection（readonly 下与原生 selection 不可靠同步）。
 *   保留 MarkdownKit / ReaderBlocksKit / CommentKit / CursorOverlayKit。
 */
export const ReaderRecordPlateKit = [
  ...MarkdownKit,
  ...ReaderBlocksKit,
  ...CommentKit,
  ...CursorOverlayKit,
];
