# Claread Reader Translation V2 / 双语文档显示 架构评审报告

> 评审范围：新版 `/app/reader-record/{recordId}` Reader Record 页面，Legacy `/app/reader/{recordId}` 不在本次范围内。  
> 数据来源：仓库代码、模块文档、Plate.js/Slate 官方文档及 Playground 样本。  
> 结论先行：**推荐“中间方案”——后端保存 per-segment translation item，前端 deterministic display grouping；Plate 仅作为 projection，不充当业务事实源。**

---

## 1. 现状扫描

### 1.1 后端现状

| 模块 | 当前事实 |
|---|---|
| [base_builder.py](file:///c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/base_builder.py) | 1 个 structure block → 1 个 Reading Unit；Unit 内按 sentence → clause → fallback_window 三级降级生成 Anchor Segment。Segment 是稳定的 span anchor，带 `anchor_segment_id`、`base_start/end_utf16`、`text_hash`。 |
| [translation_worker.py](file:///c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/translation_worker.py) | 以 unit 为输入，prompt 只传 `source_text`，输出 `TranslationLayerOutput`（`schema_version: 1`），只有整段 `translated_text`。 |
| [layer_publisher.py](file:///c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/layer_publisher.py) | `enhancement_layers` 按 `target_scope='unit'`、`target_key=unit_id` 存储 translation；未保存 segment 级对齐。 |
| [schemas/reader_orchestration.py](file:///c:/Users/nanpr/claread/claread/services/api/app/schemas/reader_orchestration.py) | `TranslationLayerOutput` 无 `items` 字段，不支持 per-anchor-segment 输出。 |

### 1.2 前端现状

| 模块 | 当前事实 |
|---|---|
| [reader-record-plate-document.ts](file:///c:/Users/nanpr/claread/claread/apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts) | `ReaderRecordPlateDocument` 包含 `paragraph`（原文 segment）、`blockquote`（unit 级译文）、`callout`（grammar/sentence_analysis）。`ReaderRecordPlateBlockquoteBlock.data` 只到 unit 粒度。 |
| [ReaderRecordPlateSurface.tsx](file:///c:/Users/nanpr/claread/claread/apps/web/src/components/reader/plate/ReaderRecordPlateSurface.tsx) | 使用 `<Plate readOnly>`；译文渲染为 `blockquote`，带左侧竖线、灰绿背景、较小字号；“译文”标签硬编码在组件内。 |
| Plate plugin kit | 已落地 `reader_paragraph`、`reader_blockquote`、`reader_callout`、vocabulary/grammar/user highlight/user note leaf、floating toolbar、comment。 |

### 1.3 文档口径

- [TMP-reader-document-graph-design-2026-06-27.md](file:///c:/Users/nanpr/claread/claread/docs/tmp/reader-orchestration/TMP-reader-document-graph-design-2026-06-27.md) 已明确提出 Translation V2 的 domain/display 分离：`TranslationLayerOutputV2.items` 为 per-segment truth，`TranslationDisplayGroup` 为 projection 派生。
- [reader-record-plate-surface-ui.md](file:///c:/Users/nanpr/claread/claread/docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md) 将 Translation V2 列为“后续需求”，V1 仅允许 unit 级“本段译文”。

---

## 2. 切分策略：如何同时满足四个目标

### 2.1 推荐切分原则

**保持现有 Reading Unit / Anchor Segment 两层结构，不因为翻译而新增第三层稳定单位。**

- **Reading Unit**：仍是 worker 调度和成本控制的窗口，按现有 structure block 边界生成（[base_builder.py](file:///c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/base_builder.py#L272-L376)）。worker 以 unit 为输入，保证上下文连贯。
- **Anchor Segment**：仍是持久 anchor 和 grounding 的最小单位。translation item 必须挂到 `anchor_segment_id`，并校验 `source_text_hash`。
- **Display Group**：不是新稳定层，而是 projection 派生组（1-3 个连续 segments），只影响前端排版，不影响后端 truth。

### 2.2 为什么不把长文拆成“每句一行”

当前 builder 在纯文本无换行时会生成一个 `body` unit 含多个 sentence segments（[reading-base-and-units.md](file:///c:/Users/nanpr/claread/claread/docs/initiatives/reader-agentic-orchestration/modules/reading-base-and-units.md#L133-L141)）。如果前端把每个 segment 都变成独立 paragraph + 独立译文块，视觉上会像诗歌分行，破坏原文段落感。因此：

- 原文段落块仍以 **Stable Document Block / Reading Unit 的 paragraph 边界** 为渲染骨架。
- Anchor Segment 只在 paragraph 内部作为 span anchor 和 subtle 边界，不强制每个 segment 独占一行。
- 译文 display group 跨 segment 合并时，仍挂在对应 source paragraph 之后，保持“段落—译文”的纵向对照关系。

### 2.3 对 enhancement layers 的插入友好性

Anchor Segment 是 sentence-like 且带 hash 的 span，天然适合：
- vocabulary/grammar mark 的 span-bound anchor；
- sentence_analysis 的 chunk offset 校验；
- translation item 的 per-segment grounding。

未来 Stable Document Blocks 落地后，block 边界成为 display group 的硬边界；anchor segment 边界成为 item 对齐的软边界。

---

## 3. Translation Worker 输入输出

### 3.1 输入上下文

**推荐：unit context + segment targets。**

Prompt 结构：

```text
<source_language>en</source_language>
<target_language>zh-CN</target_language>
<reading_goal>{goal}</reading_goal>
<variant>{variant}</variant>

<unit_context>
{unit_full_text}
</unit_context>

<anchor_segments>
[
  {"anchor_segment_id": "s3", "source_text": "..."},
  {"anchor_segment_id": "s4", "source_text": "..."}
]
</anchor_segments>

请按 anchor_segment 输出 aligned translation items，同时可选输出覆盖多个 segments 的 group_translation。
```

### 3.2 输出 schema

推荐 `TranslationLayerOutputV2`：

```python
class TranslationLayerOutputV2(BaseModel):
    schema_version: Literal[2] = 2
    target_language: str
    items: list[TranslationItemV2]
    full_translation: str | None = None   # 整段/unit 级兜底
    notes: list[str] = []
    diagnostics: list[str] = []

class TranslationItemV2(BaseModel):
    anchor_segment_id: str
    source_text: str
    source_text_hash: str
    translated_text: str
    confidence: Literal["low", "normal", "high"] = "normal"
```

### 3.3 为什么不需要 LLM 输出 display group

让 LLM 输出 segment items 即可；display grouping 由前端 deterministic 规则完成。原因：
- LLM 不掌握当前 grammar/sentence_analysis cue 分布、用户设置、沉浸/精读模式；
- deterministic grouping 可回归测试、可稳定复现；
- 减少 LLM schema 复杂度，降低输出失败率。

### 3.4 reading goal / variant 的影响

- **输入窗口**：goal/variant 作为 prompt 上下文追加，不改动 segment 边界。
- **输出 schema**：不新增字段，通过 prompt 指令让 LLM 调整措辞（如“学术精读”vs“日常泛读”）。
- **alignment**：goal/variant 可能导致同一 source segment 在不同 variant 下译文长度差异大，但 item 仍按 segment 锚定；display grouping 只合并相邻 items，不因 variant 改变 anchor。

---

## 4. Translation Domain Truth vs Display Grouping

### 4.1 后端是否保存 per-segment item

**必须保存。** `TranslationLayerOutputV2.items` 写入 `enhancement_layers.output_json`，同时 `coverage_json` 记录每个 item 覆盖的 `anchor_segment_id`。这是后续双语对照、Ask grounding、RAG citation 的事实源。

### 4.2 是否保存 group-level translation

**V2 第一版不保存 group-level truth。** 原因：
- group 是 display 决策，会随 grammar cue 密度、用户模式、未来 UI A/B 变化；
- 若 worker 输出 group_translation，仅作为非权威的 `full_translation` 或 `notes` 保存，不替代 per-segment items。

### 4.3 Display group 由谁生成

**前端 deterministic 生成，worker 不提供 placement hints。**

生成规则（按优先级）：

1. 不跨 Stable Document Block / Reading Unit 边界。
2. 不跨 paragraph 边界。
3. 优先 1-3 个连续 sentence segments 合并。
4. 遇到 grammar_note / sentence_analysis cue 且其解释将显示时，可在此处断开。
5. 过长 segment（>280 chars）单独成组。
6. quote/list/table 边界内不合并。

### 4.4 Worker 输出与 deterministic grouping 冲突时的降级

- 若 LLM 只输出整段 `full_translation` 而无 items → publisher 标记 `alignment_failed` 或降级为 V1 unit translation。
- 若 items 数量与 unit 内 segments 不匹配 → 只校验存在的 segment；缺失 segment 无译文，UI 显示“待翻译”或回退 unit translation。
- 若 `source_text_hash` 不匹配 → fail-closed，整层不发布。

### 4.5 避免前端硬切分导致语义错误

前端不能按中文句号或字数硬切译文。正确做法：
- 译文 display group 的 text 来自连续 segment items 的 `translated_text` 拼接（可能已包含 LLM 自身的合并决策）。
- 前端只决定“从哪个 segment 开始到哪个 segment 结束组成一个 display group”，不对单个 item 的 `translated_text` 再切分。
- 如果 worker 输出的是 per-segment 译文，但相邻两个英文句在中文里自然合并为一句，这个合并应发生在 worker（通过 unit context），而不是前端硬切。

---

## 5. 页面视觉体验设计

### 5.1 层次区分

| 内容 | 视觉策略 |
|---|---|
| **Stable source 正文** | 主色、常规字号、最高可读性；精读/沉浸都显示。 |
| **Translation 译文** | blockquote 形态：左侧竖线、灰/青背景、小 5-10% 字号、无衬线字体；置于对应 source paragraph 之后。 |
| **Grammar note** | 原文细下划线 + 小编号 cue；解释正文以 callout/footnote 形式出现，不默认插入文档流大卡片。 |
| **Sentence analysis** | 结构 cue（小标签/图标），点击打开 floating legend；chunk underlines 仅在 Structure Lens active 时显示。 |
| **Vocabulary** | 轻底色 mark（精读）或下划线（沉浸），hover 打开词典 peek。 |
| **User highlight/note** | 用户色高亮/comment indicator，优先级高于系统 marks。 |

### 5.2 信息密度与阅读重心

- **原文优先**：译文默认以 blockquote 收在段落后，不并列展开。
- **沉浸模式**：默认隐藏译文、grammar cue、sentence analysis；只保留 phrase/context gloss 和用户资产。
- **精读模式**：默认显示 translation group 和系统 marks；grammar/sentence_analysis 解释不默认展开。

### 5.3 插入顺序与间距

同一 source paragraph 后的插入顺序：

1. translation display group（如启用）
2. grammar note callout（多个按 cue 编号排序）
3. sentence analysis legend（用户触发后）
4. ask supplement cue（用户保存后）

间距原则：
- source paragraph 与 translation group 之间用 `mt-3` 级别间距；
- 多个系统 cue/callout 之间用 `mt-2`；
- 避免大段留白，保持“文档感”。

### 5.4 Plate.js 原生能力 vs 自定义 wrapper

**优先使用 Plate 官方能力：**

| 需求 | 推荐 Plate 能力 |
|---|---|
| 原文 paragraph | 自定义 `reader_paragraph`（已是 element plugin） |
| 译文 blockquote | 自定义 `reader_blockquote`，样式引用官方 `blockquote` 语义 |
| Grammar/sentence_analysis 解释 | 官方 `CalloutPlugin` / `TogglePlugin`（Plate UI registry 已提供 callout/toggle 组件） |
| Markdown 内容 | `@platejs/markdown` `deserializeMarkdownToBlocks`（已用） |
| 多列平行对照（未来） | `@platejs/column`（官方 Column plugin，Plate Playground 已提供） |
| 选区保持 | `CursorOverlayPlugin`（已用） |
| 浮动工具栏 | `useFloatingToolbar`（已用） |

**需要 Claread 自定义 wrapper：**

- `reader_paragraph`：必须携带 `anchorSegmentId`、`baseRange` 等 metadata。
- `reader_blockquote`：译文块需绑定 `anchor_segment_ids` 和 `layer_id`，支持点击后 Ask grounded on source。
- `reader_sentence_analysis_block`：不是普通 callout，需要结构化渲染 `chunks`。
- Mark resolver：vocab/grammar/user highlight 重叠时的样式优先级必须统一，不能各自为政。

**不推荐：** 用 Slate inline void 节点做“同一行内左英右中”的平行对照。Slate inline void 的 selection/Copy/Ask anchor 会非常复杂，且不符合“阅读重心在英文”的产品定位。

---

## 6. 候选方案对比

### 方案 A：保守方案 — 保持 unit translation，前端优化视觉

**做法：** 不改 `TranslationLayerOutput`，仍按 unit 存储整段译文；前端把 unit 级译文渲染得更美观（blockquote、标签、折叠）。

| 维度 | 评估 |
|---|---|
| Correctness | 中。无法解决“两句英文翻成一句中文”的对齐问题，双语对照粒度粗。 |
| UX | 中。视觉可优化，但用户仍难以逐句对照。 |
| 实现成本 | 最低。只改前端样式和 projection。 |
| 回归测试 | 低。schema/后端不变。 |
| Ask/RAG 接入 | 差。Ask 无法 grounded 到具体 segment 的译文。 |
| 长期维护 | 差。V2 迟早要重做，unit 级 truth 会成为历史债务。 |

### 方案 B：中间方案 — segment translation item + deterministic display grouping（推荐）

**做法：** 后端升级到 `TranslationLayerOutputV2`，保存 per-segment items；前端按 deterministic 规则合并 1-3 个连续 segments 为 display group；保留 unit 级 `full_translation` 作为降级。

| 维度 | 评估 |
|---|---|
| Correctness | 高。segment 是稳定 anchor，可校验；display grouping 可测试、可复现。 |
| UX | 高。既避免“每句一行”的诗歌感，又提供段落内自然双语对照。 |
| 实现成本 | 中。需要改 worker prompt/schema、publisher 校验、前端 projection、测试。 |
| 回归测试 | 中。新增 segment item 校验和 grouping characterization tests。 |
| Ask/RAG 接入 | 高。Ask 可直接引用 segment 级译文并回源到 stable source。 |
| 长期维护 | 高。domain/display 分离清晰，未来变 UI 分组规则不影响 truth。 |

### 方案 C：激进方案 — worker 输出 semantic display groups / placement hints

**做法：** LLM 不仅输出 per-segment items，还输出 `display_groups` 和 `placement_reason`；前端优先采用 worker 的分组建议。

| 维度 | 评估 |
|---|---|
| Correctness | 中/低。LLM 分组可能跨 paragraph、与 grammar cue 冲突、难以回归测试。 |
| UX | 理论上更自然，但实际会因 LLM 不稳定导致同一文章每次分组不同。 |
| 实现成本 | 高。需要新增 placement hint schema、校验、冲突降级、版本管理。 |
| 回归测试 | 高。LLM 输出不可完全确定性断言。 |
| Ask/RAG 接入 | 中。RAG 不应把 display group 当 citation truth，仍需回源 segment。 |
| 长期维护 | 低。LLM schema 和 prompt 会持续膨胀，grouping 规则分散在代码和 prompt 中。 |

### 对比结论

| 维度 | A 保守 | B 中间（推荐） | C 激进 |
|---|---|---|---|
| Correctness | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| UX | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 实现成本 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 回归测试 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Ask/RAG | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 可维护性 | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |

**推荐方案 B。**

---

## 7. 最终推荐方案与实施顺序

### 7.1 总体策略

采用 **方案 B：segment translation item + deterministic display grouping**。核心原则是：

- **后端只存 segment 级 translation truth**，不存 display group。
- **前端只决定如何展示 group**，不创造新 truth。
- **Plate 只是 projection**，domain anchor 仍是 `anchor_segment_id` + UTF-16 offsets。
- **保留 unit 级 `full_translation` 作为降级和沉浸模式快速预览**。

### 7.2 Phase 1：当前应做什么（不改 Translation schema）

在 Translation V2 之前，先完成以下前置工作：

1. **Stable Document Blocks 与 Canonical Text 分离落地**
   - 确认 `reading_bases.text` 只存 plain text，Markdown 语法不进入 offset 基准。
   - 输入预处理 adapter 输出 Stable Document Blocks（paragraph/heading/list/blockquote 等）。

2. **前端 projection 改为从 Stable Document Blocks 出原文结构**
   - 当前 `reader-record-plate-document.ts` 以 unit/segment 为骨架，未来应以 block 为骨架、segment 为 span anchor。
   - 不要等 Translation V2 再做这个改动，否则 V2 会同时改两层。

3. **sentence analysis 不再渲染为普通 callout**
   - 按 [reader-record-plate-surface-ui.md](file:///c:/Users/nanpr/claread/claread/docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md#L701-L746) 目标，实现 always-open structure analysis block + floating legend。

4. **grammar callout 接入官方 Plate callout/toggle**
   - 当前 `CalloutMarkdownRenderer` 是自定义递归，未来应通过 `@platejs/callout` 或 `@platejs/toggle` 渲染，保证 selection/anchor 语义一致。

5. **mark 冲突 resolver 落地**
   - 统一处理 vocab/grammar/user highlight/comment 重叠时的视觉优先级。

### 7.3 Translation V2 第一版应做什么

1. **Schema 升级**
   - 新增 `TranslationLayerOutputV2`（[schemas/reader_orchestration.py](file:///c:/Users/nanpr/claread/claread/services/api/app/schemas/reader_orchestration.py)）。
   - `enhancement_layers.schema_version` 支持 `2`。
   - 新增 `coverage_json` 记录 item → segment 映射。

2. **Worker 改造**
   - [translation_worker.py](file:///c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/translation_worker.py) prompt 改为 unit context + segment targets。
   - 输出校验：每个 item 的 `source_text_hash` 必须匹配对应 segment。
   - 保留 `full_translation` 字段用于降级。

3. **Publisher 改造**
   - [layer_publisher.py](file:///c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/layer_publisher.py) 支持 segment 级 coverage 校验。
   - 同一 unit 内缺失 segment 的 item 可发布但标记 `partial_alignment`。
   - 全缺失则 fail-closed。

4. **前端 projection 改造**
   - [reader-record-plate-document.ts](file:///c:/Users/nanpr/claread/claread/apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts) 新增 translation display group 构建：
     - 读取 V2 items；
     - 按 deterministic 规则合并；
     - 生成 `reader_translation_pair` block（可复用/扩展 `reader_blockquote`）。
   - 若只有 V1 unit translation，仍按现有 blockquote 渲染。

5. **测试**
   - Worker output schema validation tests。
   - Publisher segment hash mismatch / partial coverage tests。
   - Frontend display grouping characterization tests（覆盖 paragraph boundary、grammar cue 断开、1-3 segment 合并等）。
   - Visual regression：确认“不诗歌化”的段落感。

### 7.4 暂不做

- ❌ 让 worker 输出 display groups / placement hints。
- ❌ 在 Plate 中实现“同一行左右英中对照”的 inline parallel layout。
- ❌ 把 Markdown 语法写入 `reading_bases.text`。
- ❌ 在 translation truth 中持久化 Plate node id / Slate path。
- ❌ 支持用户对 translation 文本做持久 highlight/note（V1 只持久 source anchor）。
- ❌ 多语言 variant 的并行 translation layer（先支持单 variant schema，多 variant 后续扩展）。

### 7.5 数据结构 / Schema / Prompt / Projection / Test 调整清单

| 类别 | 调整项 |
|---|---|
| Schema | `TranslationLayerOutputV2`、`TranslationItemV2`；`enhancement_layers` 支持 schema_version=2；`coverage_json` 增加 segment coverage。 |
| Prompt | `reader_layer_translation` prompt 改为 unit context + segment targets + goal/variant instruction。 |
| Worker | `PydanticAITranslationExecutor.translate` 输出 `TranslationLayerOutputV2`；publisher 校验 items。 |
| DB | 无需新表；`enhancement_layers.output_json`/`coverage_json` 存 V2 payload。 |
| Projection | `reader-record-plate-document.ts` 新增 `buildTranslationDisplayGroups`；`ReaderRecordPlateBlockquoteBlock` 扩展支持 `anchorSegmentIds[]`。 |
| Plate | 复用 `reader_blockquote` 作为 translation group 容器；未来如需两列平行对照再评估 `@platejs/column`。 |
| Test | worker unit tests、publisher integration tests、frontend grouping characterization tests、E2E visual 检查“段落不被切碎”。 |

### 7.6 最大风险与必须提前验证的 Spike

1. **LLM 输出 per-segment alignment 的稳定性**
   - **Spike：** 用 20-50 篇真实文章测试 prompt，统计 segment item 覆盖率、hash 匹配率、漏 segment 率。
   - **风险：** 如果 LLM 经常漏 segment 或合并错位，V2 收益会被降级路径抵消。

2. **Display grouping 的视觉正确性**
   - **Spike：** 在固定 mock snapshot 上实现 grouping 算法，打印 group boundaries，人工评审 10 篇不同长度/体裁文章。
   - **风险：** 规则太保守会回到“每句一行”，太激进会丢失对照精度。

3. **Plate blockquote 作为 translation group 的 selection/Ask 体验**
   - **Spike：** 验证用户选择译文时，能否正确回源到 source segment anchor（用于 Ask / Copy / dictionary lookup）。
   - **风险：** 如果译文 block 不是 Plate-selectable 或 anchor 回源失败，会违反“Plate 是交互底座”的目标。

4. **前端从 unit/segment 骨架迁移到 block 骨架的兼容性**
   - **Spike：** 在不改 translation 的前提下，先实现 Stable Document Block projection，验证现有 marks/translation 能正确映射。
   - **风险：** 如果 block 骨架和 segment 骨架同时改动，回归测试会爆炸。

---

## 8. 结论

Claread Translation V2 的核心不是“让 LLM 决定怎么排版”，而是**把稳定、可校验的 per-segment translation 作为 domain truth，把自然、可测试的 display grouping 作为前端 projection**。这样既保留英文原文的段落感和阅读重心，又提供足够细粒度的双语对照，同时为 Ask、RAG、用户资产锚定提供可靠基础。

**下一步建议：**
1. 先完成 Phase 1 的 Stable Document Block projection 和 sentence analysis/grammar callout 改造。
2. 然后启动 Translation V2 spike，重点验证 LLM per-segment alignment 稳定性。
3. 最后按本报告的 schema/prompt/projection/test 清单实施 V2。
