# Claread Web Reader Component System

> Reader 专项规范。全站功能页组件库、token、theme、目录结构和第三方准入流程请先参考 `component-library-v0.md`。本文只保留 Reader 画布、工具层、锚点和交互特例。

> **状态**: `CURRENT` | **最后更新**: 2026-05-19
> 本文把 `apps/web/PRODUCT.md`、`apps/web/DESIGN.md` 和 `reader-ia.md` 中已经确认的方向落成 Reader 组件使用规范。方向探索和过程判断已吸收到正式文档，不再在这里重复保留。

## 1. Scope

第一阶段只覆盖 Web Reader 和 Reader 可复用组件：

- 原文画布、句子、段落、译文。
- 机器标注：`vocab_highlight`、`phrase_gloss`、`context_gloss`、`grammar_note`、`term_note`、`logic_note`。
- 用户批注：句子级 highlight / note、单句内 `text_range`，以及跨句/跨段 `multi_text` highlight / note / favorite。
- 画布左侧词典详情层、浮动上下文/阅读设置面板、右侧 AI 工作区 shell。
- `grammar_note` / `sentence_analysis` 句后卡。

暂不覆盖：

- 完整 landing、公开每日精读、Library/Vocabulary 全量组件。
- Grammar X-Ray。当前 `grammar_note` 和 `sentence_analysis` 不得被命名或渲染为 X-Ray。

当前 Reader 2.0 已确认采用 `Plate + readOnly` 作为 Web 文档 runtime 的目标方向，但本文不展开 Plate 的实施细节；具体映射、桥接层与迁移方案以对应架构文档为准。

## 2. Product Rules

1. **Reader 是品牌现场。** UI 细节优先服务原文阅读、锚点理解和可沉淀批注。
2. **原文画布优先。** 词典、语法、句子拆解、用户笔记都从正文锚点触发，并优先回到正文附近。
3. **Reader 是 Canvas Workspace，不是三栏后台。** 正文核心区居中，左右空白区按需承载工具层。
4. **左侧是详情展示区，不是应用侧栏。** 词典、术语、短语等偏确定性的详情进入画布左侧工具层；默认按需展开，宽屏可钉住。
5. **右侧不是批注仓库。** 右侧只保留 AI chatbox 和后续 AI 能力落地区，围绕当前句子、选区或全文上下文。
6. **用户批注和机器标注分层。** 机器标注解释文本，用户批注表达个人资产；两者可以叠加，但视觉语言必须不同。
7. **颜色必须语义化。** 不为了装饰新增随机颜色。
8. **选区能力按 anchor 明确分层。** Web 可操作句子、单句内 `text_range` 和跨句/跨段 `multi_text`；小程序当前以展示和回跳 Web 资产为主，不必复刻同等创建交互。

### Reader Page Alignment

当前 Reader 的页面对齐基线：

- **默认状态是一整张原文画布。** 正文、译文、句后解析卡和用户批注位于中心核心区；左侧词典详情层和右侧 AI 工作区都是画布边缘工具层，不是固定三栏。
- **正文版心是硬约束。** 英文正文文本列保持 65-75ch；宽屏外层内容容器可放宽到约 96ch 来容纳段落编号、句后卡和工具避让。任何左侧词典层、右侧 AI 区、浮层或句后卡的调整都不能把正文压成后台内容栏，也不能让常驻视窗覆盖核心区。
- **解释回到原文。** `grammar_note`、`sentence_analysis`、用户 note/highlight 都优先挂在相关句子下方或边缘；右侧不得成为解释列表、批注仓库或 AI transcript 中心。
- **轻浮层只做轻释义。** 原文附近 `ReaderLookupPreview` 只展示 surface form、类型、一个简短中文释义和必要提示，并支持再次点击同词、关闭按钮、点击正文或 `Esc` 收起；完整释义、例句、短语、消歧义、加入生词本只放 `DictionaryPanel`。
- **Slot 先于组件。** 新增常驻或可钉住视窗前必须先声明 slot：画布左侧工具层、画布右侧工具层、正文内卡片、核心区短时浮层或移动端 bottom sheet，并检查与词典、AI、上下文面板和底部导航的冲突。
- **用户界面不用 raw schema 文案。** 组件内部可以消费 `entryType`、`annotation_type` 等字段，但用户可见标签应是“语法旁注”“句子拆解”“词汇”“短语”“语境”等自然文案；`entryType: grammar_note` 这类调试字段只允许出现在开发诊断模式。
- **不可用能力不能占据主视觉。** AI 对话、导出、Grammar X-Ray 等未接入能力可以保留入口或占位，但默认视觉权重必须低，且不能挤压当前可用的阅读、词典、标注和句子卡。
- **可访问性是组件规范的一部分。** 句子可键盘聚焦；可点击查词 token 后续应提供键盘路径；色点、图标按钮和面板切换目标应达到 40-44px 的可触控尺寸或有等价键盘/菜单入口。

