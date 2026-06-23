# Reader Record Plate Surface UI

> 状态：目标方案草案
> 最后更新：2026-06-23
> 范围：`/app/reader-record/{recordId}` 在 Agentic Orchestration 架构下的 Reader Record 解析页 UI/UX、Plate.js 文档表面、选择交互、词典/Ask 联动、用户高亮/笔记和第一版实现边界。

## 目标

Reader Record 页面使用 Plate.js 作为中心阅读文档的交互底座。页面仍是 Claread Reader，不是通用富文本编辑器。

目标体验：

- 正文优先，用户第一眼看到的是一篇可阅读的文章。
- 精读时完整暴露 AI 解析层，但以文档 marks / cues / floating explanation 呈现，不再使用旧式解析卡片。
- 沉浸时减少干扰，只保留真正影响理解的提示。
- 划词、查词、Ask、评论、笔记、高亮都基于同一个 Plate selection / domain anchor。
- 左侧词典和右侧 Ask rail 保留，但不再自己主导正文选区。
- 用户资产继续写入现有 `user_annotations` / `reader_notes`，Plate comment/discussion 只是 Web projection。

非目标：

- 不把整页 shell、词典 rail 或 Ask rail 都改成 Plate document。
- 不显示 Plate Playground 式 fixed toolbar。
- 不让用户编辑 Stable Base 原文。
- 不持久化 raw Plate path、raw Slate path 或 raw Slate operation。
- 第一版不做 AI suggestion / revision。
- 第一版不新建 comment backend。

## 当前问题

当前 `/app/reader-record/{recordId}` 已接入 `ReaderPlateSnapshot`，但中心 UI 仍是旧 Workbench 映射。

主要问题：

- 新 snapshot 先转成旧 `ReaderVm`，再走旧 Reader surface，导致旧 UI 假设继续主导体验。
- unit 级译文被映射到第一个 anchor segment 后面，造成原文和译文错位。
- 选择 toolbar 依赖 DOM selection 和旧 sentence DOM 标记，容易与行内标注、卡片、词典和 Ask 冲突。
- 旧 `ReaderMarkLeaf` 同时处理多类 mark、hover、focus、active、selection overlap、notes overlap 和 analysis linkage，交互状态过重。
- `grammar_note` / `sentence_analysis` 作为卡片/accordion 插入正文，打断阅读，并让正文选择与解析内容割裂。

## 设计原则

### Document First

中心区域是一篇文档，不是一组解析卡片。

原文是主层。译文、系统标注、结构分析、用户资产和 Ask supplement 都是围绕原文的文档层。

### Read-only, Not Static

主 Reader Record surface 使用 `Plate + readOnly`。

原因：

- 需要 floating toolbar。
- 需要 comment/discussion projection。
- 需要 Cursor Overlay 保持选区。
- 需要 marks/cues 的 hover、focus、active 联动。
- 需要 Structure Lens。
- 后续需要 Ask supplement / suggestion 的运行时能力。

`PlateView` 可用于分享页、导出预览、历史快照或轻交互阅读页，不作为主解析页默认方案。

官方 Plate 文档把静态渲染定位为纯展示 / SSR / RSC 场景；如果需要 comment popover、selection 等交互式只读能力，应使用浏览器端 `<Plate>`。因此主 Reader Record surface 使用 `Plate + readOnly`，而不是静态视图。

### Domain-first

Plate document 是 Web projection。

持久事实仍是：

- Stable Reading Base
- Reading Units
- Anchor Segments
- Enhancement Layers
- User Editorial Assets
- Ask Supplements

Plate path 只能作为瞬时渲染地址。用户资产、AI layer 和 Ask supplement 必须回写 domain anchor。

### Gate Before Write

旧 `user_annotations` / `reader_notes` 表可以继续作为 V1c 存储，但旧 `render_scene` 校验不能继续作为新 Reader Record 写入 gate。

新页面启用写入前，必须具备 Reading Record anchor gate：

- 能确认 `recordId` 属于新的 Reading Record。
- 能从 Stable Reading Base / Anchor Segment 校验 `text_range` / `multi_text`。
- 能把旧 `sentence_id` 兼容字段解释为 anchor segment alias，而不是旧 render scene sentence。
- 能返回足够错误信息，区分 anchor stale、hash mismatch、range invalid 和 record mismatch。

