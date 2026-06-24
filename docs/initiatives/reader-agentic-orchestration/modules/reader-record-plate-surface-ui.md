# Reader Record Plate Surface UI

> 状态：目标方案草案；UI-D6A 默认 Plate 真实流程已验收；UI-D4 阅读态打磨已落地；UI-D5 Active Anchor Inspector 已落地；UI-D6B 真实流程验收与 UI 收敛评估已落地（含三进程真实联调）
> 最后更新：2026-06-24
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
- V1c first 只要求能从 Stable Reading Base / Anchor Segment 校验 single-range `UserEditorialAssetAnchor`。
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
| `reader_sentence_analysis` | `reader_record_sentence_analysis_cue` | V1 只投影为 Structure Lens cue；不进入文档流卡片 |
| `snapshot.user_assets` | `ReaderRecordPlateUserHighlightMark[]` + `reader_record_user_comment_cue` | quick highlight 投影为 user-owned text mark；note/comment 投影为小型 comment indicator，不进入文档流卡片 |
| `enhancement_progress` | `document.progress` + `unit.progress` | document 用于 header chip / slim strip；unit 匹配 unit 或 anchor_segment target 的 layer activity |

Translation V1 约束：

- `target_scope="unit"` 的译文只能生成 `reader_record_unit_translation`。
- `reader_record_unit_translation.placement` 必须是 `"unit"`。
- anchor segment 的 `children` 只能包含 stable source text leaves；不能包含 unit translation。
- Characterization test 必须覆盖该行为，防止旧 adapter 再次把 unit 译文塞到首个 segment 后。

Sentence Analysis V1 约束：

- `reader_sentence_analysis` 不作为 `children` 中的 block/card 输出。
- Projection 只生成 `reader_record_sentence_analysis_cue`。
- cue 保留 `analysisId`、`layerId`、`anchorSegmentId`、`label`、`analysis` 和 `chunks`。
- chunk underline 仍等 V1d best-effort 或 Sentence Analysis V2 offset schema。

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

UI-D6A 验收结论：

- `/app/reader-record/{recordId}` 默认 loaded surface 是 `ReaderRecordPlateSurface`，Workbench 只作为显式 fallback。
- `processing`、`readable_enhancing`、`failed`、`action_required` 都使用 compact progress chip / slim strip 表达；正文和已投影译文不被状态 UI 替换。
- Plate 默认路径不显示旧 Workbench 大状态卡：不出现 `reader-record-status-banner` 或 `reader-record-enhancement-progress`。
- 当前组件已符合轻量状态目标，本轮不需要额外运行时视觉改动。

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
- V1c single-range first：支持单个 `UserEditorialAssetAnchor` 表达的 sentence/full-segment 或 `text_range`。
- `multi_text` 暂不作为 V1c production 写入；后续走 `UserEditorialAssetAnchorSet`。
- 作为 user-owned mark 投影到 Plate surface。

### Comment Note

用于有正文的笔记和讨论。

- 写入现有 `reader_notes`。
- V1c single-range first：支持单个 `UserEditorialAssetAnchor` 表达的 sentence/full-segment 或 `text_range`。
- `multi_text` 暂不作为 V1c production 写入；后续走 `UserEditorialAssetAnchorSet`。
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

V1c 最小写入策略：**single-range first**。

边界判断：

- 旧表可复用：`user_annotations` 保存 quick highlight，`reader_notes` 保存 comment/note body。
- `/app/reader-record/{recordId}` 新写入必须携带 `anchor: UserEditorialAssetAnchor`；没有 `anchor` 的请求只能属于旧 `/app/reader/{recordId}` legacy 路径。
- D6-A5 当前代码已经把 `anchor` 做成 optional dual-contract：当 `anchor` 存在时，legacy 必填字段放宽，但服务层必须走 Reading Record anchor gate，绕过 legacy `target_key` / `render_scene` 校验。
- 旧请求字段（`sentence_id`、`target_key`、`paragraph_id`、offset、hash）只能作为 deprecated compatibility metadata；不能重新成为 `/app/reader-record` 写入校验事实源。
- 旧 `render_scene` 校验不可复用：新 Reading Record 的 source of truth 是 Stable Reading Base / Anchor Segment。
- Plate path / Slate path 不进入 API、不进入数据库、不进入 event log。
- D6-U2 结论：`UserEditorialAssetAnchor` 和当前 `anchor_gate` 只表达 single range；`multi_text` 不挤进该 DTO。后续 multi-range 必须使用 schema-only 草案 `UserEditorialAssetAnchorSet`，并在引入 persistence/migration 前保持 disabled。

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