### Confirmed Infrastructure

当前 Web Reader 先固定三层基础设施，再继续做组件扩展：

1. **Claread Paper theme.** 现有暖纸、墨色、`lens-blue` 和语义标注色是 Claread 的主视觉基础。Vintage Paper 等 shadcn theme 只作为 moodboard，不直接套用。后续正式初始化 shadcn/ui 时，应把现有 token 映射到 shadcn semantic tokens，而不是用第三方主题覆盖 Claread 视觉。
2. **Reader Floating Layer.** 所有锚定原文 token、句子或 DOM selection 的短时浮层统一走 Floating UI。Radix / shadcn Popover 继续用于按钮触发的常规菜单；原文画布上的轻释义、选区工具栏、语法 hover、二级操作菜单归入 Reader floating layer。
3. **Annotation Anchor Model.** 当前已支持句子级批注、单句内 `text_range` 和跨句/跨段 `multi_text`。Reader DOM 持续输出 `data-paragraph-id`、`data-sentence-id` 和原文 selection 锚点；后端已按 UTF-16 offset、hash、render scene 切片和 sentence 顺序做严格校验。后续重点是资产跳转强调和跨文章资产索引。
4. **Plate Runtime as Web Projection.** Reader 2.0 将逐步迁移到 `canonical render_scene -> Plate document` 的投影模式；后端 canonical render scene 与小程序消费链路保持不变，Plate 只作为 Web projection/document runtime。

## 3. Stack Rules

当前 Reader 依赖的基础设施：

- Tailwind CSS v4 和 Claread Web token
- Radix primitives
- Floating UI
- Motion
- lucide-react
- TanStack Query
- Zustand

当前 Reader 组件组织方式：

- Reader 专属组件放在 `apps/web/src/components/reader/`。
- Reader 2.0 允许引入 Plate 作为 Web 文档 runtime，不再以“禁止编辑器底座”作为规则。
- Ask Claread 的 compatibility layer 仍在 `apps/web/src/components/ui/`，但它不是 Reader 或功能页新的通用入口。
- 通用能力优先从 `primitives/`、`composed/`、`layout/` 取，Reader 只保留画布和锚点特例。
- 新增通用 Reader 基础设施先放在 `apps/web/src/components/reader/`，等交互稳定后再决定是否上移到 `packages/`。
- 与 Plate 相关的 projection、node types、selection bridge、jump bridge、lookup bridge、asset bridge、Ask bridge 应作为 Reader 2.0 专属模块独立组织，不继续堆进 `ReaderWorkbench.tsx`。

后续推荐正式化的 shadcn 基础组件：

| 场景 | shadcn/Radix 基础 |
| --- | --- |
| 图标按钮、普通按钮 | `Button` |
| 词典轻浮层、标注详情 | `Popover` / Floating UI |
| 工具提示 | `Tooltip` |
| 移动端 Reader 面板 | `Sheet` / `Drawer` |
| 阅读模式、标注密度 | `ToggleGroup` |
| 字号、行距 | `Slider` 或 segmented controls |
| 长面板滚动 | `ScrollArea` |
| 空态 | `Empty` |
| 保存/失败反馈 | `sonner` 或局部 inline status |

## 4. Design Tokens

使用已有 Tailwind token，不在组件内散落 raw hex。

| Token | 用途 |
| --- | --- |
| `web-canvas` | 页面外层背景 |
| `reader-paper` | Reader 纸面、轻量按钮、阅读设置背景 |
| `surface` | 面板表面、字典详情底 |
| `surface-warm` | Reader 内浮层、note slip、解释卡 |
| `ink` / `ink-soft` | 正文和主要文字 |
| `muted` / `subtle` | 元信息、次级说明、inactive 状态 |
| `hairline` | 边框、分隔、句后卡结构线 |
| `lens-blue` | CTA、焦点、当前激活、品牌记忆点 |
| `lens-blue-soft` | 当前激活浅底、提示底 |
| `vocab-amber` | 词汇、用户暖黄批注、荧光笔痕迹 |
| `phrase-lavender` | 短语/搭配 |
| `context-blue` | 语境、回源 |
| `grammar-violet` | 语法说明、句法关系 |
| `structure-green` | 句子结构、段落逻辑、完成状态 |
| `error-red` | 错误和风险 |