如果 gate 不存在，Comment/Note 和 Highlight 必须保持 disabled / coming soon。不能为了让按钮可用而把新 `recordId` 塞进旧 `analysis_results.render_scene_json` 校验路径。

### One Selection Source

中心 Plate surface 是选区和 anchor 的唯一来源。

词典、Ask、评论、笔记、高亮都读取 Plate selection 或 active mark/cue，再转换为 domain anchor。

## Plate.js API / Plugin 选型

选型依据：

- Plate MCP 当前项目 registry 已配置 `@platejs`，可用 registry UI 包括 `floating-toolbar`、`comment-node`、`block-discussion`、`cursor-overlay`、`highlight-node` 和 `editor`。
- [Plate Static Rendering](https://platejs.org/docs/static) 明确静态渲染适合非交互只读内容；交互式只读能力使用标准 `<Plate>`。
- [Plate Toolbar](https://platejs.org/docs/toolbar) 支持 fixed toolbar 和 selection-driven floating toolbar；本页只使用 floating toolbar 能力，不使用 fixed toolbar 或格式化按钮组。
- [Plate Cursor Overlay](https://platejs.org/docs/cursor-overlay) 支持 selection overlay，并提供保持 focus/selection 的交互约定。
- [Plate Comment](https://platejs.org/docs/comment) / [Discussion](https://platejs.org/docs/discussion) 可作为评论和 discussion UI projection，但 discussion plugin 本身是 UI state，不替代 Claread domain persistence。
- [Plate Basic Marks](https://platejs.org/docs/basic-marks) / [Highlight](https://platejs.org/docs/highlight) 适合表达文档 mark；Claread 系统解析层应通过自定义 leaf / decoration / overlay 投影为 reader marks/cues。
- [Plate Plugin Configuration](https://platejs.org/docs/plugin) 支持 leaf node、plugin handlers、plugin store 和 priority；Claread 应使用这些能力组织 reader-specific marks/cues，而不是把所有交互塞进一个 DOM component。

推荐使用：

| 需求 | Plate 能力 | Claread 约束 |
|---|---|---|
| 主文档只读交互 | `<Plate readOnly>` | 不允许编辑 Stable Base；仍允许 selection / hover / popover |
| 纯展示页 | `PlateStatic` / 当前项目的 `PlateView` 类静态投影 | 只用于分享、导出、历史快照 |
| 划词 toolbar | `floating-toolbar` registry UI 或等价自定义 floating layer | 只放 Claread action，不放编辑格式按钮 |
| 选区保持 | `CursorOverlayPlugin` / `cursor-overlay` | rail 获焦时保留 overlay；外部按钮使用 Plate focus preservation 约定 |
| 用户高亮 | `highlight-node` / custom leaf | 持久化仍是 `user_annotations` |
| 用户评论 | `comment-node` + `block-discussion` projection | 持久化仍是 `reader_notes`；thread id 派生自 note id |
| 系统 marks/cues | custom leaf plugins / decorations / overlays | 不持久化 Plate path；由 domain anchor 投影 |
| active 状态 | plugin store 或 React state adapter | source of truth 是 domain anchor draft，不是 Slate path |

不推荐使用：

- `FixedToolbarKit` / `FixedToolbarButtons`。
- Plate AI suggestion / revision 组件。
- Block drag/drop、block selection、insert menu、media/table/list editing controls。
- raw Plate path / Slate path 持久化。

实现上可以复用 Plate UI registry 代码，但这些组件必须被收敛为 Claread reader behavior。Plate UI 不是产品需求来源。

## 页面结构

桌面端保持三栏心智：

```text
Compact Reader Header
├── Dictionary Rail
├── Plate Document Surface
└── Ask Claread Rail
```

页面 shell：

- 顶部只保留 title、mode、状态、设置等紧凑信息。
- 不在正文前放大块系统状态卡。
- 增强进度使用 header chip、slim progress strip 和 layer activity indicator。

中心文档：

- 使用 `Plate + readOnly`。
- 不显示 fixed toolbar。
- 不显示编辑器格式按钮。
- 只展示 Claread reader marks、cues、selection 和 comment/highlight projection。

左侧词典：

- 保留为独立查询/保存工作台。
- 读取 Plate selection、clicked vocab mark 或 hover token。
- 获焦时不清除中心选区。
- 保存词汇或用户资产后，通过 domain projection 回到文档 mark。

右侧 Ask：

- 保留为独立对话和工具执行工作区。
- 读取 Plate selection、active grammar cue、active Structure Lens、active comment 或 active AI annotation。
- 获焦时不清除中心选区。
- Ask 回答默认留在 rail，不自动写入中心文档。

## Active Anchor State

Reader Record 需要一个显式 active anchor state，避免 Plate selection、系统 cue、用户 comment 和 rail focus 各自抢状态。

概念模型：

```ts
type ReaderActiveAnchorState = {
  source:
    | "none"
    | "selection"
    | "system_mark"
    | "system_cue"
    | "comment"
    | "user_highlight"
    | "rail";
  domainAnchorDraft?: ReaderAnchorPayload | null;
  activeMarkId?: string | null;
  activeCueId?: string | null;
  activeCommentId?: string | null;
  selectionText?: string | null;
  railFocus: "none" | "dictionary" | "ask" | "comment_composer";
  pinned: boolean;
};
```

规则：

- Plate selection 是最高优先级的 active source。用户正在划词时，toolbar 和 rails 都读取 selection 生成的 `domainAnchorDraft`。
- 点击系统 mark/cue 时，如果没有非折叠 selection，则 active source 变为 `system_mark` / `system_cue`。
- 点击用户 comment/highlight 时，如果没有非折叠 selection，则 active source 变为 `comment` / `user_highlight`。
- 打开 Dictionary / Ask / Comment composer 时，当前 anchor 进入 `pinned` 状态；rail 获焦不能清空中心 selection overlay。
- rail 内部继续操作时，source 可以显示为 `rail`，但必须保留被 pin 的 `domainAnchorDraft`。
- Escape 先关闭 toolbar / popover / floating legend；再解除 pinned；最后才清空 selection 或 active cue。
- 文档滚动、rail focus、popover hover 不应改变 domain anchor，只能改变 visual active state。

状态派生：

| UI 状态 | 来源 | 写入资格 |
|---|---|---|
| Lookup anchor | selection 或 clicked vocab/phrase/context mark | 只读，可 V1b 启用 |
| Copy anchor | selection | 只读，可 V1b 启用 |
| Ask anchor | selection、system cue、comment、highlight | D6-A3/A6 contract 稳定前禁用 |
| Highlight anchor | selection | V1c anchor gate 完成前禁用 |
| Comment anchor | selection、user highlight | V1c anchor gate 完成前禁用 |
| Grammar / Structure explanation anchor | system cue | 只读，可 V1a/V1d 启用 |

## Progressive Loading UX

旧页面的大块状态卡会把正文挤到页面下方。新 Plate surface 使用渐进加载状态，不在正文前插入大卡片。

状态层级：

| 状态层 | 位置 | 用途 |
|---|---|---|
| Header chip | compact header 右侧或模式栏附近 | 显示 snapshot/readability/processing 的总体状态 |
| Slim progress strip | header 下方 2-4px 条带 | 显示 translation、vocab、grammar、sentence_analysis 等 layer 的整体进度 |
| Layer activity indicator | 对应 mark/cue 或 unit margin 附近 | 显示某个 layer 正在生成、失败或可刷新 |
| Blocking error panel | 文档区域内 | 只用于正文 snapshot 完全不可读的 fatal error |

交互规则：

- 正文 snapshot 一旦可读，应立即显示正文；后续 enhancement layer 渐进出现。
- translation / grammar / sentence_analysis 未完成时，不占用大块预留空间。
- 精读模式可以在 header chip 中显示“解析生成中”；沉浸模式只显示最小状态。
- layer 局部失败时，在对应 cue 或 header chip 提供 retry / status，不把失败消息插入正文流。
- progress strip 必须有稳定高度，避免 CLS。
- loading 动效遵守 `prefers-reduced-motion`。

## 模式

### 沉浸模式

目标：连续阅读，低干扰。

默认显示：

- 原文。
- 真正影响理解的 `phrase_gloss` / `context_gloss`。
- 用户 highlight / comment indicator。

默认不显示：

- 普通 `vocab_highlight`。
- grammar cue。
- sentence analysis cue。
- 译文。
- Structure Lens。
- 系统解释正文。

译文可由用户设置打开。打开后应以 hover/reveal 或轻量显示方式出现，避免打断连续阅读。

### 精读模式

目标：完整解析，但保持文档感。

默认显示：

- 原文。
- V1 过渡期显示 unit 边界内的“本段译文”。
- Translation V2 后显示按阅读组对齐的译文。
- `vocab_highlight`、`phrase_gloss`、`context_gloss`。
- grammar cue。
- sentence structure cue。
- 用户 highlight / comment indicator。

默认不展开：

- grammar note 解释正文。
- sentence analysis chunk underlines。
- sentence analysis chunk list。
- Ask supplement 正文。

用户点击或 hover 后，相关解释按需出现。

## 译文

### 当前约束

当前 translation worker 以 unit 为输入，`TranslationLayerOutput` 只有整段 `translated_text`。

因此，当前 unit 级译文不能显示在第一个 anchor segment 下方。V1 只能作为 unit 边界内的“本段译文”块，并明确它覆盖整个 unit。

### V2 目标形态

后续 translation worker/schema 升级为：

```ts
type TranslationLayerOutputV2 = {
  schema_version: 2;
  target_language: string;
  items: Array<{
    anchor_segment_id: string;
    source_text: string;
    translated_text: string;
  }>;
  full_translation?: string;
  confidence: "low" | "normal" | "high";
  notes: string[];
};
```

worker 仍读取完整 unit 以保证翻译质量，但输出 per-anchor-segment 对齐 items。

前端再把 1-3 个连续 anchor segments 合并为 translation pair group。这样保证原文和译文可对应，同时避免文章被机械切碎。

分阶段要求：

- V1：精读模式显示“本段译文”；沉浸模式默认隐藏译文。
- V2：精读模式默认显示 translation pair group；沉浸模式仍默认隐藏，可由用户设置打开。

## 文档 Marks And Cues

系统标注不再使用多盒彩色高亮。

| Layer | 默认形态 | Active / Hover |
|---|---|---|
| `vocab_highlight` | 浅底色，精读模式显示 | 打开词典 / mark 增强 |
| `phrase_gloss` | 细实线或轻底色 | 打开结构化短语解释 |
| `context_gloss` | 点线或虚线下划线 | 打开上下文释义 |
| `grammar_note` | 细下划线 + 小编号 / cue | 显示文档注释 / 脚注式解释 |
| `sentence_analysis` | 结构 cue | 打开 Structure Lens |
| `user_highlight` | 用户色 mark | 可编辑 / 删除 |
| `comment_note` | comment underline / margin indicator | 打开 comment/discussion projection |

### Marks / Cues Conflict Resolver

系统 marks、用户 highlights、comments、selection 和 active cue 必须通过统一 resolver 产出视觉 token。不要让每个 leaf/component 自己决定叠色。

视觉优先级：

| Priority | Layer | 视觉策略 |
|---:|---|---|
| 1 | 当前 selection | 半透明 overlay，覆盖所有 mark，但不改变文字颜色 |
| 2 | active comment / active user highlight | ring / stronger underline / margin indicator 增强 |
| 3 | active system cue | cue 编号、线型或局部 underline 增强 |
| 4 | user highlight | 用户色底色，但透明度低于 selection |
| 5 | comment indicator | comment underline 或 margin dot，不抢用户 highlight 底色 |
| 6 | phrase/context/grammar system marks | 低干扰线型或轻底色 |
| 7 | vocab system mark | 最弱背景，仅精读默认显示 |

合并规则：

- selection 永远是 overlay，不和 highlight/comment/system mark 的背景色相乘。
- 同一 text leaf 同时有 user highlight 和 system mark 时，背景取 user highlight；system mark 降级为 underline / cue。
- 同一 text leaf 同时有 comment 和 user highlight 时，highlight 保留背景，comment 使用 underline 或 margin indicator。
- 同一 text leaf 同时有多个 system marks 时，只允许一个背景层；其余使用线型、编号或 popover cue。
- underline lane 最多两层：user/comment lane 和 system lane。active cue 可以临时提升到最上层，但不能导致行高跳动。
- sentence_analysis chunk underline 不参与默认叠层；只在 Structure Lens active 时显示。
- active 状态只能增强已有 token，不应创建新的文档流 block。
- resolver 输出稳定 class/data attributes；Plate leaf 只负责渲染，不重新计算业务优先级。
- 色彩不能是唯一语义。必须同时通过线型、编号、icon 或 tooltip label 区分 layer。
- 所有视觉层都不能持久化 Plate path / Slate path；保存和 reload 仍以 domain anchor 重新投影。

冲突示例：

| Overlap | 结果 |
|---|---|
| user highlight + vocab | 用户底色 + vocab 弱 underline/tooltip |
| comment + grammar_note | comment indicator 保留；grammar cue 编号可见但不加第二块背景 |
| selection + user highlight + phrase_gloss | selection overlay 覆盖；toolbar anchor 来自 selection |
| active Structure Lens + grammar_note | Structure Lens chunk line 临时增强；grammar cue 降低强调但仍可点击 |
| multi_text comment ranges + system marks | 多个 ranges 共用 comment thread indicator；system marks 按各自 range 低干扰显示 |

系统 marks 默认低干扰，active/hover 时增强。用户资产比系统 marks 更明显，但不能遮盖正文可读性。

## Grammar Note

`grammar_note` 不再渲染为卡片或 accordion。

默认形态：

- 原文上的细下划线。
- 小编号或 cue。
- 精读模式显示，沉浸模式默认隐藏。

交互：

- hover cue 显示短解释。
- click cue 固定解释 popover 或脚注式说明。
- popover 可提供 Ask / feedback 入口。
- 解释不进入文档流，不打断正文。

`grammar_note` 适合文档注释/脚注式心智，不适合大块解析面板。

## Sentence Analysis Structure Lens

`sentence_analysis` 是结构图层，不是普通注释。

默认形态：

- 精读模式只显示结构 cue。
- 沉浸模式默认隐藏。
- 不默认显示 chunk underlines。
- 不默认显示 analysis 正文。

V1 激活后：

- 显示 floating legend。
- legend 包含整体 `analysis` 和 chunk list。
- 不要求稳定显示 chunk underlines。
- 如果前端能唯一匹配 `chunks[].text`，可以做 best-effort chunk underline；不能唯一匹配时只展示 legend。

V2 激活后：

- 在原句上显示 chunk underlines / fine rules / numbered spans。
- 显示 floating legend。
- legend 包含整体 `analysis` 和 chunk list。
- hover legend item 增强对应原文 chunk。
- hover 原文 chunk 增强对应 legend item。

floating legend 是解释层，不是 side panel，也不是文档流卡片。

当前 schema 只有 `chunks[].text`，前端只能 best-effort 定位。后续应升级为：

```ts
type SentenceAnalysisChunkV2 = {
  chunk_id: string;
  order: number;
  label: string;
  text: string;
  start_offset: number;
  end_offset: number;
  role?: string;
  depth?: number;
  parent_chunk_id?: string | null;
};
```

这样 Plate surface 可以直接把 chunks 投影成 decorations / marks，避免重复子串和部分重叠造成定位错误。

## Selection Toolbar

selection toolbar 只承载当前选区的即时动作。

默认按钮：

- Lookup
- Ask
- Comment / Note
- Highlight
- Copy

启用条件：

| Action | V1 状态 | 条件 |
|---|---|---|
| Lookup | V1b 可启用 | Plate selection 或 clicked vocab/phrase/context mark 能生成 anchor draft |
| Copy | V1b 可启用 | 有非折叠 selection |
| Ask | 默认 disabled / coming soon | D6-A3/A6 新 route 和 request/response contract 稳定后才启用 |
| Comment / Note | 默认 disabled / coming soon | V1c Reading Record anchor gate 完成后才启用 |
| Highlight | 默认 disabled / coming soon | V1c Reading Record anchor gate 完成后才启用 |

Ask disabled 时仍可显示按钮，但必须有明确 disabled semantics、tooltip 或 coming-soon copy。不能调用旧 Ask route 或依赖旧 analysis record contract。

不默认放入：

- 选择整句
- 清除标注
- Feedback
- 加粗、斜体、链接、列表、表格、图片等编辑器格式按钮

上下文动作迁移：

- 选择整句放到句子 hover / block action。
- 清除标注只在选中已有 user-owned highlight/comment 时出现。
- Feedback 挂在 AI mark/cue、Structure Lens 或系统解释 popover 上。

toolbar 必须由 Plate selection 驱动。词典或 Ask 获焦后，中心文档使用 Cursor Overlay 保持选区可见。

## 用户资产

用户资产采用双轨。

### Quick Highlight

用于快速标记。

- 写入现有 `user_annotations`。
- 支持 `sentence` / `text_range` / `multi_text`。
- 作为 user-owned mark 投影到 Plate surface。

### Comment Note

用于有正文的笔记和讨论。

- 写入现有 `reader_notes`。
- 支持 `sentence` / `text_range` / `multi_text`。
- 在 Plate surface 中表现为 comment/discussion projection。
- Plate comment id 只是 Web projection key。

第一版不新增 comment backend。后续如统一为 User Editorial Assets，可再做 schema migration。

### Comment Projection Contract

`reader_notes` 到 Plate comment/discussion 的投影需要稳定映射：

| Domain | Plate projection |
|---|---|
| `reader_notes.id` | `commentId` / thread id |
| `reader_notes.target_key` | projection lookup key |
| `quote_mode` | `sentence` / `text_range` / `multi_text` display mode |
| `segments` | one or more comment mark ranges |
| `note_text` | discussion body |

规则：

- Plate comment id 由 `reader_notes.id` 派生，不能随机生成。
- `text_range` note 投影为单个 comment mark。
- `multi_text` note 投影为多个 ranges，共用同一个 thread id。
- 删除 / 更新仍走 `reader_notes` API；Plate 状态只反映服务端结果。
- V1 不处理 resolved / archived thread 状态；后续如需要再扩展 `reader_notes` 或 User Editorial Assets。

## Anchor And Persistence

V1c 复用现有 `user_annotations` / `reader_notes` API，但有前置条件。

边界判断：

- 旧表可复用：`user_annotations` 保存 quick highlight，`reader_notes` 保存 comment/note body。
- 旧请求形态可短期兼容：`sentence` / `text_range` / `multi_text` 仍可作为 API contract。
- 旧 `render_scene` 校验不可复用：新 Reading Record 的 source of truth 是 Stable Reading Base / Anchor Segment。
- 旧 `sentence_id` 只能作为 compatibility alias，不能重新成为新架构主锚点。
- Plate path / Slate path 不进入 API、不进入数据库、不进入 event log。

Plate selection adapter 需要生成现有 API 需要的 payload：

```ts
type ReaderAnchorPayload = {
  anchorType: "sentence" | "text_range" | "multi_text";
  targetKey: string;
  recordId: string;
  paragraphId?: string | null;
  sentenceId?: string | null;
  selectedText: string;
  startOffset?: number | null;
  endOffset?: number | null;
  textHash?: string | null;
  segments?: Array<{
    paragraphId?: string | null;
    sentenceId: string;
    selectedText?: string | null;
    startOffset: number;
    endOffset: number;
    textHash?: string | null;
  }>;
};
```

短期：

- 新 Plate surface 必须继续生成兼容旧 API 的 `sentence_id`、offset、hash 和 segments。
- 只有后端能够用新 Reading Record / Stable Base 校验这些 payload 时，才能在 `/app/reader-record/{recordId}` 启用写入。
- 不允许假设新 Reading Record id 一定能通过旧 `analysis_results.render_scene_json` 校验。
- Web 可以在 V1a/V1b 生成 `ReaderAnchorPayload` draft 供 Lookup/Copy/Ask 预览使用，但不能把 write action 打开。

中期：

- `user_annotations` / `reader_notes` 的 text_range / multi_text 校验从旧 `render_scene` 迁到 Stable Reading Base / Anchor Segment。
- 对外 anchor 优先使用 `anchor_segment_id`，`sentence_id` 只保留兼容 alias。
- API 错误模型需要区分 stale anchor、hash mismatch、range out of bounds、record mismatch 和 unsupported anchor mode。

如果 Stable Base / Anchor Segment 校验尚未完成，V1a / V1b 可以先显示 Comment/Highlight 按钮的 disabled 或 coming-soon 状态，但不能调用旧 render scene 写入路径。

## Ask Supplement

Ask 回答默认留在 Ask rail。

Ask 按钮和 Ask rail anchor injection 只有在 D6-A3/A6 新 route 与 request/response contract 稳定后启用。稳定前：

- selection toolbar 中的 Ask 显示 disabled / coming soon。
- active grammar cue / Structure Lens 中的 Ask entry 显示 disabled / coming soon。
- 不能调用旧 Ask route。
- 不能把旧 analysis record contract 包装成新 Reader Record Ask contract。

用户明确保存后，才进入文档 projection。

保存目标：

- 保存为 Comment Note：用于用户个人沉淀。
- 保存为 Ask Supplement：用于 AI 对 grammar、sentence_analysis、phrase/context 的补充解释。

Ask Supplement 进入文档后不渲染为卡片。它应表现为文档注释 / 脚注式 supplement cue。

第一版不实现 Ask Supplement 入文档，只保留保存策略和后续接口边界。

## 实施切片

### V1a: Direct Plate Document Surface

必须包含：

- `ReaderRecordPlateSurface` 直接消费 `ReaderPlateSnapshot.value`。
- 使用 `Plate + readOnly`。
- 不经过 `adaptReaderPlateSnapshotToReaderVm`。
- 沉浸 / 精读 visibility profile。
- 文档式 vocab / phrase / context marks。
- grammar cue。
- sentence structure cue。
- unit 级译文过渡展示为“本段译文”。
- 不显示旧式 grammar / sentence analysis 卡片。

### V1b: Plate Selection And Rails

必须包含：

- Plate selection toolbar：Lookup、Copy。
- Ask 按钮可显示，但 D6-A3/A6 新 route 和 contract 稳定前必须 disabled / coming soon。
- Comment/Note、Highlight 按钮可显示，但只有 V1c 前置条件满足后才启用写入。
- Dictionary rail 读取 Plate selection / clicked mark。
- D6-A3/A6 稳定后，Ask rail 读取 Plate selection / active cue。
- 词典或 Ask 获焦后，中心文档选区仍可见。

### V1c: User Asset Writes

前置条件：

- 后端支持用新 Reading Record / Stable Base / Anchor Segment 校验 `text_range` / `multi_text`。
- Web BFF 能把 Plate selection payload 转为现有 `user_annotations` / `reader_notes` request。

必须包含：

- 高亮持久化到现有 `user_annotations`。
- 评论/笔记持久化到现有 `reader_notes`。
- 高亮 / 笔记 reload 后能重新投影到正确 range。
- Plate comment/discussion 只作为前端 projection。

### V1d: Structure Lens Enhancement

V1d 可以在 Sentence Analysis V2 之前做基础版本：

- 点击 structure cue 显示 floating legend。
- legend 展示整体 analysis 和 chunk list。
- chunk underlines 只在唯一匹配时 best-effort 显示。

V2 schema 完成后：

- chunk underlines / numbered spans 成为必选能力。
- hover legend item 和原文 chunk 双向联动。

### 第一阶段不包含：

- translation schema V2。
- sentence_analysis chunk offset schema V2。
- Ask Supplement 入文档。
- AI suggestion / revision。
- 新 comment backend。
- projection_ops incremental applier。
- fixed toolbar 或编辑器格式化能力。

第一阶段必须 disabled / coming soon：

- Ask：直到 D6-A3/A6 新 route 和 contract 稳定。
- Comment/Note：直到 V1c Reading Record anchor gate 完成。
- Highlight：直到 V1c Reading Record anchor gate 完成。
- Ask supplement save-to-document：直到 Ask Supplement Projection 设计和 API 完成。
- sentence_analysis chunk underlines：直到 V1d best-effort 或 Sentence Analysis V2；默认不显示。
- translation pair group：直到 Translation V2。

## 后续需求

### Translation V2

升级 translation worker/schema，输出 per-anchor-segment translation items。

### Sentence Analysis V2

升级 chunk schema，增加 offsets、role、depth 和 parent relation。

### Anchor Validation Cutover

把 `user_annotations` / `reader_notes` 的 text_range / multi_text 校验从旧 `render_scene` 迁到 Stable Reading Base / Anchor Segment。

### Ask Supplement Projection

用户保存 Ask 回答后，投影为文档注释 / supplement cue。

### Suggestion / Revision

后置到：

- Ask 修订系统解析。
- Candidate Base preview/edit。
- 用户笔记改写建议。
- Ask Supplement 替换版本。

## 验收标准

V1a 验收：

- `/app/reader-record/{recordId}` 中心文档不再通过旧 `ReaderVm` 适配。
- 沉浸模式默认只显示原文和有意义的 phrase/context。
- 精读模式显示全部系统 marks/cues，但不默认展开 grammar/sentence_analysis 正文。
- unit 级译文显示为“本段译文”，不会插到单个 anchor segment 后面。
- grammar cue hover/click 能显示解释。
- 系统 marks、用户 highlights、comments、selection 的视觉层级不互相遮挡。
- 大块解析状态卡被 header chip / slim progress strip / layer activity indicator 替代。
- active anchor state 能表达 selection、system mark/cue、comment/highlight 和 rail focus。
- 不出现 Plate fixed toolbar。
- 不出现旧式 grammar / sentence analysis 卡片。

V1b 验收：

- 选中文本后 toolbar 显示 Lookup、Copy。
- Ask 按钮可见但在 D6-A3/A6 稳定前明确 disabled 或 coming soon。
- 如果 V1c 未完成，Comment/Note 和 Highlight 明确 disabled 或 coming soon。
- 打开词典或 Ask 后，中心选区仍可见。
- Dictionary 接收到的是 Plate selection 生成的 domain anchor draft。
- D6-A3/A6 稳定后，Ask 接收到的是 Plate selection / active cue 生成的 domain anchor draft。
- disabled 按钮有语义 disabled 状态，不能只是视觉灰掉。

V1c 验收：

- 高亮可保存、重新加载后仍投影到正确 range。
- 笔记可保存、重新加载后仍投影为 comment/note indicator。
- 写入路径不依赖旧 `analysis_results.render_scene_json` 校验新 Reading Record id。
- 旧 `user_annotations` / `reader_notes` 表继续复用，但校验经过 Reading Record anchor gate。
- 写入失败能区分 stale anchor、hash mismatch、range invalid 和 record mismatch。

V1d 验收：

- sentence structure cue click 后显示 floating legend。
- 当前 schema 下 chunk underlines 只在唯一匹配时显示；无法唯一匹配时不显示错误 underline。

可访问性验收：

- toolbar、popover、floating legend、rail 都支持键盘访问。
- Escape 能关闭 toolbar / popover / floating legend，并恢复合理 focus。
- focus trap 不阻断 Ask、Dictionary 和正文之间的返回路径。
- 色彩不是唯一状态指示；marks/cues 至少有线型、编号或图标差异。
- 支持 `prefers-reduced-motion`。
- disabled / coming soon action 使用真实 disabled semantics，并提供可读原因。
- comment indicator、grammar cue、structure cue 需要可通过键盘聚焦，且有 `aria-label` / tooltip 说明。
- floating toolbar 出现后不抢走正文 selection 的语义；关闭后 focus 返回正文或触发按钮。
- progress strip 不能只靠颜色表达 layer 状态，需要有文本状态入口或可访问名称。
- popover / floating legend 打开时应宣布标题和状态，不把整篇正文重新读一遍。
- 文本缩放到 200% 时，toolbar、popover 和 bottom sheet 不遮挡核心正文。

移动端验收：

- Dictionary、Ask、Comment composer 在窄屏下使用 bottom sheet 或等价单栏模式。
- 触屏 selection 不被 floating toolbar 遮挡。
- toolbar 按钮数量在移动端收敛，V1b 至少保留 Lookup、Copy；Ask/Highlight/Note 可作为 disabled / coming soon action 出现在更多菜单。
- bottom sheet 打开后保留当前 anchor 摘要，并允许返回正文重新选择。
- 触控目标不小于 44px，图标按钮需要可见 label 或 accessible label。
- 不使用需要精确点击细下划线的唯一入口；grammar / structure cue 在移动端需要有足够命中的 cue target。
- 横屏和窄屏不出现横向滚动；正文、toolbar、bottom sheet 不能互相覆盖。
- iOS/Android selection handle 与 floating toolbar 冲突时，toolbar 应下移或转为 bottom action bar。

## Open Questions

- Structure Lens floating legend 的具体位置：句子附近、selection 附近，还是文档右侧 margin。
- grammar note 的解释是否使用统一 popover，还是 footnote-like note strip。
- 精读模式下译文默认展示密度：每个 translation pair group 常显，还是可折叠。
- 沉浸模式中 phrase/context 的筛选规则：由后端 importance 决定，还是前端按类型/置信度过滤。
- 移动端 rail 行为：Dictionary / Ask / Comment 是否统一 bottom sheet。
