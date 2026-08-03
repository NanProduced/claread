# Reader Plate Component Integration

> 状态：`2026-07-01 header/control-strip and group-native translation projection frozen; code-aligned status`
> 最后更新：2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：标注 cutover 后页面统一为 `/app/reader/[recordId]`，正文历史 `/app/reader-record/{recordId}` 路径按 cutover 合同替换）
> 范围：当前 Web Reader Record 解析页与 Plate.js / @platejs packages 的真实接入状态。本文只记录代码事实和产品接入边界，不替代 UI 设计稿。

> **Cutover 注意（2026-08-03）**：Architectural Cutover Complete 后，Web 用户页面统一为 `/app/read` 与 `/app/reader/[recordId]`，Web BFF 统一为 `/api/web/reader/records/*` 与 `/api/web/reader/source-artifacts/*`。本文正文历史段保留 `/app/reader-record/{recordId}` 等 cutover 前 URL 作为历史决策证据；当前产品入口以 cutover 后合同为准。

## 结论

`/app/reader/[recordId]`（cutover 前 `/app/reader-record/{recordId}`）的默认解析页已经回到真实 `<Plate readOnly>` 文档表面，不再是完全手写 block renderer。Header / control strip 已完成旧版 editorial masthead 骨架回归并冻结；group-native translation 已接入 Web projection，当前阅读节奏是 `source group paragraph -> translation blockquote -> annotations`。当前差距不是"没有接 Plate"，而是"Plate 已接入 selection、toolbar、comment mark、Markdown children、reader blocks/leaves；移动端密度、mark 叠色、source decorations 和部分 Plate 官方组件体验仍需继续产品化"。

当前应把 Plate 接入分成三档：

| 档位 | 含义 | 当前项 |
|---|---|---|
| 真实接入 | 页面运行路径会 import、注册并由用户动作触发 | `platejs/react`、Reader custom element/leaf plugins、`@platejs/markdown`、`@platejs/floating`、`@platejs/comment` 的最小路径 |
| 部分接入 | 包或 plugin 已使用，但产品能力仍由 Claread domain state、API 或浮层补齐 | `@platejs/comment`、`@platejs/selection`、FloatingToolbar actions |
| 未接入 | 依赖存在但源码没有实际 import 或未进入产品路径 | `@platejs/ai`、`@platejs/suggestion` |

## 当前默认页面路径

- `/app/reader/[recordId]`（cutover 前 `/app/reader-record/{recordId}`）默认 `surfaceMode = "plate"`，渲染 `ReaderRecordPlateSurface`。
- `ReaderRecordPlateSurface` 使用 `usePlateEditor({ plugins: [...ReaderPlateKit], value })` 创建 editor，并用 `<Plate editor={editor} readOnly>` 包裹中心文档。
- `ReaderRecordHeader` 已冻结为独立 editorial column：Header 使用 `max-w-[82ch]`，正文 Plate document 继续使用阅读列宽。Header 由 eyebrow、中文 masthead、hairline action bar 和底部 metadata 四区组成。
- Header 标题成功态只提升 `snapshot.record.display_title_zh`；`pending` / `failed_retryable` 用中文占位，旧 snapshot 仅在 `title_generation_status` 缺失时允许 `record.title` migration fallback。底部 metadata 使用稳定源文本 word count，不回退到 sentence count 或估算分钟。
- Group-native translation projection 使用 backend `reader_translation_group`。Web projection 根据 `covered_anchor_segment_ids` 合并 source paragraph，保留 separator leaf，并在译文之后输出 grammar / sentence_analysis / supplement annotations。非法 group defensive skip，不再 append 到 unit 末尾。
- snapshot 变化时通过 `editor.tf.setValue(plateValue)` 做 full reload。`projection_ops` incremental applier 仍未端到端启用。
- Cutover 后：旧 `ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface` 已物理删除，不再有 Workbench fallback；`/app/reader/{recordId}` 与 `/app/reader/[recordId]` 是同一运行时动态路由，cutover 替换的是页面实现而不是 URL。

## Package / Component Matrix