### Token Constraints

- `lens-blue` 不用于普通按钮海洋。
- Reader 正文不使用大面积纯白后台感；默认保留纸面。
- 用户批注高亮比机器标注更宽、更软，但不压过英文正文。
- `grammar-violet` 默认用 dotted underline 或细边框，不做整句紫色背景。

## 5. Component Layers

### Layer A: Reader Layout

| Component | Role |
| --- | --- |
| `ReaderShell` | 中心原文画布优先的 Canvas Workspace；正文核心区稳定，左右画布空白区按需承载工具层，三栏不是默认形态 |
| `ReaderCanvas` | 文章面板、标题、状态提示、段落列表 |
| `ReaderParagraph` | 段落编号、段落内句子栈、段落结构线 |
| `ReaderControls` | 顶部显示模式、收藏、Aa、更多操作 |

### Layer B: Text and Marks

| Component | Role |
| --- | --- |
| `ReaderSentence` | 单句焦点、译文、机器标注、用户批注、句后卡挂载 |
| `InlineMarkToken` | 机器标注 token，处理语义样式、点击查词和 active state |
| `PlainLookupToken` | 已弱化为句子级点击定位逻辑，避免逐词 DOM 节点破坏浏览器选区 |
| `ReaderLookupPreview` | 原文附近轻释义，只做即时反馈 |
| `MarkLegend` | 标注类型/密度说明和开关 |
| `ReaderFloatingLayer` | Floating UI 统一封装，用于原文锚点浮层和后续 selection toolbar |

### Layer C: User Annotations

| Component | Role |
| --- | --- |
| `AnnotationGutter` | 句子边缘 marker，显示本句有高亮/笔记 |
| `AnnotationSlip` | 句后用户笔记纸条 |
| `AnnotationColorSwatch` | 高亮颜色选择 |
| `SelectionToolbar` | sentence / `text_range` / `multi_text` 选区工具栏，使用 Floating UI virtual element 和 live DOM Range |
| `reader-anchors` | Reader DOM 锚点属性生成器；先输出句子和句内 text range 属性 |

### Layer D: Explanation Cards

| Component | Role |
| --- | --- |
| `SentenceEntrySummary` | 未展开时的句后 chips |
| `GrammarNoteCard` | `grammar_note` 局部语法说明 |
| `SentenceAnalysisCard` | `sentence_analysis` 长难句拆解 |
| `TermNoteCard` / `LogicNoteCard` | Academic 增强模式后续扩展 |

### Layer E: Workbench Panels

| Component | Role |
| --- | --- |
| `DictionaryPanel` | 画布左侧词典详情层，完整词条、歧义选择、未收录、加入生词本、本次查词；默认按需展开，宽屏可钉住 |
| `ReaderContextPanel` | 核心区短时浮层：句子操作或阅读设置，不作为常驻侧栏 |
| `ReaderSettingsPanel` | 字号、行距、背景、译文、标注密度 |
| `AiWorkspacePanel` | 画布右侧 AI 工作区 shell，默认收起，可展开为 chatbox |

## 6. Annotation Visual Rules

### Machine Marks

| Type | Default Visual | Active Visual |
| --- | --- | --- |
| `vocab_highlight` | amber inline highlight on original English text only | stronger amber highlight + lightweight lookup preview |
| `phrase_gloss` | purple/lavender inline highlight, visually distinct from grammar underline | stronger purple highlight + phrase label in lightweight preview |
| `context_gloss` | context-blue inline highlight on original English text only | stronger context-blue highlight + current-context meaning preview |
| `grammar_note` | grammar-violet low highlight / subtle underline, no heavy fill | opens `GrammarNoteCard` |
| `term_note` | structure-green low underline | opens term card |
| `logic_note` | lens-blue / structure-green low underline | opens logic card |

### User Annotations

| Type | Default Visual | Active Visual |
| --- | --- | --- |
| sentence highlight | wide translucent highlighter behind the sentence text | stronger edge marker + subtle sentence background |
| text range highlight | inline paper marker on selected text | toolbar反显颜色，可取消或更新 |
| note | gutter marker + sentence-side paper slip with scope label (`整句` / `局部选区` / `跨句选区`) | slip lift + current sentence dot |
| favorite | small bookmark marker near sentence or header | warm amber icon fill |

Rules:

