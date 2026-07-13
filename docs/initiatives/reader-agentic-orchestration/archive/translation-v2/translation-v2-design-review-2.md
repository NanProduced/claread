# Translation V2 / 双语文档显示方案专项评审 2

Date: 2026-06-27

Status: architecture review

Scope:

- 新版 `/app/reader-record/{recordId}` Reader Record。
- Translation V2、双语文档显示、Stable Source Truth 到 Plate projection 的边界。
- 不修改 legacy `/app/reader/{recordId}`。

Constraints:

- 不把 Plate value / DOM selection / Slate path 当业务事实源。
- 不把 Markdown 语法写入 `reading_bases.text`。
- Stable source 仍是事实源，Plate 只是 projection。
- 本报告只做评估和设计建议，不代表已修改代码。

## 0. 结论

推荐采用 **中间方案加强版**：

```text
Translation worker 仍以 Reading Unit 为执行窗口
-> 输入中带 unit context + ordered anchor segment targets
-> 输出 per-anchor-segment translation items
-> 可选输出 worker semantic group translations / placement hints
-> publisher 对 segment/hash/group 连续性做 fail-closed 校验
-> projection deterministic 生成最终 display groups
-> Plate 只渲染 projection，不成为 truth
```

核心判断：

1. **Domain truth 必须是 segment-grounded**。Translation V2 至少要保存 `anchor_segment_id + source_text_hash + translated_text`，否则 Ask/RAG、选区回源和双语对照都会继续停留在 unit 粒度。
2. **Display grouping 不能完全交给前端硬切，也不能完全交给 worker**。worker 可以提供 semantic group / placement hints，但 projection 必须用 Stable Document Blocks、Anchor Segment 顺序和 layer cue 决定最终展示。
3. **当前代码 V1 是 unit translation**。`translation_worker.py` 只加载单个 unit text；`TranslationLayerOutput` 只有整段 `translated_text`；`layer_publisher.py` 以 `target_scope='unit'` 写入；`snapshot.py` 再生成 unit-level `reader_translation`；Web projection 把它渲染成 blockquote。这个路径可作为 fallback，但不足以支撑 Translation V2。
4. **不要为了双语显示重切 Stable Base**。Reading Unit / Anchor Segment 的现有分层方向是对的；需要修正的是 worker schema、publisher validation 和 Web projection，不是把 source 变成每句一行。
5. **Phase 1 先修页面读感和 projection 结构**。Translation V2 第一版再引入 segment items、group validation、pair group rendering。

## 1. 当前代码事实

### 1.1 Stable Base / Unit / Segment

`services/api/app/services/reader_orchestration/base_builder.py` 当前做法：

- `_split_structure_blocks(...)` 按空行/可见行切 structure block。
- `_build_reading_base_core(...)` 当前仍是一个 structure block 生成一个 Reading Unit。
- `_build_segment_spans(...)` 在 unit 内优先生成 sentence segments，失败时降级为 clause，再降级为 fallback windows。
- `validate_reading_base_build_result(...)` 校验 unit / segment 能从 `reading_bases.text` 通过 UTF-16 offsets 和 hash 回源。

这说明当前切分已经有三层：

```text
Canonical Text / reading_bases.text
-> structure block spans
-> Reading Units
-> Anchor Segments
```

问题不是没有 anchor segment，而是 Web projection 当前把 source segment 作为独立 paragraph 渲染，容易造成视觉上的“每句一行”。

### 1.2 Translation Worker

`services/api/app/services/reader_orchestration/translation_worker.py` 当前做法：

- `_load_job_context(...)` 从 `reading_bases.text` 切出当前 `unit.base_start_utf16..base_end_utf16`。
- `TranslationJobContext` 只包含 `unit_id/source_text/text_hash/source_language/target_language` 等 unit-level 信息。
- `_build_translation_prompt(...)` prompt 是 “Translate the following reading unit.”，没有 anchor segment target 列表。
- executor 使用 `TranslationLayerOutput` 作为 Pydantic structured output。

`services/api/app/schemas/reader_orchestration.py` 当前 `TranslationLayerOutput`：

```python
schema_version: Literal[1] = 1
target_language: str
translated_text: str
notes: list[str]
confidence: Literal["low", "normal", "high"]
```

没有 per-segment alignment，也没有 group-level alignment。

### 1.3 Layer Publisher / Snapshot / Web Projection