| 依赖或组件 | 当前状态 | 代码落点 | 说明 |
|---|---|---|---|
| `platejs` / `platejs/react` | 真实接入 | `ReaderRecordPlateSurface.tsx`、`PlateReaderSurface.tsx`、`ImmersiveReaderSurface.tsx` | 默认 Reading Record 页面使用 `Plate`、`usePlateEditor`、`Editor`、`EditorContainer`。旧 `ReaderPlateSnapshotSurface.tsx` 已在 cutover 中物理删除。 |
| `ReaderPlateKit` | 真实接入 | `apps/web/src/components/editor/plugins/reader-plate-kit.ts` | 聚合 Markdown、reader blocks、reader leaves、floating toolbar、comment、cursor overlay。 |
| Reader block plugins | 真实接入 | `reader-blocks-kit.tsx` | `reader_paragraph`、`reader_blockquote`、`reader_callout`、`reader_sentence_analysis` 和 Markdown 基础 element/leaf 通过 `createPlatePlugin` 注册 component。 |
| Reader leaf plugins | 真实接入但视觉仍需打磨 | `reader-leaf-kit.tsx` | vocabulary / grammar / user highlight / user note 通过 leaf plugin 渲染。用户 highlight 已消费 `warm_yellow` / `soft_blue` / `soft_rose` color token。 |
| `@platejs/markdown` | 真实接入但范围有限 | `markdown-kit.ts`、`markdown/deserialize.ts` | 用于 grammar / sentence_analysis / supplement callout 内容的 Markdown deserialize。当前不用于 Stable source 原文主文档结构。 |
| Markdown element/leaf plugins | 真实接入 | `reader-blocks-kit.tsx` | Markdown deserialize 产物通过 Plate children 渲染；旧 `CalloutMarkdownRenderer` 已删除。 |
| Reader Record Header / control strip | 已冻结 | `ReaderRecordPlateSurface.tsx` | Header 与正文列宽解耦，使用旧版 editorial masthead + hairline action bar。右侧 cell 为收藏、精读、沉浸、阅读设置；不再使用 pill segmented control。 |
| Group-native translation projection | 已冻结为当前 baseline | `reader-record-plate-document.ts`、`reader-record-plate-to-plate-value.ts`、`reader-record-active-anchor.ts` | `reader_translation_group` 渲染为合并 source paragraph + translation blockquote；非首 anchor 的 active anchor / lookup context 由 leaf metadata 回源。 |
| `@platejs/floating` | 真实接入 | `ui/floating-toolbar.tsx`、`floating-toolbar-kit.tsx` | FloatingToolbar 使用 `useFloatingToolbar` / `useFloatingToolbarState`，由 Plate selection 管理显示；新版 surface 不再渲染旧 `SelectionActionStrip`。 |
| Floating toolbar buttons | 真实接入 | `reader-floating-toolbar-buttons.tsx`、`ui/ai-menu.tsx` | 按钮通过 Plate toolbar primitives + Context 调用 Claread actions：Ask、Lookup、Copy、Highlight、Note。Ask 使用 Claread 自定义 menu 和 `AiWorkspacePanel`，不使用 Plate AI plugin。 |
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
- Group paragraph 的 `data.anchorSegmentId` 仍是 primary anchor；真实 selection、mark 和 lookup 必须依赖 leaf-level anchor metadata，不能退回 paragraph primary anchor。

## 下一轮 UI/UX 对接焦点

1. Header / control strip 进入冻结期；后续只修数据状态、响应式溢出、可访问性和真实来源展示 bug，不再重排骨架。
2. 建立视觉基线截图：desktop/mobile、精读/沉浸、selection toolbar、Quick Peek、note/highlight、sentence_analysis。
3. 中心内容区继续按 Plate editors demo 的文档阅读体验收口：正文、列表、引用、callout 和 sentence-analysis 都应像 editor document 的自然 block，而不是外层卡片堆叠。
4. `sentence_analysis` 已确定走精读模式常显的 Plate-native structure block；下一步优先做 source decorations、双向 hover/active anchor，而不是再调整 Header 或 translation grouping。
5. 建立统一 mark visual resolver，继续打磨 vocabulary / grammar / user highlight / comment / selection 叠色。
6. 移动端收敛为统一 `ReaderMobileActionSheet`：Dictionary 已有 compact bottom panel，但 Ask 和 Note 仍是独立 surface，需要共用 pinned Plate selection / anchor。
7. 明确哪些 Plate 包短期不接：AI、suggestion、block selection、table/media/code editing controls 不应被包依赖误导。
