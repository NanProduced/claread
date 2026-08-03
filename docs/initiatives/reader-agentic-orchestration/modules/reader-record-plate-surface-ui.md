# Reader Record Plate Surface UI

> 状态：目标方案 + 当前实现基线；T5.1 L0/L1 deterministic navigation 已闭合；T5.3 semantic outline durable 已闭合但 **UI 未交付**；DOC-R2 progressive transition 引用仍有效
> 最后更新：2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：标注 cutover 后 URL 统一为 `/app/reader/[recordId]`，正文历史 `/app/reader-record/{recordId}` 路径按 cutover 合同替换）
> 范围：`/app/reader/[recordId]`（cutover 前 `/app/reader-record/{recordId}`）在 Agentic Orchestration 架构下的 Reader Record 解析页 UI/UX、Plate.js 文档表面、选择交互、词典/Ask 联动、用户高亮/笔记和第一版实现边界。

> **Cutover 注意（2026-08-03）**：Architectural Cutover Complete 后，Web 用户页面统一为 `/app/read` 与 `/app/reader/[recordId]`，Web BFF 统一为 `/api/web/reader/records/*` 与 `/api/web/reader/source-artifacts/*`。本文正文历史段保留 `/app/reader-record/{recordId}`、`/api/web/reader-plate/*` 等 cutover 前 URL 作为历史决策证据；当前产品入口与写入路径以 cutover 后合同为准，不再保留旧 alias、旧 BFF route 与旧 `/app/reader/{recordId}` legacy 写入分支。

当前代码接入矩阵见 [`reader-plate-component-integration.md`](./reader-plate-component-integration.md)。本文件描述目标 UI/UX 和产品边界；若本文与代码事实冲突，以接入矩阵和当前代码为准，再反向更新本文。

## Deterministic Navigation（L0 / L1）

> 来源：T5.1a–d 已提交实现与 Chromium 合同（`701a9463` L0 文案、`970d54d8` L1 projection、`20be3d75` target-cache revalidation、`9fe6d94d` Chromium）。权威代码：`apps/web/src/lib/reader-plate/projection/reader-record-navigation.ts`、`apps/web/src/components/reader/plate/ReaderRecordNavigationRail.tsx`。本节描述**已落地**的确定性阅读定位，不是 semantic outline。

### 分层与术语

| 模式 | 产品名 | 数据来源 | 列表形态 |
|------|--------|----------|----------|
| **L0** | 段落导航 | `snapshot.navigation.units` 全量 reading units（空 units 时可 document-fallback 派生 unit 列表） | 扁平：一 unit 一行 / 一 tick |
| **L1** | 章节导航 | 仅 `unit_type === "heading"` 的 snapshot units；前端纯派生 coverage | 扁平 heading 列表；**无** depth / tree / children |
| （UI 未交付） | 内容大纲 | 后端 durable `enhancement_layers.semantic_outline` 已存在；**未**进 snapshot / 本 rail | 与 L0/L1 独立；见 T5.4-R0 / T5.5 |

- `<nav aria-label>` **始终**为「阅读定位」。
- **禁止**用「文章目录 / 大纲 / 第 N 节」描述当前确定性能力。
- L0 trigger：`打开/关闭段落导航`；有 active 时追加「，当前第 N 段」。
- L1 trigger：`打开/关闭章节导航`；仅当 active 非 null 时追加「，当前第 N 项」。lead 区不得声称「当前第 N 项」。

### L1 启用门槛（严格 AND）

```text
enable_L1 =
  navigation.units 非空
  && unit_count >= 6
  && heading_count >= 2
```

- document-fallback（`navigation.units` 为空）**永不** L1：无可靠 `unit_type`，禁止猜 heading 或合成伪节点/伪层级。
- 未过门槛 → **完整回退 L0**（全 unit 段落导航）。
- L1 行 identity = heading `unit_id`；coverage = 阅读序闭区间 `[startUnitId … endUnitId]`（含中间 body/list/quote）。首个 heading 之前的 lead body **不**占 L1 行。

### 点击、scroll spy 与 lead

- 定位只在 `.reader-record-plate-document` 内解析 paragraph；优先 `data-reader-record-unit-start="true"`，否则同 unit 首段。**禁止**全局 `[data-unit-id]`（rail 自身也可能带该属性）。
- L1 点击 / scroll spy **只锚定 heading unit 的 unit-start**，不滚到 coverage 的 end unit。
- safeTop = topbar 56px + 8px。
- **L0 active**：`last unit with top <= safeTop`，否则 first below，否则 first item。
- **L1 active**：仅 `last heading with top <= safeTop`；若所有 heading `top > safeTop` → **lead**：`activeUnitId = null`，无 `aria-current`，trigger 不得写「当前第 N 项」。panel 打开时 keyboard focus 可落到第一项 heading，**不等于** active。
- body 不在 L1 候选中；用户滚过 heading 后的 body 时，上一 heading 保持 active。

### Source identity 与 target cache

- `sourceIdentityKey = base_id:generation`（`buildReaderRecordSourceIdentityKey`）。`base_id` 或 `generation` **任一**变化时，无论 unitId 是否仍像 `u1`/`u2`，必须清空：`activeUnitId`、`focusedUnitId`、scroll-lock、`targetMap`。
- Plate `setValue` 会 remount DOM。target cache **不得**信任 map size；每项经单一 `resolveValidatedUnitTarget`（scroll spy 与 click **共用**）校验：
  1. `el.isConnected`
  2. 仍属于当前 `.reader-record-plate-document`
  3. 仍是 `data-reader-record-node="paragraph"` 且 `data-unit-id` 匹配
  - 失效 → 立刻从 cache 删除并重新 `findUnitTarget`。**禁止**用 detached 节点的 `getBoundingClientRect` 驱动 spy 或 click。
- scroll spy 的 rAF 回调必须带 **source-identity fence**：调度时的 `sourceIdentityKey` 与当前 ref 不一致时，**不得** `setActiveUnitId`。

### Snapshot / event 边界