- `user_annotations` / `reader_notes` 的 single-range 校验从旧 `render_scene` 迁到 Stable Reading Base / Anchor Segment，并保持 legacy `/app/reader/{recordId}` 行为不变。
- 对外 anchor 优先使用 `anchor_segment_id`，`sentence_id` 只保留兼容 alias。
- `multi_text` 需要 `UserEditorialAssetAnchorSet` / multi-range gate、projection reload 规则和 persistence contract 后再启用。
- API 错误模型需要区分 stale anchor、hash mismatch、range out of bounds、record mismatch 和 unsupported anchor mode。

如果 Stable Base / Anchor Segment 校验或 persistence 尚未完成，V1a / V1b 可以先显示 Comment/Highlight 按钮的 disabled 或 coming-soon 状态，但不能调用旧 render scene 写入路径。

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

当前实现状态（UI-D3 / UI-D4 / UI-D5 / UI-D6A read-only scaffold）：

- 已新增 `ReaderRecordPlateSurface` 组件，输入为 `ReaderPlateSnapshotDto`，内部直接调用 `projectReaderPlateSnapshotToReaderRecordPlateDocument(snapshot)`。
- scaffold 使用 `Plate + readOnly` 渲染，不显示 editor formatting toolbar。
- scaffold 最小展示 stable source text、unit-level translation block、vocab / grammar marks、grammar / sentence-analysis cues，以及 compact reading header / slim strip / layer activity indicator。
- UI-D4 起 scaffold 只读渲染 `snapshot.user_assets`：quick highlight 作为 user highlight mark；note/comment 作为小型 comment indicator；二者都通过 domain anchor 投影，不使用 Plate path / Slate path。
- scaffold 不经过 `adaptReaderPlateSnapshotToReaderVm`、不接旧 `ReaderVm`，也不经过 `renderSceneToPlateDocument`。
- UI-D5 起 `/app/reader-record/{recordId}` loaded state 默认渲染 `ReaderRecordPlateSurface`，用于真实页面验证新 Plate.js 解析页。
- Workbench-backed surface 仍保留为受控 fallback：设置 `NEXT_PUBLIC_READER_RECORD_SURFACE_MODE=workbench`，或在浏览器 localStorage 写入 `claread:reader-record-surface-mode=workbench` 可切回。
- UI-D5 不影响 legacy `/app/reader/{recordId}`，不改 `/app/read` submit 逻辑，也不迁移 Library、Vocabulary source links、command palette legacy records 或 active analysis task。
- UI-D6A 锁定 `/app/read -> /app/reader-record/{recordId}` 的默认真实阅读路径：默认 mode 为 `plate`；Workbench fallback 只能通过 helper/env/localStorage 显式选择，不改变默认产品页。
- UI-D6A 已用 characterization tests 覆盖 Plate progressive loading：`processing` / `readable_enhancing` / `failed` / `action_required` 均保留正文并使用 compact progress，不回退到旧 Workbench 状态卡。
- UI-D6A 静态 guard 锁定默认 Plate route / surface / projection 不引用 `/scene`、`render_scene_json`、`analysis-tasks`、legacy reader route 或 Ask/notes/annotations 写 API。
- D6-U6 起 Lookup / Copy 在有效 stable-source single-range selection 上启用：Copy 使用浏览器 clipboard API 复制 `selected_text`；Lookup 复用 `/api/web/dict/lookup` 只读 BFF 并显示轻量结果面板。
- D6-U7 起 Highlight / Note 在有效 stable-source single-range selection 上启用最小写入：Web 入口为 `/api/web/reading-record/highlights` 和 `/api/web/reading-record/notes`，payload 必须携带 nested `anchor: UserEditorialAssetAnchor`；后端复用 `user_annotations` / `reader_notes` 的 Reading Record anchor gate + V1c persistence；写入成功后触发 snapshot reload 后再由 projection 显示用户资产。
- UI-D4 阅读态打磨把顶部工程化 progress 改为 reading header：只显示产品态、增强层数量和细进度条；不再作为大块状态卡或调试面板。
- UI-D4 阅读态打磨把固定按钮排收敛为 `selection-context` action strip：无选区或不支持的选区只给轻提示，不渲染整排 disabled action buttons；有 stable-source single-range selection 时才显示 Lookup / Copy / Highlight / Note 的上下文动作。Ask / Feedback 仍为 coming soon 次级提示，不作为可执行按钮出现。
- UI-D4 阅读态打磨把 grammar / sentence-analysis cues 从正文内完整 chip 改为低干扰 marker（`G` / `S` / `笔记`）；默认阅读流中不再堆叠绿色 chip 文案。
- UI-D5 Active Anchor Inspector 让 vocabulary / grammar / user highlight marks，以及 grammar / sentence-analysis / user note-comment cues 可点击或键盘聚焦后查看详情；详情面板在中心阅读列内轻量展示，可 Close 或 Escape 关闭。
- UI-D5 review 收口：snapshot 刷新会清空 active inspector；重叠 marks 不再嵌套 button-like spans，而是合并为单个 focusable mark stack，inspector 内展示 stack 明细。
- UI-D4 阅读态打磨把 unit translation 视觉改为 `supporting-paragraph` 辅助译文，保留段落下方轻量展示，仍不挂到 source segment 内。
- multi-segment / 非 stable-source selection 暂不暴露可执行 Highlight / Note 写动作；`multi_text` 仍等待 `UserEditorialAssetAnchorSet` persistence contract。
- Ask / Feedback 在 UI-D3 / UI-D4 / UI-D5 / UI-D6A / D6-U7 必须 disabled / coming soon，不允许调用 `/api/web/reader-ask`、`/api/web/reader-notes`、`/api/web/reader-annotations`。
- UI-D3 / UI-D4 / UI-D5 / UI-D6A 不持久化 Plate path / Slate path；所有 DOM data attribute 只暴露 stable domain id，供后续 active anchor adapter 与 selection bridge 使用。
- UI-D5 保留原有 snapshot polling / reload 行为；`layer_published`、`record_product_state_updated`、`projection_reset_required` 触发 reload 后，Plate surface 读取新的 `ReaderPlateSnapshotDto` 并重新 projection。

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
- `multi_text` 不属于 V1c first production 写入；必须等 `UserEditorialAssetAnchorSet` persistence contract。

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