`services/api/app/services/reader_orchestration/layer_publisher.py` 当前 `publish_unit_translation(...)`：

- 要求 job 是 `translate_unit` + `target_type='unit'`。
- 写入 `enhancement_layers`：
  - `layer_type='translation'`
  - `target_scope='unit'`
  - `target_key=unit_id`
  - `schema_version=output.schema_version`
  - `output_json=TranslationLayerOutput`

`services/api/app/services/reader_orchestration/snapshot.py` 当前 `_build_translation_nodes_for_layers(...)`：

- 读取 `TranslationLayerOutput`。
- 生成 `reader_translation` node。
- children 只有 `{"text": output.translated_text}`。

`apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts` 当前：

- `buildParagraphBlock(...)` 对每个 `reader_anchor_segment` 生成 paragraph。
- `buildBlockquoteBlock(...)` 只接受 `target_scope === "unit"` 的 `reader_translation`。
- `mapUnitToBlocks(...)` 先渲染所有 source segments，再把 unit translation 渲染为 blockquote。

`apps/web/src/components/reader/plate/ReaderRecordPlateSurface.tsx` 当前：

- 已使用 `<Plate readOnly>` + `ReaderPlateKit`。
- `surfaceMode === "intensive"` 显示所有 blocks。
- immersive 模式只保留 paragraph，隐藏 callout 和 blockquote。

这个现状适合作为 V1 fallback，但 Translation V2 需要补齐 segment-grounded output 和 display grouping。

## 2. 外部资料校准

本次设计建议参考了以下外部资料，取其稳定原则，不把具体实现照搬到 Claread：