- L0/L1 是 **accepted snapshot** 上的本地 deterministic projection，不新增 enhancement layer、reader event、polling 协议或 transport。
- rejected stale/fence snapshot 只在 polling/page seam 处理，不得进入 Surface 或导航状态交换（见 [`representation-event-contract.md`](./representation-event-contract.md#deterministic-navigation-与-accepted-snapshot-边界)）。
- Surface same-snapshot early-return = duplicate accepted snapshot guard，**不是** stale/fence rejection。
- **未批准** SSE、WebSocket、JSON Patch、ETag/304、通用 Plate tree diff 作为导航交付手段。

### 与 semantic outline 的边界

- 当前 L1 **不是** semantic outline 的替代实现，也不得被表述为「内容大纲已交付」。
- **T5.3 已闭合**：后端可向 `enhancement_layers` 发布 `layer_type='semantic_outline'`（record/`document`，默认不请求，带 job lease fence）。详见 [`implementation-plan.md`](../implementation-plan.md#t53-semantic-outline-worker--durable-layer)。
- **当前 Reader UI 仍不展示内容大纲**：outline **未**挂 `ReaderPlateSnapshot`，本 rail **不**消费 outline layer。T5.4-R0 设计 snapshot projection；T5.5 才做 UI。
- outline **不得**污染 `navigation.units`，不阻塞 `article_ready`。
- 本文件 **不**冻结 L2 / 内容大纲 UI IA、partial 节点与 L0/L1 混排、或 request eligibility 产品阈值。

### 实现落点（索引）

| 能力 | 位置 |
|------|------|
| mode / gate / L1 items / sourceIdentityKey | `projectReaderRecordNavigation` 等 pure projection |
| rail UI、spy、click、cache、fence | `ReaderRecordNavigationRail` |
| Chromium 合同 | `apps/web/tests/e2e/plate-surface-l1-heading-navigation-t5-1d.spec.ts` |

## Progressive Transition UX 引用

`/app/reader-record/{recordId}` 的 `reloadSnapshot` 已接入 T4.2a-PUX-R2 progressive transition 校验：

- canonical replay 与 stale/layer 单调 helpers 来自 T4.2a-PUX-R1 fixture 合同（21 tests）。
- stale 拒绝时 cursor hold，不覆盖 UI；layer regression 同样不覆盖 UI。
- 底部 progressive status strip 显示「正文可读 → 译文先到 → 批注逐步丰富 → 完整解析」状态。
- Plate generation-scoped clear + scroll restore 在 reload 时保留用户阅读位置。

详细 envelope 合同、gap detection 与 polling cursor 归 [`./streaming-and-projection.md`](./streaming-and-projection.md#t42a-pux-r2-runtime-integration)；任务状态与测试计数归 [`../implementation-plan.md`](../implementation-plan.md) T4.2a-PUX-R1 / T4.2a-PUX-R2 章节；决策记录归 [`../target-architecture.md`](../target-architecture.md#决策记录) `T4.2a-PUX-R1` / `T4.2a-PUX-R2` 行。

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
- 原文 Stable Base 的结构化（Stable Document Blocks）属于阶段二，阶段一仅 callout 内容支持 Markdown；Canonical Text Layer（`reading_bases.text`）始终为纯文本，不被 Markdown 语法污染（见"Stable Document Blocks 与 Canonical Text Layer 分离"section）。

## 已确认方向（2026-06-27）

当前解析页采用 **Anchor-backed Plate Document** 方案：

- 后端真源是 Stable Reading Document、Enhancement Layers、User Assets 和 source-grounded anchor ranges。
- Plate.js 是中心文档的真实交互/渲染层，必须使用 Plate node / mark / comment / selection 机制承载正文、译文、批注、笔记和高亮。
- Plate value 是可重建投影，不是唯一业务真源；Plate path / Slate path / DOM range 不持久化。
- 用户笔记、高亮、Ask、词典 lookup 都从 Plate selection 或 active Plate mark/node 进入，再解析为 source-grounded `anchor_set` 或当前过渡 single-range anchor。
- 可见文本可以比持久化 anchor 更广：译文、grammar note、sentence analysis、Ask supplement 可被选取和 Ask；持久用户资产默认仍回源到 stable source。

已确认的产品交互边界：

- 页面只保留 **精读模式** 和 **沉浸模式** 两态，不再引入第三个“原文/双语”模式。
- 精读模式默认显示译文和解析批注；沉浸模式不显示译文、grammar note、sentence analysis 正文。
- 用户高亮只用三色：`warm_yellow`（重点）、`soft_mint`（疑问）、`soft_rose`（难点）。用户高亮主要使用半透明背景；用户笔记使用高亮背景 + 同色系下划线；AI 词汇三类使用各自高亮色系；语法标注使用清晰下划线 + hover/active 浅色底。
- 单击 `vocab_highlight` 才触发词典 quick peek 并调用词典接口；单击 `phrase_gloss` / `context_gloss` 打开已有解释，不再查词典；普通原文单击不自动查词，划选后从 toolbar 触发 Lookup。
- Ask Claread 采用全局可选、稳定回源、全上下文引用：payload 必须包含 `visible_selected_text`、source `anchor_set`、source context、相关译文/词汇/语法/句析/用户资产。
- 个人笔记使用 Plate Comment UI 但没有协作语义；重复同一选区应提醒但允许新增，不允许静默覆盖。

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

- Stable Reading Document
- Stable Document Blocks
- Canonical Text Layer（当前过渡实现仍使用 Stable Reading Base）
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
- V1c first 只要求能从 Canonical Text Layer / Anchor Segment 校验 single-range `UserEditorialAssetAnchor`；当前代码可继续通过过渡 Stable Reading Base 实现。
- `multi_text` 不复用旧 render_scene 校验；后续必须走 `UserEditorialAssetAnchorSet` / multi-range DTO 和对应 gate。
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

### Plugin Kit 清单（当前代码）

当前 `apps/web/src/components/editor/plugins/` 下的 Reader Plate plugin 入口是 `reader-plate-kit.ts`，实际聚合如下：

| Kit 文件 | 职责 | 包含的 plugin |
|----------|------|---------------|
| `markdown-kit.ts` | Markdown deserialize | `MarkdownPlugin`；用于 callout Markdown 转 Plate `Descendant[]` |
| `reader-blocks-kit.tsx` | Reader block elements | `reader_paragraph` / `reader_blockquote` / `reader_callout` / `reader_sentence_analysis` / Markdown 基础 element/leaf |
| `reader-leaf-kit.tsx` | Reader inline marks | vocabulary / grammar / user_highlight / user_note leaf plugin |
| `floating-toolbar-kit.tsx` | Selection floating toolbar | 通过 `@platejs/floating` hook 渲染 Claread action toolbar |
| `comment-kit.tsx` | Comment mark | `BaseCommentPlugin` + `CommentLeaf`；持久化仍走 `reader_notes` |
| `cursor-overlay-kit.tsx` | Selection overlay | `CursorOverlayPlugin`；rail/toolbar 获焦时保留选区视觉 |
| `reader-plate-kit.ts` | 聚合入口 | 聚合上述 kit，供 `ReaderRecordPlateSurface` 的 `usePlateEditor` 使用 |

当前没有独立的 `basic-blocks-kit.ts` / `basic-marks-kit.ts`。Markdown callout / sentence analysis 的 children 已进入 Plate 节点树，并由 `reader-blocks-kit.tsx` 中注册的 Plate element/leaf components 渲染；旧 `CalloutMarkdownRenderer` 已删除。

## Markdown 渲染

### 设计目标

让 callout 内容（`grammar_note.note` / `sentence_analysis.analysis` / `ask_supplement.content_md`）支持 Markdown 渲染，实现 Notion 文档形态的富文本讲解。

### 数据模型变更

`ReaderRecordPlateCalloutBlock.children` 类型从纯文本 leaf 改为 Plate 节点树：

```ts
// 阶段一变更前（纯文本）
interface ReaderRecordPlateCalloutBlock {
  type: "callout";
  children: ReaderRecordPlateCalloutTextLeaf[];  // 只有 { text: string }
}

// 阶段一变更后（Plate 节点树）
import type { Descendant } from 'platejs';

interface ReaderRecordPlateCalloutBlock {
  type: "callout";
  children: Descendant[];  // 标准 Plate 节点树
}
```

`ReaderRecordPlateCalloutTextLeaf` 保留为 deprecated 兼容别名。

### Projection 层 deserialize

Projection 层调用 `deserializeMarkdownToBlocks(markdown)` 把 LLM 输出的 markdown 字符串转为 Plate 节点树：

```ts
// apps/web/src/lib/reader-plate/markdown/deserialize.ts
export function deserializeMarkdownToBlocks(markdown: string): Descendant[]
```

对应 builder 调用此 utility：
- `buildGrammarCalloutBlocks`：`children: deserializeMarkdownToBlocks(note)`
- `buildSentenceAnalysisBlocks`：生成独立 `sentence_analysis` document block，`children` 含 chunks element 与 `deserializeMarkdownToBlocks(analysis)`
- `buildSupplementCalloutBlocks`：`children: deserializeMarkdownToBlocks(contentMd)`

`sentence_analysis.chunks` 仍保持原有结构化数据形态（chunks 是结构化数据，不是 markdown 文本）。

### 渲染层

Reader block components 直接渲染 Plate 注入的 `{children}`：

```tsx
<div {...attributes}>{children}</div>
```

Markdown 基础 block / mark 由 `reader-blocks-kit.tsx` 中的 Plate plugins 注册。Callout、sentence analysis、supplement 都不再绕过 Plate children 渲染路径。

### 支持的 Markdown 语法

| 语法 | 渲染效果 | 用途 |
|------|----------|------|
| `**加粗**` | **加粗** | 强调关键词 |
| `*斜体*` | *斜体* | 补充说明 |
| `` `code` `` | `code` | 语法术语、代码片段 |
| `- 列表项` | 无序列表 | 要点列举 |
| `1. 有序项` | 有序列表 | 步骤说明 |
| `> 引用` | 引用块 | 原文引用 |
| ```` ``` ```` | 代码块 | 语法结构示例 |
| `# 标题` | H1-H6 | 分级讲解 |

### Stable Document Blocks 与 Canonical Text Layer 分离（架构定论）

> 来源：与输入预处理 coding agent 对接确认（2026-06-26）。

原文渲染的结构化与 anchor 的纯文本基准必须分离，不再有"Stable Base 是否 Markdown 化"的二选一问题：

- **Stable Document Blocks**：结构 truth，包含 paragraph/heading/list/blockquote/code_block/table/image/footnote 等 block 类型和 `text_content`；给 Plate/Markdown 投影、RAG citation、table/image/footnote 保留用。
- **Canonical Text Layer（`reading_bases.text`）**：从 main_reading blocks 的 `text_content` 派生的纯文本；给 UTF-16 offset、Reading Units、Anchor Segments、translation/vocabulary/grammar grounding 用。**不含 Markdown 语法字符**（`#`/`-`/`>`/代码围栏/GFM 表格/脚注语法不进入 offset 基准）。
- **渲染投影**：前端从 Stable Document Blocks 投影出 Markdown/Plate 节点树渲染原文；translation/vocabulary 保持纯文本固定样式；grammar_note/sentence_analysis/ask_supplement 走 Markdown callout 渲染。

正确派生顺序：`input artifact → normalized Stable Document Blocks → main_reading blocks.text_content → canonical plain text`。Markdown/Plate 是从 blocks 投影出来的渲染层，不是 canonical source。

阶段一现状：D4 只冻结 `reading_bases.text`（纯文本），Stable Document Blocks 在 D6+ 实现。
阶段二方向：Stable Document Blocks 冻结后，前端原文渲染从 blocks 投影，不再只依赖纯文本。

**block type 与 payload_json 子契约（与输入预处理 agent 对接确认）**：

block_type 枚举（与后端 schema/migration 一致）：`paragraph` / `heading` / `list_item` / `blockquote` / `table` / `table_row` / `table_cell` / `footnote` / `image` / `image_ocr` / `caption` / `code_block` / `unknown`。

- `divider`：文档希望有但后端 schema/migration 待补，补上前用 `unknown` + payload_json 兜底。
- `degraded block notice` / `page/source artifact reference`：不做正文 block，放入对应 block 的 payload_json / source_refs_json / quality_json。

payload_json V1 子契约：
- `list_item`：`{ list_id, ordered, ordinal, depth, marker }`，text_content 是项纯文本（不含 marker）；前端按 `list_id` + `ordered` 还原 `ul`/`ol` 容器。
- `heading`：`{ level }`，范围 1..6，超过 6 降级到 6 并在 quality_json 标记；text_content 只放标题文本（不含 `#`）。
- `code_block`：`{ language, info_string }`，text_content 是纯代码文本（不含 ``` 围栏）。

canonical text 拼接规则：`interpretation_policy.default_route == "main_reading"` 的 block 用 `\n\n` 连接。默认进入：paragraph / heading / list_item / blockquote / caption。默认不进入：table / table_row / table_cell / image / image_ocr / footnote / code_block / unknown（除非 Candidate confirm 显式提升）。

详见 [input-adapter.md](file:///c:/Users/nanpr/claread/claread/docs/initiatives/reader-agentic-orchestration/modules/input-adapter.md) 的"Stable Document Blocks 与 Plate Snapshot"section。

## Plate Editors Demo 组件复用

> 来源：`https://platejs.org/editors` 官方 Playground（2026-06-26 抓取）

### 直接复用组件

| 组件 | 来源 | 复用方式 | 阶段 |
|------|------|----------|------|
| `FloatingToolbarKit` | Plate editors demo（选中文本后浮现，含 "Ask AI" 按钮） | 定制按钮为 Claread action（Lookup / Ask / Comment / Highlight / Copy），移除格式化按钮 | 阶段二 V2-Step-1 ✅ 已落地 |
| `CommentKit` + `CommentLeaf` | Plate editors demo（"overlapping annotations" 多段文本重叠评论） | mark 模型复用，`comment_<noteId>` 派生自 `reader_notes.id`；移除 draft → resolved 流转，简化为"选区即笔记" | 阶段二 V2-Step-2 ✅ 已落地 |
| `ReaderCalloutPlugin` | 自定义 Plate element plugin | 形态参考 callout，但当前没有直接接入 `@plate/callout-node`；children 走 Plate element/leaf 渲染 | 已落地 |
| `MarkdownPlugin` + `remarkGfm` | `@platejs/markdown` | deserialize API 复用，不需要 serialize（阅读态不编辑） | 阶段一 P0-Step-3 |

### 不复用组件

| 组件 | 原因 |
|------|------|
| Suggestion | Claread 不做多用户协作流转 |
| AI Menu（⌘+J / 空行 Space） | Ask Claread rail 已有独立实现 |
| Slash Command | 阅读态 readOnly 不需要插入菜单 |
| Drag Handle | 阅读态禁用 block 拖拽 |
| Multi-select | 阅读态不需要 |
| Collaboration (Yjs) | 单人阅读场景不需要 |
| FixedToolbar | 明确不使用（见非目标） |

### Demo 揭示的注意事项

- Demo 中 comments 支持 "overlapping annotations"（重叠批注），`CommentLeaf` mark 模型天然支持，适合 Claread 多层 marks（vocab + grammar + user highlight）重叠场景
- Demo 中 markdown blockquote "keep nested structure instead of flattening it"——译文 blockquote 需保留段落嵌套，不能 flatten
- Demo 中 autoformatting 是输入态能力，阅读态 readOnly 不触发，但 deserialize 必须能解析这些语法

## Projection Schema

UI-D1 新增前端纯 projection helper：

```ts
projectReaderPlateSnapshotToReaderRecordPlateDocument(
  snapshot: ReaderPlateSnapshotDto,
): ReaderRecordPlateDocument
```

边界：

- 只消费 `ReaderPlateSnapshotDto.value` 和 snapshot wrapper metadata。
- 不调用 `adaptReaderPlateSnapshotToReaderVm`。
- 不调用 `renderSceneToPlateDocument`。
- 不接旧 `ReaderVm`。
- 不读写 API，不启用 Ask / notes / highlights 写入。
- 不持久化 Plate path / Slate path。

`ReaderRecordPlateDocument` 是新 Reader Record Plate surface 的前端 projection schema。它保留 Plate-like `type` / `children` / `text` 结构，但所有可持久定位的信息都来自 domain ids。

```ts
type ReaderRecordPlateDocument = {
  type: "reader_record_plate_document";
  schemaVersion: "reader-record-plate-document/v1";
  record: {
    recordId: string;
    title: string;
    generation: number;
    productState: string;
    readinessState: string;
  };
  snapshot: {
    snapshotId: string;
    snapshotTakenAt: string;
    lastEventSequence: number;
  };
  base: {
    baseId: string;
    contentSha256: string;
    textLengthUtf16: number;
    hashAlgorithm: "fnv1a32-utf16";
  };
  progress: ReaderRecordPlateProgress;
  children: ReaderRecordPlateUnitNode[];
};
```

映射规则：

| Snapshot source | Projection target | 说明 |
|---|---|---|
| `reader_unit` | `reader_record_unit` | 保留 `unitId`、`baseId`、`orderIndex`、unit/base ranges、hash、parsed decision、unit progress |
| `reader_source_block` | `reader_record_source_block` | 只承载稳定原文和 separator |
| `reader_anchor_segment` | `reader_record_anchor_segment` | 保留 `anchorSegmentId`、`sentenceId`、segment/base/unit ranges、hash；作为 selection 和 marks/cues 的稳定锚点 |
| stable segment leaf | `ReaderRecordPlateTextLeaf` | `text` + stable metadata + `marks[]`；不嵌入 translation 或 analysis block |
| stable separator leaf | `ReaderRecordPlateSeparatorLeaf` | 保留 separator text 和 base range |
| `reader_translation[target_scope="unit"]` | `reader_record_unit_translation` | V1 unit 级“本段译文”；作为 unit child，不能挂到第一个 anchor segment 后面 |
| `reader_vocabulary_marks` | `ReaderRecordPlateVocabularyMark[]` | `vocab_highlight` / `phrase_gloss` / `context_gloss` 进入 text leaf marks |
| `reader_grammar_note_marks` | `ReaderRecordPlateGrammarMark[]` + `reader_record_grammar_cue` | span 进入 text leaf mark；`show_note_chip` 的 span 生成 grammar cue |
| `reader_sentence_analysis` | `reader_record_sentence_analysis_block` + optional decorations | 精读模式投影为常显的 Plate-native structure block；可在原文上做 best-effort chunk decorations |
| `snapshot.user_assets` | `ReaderRecordPlateUserHighlightMark[]` + `reader_record_user_comment_cue` | quick highlight 投影为 user-owned text mark；note/comment 投影为小型 comment indicator，不进入文档流卡片 |
| `enhancement_progress` | `document.progress` + `unit.progress` | document 用于 header chip / slim strip；unit 匹配 unit 或 anchor_segment target 的 layer activity |

Translation V1 约束：

- `target_scope="unit"` 的译文只能生成 `reader_record_unit_translation`。
- `reader_record_unit_translation.placement` 必须是 `"unit"`。
- anchor segment 的 `children` 只能包含 stable source text leaves；不能包含 unit translation。
- Characterization test 必须覆盖该行为，防止旧 adapter 再次把 unit 译文塞到首个 segment 后。

Sentence Analysis V1 约束：

- `reader_sentence_analysis` 进入文档流，但必须是 Plate-native structure block，不是旧式卡片，也不是默认折叠 toggle。
- Projection 生成 `reader_record_sentence_analysis_block`，保留 `analysisId`、`layerId`、`anchorSegmentId`、`label`、Markdown `analysis` 和结构化 `chunks`。
- block 内默认展示 chunk rows + Markdown analysis；chunk rows 的最终版式是下一轮 UI 决策点。
- chunk underline / numbered decorations 只在可唯一定位时 best-effort 显示；不能唯一定位时只显示 structure block，不画错误 underline。

2026-06-28 代码校准：当前实现已把 `sentence_analysis` 从通用 `reader_callout` 拆成独立 `sentence_analysis` document block，并在 Plate value 中映射为 `reader_sentence_analysis`、`reader_sentence_analysis_chunks` 和 `reader_sentence_analysis_chunk` elements。后续重点不再是“拆类型”，而是继续打磨 chunk rows、source decorations 和移动端视觉密度。

Progress projection：

- `document.progress.overallStatus` 直接来自 `enhancement_progress.overall_status`；缺失时为 `"unknown"`。
- `document.progress.layers[]` 使用 capability、target scope/key、layer id 或 job id 生成稳定 id。
- `unit.progress[]` 只收录 target 为当前 unit，或 target 为当前 unit 内 anchor segment 的 progress layer。
- record-level progress 只留在 document 层，不强行塞到某个 unit。

User Asset projection（UI-D4）：

- `snapshot.user_assets` 消费后端 D6-U5 的 nested anchor shape：`asset_id`、`asset_type`、`owner`、`reading_record_id`、`generation`、`anchor: ReaderTextRangeAnchor`、`note_text`、`color`、`created_at`、`updated_at`。
- 前端 projection 不接受 raw Plate path / Slate path，也不再以 flat user asset DTO 作为目标合同；`base_id`、`unit_id`、`anchor_segment_id`、UTF-16 offsets、`selected_text` 和 `text_hash` 均从 `anchor` 读取。
- `quick_highlight` / `highlight` / `user_highlight` 投影为 `ReaderRecordPlateUserHighlightMark`，owner 为 `user`，DOM 暴露 `data-reader-record-user-asset-id`。
- `note` / `comment` / `reader_note` 投影为 `reader_record_user_comment_cue`，在原文旁显示小型 comment indicator，不显示大卡片。
- projection 只显示能通过本地只读校验的 asset：record、base、generation、unit、anchor segment、UTF-16 range 和 `fnv1a32-utf16(selected_text)` 都必须匹配。
- 用户高亮按 UTF-16 anchor range 切分 `segment_text` leaf 后再渲染；不得把一个词级 highlight 扩大成整句或整段背景。
- UI-D4 只读渲染 reload 后的资产，不打开 Highlight / Note 写入口，不调用 `/api/web/reader-notes`、`/api/web/reader-annotations` 或 `/api/web/reader-ask`。
- 不输出、不保存、不比较 raw Plate path / Slate path。

Domain ids：

- document 使用 `recordId`、`snapshotId`、`baseId`、`lastEventSequence`。
- unit 使用 `unitId`。
- source segment 使用 `anchorSegmentId` 和 `sentenceId`。
- marks/cues 使用 `markId`、`itemId`、`analysisId`、`layerId`、`assetId`。
- progress 使用 `capability + targetScope + targetKey + layerId/jobId`。
- 不输出、不保存、不比较 raw Plate path / Slate path。

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
  domainAnchorDraft?: UserEditorialAssetAnchor | null;
  activeMarkId?: string | null;
  activeCueId?: string | null;
  activeCommentId?: string | null;
  selectionText?: string | null;
  railFocus: "none" | "dictionary" | "ask" | "comment_composer";
  pinned: boolean;
};
```

UI-D2 新增只读 active anchor adapter：

```ts
userEditorialAssetAnchorDraftForActiveAnchor(
  document: ReaderRecordPlateDocument,
  active: ReaderRecordActiveAnchorInput,
): UserEditorialAssetAnchor | null
```

组装规则：

| Field | 来源 |
|---|---|
| `record_id` | `ReaderRecordPlateDocument.record.recordId` |
| `base_id` | `ReaderRecordPlateDocument.base.baseId` |
| `generation` | `ReaderRecordPlateDocument.record.generation` |
| `unit_id` | active source anchor |
| `anchor_segment_id` | active source anchor |
| `start_offset` / `end_offset` | active source anchor 的 unit-local UTF-16 offsets |
| `selected_text` | active source anchor |
| `text_hash` | active source anchor，且必须等于 `fnv1a32-utf16(selected_text)` |
| `hash_algorithm` | active source anchor，必须是 `fnv1a32-utf16` |
| `scope` | selection 使用自身 scope；system mark/cue 使用 `system_ai_layer` |

active source 类型：

- `selection`：来自 Plate selection 生成的 selection anchor draft。
- `system_mark`：来自 `ReaderRecordPlateTextAnchor`，例如 vocab / phrase / context / grammar mark。
- `system_cue`：来自 `ReaderRecordPlateTextAnchor`，例如 grammar cue；Structure Lens V1 如需发起 Ask，也应先落到同形 anchor。

adapter 只生成 read-only draft，用于 Lookup、Copy、disabled Ask preview、popover/rail 上下文传递。它不调用 API，不打开 Ask/Highlight/Note 写入，也不持久化 Plate path / Slate path。

UI-D5 Active Anchor Inspector 已在 `ReaderRecordPlateSurface` 内落地为前端局部状态：

- active mark 支持 vocabulary / grammar / user highlight；active cue 支持 grammar cue / sentence-analysis cue / user note/comment cue。
- active state 只保存 projection 中的 domain id、`anchor_segment_id`、`selected_text` 与对应 domain anchor；不保存、不序列化、不持久化 Plate path / Slate path。
- snapshot identity / event sequence / generation 变化时必须清空 active inspector，避免刷新后继续展示旧 layer 或旧 user asset 的陈旧 anchor。
- 点击或键盘聚焦 mark / cue 后，在中心阅读列内显示 compact detail panel；panel 使用 `aria-live`，可通过 Close 按钮或 Escape 关闭。
- vocabulary detail 显示 headword / phrase / gloss / example / reason；grammar detail 显示 grammar point / pattern / note；sentence-analysis detail 显示 label / analysis / chunks；user note/comment detail 显示“笔记/评论”、asset id 和已投影的 note text。
- cue marker 使用真实 button；mark 使用单个带 `role="button"`、`tabIndex`、click / keydown / focus handler 的 inline stack entry，避免无 handler 的伪 button，也避免重叠 marks 形成嵌套可交互元素。
- 同一 leaf 内 vocab / grammar / user highlight 重叠时，只暴露一个可聚焦 mark stack 入口；inspector 内按优先级展示 stack 中的全部 mark details。

返回 `null` 的情况：

- document root 缺少 `recordId`、`baseId` 或有效 `generation`。
- active source 缺少 `unit_id` / `anchor_segment_id`。
- `end_offset <= start_offset`。
- `selected_text` 为空或 UTF-16 长度与 offset span 不一致。
- `text_hash` 与 `selected_text` 的 `fnv1a32-utf16` 不一致。

规则：

- Plate selection 是最高优先级的 active source。用户正在划词时，toolbar 和 rails 都读取 selection 生成的 `domainAnchorDraft`。
- 点击系统 mark/cue 时，如果没有非折叠 selection，则 active source 变为 `system_mark` / `system_cue`。
- 点击用户 comment/highlight 时，如果没有非折叠 selection，则 active source 变为 `comment` / `user_highlight`。
- 打开 Dictionary / Ask / Comment composer 时，当前 anchor 进入 `pinned` 状态；rail 获焦不能清空中心 selection overlay。
- rail 内部继续操作时，source 可以显示为 `rail`，但必须保留被 pin 的 `domainAnchorDraft`。
- Escape 先关闭 toolbar / popover / annotation panel；再解除 pinned；最后才清空 selection 或 active cue。
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

目标：连续阅读，低干扰。它是旧版本“原文/沉浸模式”的文档化延续，而不是弱化版精读。

默认显示：

- 原文。
- 用户 highlight / comment indicator；用户自己的标注不能因为切换沉浸模式而消失。
- 轻量 vocabulary / phrase / context 标注；只保留弱提示，视觉必须明显弱于精读模式。

词汇类标注在沉浸模式中的视觉规则：

- 不使用大面积高亮背景。
- 不显示词汇 chip、解析正文或行间卡片。
- `vocab_highlight` 使用最弱视觉，例如低透明下划线、点线或轻微字体提示。
- `phrase_gloss` / `context_gloss` 可以比普通词汇略明显，但仍不能打断正文流。
- 单击词汇类标注只打开 quick peek / rail，不在正文中插入解释块。

默认不显示：

- 译文。
- grammar note / grammar explanation。
- sentence analysis / Structure Lens 正文。
- Structure Lens。

沉浸模式不是第三种“原文/双语”视图，它就是无译文、无重解析插块的连续阅读体验。沉浸模式不提供逐段打开译文的主路径；需要译文和解析时切回精读模式。模式切换只改变 display policy，不改变 Plate selection pipeline、Ask 能力、用户资产投影或后端 source/layer 事实。

### 精读模式

目标：完整解析，但保持文档感。

默认显示：

- 原文。
- Group-native translation 按阅读组显示译文，source group 后紧跟 translation lane。
- `vocab_highlight`、`phrase_gloss`、`context_gloss`。
- grammar note / grammar explanation，作为文档式行间注释，而不是旧式卡片。
- sentence analysis structure block，默认展开但保持紧凑。
- 用户 highlight / comment indicator。

默认不显示 Ask supplement 正文；用户显式保存或展开后再进入文档。

## 译文

### 当前约束

旧 unit-level translation 只有整段 `translated_text`，不能安全插到第一个
anchor segment 下方。Backend group-native translation 已改为由 worker 按
unit 生成连续 anchor segment groups，再由 snapshot 输出
`reader_translation_group`。Web Reader Record Plate 已在 projection 层接入该
node contract，并按 group 覆盖的最后一个 anchor segment 后插入译文块。

### Group-Native Translation 目标形态

冻结日期：2026-06-30。以下字段合同已作为 backend implementation 基线落地。

Translation worker/schema 已升级为 group-native contract。Translation worker 仍按
Reading Unit 接收完整 unit 文本，但 LLM 只输出 unit 内的语义分组决策和自然中文译文；
server 再根据 Stable Reading Base / Anchor Segment context hydrate source/hash 等事实字段。

```ts
type TranslationLayerOutput = {
  groups: Array<{
    group_id: string;
    anchor_segment_ids: string[];
    source_text_hash: string;
    translated_text: string;
  }>;
};
```

Translation Group 粒度由 LLM 在 unit 内基于语义决定。后端不设置 group size 上限、
数字阈值或 "1-3 个 segment" 规则；publisher 只校验事实合同：covered anchor ids
存在、按 unit 顺序连续、无 overlap、覆盖完整、source/hash/fingerprint 正确。
`group_id` 由 server 在 hydrate 阶段基于 unit/group segment range 确定性生成，
只作为 snapshot/render key；它不是 LLM 输出，也不是 stable source anchor。
per-segment source echo 不进入 durable `output_json`；相关 source/hash/type/boundary
事实留在 Stable Reading Base / Anchor Segment context 与 publisher validation 中。
group source text 也不进入 durable `output_json`；需要展示或校验时通过
`anchor_segment_ids` 回源到 Stable Reading Base 重新切片。

前端解析页直接消费 backend `reader_translation_group` / Translation Group。projection
按 `covered_anchor_segment_ids` 定位 group 覆盖范围，先输出一个合并后的 source group
paragraph，再紧跟对应译文 blockquote，然后输出该 group 覆盖范围内的 grammar_note /
sentence_analysis / ask supplement。这样译文贴近原文，解析块仍保留在对应 source group
之后。
若某个 group 内部因为 grammar_note、sentence_analysis 或版式需要出现视觉换行/插块，这属于
display-only layout policy：不得拆分后端 `TranslationGroup`，不得生成新的翻译事实，也不得按中文标点重新切译文。

分阶段要求：

- 当前前端：精读模式默认显示 backend translation group；沉浸模式隐藏译文。
- Defensive policy：`covered_anchor_segment_ids` 为空或无法全部定位到当前 unit source anchors 时，
  Web projection 跳过该 translation group，不在 unit 末尾兜底渲染。

## 文档 Marks And Cues

系统标注不再使用“全都下划线”的低可见度方案。2026-07-03 视觉基线采用单层
mark stack：Reader Record source text 的一个 leaf 只渲染一个 stack span，避免
vocabulary / grammar / user highlight / user note 嵌套 span 互相抢 selection 或 click。

| Layer | 默认形态 | Active / Hover |
|---|---|---|
| `vocab_highlight` | amber 高亮底 + 同色系深色文字，无下划线 | 打开词典 Quick Peek；hover 加深底色和文字 |
| `phrase_gloss` | violet/lavender 高亮底 + 同色系深色文字，无下划线 | 打开结构化短语解释；hover 加深 |
| `context_gloss` | sky/context 高亮底 + 同色系深色文字，无下划线 | 打开上下文释义；hover 加深 |
| `grammar_note` | link-like 清晰下划线，不默认加大块底色 | hover/active 时加 grammar-violet 浅底 + 更强下划线，并联动 grammar callout |
| `sentence_analysis` | source text 默认不显示 chunk 标注 | 只在 chunk row hover/focus/tap 时显示 borderless source overlay |
| `user_highlight` | 用户荧光笔背景 mark，无下划线 | 点击打开改色/删除；hover 加深但不产生边框/ring |
| `comment_note` / user note | 用户荧光笔背景 + 同色系较深下划线 | 打开个人笔记；hover/active 加深但不使用蓝色虚线 |

用户高亮只保留三种语义色：

- `warm_yellow`：重点，默认色。
- `soft_mint`：疑问，适合后续 Ask。
- `soft_rose`：难点，适合语法难点或复习点。

用户高亮的默认视觉只使用半透明背景，不改变正文颜色，不使用系统标注下划线。用户笔记 quote 可以有同色系下划线，因为它是 user-owned 资产。AI vocabulary/phrase/context 使用高亮底 + 字色；当它们与用户资产重叠时，用户资产背景优先，AI mark 降级为文字色。这样同一段文本同时有用户高亮、AI 标注和笔记时仍能保持正文可读。

### Marks / Cues Conflict Resolver

系统 marks、用户 highlights、comments、selection 和 active cue 必须通过统一 resolver 产出视觉 token。不要让每个 leaf/component 自己决定叠色。

视觉优先级：

| Priority | Layer | 视觉策略 |
|---:|---|---|
| 1 | 当前 selection | 半透明 overlay，覆盖所有 mark，但不改变文字颜色 |
| 2 | active user note / active user highlight | 高亮底色或同色下划线增强；不使用 border/ring/inset 导致行高跳动 |
| 3 | active system cue | grammar underline / sentence chunk overlay 临时增强 |
| 4 | user highlight / user note | 用户三色半透明底色；note 额外使用同色系下划线 |
| 5 | vocabulary / phrase / context system marks | amber / violet / sky 高亮底 + 同色文字；与用户资产重叠时降级为文字色 |
| 6 | grammar system mark | 默认清晰下划线；hover/active 才出现浅底色 |
| 7 | sentence-analysis chunk source overlay | 默认不显示；只由 chunk row hover/focus/tap 单向激活 |

合并规则：

- selection 永远是 overlay，不和 highlight/comment/system mark 的背景色相乘。
- 同一 text leaf 同时有 user highlight 和 vocabulary/phrase/context mark 时，背景取 user asset；system vocabulary mark 降级为文字色。
- 同一 text leaf 同时有 user note 和 user highlight 时，note 的下划线保留，背景仍保持 user asset 单层高亮。
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
| user highlight + vocab | 用户底色 + vocab 同色系文字；不叠第二层背景 |
| user note + grammar_note | note 底色/下划线保留；grammar 默认下划线或 active 浅底由 resolver 控制 |
| selection + user highlight + phrase_gloss | selection overlay 覆盖；toolbar anchor 来自 selection |
| active Structure Lens + grammar_note | Structure Lens chunk line 临时增强；grammar cue 降低强调但仍可点击 |
| multi_text comment ranges + system marks | 多个 ranges 共用 comment thread indicator；system marks 按各自 range 低干扰显示 |

系统 marks 默认低干扰，active/hover 时增强。用户资产比系统 marks 更明显，但不能遮盖正文可读性。

## Grammar Note

`grammar_note` 不再渲染为旧式卡片或 accordion，也不默认折叠到 hover popover。它应该像纸质书上的行间批注：在精读模式中常显、低干扰、紧贴相关原文。

默认形态：

- 原文上的细下划线 / cue 用于说明关联范围。
- 相关解释渲染为 Plate-native callout / annotation block，位于对应原文/译文组之后。
- callout 可包含 Markdown 内容，但必须通过 Plate-compatible children 渲染，不能退回孤立 HTML 递归。
- 精读模式显示；沉浸模式隐藏 explanation block，仅保留必要的轻量 lexical 标注。

交互：

- hover / focus cue 可以高亮 source span 和 callout 的关联。
- click cue 可以滚动或定位到对应 callout。
- popover 可提供 Ask / feedback 入口。

`grammar_note` 适合文档注释/脚注式心智，不适合大块业务面板。

### Markdown 支持（阶段一）

`grammar_note.note` 字段支持 Markdown 格式输出，projection 层通过 `deserializeMarkdownToBlocks(note)` 转为 Plate 节点树，由 `ReaderCalloutComponent` 渲染 Plate 注入的 children。

支持的 Markdown 语法：加粗、斜体、行内代码、无序/有序列表、引用块、代码块、H1-H6 标题（见"Markdown 渲染"section 完整清单）。

Prompt 层面引导 LLM 输出 markdown 格式的讲解内容，提升可读性。

## Sentence Analysis Structure Lens

`sentence_analysis` 是结构图层，不是普通注释，也不是默认折叠的 toggle。精读模式中应渲染为常显、紧凑的 Plate-native structure block，服务于“句子成分笔记”的阅读心智。

默认形态：

- 精读模式显示 always-open `reader_sentence_analysis_block`。
- block 内先展示结构化 `chunks` / 句子成分，再展示 Markdown `analysis`。
- 沉浸模式默认隐藏。
- chunk underlines 只在可唯一定位时 best-effort 显示，不能唯一定位时不画错误 underline。

已确认版式：

- block 放在对应原文/译文组之后，视觉上像文档内的手写结构笔记，而不是业务卡片。
- header 使用轻量图标 + `句子成分` 标签，不放折叠箭头，不做 toggle。
- `chunks` 区域优先展示，按 `order` 排列；每行包含角色标签、英文片段和中文说明。角色标签可以使用低饱和蓝/紫/青等小号文字，不使用大面积色块。
- 英文片段使用正文同族字体，字号略小于正文，可用 italic / medium weight 区分；不要做大段彩色背景。
- Markdown `analysis` 放在 chunk rows 之后，字号略小、行高紧凑，用于补充解释，不抢正文重心。
- 如果 `analysis` 已经完整覆盖结构说明，chunk rows 仍保留；它们是结构化导航，不只是解析正文的重复展示。
- block 宽度跟随正文栏，不跨出阅读列；背景使用极浅灰或低透明 tint，边框弱化，radius 不超过正文 callout 的视觉强度。
- 同一句如果同时有 grammar note 和 sentence analysis，顺序为：原文 -> 译文 -> grammar note -> sentence analysis；除非 grammar note 只解释句析块中的某个成分。

V1 激活后：

- 显示 structure block。
- block 包含整体 `analysis` 和 chunk list。
- 如果前端能唯一匹配 `chunks[].text`，可以做 best-effort chunk underline；不能唯一匹配时只展示 structure block，不画错误 underline。

V2 激活后：

- 在原句上显示 chunk underlines / fine rules / numbered spans。
- structure block 和原文 chunk 双向联动。
- hover legend item 增强对应原文 chunk。
- hover 原文 chunk 增强对应 legend item。

structure block 是文档内批注，不是 side panel，也不是旧式业务卡片。

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

### Markdown 支持（阶段一）

`sentence_analysis.analysis` 字段支持 Markdown 格式输出，projection 层通过 `deserializeMarkdownToBlocks(analysis)` 转为 Plate 节点树，由 `ReaderSentenceAnalysisComponent` 渲染 Plate 注入的 children。

`sentence_analysis.chunks` 仍保持原有结构化数据形态（chunks 是结构化数据，不是 markdown 文本），不受 Markdown 化影响。

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
| Ask | 已启用第一轮 Reading Record scope；需升级到全上下文 anchor set | Plate selection 或 active note/callout 能生成 reading record anchor attachment；仍不走 Plate AI plugin |
| Comment / Note | 目标为 anchor-backed user asset；当前代码仍是 single-range 过渡 | source selection 写 source anchor ranges；非 source visible text 回源并保存 provenance |
| Highlight | 目标为 anchor-backed user asset；当前代码仍是 single-range 过渡 | source selection 写 source anchor ranges；非 source visible text 不直接持久化为 AI-text highlight |

不可执行状态仍必须有明确 disabled semantics、tooltip 或 coming-soon copy。Reading Record 页面不能回退调用旧 Ask route 或依赖旧 analysis record contract。

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

### Lookup Interaction

Lookup 是 Claread 核心能力，但不能破坏文档阅读和 Plate selection。

默认触发规则：

- 单击 `vocab_highlight`：打开 dictionary quick peek，并调用词典接口。
- 单击 `phrase_gloss` / `context_gloss`：打开已发布的短语/语境解释，不默认调用词典接口。
- 单击 `grammar_note`：打开或定位 grammar note，不触发词典。
- 单击普通 source text：不自动查词。
- 拖选普通 source text：打开 selection toolbar，用户显式选择 Lookup / Ask / Note / Highlight / Copy。
- 选中译文、grammar note、sentence analysis 等 AI-visible text：不触发自动 quick peek；可 Ask / Note / Copy，英文片段可通过 toolbar 显式 Lookup。

后续可以增加“单击普通原文查词”设置项，但默认关闭。该能力难点在前端 click hit-test、拖选/双击冲突治理、Plate mark 优先级和词典查询质量，不是后端 anchor 数据结构。

### Quick Peek source-identity close 与 frozen rect

> 来源：T4.2a-PUX-R4-R3-R1 闭合（commit `9a925f82`）。本节固化 Quick Peek 在 full reload 与 source identity 切换时的视觉/交互语义。

#### 稳定身份与重新锚定

Quick Peek 的稳定身份是 `anchor_segment_id + markId + generation + baseId` 四元组。full reload（如同 source identity 内的 snapshot 刷新、Plate value 重建）发生时，若四元组未变，Quick Peek 必须保持打开并重新锚定到原 vocabulary mark：

- 重新锚定使用 `[data-anchor-segment-id] [data-reader-record-vocabulary-mark-id]` 组合选择器精确定位原 mark，**不得**回退挂到同段 sibling mark。
- 从 `setValue` 到 rAF 回调之间，Quick Peek 浮层使用 frozen rect 维持位置，避免出现 detached `(0,0)` panel。
- rAF guard 的终态必须区分：restore token 失配或 markId 切换时，旧 rAF **只 abort，不触碰**当前 Quick Peek/ref；最新 interaction 拥有状态。
- `{generation, base_id}` fence 失配时，source-identity reset 负责关闭 Quick Peek；旧 rAF 只清理其 stale anchor，避免 detached panel。
- 精确 resolver 找不到原 mark 时，才由该 restore 请求确定性关闭 Quick Peek。

#### Source-identity close

`{generation, base_id}` 共同构成 source identity。任一变化时，Surface 必须一次性清理：

- `activeSelection`
- `lookupState` / `inspectState`
- Quick Peek restore token（递增，使 pending rAF 失效）
- `quickPeekAnchorRef`
- `activeSentenceChunkId` / `activeGrammarItemId`
- `grammarExpansionControlRef`（grammar expansion）

禁止跨 source identity 恢复 Quick Peek 或 grammar expansion。source identity 切换后的旧 Quick Peek panel 必须关闭，不得遗留 detached panel。

#### 与 polling/page seam 的关系

rejected stale/fence snapshot 属于 polling/page seam（见 [`representation-event-contract.md`](./representation-event-contract.md#polling--page-seamacceptedrejected-snapshot-合同边界)）：当前 accepted UI 与已打开的 Quick Peek 必须保持，rejected snapshot 不得进入 Surface value swap 路径。Surface 的 same-snapshot early-return 只是 duplicate accepted snapshot guard，不承担 stale/fence rejection 语义。

### FloatingToolbarKit 接入（阶段二 V2-Step-1）✅ 已落地

已用 Plate 官方 floating hook 接入 Claread selection toolbar，并收敛为新版 surface 的唯一划选 toolbar：

- 安装并使用 `@platejs/floating`
- 新建 `apps/web/src/components/editor/plugins/floating-toolbar-kit.tsx`，定制按钮为 Claread action
- 修改 `ReaderRecordPlateSurface.tsx`，把 Plate surface 从纯 React 改为真正 `<Plate + readOnly>` + `FloatingToolbar`
- `FloatingToolbar` 使用 `useFloatingToolbar` / `useFloatingToolbarState`，由 Plate selection 管理显隐
- `ReaderFloatingSurface` 仍用于 quick peek、highlight menu、feedback 等 Claread 浮层；旧 `SelectionActionStrip` 不在新版 surface 生产路径

关键约束：
- toolbar 按钮只放 Claread action，不放编辑格式按钮
- Ask / Comment / Highlight 只在可生成合法 Reading Record anchor 时执行
- 来源：Plate editors demo 验证 `FloatingToolbarKit` 支持选中文本后浮现（含 "Ask AI" 按钮，可定制）

## 用户资产

用户资产采用双轨。

### Quick Highlight

用于快速标记。

- 目标模型写入 User Asset / anchor ranges；当前过渡仍可复用 `user_annotations`。
- 当前 V1c 代码只支持单个 `UserEditorialAssetAnchor` 表达的 sentence/full-segment 或 `text_range`，这是实现现状，不是最终产品边界。
- 多段 / 跨 block 高亮后续应走 `UserEditorialAssetAnchorSet` 或等价 `user_asset_anchor_ranges` persistence contract。
- 作为 user-owned mark 投影到 Plate surface。
- 仅三种颜色：`warm_yellow` / `soft_mint` / `soft_rose`；默认 `warm_yellow`。
- 用户高亮视觉使用半透明背景，不使用 AI 系统标注的下划线语义。

### User Note

用于用户自己的阅读笔记。Claread 当前没有协作评论需求，Plate Comment 只作为 UI/projection 能力使用，不成为产品语义或持久化事实。

- 写入现有 `reader_notes`。
- 目标模型写入 User Asset / anchor ranges；当前过渡仍可复用 `reader_notes`。
- 当前 V1c 代码只支持单个 `UserEditorialAssetAnchor` 表达的 sentence/full-segment 或 `text_range`，这是实现现状，不是最终产品边界。
- 多段 / 跨 block 笔记后续应走 `UserEditorialAssetAnchorSet` 或等价 `user_asset_anchor_ranges` persistence contract。
- 使用官方 Plate Comment 组件/mark 模型渲染为 comment-style mark / indicator / note panel。
- Plate comment id 只是 Web projection key，由 `reader_notes.id` 派生。
- 不建模多人协作、回复、mention、resolved/archive 状态或 comment 权限。

第一版不新增 comment backend。后续如统一为 User Editorial Assets，可再做 schema migration。

### CommentKit 改造（阶段二 V2-Step-2）✅ 已落地

已用 Plate `CommentPlugin` + `CommentLeaf` mark 承接个人笔记 mark 和 draft active state；笔记面板、持久化和动作仍是 Claread 自定义：

- 安装并使用 `@platejs/comment`
- 新建 `apps/web/src/components/editor/plugins/comment-kit.tsx`，改造 CommentKit：
  - `CommentLeaf` 高亮样式改为 Reader 划线色
  - 移除 draft -> 正式评论的多用户流转，简化为"选区即个人笔记"
  - `comment_<noteId>` mark 的 `<noteId>` 派生自 `reader_notes.id`
  - 点击 mark 通过 `activeId` 打开 `InlineCommentPanel`
- 新建 `InlineCommentPanel` 作为个人笔记 composer/view/edit/delete 面板；未接入 `DiscussionKit` / `BlockDiscussion`
- `ReaderRecordPlateSurface.tsx` 的渲染路径已从 `ReaderRecordNoteComposer` 迁到 `InlineCommentPanel`，但 `ReaderRecordNoteComposer` 函数定义仍是遗留死代码，后续应清理
- 保留 `/api/web/reading-record/notes` 端点，但前端走 Plate comment projection

关键约束：
- Plate comment id 只是 Web projection key，不持久化为业务事实
- 持久化仍是 `reader_notes` 表
- 来源：Plate editors demo 验证 `CommentKit` 支持 "overlapping annotations"（多段文本重叠评论），适合 Claread 多层 marks 重叠场景

### User Note Projection Contract

`reader_notes` 到 Plate comment-style UI 的投影需要稳定映射：

| Domain | Plate projection |
|---|---|
| `reader_notes.id` | `commentId` / note projection id |
| `reader_notes.target_key` | projection lookup key |
| `quote_mode` | `sentence` / `text_range` / `multi_text` display mode |
| `segments` | one or more comment mark ranges |
| `note_text` | note body |

规则：

- Plate comment id 由 `reader_notes.id` 派生，不能随机生成。
- `text_range` note 投影为单个 comment mark。
- `multi_text` note 投影为多个 ranges，共用同一个 note projection id。
- 允许多个个人笔记的 anchor 重叠、互相包含或共享同一 source segment；这些笔记不合并、不互斥。
- Projection 必须按所有 user note anchor 边界切分 source leaves，并在每个最小 leaf 上叠加所有覆盖该 leaf 的 Plate comment mark keys，例如 `comment_<noteId>`。
- 不应继续用单个 `user_note: true` + 单个 `user_note_data` 表达用户笔记；该形态无法正确表达重叠/子串笔记。
- Plate `getCommentCount(leaf)` / overlapping comment behavior 用于显示重叠注释状态。
- 点击覆盖多个笔记的文本时，note panel 应展示所有覆盖该 leaf 的个人笔记，并明确当前 active note。
- 在已有笔记范围内新建更小范围笔记是合法操作；它创建新的独立个人笔记，不是 reply。
- 点击某个笔记 mark 时，打开当前 source sentence / anchor segment 下的 note stack，并滚动或聚焦到被点击的那条个人笔记。
- stack 中每条笔记先展示自己的选区 quote，再展示 note body。
- 如果多个笔记使用完全相同的 normalized anchor range，正文视觉上会落在同一个 text span；stack 仍展示所有匹配笔记，默认让最新或显式点击的笔记成为 active。
- 已确认：用户在完全相同选区上再次创建笔记时，不静默覆盖已有笔记；UI 应提示“该文本已有笔记”，提供查看/编辑已有笔记，并把“仍新增一条”作为明确 secondary action。
- 用户创建部分重叠或子串笔记时，不需要重复提醒；这是正常 nested / overlapping annotation。
- 已确认：用户可以从译文、grammar note、sentence analysis、Ask supplement 等非原文可选文本发起个人笔记，但 V1 持久化仍映射回对应 stable source anchor。
- 非原文发起的笔记需要保存非权威 provenance，例如 `created_from_visible_scope` 和 `selected_visible_text`，用于解释“这条笔记是从译文/解析内容发起，但绑定到对应原文”。
- V1 不把个人笔记直接持久化到可再生成的 AI 文本本身，避免 layer regenerate 后笔记漂移。
- 删除 / 更新仍走 `reader_notes` API；Plate 状态只反映服务端结果。
- V1 不处理协作回复、resolved / archived thread 状态；后续如需要再扩展 `reader_notes` 或 User Editorial Assets。

## Anchor And Persistence

目标写入策略：**user asset + source-grounded anchor ranges**。

当前 V1c 最小实现仍是 **single-range first**。这是为了让现有 `/app/reader-record/{recordId}` 能先安全写入，不代表产品最终只允许单段笔记/高亮。

最终模型应满足：

- 一个用户资产有稳定 asset id。
- 一个资产可以有 1..N 个 source anchor ranges。
- `visible_selected_text` 记录用户肉眼选中的文本。
- `created_from_visible_scope` 记录来源：source / translation / grammar_note / sentence_analysis / ask_supplement / mixed。
- 非 source 可见文本发起的持久资产必须回源到 stable source anchor ranges，不能直接绑定到可再生成 AI 文本。
- 同一 normalized range 可以有多条独立 note；UI 提醒但允许新增，后端不能静默 upsert 覆盖旧笔记。

V1c 过渡写入策略：**single-range first**。

边界判断：

- 旧表可复用：`user_annotations` 保存 quick highlight，`reader_notes` 保存 comment/note body。
- `/app/reader-record/{recordId}` 新写入必须携带 `anchor: UserEditorialAssetAnchor`；没有 `anchor` 的请求只能属于旧 `/app/reader/{recordId}` legacy 路径。
- D6-A5 当前代码已经把 `anchor` 做成 optional dual-contract：当 `anchor` 存在时，legacy 必填字段放宽，但服务层必须走 Reading Record anchor gate，绕过 legacy `target_key` / `render_scene` 校验。
- 旧请求字段（`sentence_id`、`target_key`、`paragraph_id`、offset、hash）只能作为 deprecated compatibility metadata；不能重新成为 `/app/reader-record` 写入校验事实源。
- 旧 `render_scene` 校验不可复用：新 Reading Record 的 source of truth 是 Canonical Text Layer / Anchor Segment；当前过渡实现可继续通过 Stable Reading Base 校验。
- Plate path / Slate path 不进入 API、不进入数据库、不进入 event log。
- D6-U2 结论：`UserEditorialAssetAnchor` 和当前 `anchor_gate` 只表达 single range；`multi_text` 不挤进该 DTO。后续 multi-range 必须使用 schema-only 草案 `UserEditorialAssetAnchorSet` 或等价 anchor ranges contract；在 persistence/migration 完成前，相关写入口可作为过渡实现临时 disabled，但这不是产品边界。

Plate selection adapter 需要生成的新写入 payload：

```ts
type UserEditorialAssetAnchor = {
  record_id: string;
  base_id: string;
  generation: number;
  unit_id: string;
  anchor_segment_id: string;
  scope?: "stable_source" | "translation" | "system_ai_layer" | "ask_supplement";
  offset_unit?: "utf16";
  start_offset: number;
  end_offset: number;
  selected_text: string;
  text_hash: string;
  hash_algorithm?: "fnv1a32-utf16";
};

type ReaderRecordUserAssetWritePayload = {
  anchor: UserEditorialAssetAnchor;
  selected_text: string;
  note_text?: string;
  color?: string;
};
```

短期：

- 新 Plate surface 可继续生成 legacy alias metadata 供调试/兼容，但 write action 必须以 `anchor` 为唯一校验输入。
- D6-U7 当前后端已完成 Reading Record anchor gate + V1c persistence，Web Plate surface 可在 stable-source single-range selection 上启用 Highlight / Note 最小写入。
- D6-U7 Web 写入口为 `/api/web/reading-record/highlights` 与 `/api/web/reading-record/notes`，请求必须携带 nested `anchor`；保存成功后触发 snapshot reload。
- 不允许假设新 Reading Record id 一定能通过旧 `analysis_results.render_scene_json` 校验。
- Web 可以继续生成 `UserEditorialAssetAnchor` draft 供 Lookup/Copy/Ask 预览使用；Ask / Feedback 写入口仍 disabled。

中期：

- `user_annotations` / `reader_notes` 的 single-range 校验从旧 `render_scene` 迁到 Canonical Text Layer / Anchor Segment，并保持 legacy `/app/reader/{recordId}` 行为不变。
- 对外 anchor 优先使用 `anchor_segment_id`，`sentence_id` 只保留兼容 alias。
- `multi_text` 需要 `UserEditorialAssetAnchorSet` / multi-range gate、projection reload 规则和 persistence contract 后再启用。
- API 错误模型需要区分 stale anchor、hash mismatch、range out of bounds、record mismatch 和 unsupported anchor mode。

如果 Canonical Text Layer / Anchor Segment 校验或 persistence 尚未完成，V1a / V1b 可以先显示 Comment/Highlight 按钮的 disabled 或 coming-soon 状态，但不能调用旧 render scene 写入路径。

## Ask Claread Context

Ask Claread 是全局可选能力，但上下文必须稳定回源。

可发起 Ask 的可见范围：

- source text。
- translation text。
- grammar note。
- sentence analysis。
- user highlight。
- user note quote / note stack。
- ask supplement。
- 多段 Plate selection。

Ask payload 必须包含：

- `visible_selected_text`：用户肉眼选中的可见文本。
- `created_from_visible_scope`：source / translation / grammar_note / sentence_analysis / user_asset / ask_supplement / mixed。
- `anchor_set`：映射回 stable source 的 1..N 个 source anchor ranges。
- source context window：选区附近 source segments / unit context。
- related enhancements：相关 translation items、vocabulary marks、grammar notes、sentence analysis。
- related user assets：相关 user highlights / notes；仅在用户明确选中或上下文必要时提供。
- reading goal / variant / translation profile metadata。

规则：

- 对 AI 生成内容发问时，也必须回源到对应 source anchor ranges，并同时带上被选中的 AI 文本。
- 多段选区按文档顺序传入多个 anchor ranges。
- 无法稳定回源的纯 UI 文本只能触发 degraded Ask，不得创建持久用户资产。
- Ask 不从 raw Plate JSON、Plate path 或 DOM range 反推业务上下文。

## Ask Supplement

Ask 回答默认留在 Ask rail。

Ask 按钮和 Ask rail anchor injection 已在 Reading Record scope 上完成第一轮接线：

- `ReaderRecordPlateSurface` 以 `recordScope="reading_record"` 打开 `AiWorkspacePanel`。
- selection / saved note / callout 能生成 reading record anchor attachment 后可进入 Ask。
- 不调用旧 Ask route，不把旧 analysis record contract 包装成新 Reader Record Ask contract。
- 后续 action proposal、cross-record grounding 和 Structure Lens cue 入口仍需单独完成。

用户明确保存后，才进入文档 projection。

保存目标：

- 保存为 Comment Note：用于用户个人沉淀。
- 保存为 Ask Supplement：用于 AI 对 grammar、sentence_analysis、phrase/context 的补充解释。

Ask Supplement 进入文档后不渲染为卡片。它应表现为文档注释 / 脚注式 supplement cue。

第一版不实现 Ask Supplement 入文档，只保留保存策略和后续接口边界。

## 实施切片

### V1a: Direct Plate Document Surface

必须包含：

- `ReaderRecordPlateSurface` 通过 `projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot)` 消费新 projection schema。
- 使用 `Plate + readOnly`。
- 不经过 `adaptReaderPlateSnapshotToReaderVm`。
- 不经过 `renderSceneToPlateDocument`。
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

- 后端支持用新 Reading Record / Stable Base / Anchor Segment 校验 single-range `UserEditorialAssetAnchor`。
- Web BFF 能把 Plate selection payload 转为现有 `user_annotations` / `reader_notes` request。

必须包含：

- 高亮持久化到现有 `user_annotations`。
- 评论/笔记持久化到现有 `reader_notes`。
- 高亮 / 笔记 reload 后能重新投影到正确 range。
- Plate comment/discussion 只作为前端 projection。
- `multi_text` 不属于当前 V1c first production 写入；必须等 `UserEditorialAssetAnchorSet` 或等价 multi-range persistence contract。但产品目标已经确认需要支持 source-grounded multi-range note/highlight。

### V1d: Sentence Analysis Structure Block

V1d 可以在 Sentence Analysis V2 offset schema 之前做基础版本：

- 用专用 `reader_sentence_analysis_block` 替代当前通用 callout。
- block 默认常显，不使用默认折叠 toggle，也不依赖 floating legend 作为主入口。
- block 内先展示 chunk rows，再展示 Markdown analysis。
- chunk underlines / numbered decorations 只在唯一匹配时 best-effort 显示。
- 不能唯一匹配时只显示 structure block，不画错误 source decoration。

Group-native schema 完成后：

- chunk underlines / numbered spans 成为必选能力。
- hover chunk row 和原文 chunk 双向联动。

### 第一阶段不包含：

- group-native translation 的最终 Web Plate 渲染接入。
- sentence_analysis chunk offset schema V2。
- Ask Supplement 入文档。
- AI suggestion / revision。
- 新 comment backend。
- projection_ops incremental applier。
- fixed toolbar 或编辑器格式化能力。

第一阶段仍有限制 / disabled / coming soon：

- Ask：已完成第一轮 Reading Record scope 接线；后续升级为 `visible_selected_text + anchor_set + full context`，不能回退到 legacy Ask contract。
- Comment/Note：D6-U7 起仅 stable-source single-range selection 可写是当前过渡状态；目标方案需要支持 source-grounded multi-range note，并允许非 source visible text 回源创建 note。
- Highlight：D6-U7 起仅 stable-source single-range selection 可写是当前过渡状态；目标方案需要支持 source-grounded multi-range highlight。非 source visible text 不直接生成持久 AI-text highlight。
- Feedback：直到 AI mark/cue feedback contract 与新 route 稳定。
- Ask supplement save-to-document：直到 Ask Supplement Projection 设计和 API 完成。
- sentence_analysis chunk underlines：直到 V1d best-effort 或 Sentence Analysis V2；默认不显示。
- backend translation group：backend contract 与 Web Plate projection 接入已完成。

## 后续需求

### Group-Native Translation

Backend 已升级 translation worker/schema，输出 backend group-native translation。
LLM 在单个 Reading Unit 内决定语义分组；server hydrate source/hash；Web projection
消费 `reader_translation_group` 并按 covered span interleave 译文块。后续只在
display policy 中处理 grammar_note / sentence_analysis 引发的视觉换行或插块。

### Sentence Analysis V2

升级 chunk schema，增加 offsets、role、depth 和 parent relation。

### Anchor Validation Cutover

把 `user_annotations` / `reader_notes` 的 single-range 校验从旧 `render_scene` 迁到 Canonical Text Layer / Anchor Segment；当前过渡实现可继续走 Stable Reading Base，`multi_text` 另走 `UserEditorialAssetAnchorSet` contract。

### Ask Supplement Projection

用户保存 Ask 回答后，投影为文档注释 / supplement cue。

### Suggestion / Revision

后置到：

- Ask 修订系统解析。
- Candidate Document preview/edit。
- 用户笔记改写建议。
- Ask Supplement 替换版本。

## 验收标准

V1a 验收：

- `/app/reader-record/{recordId}` 中心文档不再通过旧 `ReaderVm` 适配。
- 沉浸模式显示原文、轻量 vocabulary/phrase/context 标注、用户高亮和笔记；不显示译文、grammar explanation、sentence analysis 正文。
- 精读模式显示译文、grammar note callout、sentence analysis structure block 和系统 marks/cues。
- unit 级译文显示为“本段译文”，不会插到单个 anchor segment 后面。
- grammar note 以 Plate-native callout / annotation block 常显，不以旧式卡片或默认折叠 popover 呈现。
- sentence analysis 以 always-open structure block 呈现，不以旧式卡片或默认折叠 toggle 呈现。
- 系统 marks、用户 highlights、comments、selection 的视觉层级不互相遮挡。
- 大块解析状态卡被 header chip / slim progress strip / layer activity indicator 替代。
- active anchor state 能表达 selection、system mark/cue、comment/highlight 和 rail focus。
- 不出现 Plate fixed toolbar。
- 不出现旧式 grammar / sentence analysis 卡片。

V1b 验收：

- 选中文本后 toolbar 显示 Lookup、Copy。
- Ask 按钮使用 Reading Record scope，并准备接入 `anchor_set + full context`。
- D6-U7 当前 Comment/Note 和 Highlight 对 stable-source single-range selection 可执行；multi-segment / 非 stable-source selection 是实现债务，不是产品否决。
- 打开词典或 Ask 后，中心选区仍可见。
- Dictionary 接收到的是 Plate selection 生成的 domain anchor draft。
- Ask 接收到的是 Plate selection / active cue 生成的 visible selection + source-grounded anchor data。
- disabled 按钮有语义 disabled 状态，不能只是视觉灰掉。

V1c 验收：

- 高亮可保存、重新加载后仍投影到正确 range。
- 笔记可保存、重新加载后仍投影为 comment/note indicator。
- 写入路径不依赖旧 `analysis_results.render_scene_json` 校验新 Reading Record id。
- 旧 `user_annotations` / `reader_notes` 表继续复用，但校验经过 Reading Record anchor gate。
- 写入失败能区分 stale anchor、hash mismatch、range invalid 和 record mismatch。

V1d 验收：

- sentence analysis 使用专用 always-open structure block，不再伪装为通用 callout。
- structure block 内 chunk rows 在 Markdown analysis 之前显示。
- 当前 schema 下 chunk underlines 只在唯一匹配时显示；无法唯一匹配时不显示错误 underline。

### 移动端统一 Action Sheet

窄屏下 Dictionary、Ask、Comment / Note composer 使用同一个底部容器，暂命名为 `ReaderMobileActionSheet`。桌面端仍可保留 docked rail、floating popover 或 comment panel；统一 bottom sheet 只约束移动端默认交互。

当前代码状态：

- `ReaderRecordPlateSurface` 已经为 Dictionary detail 提供 `xl:hidden` 的 bottom compact panel。
- Ask 仍由独立 `AiWorkspacePanel` 承载。
- Note 仍由 `InlineCommentPanel` 通过 floating layer 锚定选区或 comment mark。
- Quick Peek 仍是 selection / mark 附近的 floating preview。

目标规则：

- Plate selection toolbar 或 active mark 触发 Lookup / Ask / Note 后，移动端统一打开 `ReaderMobileActionSheet`。
- sheet header 显示当前 `visible_selected_text` / anchor 摘要，用户能确认自己正在操作哪一处文本。
- sheet content 按当前任务切换为 Dictionary、Ask 或 Note；切换内容不能清空 pinned Plate selection / anchor。
- 同一时间只打开一个移动端 action sheet，避免 Dictionary、Ask、Note 多个浮层互相遮挡。
- sheet 关闭后 focus 返回正文或触发按钮；如果用户继续阅读，selection overlay 可以随关闭动作清理。
- quick peek 可以保留为轻量预览，但进入详情、Ask 或 Note 后必须收敛到统一 sheet。
- sheet 高度默认不超过视口 72%，支持内部滚动，不能把正文和 selection toolbar 同时遮住。

可访问性验收：

- toolbar、popover、structure block、desktop rail 和 mobile sheet 都支持键盘访问。
- Escape 能关闭 toolbar / popover / annotation panel，并恢复合理 focus。
- focus trap 不阻断 Ask、Dictionary 和正文之间的返回路径。
- 色彩不是唯一状态指示；marks/cues 至少有线型、编号或图标差异。
- 支持 `prefers-reduced-motion`。
- disabled / coming soon action 使用真实 disabled semantics，并提供可读原因。
- comment indicator、grammar cue、structure cue 需要可通过键盘聚焦，且有 `aria-label` / tooltip 说明。
- floating toolbar 出现后不抢走正文 selection 的语义；关闭后 focus 返回正文或触发按钮。
- progress strip 不能只靠颜色表达 layer 状态，需要有文本状态入口或可访问名称。
- popover / annotation panel 打开时应宣布标题和状态，不把整篇正文重新读一遍。
- 文本缩放到 200% 时，toolbar、popover 和 bottom sheet 不遮挡核心正文。

移动端验收：

- Dictionary、Ask、Comment composer 在窄屏下使用统一 bottom sheet / `ReaderMobileActionSheet`。
- 触屏 selection 不被 floating toolbar 遮挡。
- toolbar 按钮数量在移动端收敛，V1b 至少保留 Lookup、Copy；Ask/Highlight/Note 可作为 disabled / coming soon action 出现在更多菜单。
- bottom sheet 打开后保留当前 anchor 摘要，并允许返回正文重新选择。
- 触控目标不小于 44px，图标按钮需要可见 label 或 accessible label。
- 不使用需要精确点击细下划线的唯一入口；grammar / structure cue 在移动端需要有足够命中的 cue target。
- 横屏和窄屏不出现横向滚动；正文、toolbar、bottom sheet 不能互相覆盖。
- iOS/Android selection handle 与 floating toolbar 冲突时，toolbar 应下移或转为 bottom action bar。