第一阶段仍有限制 / disabled / coming soon：

- Ask：直到 D6-A3/A6 新 route 和 contract 稳定。
- Comment/Note：D6-U7 起仅 stable-source single-range selection 启用；multi-segment、非 stable-source 和 `multi_text` 继续 disabled。
- Highlight：D6-U7 起仅 stable-source single-range selection 启用；multi-segment、非 stable-source 和 `multi_text` 继续 disabled。
- Feedback：直到 AI mark/cue feedback contract 与新 route 稳定。
- Ask supplement save-to-document：直到 Ask Supplement Projection 设计和 API 完成。
- sentence_analysis chunk underlines：直到 V1d best-effort 或 Sentence Analysis V2；默认不显示。
- translation pair group：直到 Translation V2。

## 后续需求

### Translation V2

升级 translation worker/schema，输出 per-anchor-segment translation items。

### Sentence Analysis V2

升级 chunk schema，增加 offsets、role、depth 和 parent relation。

### Anchor Validation Cutover

把 `user_annotations` / `reader_notes` 的 single-range 校验从旧 `render_scene` 迁到 Stable Reading Base / Anchor Segment；`multi_text` 另走 `UserEditorialAssetAnchorSet` contract。

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
- D6-U7 起 Comment/Note 和 Highlight 对 stable-source single-range selection 可执行；multi-segment / 非 stable-source selection 明确 disabled。
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

## UI-D6B 真实流程验收与 UI 收敛评估

> 验收时间：2026-06-24
> 验收分支：`reader-agentic-orchestration`
> 验收范围：`/app/read -> /api/web/reading-record/submit -> /app/reader-record/{recordId}` 默认 Plate surface 端到端流程与 UI/UX 收敛评估。

### 验收方法

本轮验收包含两阶段：

1. **静态契约测试 + characterization 测试 + 源码审计**：覆盖 progressive loading 状态、selection action strip、lookup panel、note composer、active anchor inspector、user asset projection 和静态契约 guard，锁定 UI 收敛。
2. **三进程真实联调**：按 `local-real-chain-runbook.md` 启动 API（uvicorn port 8000）、Worker（reader-enhancement-worker）、Web（Next.js port 3000），使用 mock phone auth + debug session token，从 `/app/read` 提交真实英文文章，验证端到端流程。