- User note text appears as `AnnotationSlip`, not as a right-side list by default.
- Gutter markers should sit outside the reading text flow and must not shrink the 65-75ch line length.
- Vocabulary marks are rendered only on the original English text. The current backend does not provide original-to-translation word alignment, so translated Chinese text must not mirror the same per-word colors.
- The lightweight follow-card near the original text should contain only fast context: surface form, type label, one short Chinese meaning, and optional reason. Full meanings, examples, phrases, disambiguation, and vocabulary-save controls belong in `DictionaryPanel`.
- When a sentence has both machine marks and user highlight, machine highlights remain distinguishable above the softer sentence-level user highlighter.
- Do not use color alone: include marker shape, icon, label, or placement.

## 7. Interaction Rules

### Inline Lookup

1. Click inline mark or plain word.
2. `ReaderLookupPreview` appears near original text.
3. `DictionaryPanel` opens in the left canvas tool layer and receives full lookup state.
4. The lightweight preview floats over the reader surface and must not change sentence line-height or paragraph spacing.
5. The lightweight preview can be dismissed without clearing the left dictionary detail.
6. History chips update only for current session.

### Sentence Action

1. Click or keyboard-focus sentence.
2. Sentence gets subtle active background and side marker.
3. `ReaderContextPanel` switches to sentence action.
4. Saving highlight/note writes sentence-level `user_annotations`.
5. Saved note appears back in `AnnotationSlip`.

### Reading Settings

1. Click `Aa`.
2. `ReaderContextPanel` switches to settings.
3. Settings are local UI state first; persistence can be added later.

### Text Range Selection

`SelectionToolbar` is enabled for sentence, single-sentence `text_range`, and cross-sentence `multi_text` selections. It must use:

- DOM `Selection` / `Range`
- `data-paragraph-id`, `data-sentence-id`, `data-start`, `data-end`
- Floating UI virtual element with `range.getBoundingClientRect()` and `range.getClientRects()`
- backend `anchor_type="text_range"`, `start_offset`, `end_offset`, and `text_hash`
- backend `anchor_type="multi_text"` with `payload_json.segments[]` for cross-sentence persistence

It must not force ordinary words into separate interactive DOM nodes. Plain text should remain selectable as continuous text; click-to-lookup can be implemented from sentence-level hit testing.
If a manual DOM selection exactly covers the full sentence text, the client should normalize it to a sentence anchor instead of persisting a fake full-length `text_range`.
If a manual DOM selection crosses sentence or paragraph boundaries, the client should preserve it as `multi_text` and expose the mode explicitly in the toolbar instead of silently collapsing it into a sentence or partial `text_range`.
When `/library/assets` or miniprogram excerpts jump into a saved asset, Reader should use a short-lived route focus layer for favorites, annotations, and mixed assets across sentence / `text_range` / `multi_text` anchors instead of reusing the live selection state.

## 8. File Ownership

Current extraction:

```text
apps/web/src/components/reader/
  AnnotationGutter.tsx
  AnnotationSlip.tsx
  ReaderFloatingLayer.tsx
  ReaderContextPanel.tsx
  ReaderCanvas.tsx
  ReaderSentenceRow.tsx
  ReaderAnnotationOverlay.tsx
  SelectionToolbar.tsx
  SentenceEntryCard.tsx
  AiWorkspacePanel.tsx
  reader-anchors.ts
  reader-entry-utils.ts
  reader-selection.ts
  index.ts
```

Keep in `ReaderWorkbench.tsx` during first pass:

- top-level state
- fetch/mutation functions
- maps derived from record data
- inline token rendering, lightweight lookup preview, dictionary detail panel, toolbar mutations, and integration between panels

Move later:

- Zustand store for Reader UI state
- persisted Reader preferences
- `DictionaryPanel`, `ReaderLookupPreview`, `InlineMarkToken`, entry card rendering, and toolbar mutation wiring once behavior stabilizes
- Reader 2.0 的 Plate projection、node components、selection bridge、lookup bridge、asset bridge、Ask bridge

## 9. Component Preview Policy

PNG component preview sheets are local review artifacts and are ignored by Git under `apps/web/docs/design/**/*.png`. Stable decisions must be reflected in implementation and in this document rather than depending on image files.

## 10. Verification

Each componentization batch must pass:

```powershell
pnpm --filter=@claread/web lint
pnpm --filter=@claread/web typecheck
pnpm --filter=@claread/web build
```

For visual changes, also verify in browser:

- `/reader/[recordId]` desktop at a wide viewport.
- Reader with dictionary open.
- Reader with sentence note/highlight.
- Reader with `grammar_note` and `sentence_analysis` visible.
- AI workspace collapsed and expanded.