- Plate 官方文档：
  - Static rendering 适合纯展示/SSR/RSC；交互式只读阅读面应继续使用浏览器端 `<Plate readOnly>`。
  - Floating toolbar、comments、plugins 可以作为 selection / note / highlight 的 UI 基础，但 Claread action 和 persistence 必须仍由 domain anchor 驱动。
  - References: [Plate Static](https://platejs.org/docs/static), [Plate Toolbar](https://platejs.org/docs/toolbar), [Plate Comment](https://platejs.org/docs/comment), [Plate Plugin](https://platejs.org/docs/plugin)
- Slate 官方 locations 文档：
  - Path / Point / Range 是 editor document 内的位置表达，适合瞬时 selection 和 transforms，不适合作为跨 snapshot 的业务 anchor。
  - Reference: [Slate Path](https://docs.slatejs.org/api/locations/path)
- Unicode UAX #29：
  - Sentence boundary 是可算法化的文本分割，但不同语言、缩写、标点和脚注会有边界歧义；因此 Anchor Segment 需要 `segment_type/boundary_quality`，UI 不应假设所有 segment 都是真实句子。
  - Reference: [Unicode Text Segmentation UAX #29](https://www.unicode.org/reports/tr29/)
- XLIFF 2.1：
  - 行业标准也区分 translation unit 和 segment，支持 unit-level 上下文与 segment-level 对齐并存。
  - Reference: [XLIFF Core 2.1](https://docs.oasis-open.org/xliff/xliff-core/v2.1/os/xliff-core-v2.1-os.html)
- DeepL API context parameter：
  - 翻译质量通常需要额外上下文，但上下文不一定等同于待翻译 target 本身；这支持 “unit context + segment targets” 的输入形态。
  - Reference: [DeepL Translate API](https://developers.deepl.com/docs/api-reference/translate)

## 3. 问题 1：Stable Base / Blocks / Units / Anchor Segments 如何切分

### 推荐分层

```text
Stable Reading Document
  Stable Document Blocks
    paragraph / heading / list_item / blockquote / table / image / footnote / code_block ...

Canonical Text Layer
  从 main_reading blocks 的 text_content 派生
  UTF-16 offsets / hash / unit / segment / user asset anchor 的唯一文本基准

Reading Units
  worker scheduling / parsed coverage / progress / cost attribution window

Anchor Segments
  sentence-like / clause / fallback window
  user selection / span-bound layer / Ask / RAG grounding anchor

Reader Document View Model / Plate Projection
  display groups / visual hierarchy / mode visibility
```

### 设计原则

1. **Stable Document Blocks 保留作者结构**  
   paragraph、heading、list、blockquote、table、footnote 等必须在 Stable Document Blocks 表达。不要把 Markdown 标记写入 `reading_bases.text`。

2. **Canonical Text 只做 offset 基准**  
   `reading_bases.text` 在迁移期仍可作为 Canonical Text Layer，但只应是纯文本。`#`、`-`、`>`、code fence、GFM table 语法都不应成为业务 offset 基准。

3. **Reading Unit 是 worker 窗口，不是 UI 行块**  
   Unit 可以是一个 paragraph，也可以是超长 paragraph 的 deterministic regroup。它用于 job/bootstrap/publisher/parsed coverage，不应直接决定页面每一行怎么显示。

4. **Anchor Segment 是锚点，不等于视觉 paragraph**  
   Anchor Segment 应该保持 sentence-like 粒度，用于校验和 grounding；Web projection 可以把同一个 source block 内的多个 anchor segments 渲染到同一个 paragraph 或 source display group 中。

5. **避免“每句一行”**  
   Source display 应优先按 Stable Document Block 渲染。Anchor Segment metadata 应作为 inline span / metadata / decoration 存在，不应强制每个 segment 都成为一个段落 block。

6. **AI worker 可按块渐进处理**  
   worker 继续以 Reading Unit 为 claim/publish 单位；unit 内带 segment targets。这样既保留渐进式处理，又避免单句翻译丢上下文。

### 对当前代码的具体建议

Phase 1 不需要重写 `base_builder.py`。更应该先改 Web projection：

- 继续从 snapshot 中拿 `reader_anchor_segment`。
- source visual block 应该按 stable block / unit 内连续 segments 合并显示。
- 每个 text leaf 保留 `anchor_segment_id/unit_id/base_id/unit offsets/text_hash`。
- translation / grammar_note / sentence_analysis 按 display policy 插到 source display group 后。

## 4. 问题 2：Translation Worker 输入上下文和输出粒度

### 推荐输入：unit context + segment targets

不要只传单句，也不要只传整段让模型自由输出。推荐 worker 输入为：

```json
{
  "schema_version": "translation-worker-input/v2",
  "reading_record_id": "...",
  "base_id": "...",
  "generation": 1,
  "unit": {
    "unit_id": "u1",
    "order_index": 1,
    "unit_type": "body",
    "boundary_quality": "normal",
    "source_text": "full unit text",
    "source_text_hash": "8hex"
  },
  "source_language": "en",
  "target_language": "zh-CN",
  "translation_profile": {
    "reading_goal": "daily_reading",
    "reading_variant": "intermediate_reading",
    "style": "study_reader"
  },
  "source_block_context": {
    "block_id": "b1",
    "block_type": "paragraph",
    "heading_path": []
  },
  "previous_context_segments": [
    {
      "anchor_segment_id": "s0",
      "text": "previous sentence"
    }
  ],
  "target_segments": [
    {
      "anchor_segment_id": "s1",
      "order_index": 1,
      "segment_type": "sentence",
      "boundary_quality": "normal",
      "source_text": "Few people can turn passion into reliable income.",
      "source_text_hash": "8hex"
    }
  ],
  "next_context_segments": [
    {
      "anchor_segment_id": "s2",
      "text": "next sentence"
    }
  ]
}
```

关键点：

- `unit.source_text` 给语境。
- `target_segments[]` 给对齐目标。
- previous/next context 只辅助翻译，不允许输出这些 context 的 translation items。
- reading goal / variant 进入 `translation_profile`，并进入 job input signature / operation fingerprint，否则不同目标可能复用错误 layer。

### 推荐输出：segment items + optional group translation

```json
{
  "schema_version": 2,
  "target_language": "zh-CN",
  "translation_profile": {
    "reading_goal": "daily_reading",
    "reading_variant": "intermediate_reading",
    "style": "study_reader"
  },
  "items": [
    {
      "item_id": "tr_s1",
      "anchor_segment_id": "s1",
      "source_text_hash": "8hex",
      "translated_text": "很少有人能把热爱变成稳定收入。",
      "confidence": "high"
    }
  ],
  "semantic_groups": [
    {
      "group_id": "g1",
      "anchor_segment_ids": ["s1", "s2"],
      "translated_text": "两句合并后的自然中文译文。",
      "reason": "combined_for_natural_zh_flow"
    }
  ],
  "unit_translation": {
    "translated_text": "整段兜底译文。",
    "usage": "fallback_only"
  },
  "notes": [],
  "diagnostics": []
}
```

### 为什么要同时支持 items 和 semantic_groups

只存 per-segment items 的优点是 grounding 清楚，但遇到双语自然不一一对应时会牺牲中文流畅度。只存 group translation 的优点是自然，但失去稳定 segment anchor。两者并存更稳：

- `items[]` 是 domain grounding truth。
- `semantic_groups[]` 是 worker 提供的对齐建议。
- projection 最终决定显示 group。
- group 校验失败时，丢弃 group，不丢弃 segment items。

### reading goal / variant 的影响

Claread 的 translation 不是通用翻译，而是阅读理解产品里的学习层。reading goal / variant 至少会影响：

- 译文是否保留英文句法顺序。
- 是否显化省略主语、逻辑连接和指代。
- 术语是意译、保留英文，还是附中文解释。
- 中文句子长短和注释密度。

因此 Translation V2 不能只在 prompt 文本里临时写 goal。推荐：

- job bootstrap 写入 `reading_goal/reading_variant/translation_profile_version`。
- operation fingerprint 包含 profile version 和 goal/variant。
- output_json 保存 `translation_profile`。
- snapshot 暴露 profile metadata，便于 Ask/RAG 解释“这是一份面向某目标的译文”。

## 5. 问题 3：Domain Truth 与 Display Grouping 如何拆分

### 后端应该保存什么

后端应该保存：

- `schema_version=2`
- `target_language`
- `translation_profile`
- per-segment `items[]`
- optional `semantic_groups[]`
- optional `unit_translation` fallback
- diagnostics / confidence / notes

短期不建议新增 translation item 表。更保守的落地是：

```text
enhancement_layers
  layer_type='translation'
  target_scope='unit'
  target_key=unit_id
  schema_version=2
  output_json=TranslationLayerOutputV2
  coverage_json={ segment ids, missing ids, group validation summary }
  quality_json={ alignment failure count, fallback rate, prompt version }
```

这样能复用当前 `enhancement_layers`、publisher、snapshot reload 和 parsed decision 机制，迁移成本低。

### Projection 如何生成 display groups

最终 display group 应由 projection deterministic 生成，输入为：

- Stable Document Block boundary。
- Reading Unit order。
- Anchor Segment order/type/boundary_quality。
- Translation V2 `items[]`。
- Worker `semantic_groups[]`。
- Grammar note / sentence analysis / user asset 是否存在。
- 当前 display mode：intensive / immersive / compact 等。

推荐 policy：

1. 不跨 Stable Document Block。
2. 不跨 non-main-reading block。
3. 不跨 list item / quote / heading boundary。
4. 优先采用通过校验的 worker semantic group。
5. worker group 必须满足：
   - segment ids 存在；
   - 同 unit；
   - 同 stable block；
   - 连续；
   - 没有重复；
   - 每个 segment hash 匹配；
   - translated_text 非空；
   - group size 在 policy limit 内。
6. worker group 校验失败时丢弃该 group hint，使用 deterministic fallback。
7. deterministic fallback 默认 1-3 个连续 sentence segments 一组。
8. 遇到 grammar_note / sentence_analysis 常显 block 时 flush group，避免译文和解析层互相挤压。
9. `fallback_window` 或 `boundary_quality='low'` 的 segment 不强行单独对照，可并入邻近 group 或 unit fallback。

### 冲突和回退策略

| 情况 | 处理 |
|---|---|
 item hash mismatch | layer publish fail-closed，不入库 |
 item 缺少 segment | 可发布但 `coverage_json.missing_segments` 记录；projection 用 unit fallback 或隐藏缺失部分 |
 worker group 跨 block | 丢弃 group hint，保留 items |
 worker group 非连续 | 丢弃 group hint，保留 items |
 worker group 漏/重复 segment | 丢弃 group hint，保留 items |
 worker group translated_text 空 | 丢弃 group hint |
 deterministic grouping 找不到 item | fallback 到 `unit_translation` 或 V1 unit translation |

### 避免前端硬切分语义错误

前端不应按中文标点切译文，也不应把一整段中文平均分配给英文句子。正确做法：

- 前端只使用 worker 已对齐到 segment/group 的 translation text。
- 前端可以合并相邻 segment items，但不能拆 `translated_text` 内部。
- 如果只有 V1 unit translation，前端只能显示 unit-level fallback，不要伪造 segment-level bilingual pairs。

## 6. 问题 4：页面视觉体验设计

### 视觉层级

推荐把页面分成四种可见层：

| 层 | owner | 视觉 |
|---|---|---|
 stable source | `stable_source` | 主字号、最高对比、正常文档段落 |
 translation | `system_ai.translation` | 小一档字号、muted text、左侧细线或浅底、紧贴对应 source group |
 grammar_note | `system_ai.grammar` | inline cue + compact note / callout，低干扰 |
 sentence_analysis | `system_ai.structure` | always-open structure block 或 floating legend，不用普通 callout 卡片 |

### Translation 视觉建议

译文不建议使用语义 blockquote，因为 quote 暗示引用原文，容易混淆。可以保留 quote-like visual，但使用 Claread 自定义 element：

```text
reader_translation_group
  left border: emerald / neutral
  background: subtle neutral/green tint
  font: sans, 0.92-0.95em
  line-height: relaxed
  label: 译文
  source anchors: anchor_segment_ids
```

样式原则：

- 原文始终是阅读重心。
- 译文明显低一层，但不能像 disabled text。
- 不要让译文成为卡片墙。
- group 间距小于 source paragraph 间距。
- 多个 AI layers 同时存在时，translation 紧跟 source，grammar/analysis 再跟随。

### 插入顺序

推荐默认顺序：

```text
source display group
translation display group
grammar note compact blocks
sentence analysis structure block
ask/user supplement cues
```

原因：

- 双语阅读时用户先看英文，再看译文。
- grammar_note 往往解释 source 局部，放在译文后不会打断第一遍阅读。
- sentence_analysis 信息密度最高，应放最后，并允许 compact / legend 模式。

### Plate.js 能力选型

尽量使用官方能力：

- `<Plate readOnly>`：主 Reader Record surface。
- floating toolbar：Lookup / Ask / Note / Highlight / Copy。
- comment/highlight marks：用户笔记和高亮 projection。
- cursor overlay：rail 获焦时保留选区。
- plugin configuration / custom leaf：vocab、grammar、user asset marks。

需要 Claread 自定义 wrapper：

- `reader_translation_group`：因为它不是 quote，也不是普通 callout，而是 source-grounded generated text。
- `reader_sentence_analysis_block`：因为它是结构分析，不应默认折叠成 toggle 或普通 callout。
- Ask supplement cue：因为它有 user-confirmed/generated provenance。

不应使用：

- fixed rich-text toolbar。
- Plate AI suggestion/revision 直接改 Stable Source。
- raw Slate operations / paths 作为 API payload。

## 7. 候选方案比较

### 方案 A：保守方案，保持 unit translation，只优化视觉

做法：

- 保持当前 worker/publisher/schema。
- 前端把 unit blockquote 样式优化为“本段译文”。
- 不做 segment items。

评估：

| 维度 | 评价 |
|---|---|
 correctness | 中。不会破坏 truth，但没有 segment grounding |
 UX | 中低。能读，但双语对照弱 |
 实现成本 | 低 |
 回归测试难度 | 低 |
 Ask/RAG 接入 | 弱，只能 unit-level |
 长期维护 | 差，会阻塞 Translation V2、Ask/RAG 精细引用 |

适合作为 Phase 1 fallback，不适合作为最终方向。

### 方案 B：中间方案，segment items + deterministic display grouping

做法：

- worker 输入 unit context + segment targets。
- worker 输出 per-segment items。
- projection deterministic grouping。
- 不保存 worker placement hints。

评估：

| 维度 | 评价 |
|---|---|
 correctness | 高。每条 translation item 都能回源校验 |
 UX | 高。能做自然双语组，但不会失去 anchor |
 实现成本 | 中 |
 回归测试难度 | 中 |
 Ask/RAG 接入 | 强，能按 anchor_segment_id grounding |
 长期维护 | 高，domain/display 边界清晰 |

这是最低风险的正式 V2 方案。

### 方案 C：激进方案，worker 输出 semantic display groups / placement hints

做法：

- worker 除 segment items 外，输出 group translation 和 placement/group hints。
- projection 优先采用通过校验的 hints。
- hints 不通过则降级 deterministic。

评估：

| 维度 | 评价 |
|---|---|
 correctness | 中高。取决于 publisher 是否能严格校验 |
 UX | 高。中文自然度最好 |
 实现成本 | 高 |
 回归测试难度 | 高 |
 Ask/RAG 接入 | 强，但 group 解释链复杂 |
 长期维护 | 中。若 hints 变成事实源会污染 projection 边界 |

不建议作为 Translation V2 第一版的唯一机制。但可以作为 **方案 B 的非权威增强**：保存 semantic groups，必须可校验、可丢弃、可回退。

## 8. 最终推荐

### Phase 1 应做什么

Phase 1 先修当前 Reader Record 页面的 projection 和读感，不改后端 translation schema：

1. 保持 unit translation fallback。
2. source visual block 不再强制每个 anchor segment 成为单独 paragraph。
3. 把 unit translation 从语义 blockquote 收敛为 Claread translation lane visual。
4. grammar/sentence_analysis 不要继续像普通卡片堆叠，至少明确 compact display order。
5. 保持 `<Plate readOnly>` 和 Plate selection/action pipeline。
6. 写 characterization tests，锁住：
   - V1 unit translation 不挂到第一个 segment 后；
   - immersive 隐藏 translation；
   - intensive 展示 translation；
   - source anchor metadata 仍可生成 selection anchor。

### Translation V2 第一版应做什么

1. 新增 `TranslationLayerOutputV2`。
2. job bootstrap input 加：
   - `anchor_segments`
   - `reading_goal`
   - `reading_variant`
   - `translation_profile_version`
3. operation fingerprint 加入 translation profile。
4. worker prompt 改为 unit context + segment targets。
5. publisher 增加 V2 分支：
   - 校验 item segment 存在；
   - 校验 source hash；
   - 校验 semantic group 连续性；
   - 写 coverage_json / quality_json；
   - group hint 失败不一定让整层失败，但 item hash 失败必须 fail-closed。
6. snapshot 暴露 V2 translation data。
7. frontend projection 生成 `reader_translation_group`。
8. legacy V1 unit translation 作为 fallback 渲染，不伪造成 segment pair。

### 暂不做

- 不新增 persistent backend reader document graph table。
- 不持久化 raw Plate value。
- 不在 V1/V2 阶段支持 AI 文本上的持久 highlight/note。
- 不让 worker 输出 Plate JSON。
- 不把 display group 当 RAG citation truth。
- 不改 legacy `/app/reader/{recordId}`。
- 不把 sentence_analysis chunk offset V2 和 Translation V2 绑在同一个最小切片里。
- 不用 frontend 中文标点硬拆 V1 unit translation。

## 9. 必要数据结构建议

### Backend schema 草案

```python
class TranslationItemV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    anchor_segment_id: str = Field(min_length=1)
    source_text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")
    translated_text: str = Field(min_length=1)
    confidence: Literal["low", "normal", "high"] = "normal"


class TranslationSemanticGroupV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1)
    anchor_segment_ids: list[str] = Field(min_length=1, max_length=4)
    translated_text: str = Field(min_length=1)
    reason: Literal[
        "one_to_one",
        "combined_for_natural_zh_flow",
        "split_long_sentence",
        "fallback_low_boundary",
    ]


class TranslationProfileV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_goal: str | None = None
    reading_variant: str | None = None
    profile_version: str
    style: str = "study_reader"


class TranslationLayerOutputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    target_language: str = Field(min_length=1)
    translation_profile: TranslationProfileV2
    items: list[TranslationItemV2] = Field(min_length=1)
    semantic_groups: list[TranslationSemanticGroupV2] = Field(default_factory=list)
    unit_translation: str | None = None
    notes: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    confidence: Literal["low", "normal", "high"] = "normal"
```

### Projection type 草案

```ts
type ReaderTranslationDisplayGroup = {
  type: "reader_translation_group";
  groupId: string;
  owner: "system_ai";
  source: {
    baseId: string;
    unitId: string;
    anchorSegmentIds: string[];
    layerId: string;
    itemIds: string[];
  };
  translatedText: string;
  placementReason:
    | "worker_semantic_group"
    | "deterministic_sentence_group"
    | "unit_fallback"
    | "low_boundary_fallback";
  quality: "normal" | "fallback" | "alignment_warning";
};
```

## 10. 测试建议

### Backend tests

- V2 output schema rejects extra fields.
- item with unknown `anchor_segment_id` fails.
- item hash mismatch fails.
- duplicate item for same segment fails.
- missing sentence segment records `missing_segments`.
- semantic group across unit fails group validation.
- semantic group across block fails group validation.
- semantic group with non-contiguous segments is discarded.
- V1 output still snapshots as unit fallback.
- reading_goal / variant changes operation fingerprint.

### Frontend tests

- V1 unit translation renders as fallback, not segment pair.
- V2 items render into translation groups.
- valid worker semantic group is used.
- invalid worker semantic group falls back deterministic.
- source paragraph does not become one visual paragraph per sentence when block metadata supports grouping.
- grammar/sentence_analysis presence flushes translation group.
- immersive mode hides translation groups.
- selection still maps to stable source anchor, not translation text.

### Visual checks

- Desktop intensive mode: source remains dominant.
- Desktop immersive mode: no translation/callout clutter.
- Mobile: translation group does not overlap floating toolbar or bottom sheets.
- 200% text zoom: source/translation/callout do not overlap.

## 11. 最大风险和必须验证的 Spike

### 最大风险

1. **LLM alignment 不稳定**  
   模型可能漏 segment、重复 segment、翻译错位或把 context segment 也输出。

2. **中文自然度与 segment grounding 冲突**  
   两句英文合一句中文、一句英文拆两句中文是正常翻译行为。过度强制 one-to-one 会让译文僵硬。

3. **projection group policy 过早产品化**  
   如果把当前 UI 密度策略写进 backend truth，后续调样式会变成数据迁移。

4. **reading_goal/variant 漏进 fingerprint**  
   不同学习目标下同一 unit 可能产生不同译文。若 fingerprint 不区分，会错误复用 layer。

5. **source display 仍按 segment 拆段**  
   即使 Translation V2 做对，如果 source projection 仍是一句一段，页面仍会像诗歌。

### 必做 Spike

#### Spike A：V2 prompt alignment

目标：验证 LLM 在 unit context + segment targets 下能稳定输出合法 V2 schema。

样本：

- 普通短段落。
- 长单段落。
- 引号/括号/缩写多的段落。
- 两句英文自然合成一句中文。
- 一句英文自然拆成多句中文。
- fallback_window / low boundary 段落。

通过标准：

- item segment coverage 达标。
- source_text_hash 100% match。
- invalid group hint rate 可接受。
- 失败能被 publisher 清晰降级或拒绝。

#### Spike B：Display grouping visual prototype

目标：确认不同 group policy 下页面是否仍像文档，而不是解析卡片列表。

要比较：

- V1 unit fallback。
- deterministic 1 sentence group。
- deterministic 1-3 sentence group。
- worker semantic group。
- grammar/sentence_analysis 同时出现的密度。

输出：

- 截图。
- 规则表。
- 推荐 default policy。

#### Spike C：Profile-sensitive translation

目标：确认 reading_goal / variant 是否需要第一版强接。

比较：

- daily/intermediate。
- exam/intensive。
- academic-like but still learning-only。

输出：

- 是否第一版必须接 profile。
- 哪些字段进入 fingerprint。

## 12. 推荐实施顺序

```text
Phase 1: Reader Record projection and visual cleanup
  - source display grouping
  - V1 unit translation lane visual
  - grammar/sentence_analysis display order
  - characterization tests

Phase 2: Translation V2 backend schema and publisher
  - TranslationLayerOutputV2
  - worker input context
  - publisher item/group validation
  - coverage_json / quality_json

Phase 3: Web Translation V2 projection
  - snapshot V2 translation nodes
  - display group builder
  - reader_translation_group Plate element
  - V1 fallback compatibility

Phase 4: Ask/RAG read expansion
  - Ask can reference translation group selection
  - resolver maps translation group back to source segments
  - RAG indexes segment-grounded translation facts if needed

Phase 5: Optional non-source user asset writes
  - only after regenerate/rebase/orphan policy exists
```

## 13. Final Recommendation

Translation V2 第一版不要继续停留在 unit translation，也不要直接走 worker-owned display placement。最稳的方案是：

```text
unit execution window
+ segment-grounded translation items
+ optional validated semantic groups
+ deterministic projection display groups
+ V1 unit fallback
```

这样同时满足：

- AI worker 有足够语境。
- anchor 稳定、可校验。
- 页面不被拆成每句一行。
- 译文、grammar_note、sentence_analysis 能自然进入文档流。
- Ask/RAG 后续能回源到 stable source。
- Plate 继续是 projection，不成为事实源。
