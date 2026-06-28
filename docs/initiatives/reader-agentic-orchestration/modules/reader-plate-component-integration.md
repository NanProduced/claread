# Reader Plate Component Integration

> 状态：`2026-06-28 code-aligned status`
> 最后更新：2026-06-28
> 范围：当前 Web Reader Record 解析页与 Plate.js / @platejs packages 的真实接入状态。本文只记录代码事实和产品接入边界，不替代 UI 设计稿。

## 结论

`/app/reader-record/{recordId}` 的默认解析页已经回到真实 `<Plate readOnly>` 文档表面，不再是完全手写 block renderer。当前差距不是"没有接 Plate"，而是"Plate 已接入 selection、toolbar、comment mark、Markdown children、reader blocks/leaves；文档视觉、移动端密度和部分 Plate 官方组件体验仍需继续产品化"。

当前应把 Plate 接入分成三档：

| 档位 | 含义 | 当前项 |
|---|---|---|
| 真实接入 | 页面运行路径会 import、注册并由用户动作触发 | `platejs/react`、Reader custom element/leaf plugins、`@platejs/markdown`、`@platejs/floating`、`@platejs/comment` 的最小路径 |
| 部分接入 | 包或 plugin 已使用，但产品能力仍由 Claread domain state、API 或浮层补齐 | `@platejs/comment`、`@platejs/selection`、FloatingToolbar actions |
| 未接入 | 依赖存在但源码没有实际 import 或未进入产品路径 | `@platejs/ai`、`@platejs/suggestion` |

## 当前默认页面路径

- `/app/reader-record/{recordId}` 默认 `surfaceMode = "plate"`，渲染 `ReaderRecordPlateSurface`。
- `ReaderRecordPlateSurface` 使用 `usePlateEditor({ plugins: [...ReaderPlateKit], value })` 创建 editor，并用 `<Plate editor={editor} readOnly>` 包裹中心文档。
- snapshot 变化时通过 `editor.tf.setValue(plateValue)` 做 full reload。`projection_ops` incremental applier 仍未端到端启用。
- Workbench fallback 仍保留；旧 `/app/reader/{recordId}` 也仍存在。

## Package / Component Matrix

| 依赖或组件 | 当前状态 | 代码落点 | 说明 |
|---|---|---|---|
| `platejs` / `platejs/react` | 真实接入 | `ReaderRecordPlateSurface.tsx`、`ReaderPlateSnapshotSurface.tsx`、`PlateReaderSurface.tsx`、`ImmersiveReaderSurface.tsx` | 默认 Reading Record 页面使用 `Plate`、`usePlateEditor`、`Editor`、`EditorContainer`。 |
| `ReaderPlateKit` | 真实接入 | `apps/web/src/components/editor/plugins/reader-plate-kit.ts` | 聚合 Markdown、reader blocks、reader leaves、floating toolbar、comment、cursor overlay。 |
| Reader block plugins | 真实接入 | `reader-blocks-kit.tsx` | `reader_paragraph`、`reader_blockquote`、`reader_callout`、`reader_sentence_analysis` 和 Markdown 基础 element/leaf 通过 `createPlatePlugin` 注册 component。 |
| Reader leaf plugins | 真实接入但视觉仍需打磨 | `reader-leaf-kit.tsx` | vocabulary / grammar / user highlight / user note 通过 leaf plugin 渲染。用户 highlight 已消费 `warm_yellow` / `soft_blue` / `soft_rose` color token。 |
| `@platejs/markdown` | 真实接入但范围有限 | `markdown-kit.ts`、`markdown/deserialize.ts` | 用于 grammar / sentence_analysis / supplement callout 内容的 Markdown deserialize。当前不用于 Stable source 原文主文档结构。 |
| Markdown element/leaf plugins | 真实接入 | `reader-blocks-kit.tsx` | Markdown deserialize 产物通过 Plate children 渲染；旧 `CalloutMarkdownRenderer` 已删除。 |
| `@platejs/floating` | 真实接入 | `ui/floating-toolbar.tsx`、`floating-toolbar-kit.tsx` | FloatingToolbar 使用 `useFloatingToolbar` / `useFloatingToolbarState`，由 Plate selection 管理显示；新版 surface 不再渲染旧 `SelectionActionStrip`。 |
| Floating toolbar buttons | 真实接入 | `reader-floating-toolbar-buttons.tsx` | 按钮通过 Plate toolbar primitives + Context 调用 Claread actions：Lookup、Copy、Ask、Highlight、Note。 |
| `@platejs/comment` | 部分接入 | `comment-kit.tsx`、`ui/comment-node.tsx`、`InlineCommentPanel.tsx` | CommentPlugin / CommentLeaf / draft mark 已接上，笔记写入仍走 `reader_notes` API；未接 DiscussionKit，线程/面板是 Claread 自定义 `InlineCommentPanel`。 |
| `@platejs/selection` | 部分接入 | `cursor-overlay-kit.tsx`、`ui/cursor-overlay.tsx` | CursorOverlayPlugin 已注册，用于 rail/toolbar 获焦后的选区 overlay。domain anchor 读取主要由 `SelectionAnchorBridge` 通过 `platejs/react` 的 `useEditorSelection` 完成。 |
| `SelectionAnchorBridge` | 真实接入 | `SelectionAnchorBridge.tsx` | 在 `<Plate>` 内订阅 editor selection，转成 Reading Record anchor draft。jsdom 下仍有 DOM selection fallback。 |
| `@platejs/ai` | 未接入 | `apps/web/package.json` only | 源码无 import。Ask 继续走 Claread `AiWorkspacePanel` + reader-ask BFF，不走 Plate AI plugin。 |
| `@platejs/suggestion` | 未接入 | `apps/web/package.json` only | 源码无 import。第一版不做 AI suggestion / revision。 |

## 当前弱接入 / 虚假接入风险

- 安装 `@platejs/ai` / `@platejs/suggestion` 不等于产品接入；当前没有源码 import。
- CommentKit 已接入 mark 和 activeId，但不是 Plate 官方完整 discussion workflow；持久化、面板和 action 仍是 Claread 自定义。
- `@platejs/selection` 当前主要用于 cursor overlay；block selection、grammar chunk underline、Structure Lens decorations 还没做。
- `reader_callout` 和 `reader_sentence_analysis` 已走 Plate children，但不是官方 `@plate/callout-node` registry 的完整形态。
- `sentence_analysis.chunks` 已进入专用 Plate elements 并渲染 chunk rows，但 source decorations / 双向 hover 联动仍未完成。
- 非原文 selection 当前只开放 Copy / Ask；Highlight / Note / Lookup 仍需后续 anchor-set contract 扩展后再开放。

## 下一轮 UI/UX 对接焦点

1. 中心内容区要按 Plate editors demo 的文档阅读体验重构：正文、标题、列表、引用、callout 都应是 editor document 的自然 block，而不是外层卡片堆叠。
2. `sentence_analysis` 已确定走精读模式常显的 Plate-native structure block，不做默认折叠 toggle；下一步实现 chunk rows 优先、Markdown analysis 在下、source decorations best-effort 的专用 block。
3. 建立统一 mark visual resolver，继续打磨 vocabulary / grammar / user highlight / comment / selection 叠色。
4. 移动端收敛为统一 `ReaderMobileActionSheet`：Dictionary 已有 compact bottom panel，但 Ask 和 Note 仍是独立 surface，需要共用 pinned Plate selection / anchor。
6. 明确哪些 Plate 包短期不接：AI、suggestion、block selection、table/media/code editing controls 不应被包依赖误导。