### 三进程真实联调结论

> 验收时间：2026-06-24
> 验收分支：`reader-agentic-orchestration`
> 真实 record_id：`a6621b52-3f51-49b0-9a5f-f630a6c5818a`
> base_id：`7ffecbe1-8ce3-485c-af89-70a2381d8816`
> 文章标题：Institutional Memory

**三进程启动状态**：API、Worker、Web 三进程均成功启动并保持运行。API `/health` 返回 `status: ok`，`postgres: true`，`redis: true`，`worker: true`。

**端到端流程验证**：

1. ✅ 从 `/app/read` 提交英文文章，请求走 `/api/web/reading-record/submit`，成功 landing 到 `/app/reader-record/a6621b52-3f51-49b0-9a5f-f630a6c5818a`。
2. ✅ Progressive loading 合理：header chip 显示状态（解析完成），slim strip 显示 4 个增强层进度，不替换正文，不显示旧 Workbench 状态卡。
3. ✅ Translation / vocabulary / grammar / sentence-analysis 逐步出现：4 个增强层（translation、vocabulary、grammar_note、sentence_analysis）全部 succeeded，`overall_status: ready`。页面渲染译文块、词汇 marks（phrase_gloss）、语法 marks（grammar_note）、句式分析 cues（G/S markers）。
4. ✅ Lookup / Copy / Highlight / Note 可用：`SelectionActionStrip` 在 stable-source single-range selection 时启用查词/复制/高亮/笔记按钮。
5. ✅ 保存 highlight 后 snapshot reload 刷新到 user_assets projection：通过 Web BFF `POST /api/web/reading-record/highlights` 保存高亮（selected_text: "Institutional"，anchor 携带完整 Reading Record anchor），返回 201，`analysis_record_id: null`（正确，未映射到 legacy）。snapshot reload 后 `user_assets` 包含该高亮，`reading_record_id` 匹配当前 record，`asset_type: highlight`，`color: soft_green`。
6. ✅ 保存 note 后 snapshot reload 刷新到 user_assets projection：通过 Web BFF `POST /api/web/reading-record/notes` 保存笔记（selected_text: "memory"，noteText: 测试笔记内容），返回 201，`analysis_record_id: null`。snapshot reload 后 `user_assets` 包含该笔记，`asset_type: note`，`note_text` 正确存储。
7. ✅ Active anchor inspector 可用：点击词汇 mark（phrase_gloss）后 inspector 出现，显示中文标签（词汇、例句：、锚点、关闭）。
8. ✅ Ask / Feedback 保持 coming soon：`SelectionActionStrip` 渲染静态文案"Ask / 反馈 即将推出"，不调用 `/api/web/reader-ask`、`/api/web/reader-notes`、`/api/web/reader-annotations`。
9. ✅ 所有可见 UI 文案为中文（解析完成、增强层、划取原文后可查词、复制、标记或记录笔记、译文等）。
10. ✅ 文章标题显示在 header 中（本轮修复项，见下文）。

### 已修复内容

**UI 文案收敛（状态文案错误修复）**：

对比旧 AI Workflow 版本 `ReaderWorkbench.tsx`（经多轮优化、使用一致中文文案：查词、复制、高亮、笔记、精读、沉浸、阅读设置），新 `ReaderRecordPlateSurface.tsx` 存在明显中英文混排状态文案错误。本轮将所有英文 UI 文案收敛为中文，匹配旧 Workbench 基线：

- 操作按钮：Lookup → 查词、Copy → 复制、Highlight → 高亮、Note → 笔记、Saving → 保存中、Looking up → 查询中
- 写入状态：Saving highlight → 正在保存高亮、Saving note → 正在保存笔记、Highlight saved → 高亮已保存、Note saved → 笔记已保存、Highlight save failed → 高亮保存失败、Note save failed → 笔记保存失败
- 复制状态：Copied → 已复制、Copy failed → 复制失败
- Section 标签：Vocabulary → 词汇、Grammar → 语法、Sentence Structure → 句子结构、User Highlight → 用户高亮、Comment → 笔记/评论、Lookup → 查词、Note → 笔记
- 状态文本：overlapping annotations → 处重叠标注、Anchor → 锚点、Asset → 资产、Example: → 例句：、Reason: → 原因：、Selected: → 已选：、Looking up... → 查询中...
- 词典面板：No concise definition is available for this entry. → 该词条暂无简明释义。、Multiple dictionary candidates found: → 发现多个词典候选：、No dictionary entry found. → 未找到词典条目。、Dictionary lookup failed. → 词典查询失败。
- 按钮标签：Save → 保存、Cancel → 取消、Close → 关闭
- aria-label：Reader Record Plate actions → Reader Record Plate 操作、Dismiss lookup → 关闭查词、Close active anchor details → 关闭锚点详情、Active anchor details → 锚点详情
- disabledReason：Select stable source text to enable this action → 请选择稳定原文以启用此操作、Action is currently unavailable → 操作当前不可用、Multi-segment selection is not supported yet → 暂不支持跨段选区
- Ask / Feedback coming soon → Ask / 反馈 即将推出

**异常分支中文 fallback（P2 修复）**：

异常分支不再把 `error.message` 直接展示给用户，避免浏览器原始英文错误（如 `Failed to fetch`）绕过中文文案收敛。三处 catch 分支改为固定中文 fallback，原始 error 只用于 `console.warn` 日志：

- 词典查询失败：`词典查询失败，请稍后重试。`
- 高亮保存失败：`高亮保存失败，请稍后重试。`
- 笔记保存失败：`笔记保存失败，请稍后重试。`

**文章标题缺失修复（三进程联调发现）**：

`CompactProgress` 组件原先只显示状态 + 进度条，不显示文章标题。三进程真实联调时发现 header 缺少标题，影响页面可读性。修复方式：

- `CompactProgress` 新增 `title?: string` prop。
- 当 `title` 存在时，在 header 顶部渲染 `<h1 data-reader-record-reading-title>` 元素，使用 `reader-serif` 字体和 `text-xl sm:text-2xl` 字号。
- 调用点传入 `plateDocument.record.title` 作为 title。
- 真实联调验证：页面 header 现在显示"Institutional Memory"标题。

**Smoke guard**：

在 `page.test.tsx` 静态契约 describe 中新增两层 guard：

1. `ReaderRecordPlateSurface uses converged Chinese copy instead of legacy English UI labels`：源码静态扫描，覆盖引号字符串、模板字面量、aria-label、disabledReason 等模式，防止英文 UI 文案回归。
2. `ReaderRecordPlateSurface renders Chinese copy when vocabulary mark inspector is active`：真实渲染断言，渲染含 vocabulary/grammar/sentence-analysis/user asset 的 fixture，验证 action strip 和 surface 不出现英文标签（`Vocabulary`、`Grammar`、`Lookup`、`Copy`、`coming soon`、`Example: `、`Reason: ` 等），同时确认中文 hint `划取原文后可查词、复制、标记或记录笔记` 存在。

### 验收结论

**已通过（静态契约 + 三进程真实联调）**：

1. `/app/read` 默认提交走 `/api/web/reading-record/submit`，成功进入 `/app/reader-record/{recordId}`（真实联调验证 record_id `a6621b52-3f51-49b0-9a5f-f630a6c5818a`，由 `READ_PAGE_SUBMIT_MODE = "reading-record"` 和 `AnalyzeSubmitForm.tsx` 跳转逻辑保证，characterization 测试已锁定）。
2. progressive loading 使用 compact chip / slim strip，不替换正文，不显示旧 Workbench 状态卡（真实联调验证 4 个增强层全部 succeeded，`overall_status: ready`；UI-D6A characterization 测试已覆盖 `processing`、`readable_enhancing`、`failed`、`action_required` 状态）。
3. translation/vocabulary/grammar/sentence-analysis 通过 Plate document projection 逐步出现（真实联调验证页面渲染译文块、词汇 marks、语法 marks、句式分析 cues；projection schema 测试已覆盖）。
4. Lookup / Copy / Highlight / Note 按 stable-source single-range selection 工作（真实联调验证 highlight 和 note 保存成功；`SelectionActionStrip` 在 `singleRangeReady` 时启用，多段选区和非 stable-source selection 明确 disabled，characterization 测试已覆盖）。
5. 保存 highlight/note 后触发 snapshot reload 并出现在 user_assets projection（真实联调验证：highlight asset_id `601d2b1f-d310-4d56-8f5e-85eb647020c6`、note asset_id `dd63d85e-84b4-40c0-9298-c3a48715dc0a` 均出现在 snapshot `user_assets` 中，`reading_record_id` 匹配当前 record，`analysis_record_id: null`；`handleHighlight` / `handleSaveNote` 调用 `onRequestSnapshotReload`，projection 消费 `snapshot.user_assets`，characterization 测试已覆盖）。
6. active anchor inspector 对 vocab/grammar/user highlight/note cue 的展示可用（真实联调验证点击词汇 mark 后 inspector 出现中文标签；`ActiveAnchorInspector` 渲染 `VocabularyAnchorDetails`、`GrammarAnchorDetails`、`SentenceAnalysisAnchorDetails`、`UserHighlightAnchorDetails`、`UserCommentAnchorDetails`，characterization 测试已覆盖）。
7. Ask / Feedback 保持 coming soon，不调用旧 API（真实联调验证静态文案"Ask / 反馈 即将推出"；静态契约 guard 已锁定不引用 `/api/web/reader-ask`、`/api/web/reader-notes`、`/api/web/reader-annotations`、`/api/web/annotations`）。
8. 没有接旧 `/scene`、`render_scene_json`、`analysis-tasks`：静态契约 guard 已锁定。
9. UI 文案与旧 Workbench 基线一致，全部使用中文（真实联调验证所有可见文案为中文）。
10. 文章标题显示在 header 中（三进程联调发现并修复，`CompactProgress` 现渲染 `<h1 data-reader-record-reading-title>`）。

**未修复但建议后续做的 UI/UX 项**（不在本轮修复范围，记录为后续任务）：

1. ~~**loaded state header 无文章标题**~~：**已修复（三进程联调）**。`CompactProgress` 现在接受 `title` prop 并渲染 `<h1>` 标题。
2. **无阅读模式切换器**（精读/沉浸）：旧 Workbench 有 intensive/immersive mode switcher。Plate surface 当前是单一 readonly mode，by design for V1，后续按需补。
3. **无词典 rail**：旧 Workbench 左侧有 `ReaderDictionaryRail`。Plate surface 使用 inline lookup panel，by design for V1，后续按需补。
4. **无 Ask rail**：旧 Workbench 右侧有 `AiWorkspacePanel`。Plate surface 的 Ask 保持 coming soon，by design，后续按需补。
5. **inline action strip vs floating toolbar**：旧 Workbench 使用 floating `SelectionToolbar`。Plate surface 使用 `SelectionActionStrip`（固定在正文上方），by design（UI-D4），后续按需评估。
6. **无收藏按钮**：旧 Workbench header 有 favorite action。Plate surface 暂无，后续按需补。
7. **无阅读设置**（排版、主题）：旧 Workbench 有 typography/theme settings。Plate surface 暂无，后续按需补。

### 手动验证 checklist（三进程联调）

如需真实浏览器验证，按以下步骤执行：

1. 启动 API：`uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`（在 `services/api/` 下）
2. 启动 Worker：`uv run reader-enhancement-worker`（在 `services/api/` 下）
3. 启动 Web：`pnpm web:dev`（在仓库根目录）
4. 访问 `/app/read`，粘贴一段英文文本，提交
5. 确认跳转到 `/app/reader-record/{recordId}`
6. 观察 progressive loading：header chip 显示状态，slim strip 显示 layer 进度
7. 等待 translation/vocabulary/grammar/sentence-analysis 逐步出现
8. 选中一段 stable source text，确认 `SelectionActionStrip` 出现查词/复制/高亮/笔记按钮
9. 点击查词，确认 lookup panel 出现并显示词典结果
10. 点击高亮，确认写入成功后 snapshot reload，高亮出现在 user_assets projection
11. 点击笔记，输入笔记内容，保存后确认 snapshot reload，笔记 indicator 出现
12. 点击 vocab/grammar mark 或 cue，确认 active anchor inspector 出现
13. 确认 Ask / Feedback 显示"Ask / 反馈 即将推出"，不调用旧 API
14. 确认所有 UI 文案为中文

## Open Questions

- Structure Lens floating legend 的具体位置：句子附近、selection 附近，还是文档右侧 margin。
- grammar note 的解释是否使用统一 popover，还是 footnote-like note strip。
- 精读模式下译文默认展示密度：每个 translation pair group 常显，还是可折叠。
- 沉浸模式中 phrase/context 的筛选规则：由后端 importance 决定，还是前端按类型/置信度过滤。
- 移动端 rail 行为：Dictionary / Ask / Comment 是否统一 bottom sheet。
