# Enhancement Layers 与 Parsed Decision

> 状态：`D5 修订`
> 最后更新：2026-06-21
> 范围：增强层 schema、anchor 合同、发布门禁和 parsed coverage。

## Layer 原则

- Enhancement Layer 建立在 Stable Reading Base / Reading Units / Anchor Segments 上。
- Layer 可再生、可局部重试。
- Layer 不得修改 Stable Base。
- Layer 不得写入 User Editorial Assets。
- Published layer 必须通过 schema validation 和 anchor validation。
- Published layer 可以触发 Plate projection operations，但 projection 不是 layer truth。

## D4 Layer Scope

D4 只实现：

- `translation` layer
- unit-level anchor
- Parsed Decision 最低门槛：该 unit 的 translation 已 published

D5-V1/V2/V3 已实现 vocabulary backend slice、snapshot/Web projection 和 real PydanticAI executor。Grammar bundle 和 summary 仍后置：Grammar bundle 可由一个 worker 生成，但发布、存储、RAG、eval 和 projection 必须区分 `grammar_note` 与 `sentence_analysis` 两个 layer subtype。Semantic Outline 延后到 D6 或更晚评估。

## System Annotation Layer

System Annotation Layer，也可称 AI Annotation Layer，是 Enhancement Layer 的子类，表示系统 worker 生成的 AI 批注层。

包含：

- vocabulary / phrase / context gloss
- grammar note
- sentence analysis（长难句拆解是它的常见适用场景）
- future rhetorical / logic / term notes

不包含：

- 用户高亮
- 用户笔记
- Ask Claread 保存的用户笔记或高亮
- Ask Supplement

系统 AI 批注层可被重新生成、局部重试或替换。它不能覆盖用户编辑资产，也不能把用户确认状态当作系统 parsed 的证据。

## User Editing Boundary

User Editing Surface 展示并编辑 User Editorial Assets。

User Editorial Assets 包括：

- highlight
- reader note
- Ask Claread 经用户确认后保存的 note / highlight

这些资产必须和系统层使用同一 anchor contract，但生命周期独立：

- User Editorial Assets 不随 Enhancement Layer retry 被删除或改写。
- Ask 只能通过 action proposal 请求写入，确认后才创建 User Editorial Assets。
- User Editorial Assets 的颜色、正文、删除状态由用户动作控制。

Ask Supplement 是单独概念：它是 Ask 生成并经用户确认后追加到页面的 AI 补充入口，显示上可接近 AI 批注，但来源和生命周期必须标记为 `ask_supplement` / `assistant_supplement`，不能混入系统 worker 生成的 System Annotation Layer。

## Anchor Schema

Unit-level anchor：

```json
{
  "anchor_type": "unit",
  "base_id": "base_...",
  "unit_id": "unit_...",
  "text_hash": "1a2b3c4d",
  "hash_algorithm": "fnv1a32-utf16"
}
```

Span-level anchor：

```json
{
  "anchor_type": "text_range",
  "base_id": "base_...",
  "unit_id": "unit_...",
  "anchor_segment_id": "s1",
  "sentence_id": "s1",
  "segment_type": "sentence",
  "offset_unit": "utf16",
  "start_offset": 10,
  "end_offset": 24,
  "selected_text": "selected words",
  "text_hash": "1a2b3c4d",
  "hash_algorithm": "fnv1a32-utf16"
}
```

Span anchors 必须复用 `app/contracts/annotation.py` 的 UTF-16 slicing 和 `fnv1a32-utf16` hash。

Coordinate scope：

- `unit` anchor 校验 `reading_units` 的 base-absolute offsets 和 unit text hash。
- `text_range` anchor 的 `start_offset` / `end_offset` 是 unit-local UTF-16 offsets；多数 segment 是 sentence，少数可为 clause 或 fallback window。
- `anchor_segment_id` 用于确认 span 落在目标 segment 的 unit range 内；不能把 base-absolute offsets 或 Plate path 写入 span anchor fields。
- Cross-segment selection 使用 `multi_text`，由多个 ordered Anchor Segment ranges 组成。

## Layer Content Schema

D4 translation layer：

```json
{
  "schema_version": 1,
  "target_language": "zh-CN",
  "translated_text": "...",
  "notes": [],
  "confidence": "normal"
}
```

D5 vocabulary layer 保留旧 AI Workflow 的三类词汇批注语义，但它们是同一个 `vocabulary` layer 内的 `item_type`，不是三个顶层 layer type：

| `item_type` | 说明 | Projection 形态 |
|---|---|---|
| `vocab_highlight` | 有学习价值的单词高亮，可只有高亮，也可带简短解释。 | inline AI mark；如有解释可展示 tooltip / popover。 |
| `phrase_gloss` | 短语、搭配、习语、专名或复合表达解释。 | inline AI mark + note。 |
| `context_gloss` | 依赖当前上下文才能解释的词义或表达。 | inline AI mark + contextual note。 |

最小形态：

```json
{
  "schema_version": 1,
  "items": [
    {
      "item_type": "vocab_highlight",
      "anchor": {
        "anchor_type": "text_range",
        "base_id": "base_...",
        "unit_id": "unit_...",
        "anchor_segment_id": "s1",
        "sentence_id": "s1",
        "segment_type": "sentence",
        "offset_unit": "utf16",
        "start_offset": 10,
        "end_offset": 24,
        "selected_text": "phrase",
        "text_hash": "1a2b3c4d",
        "hash_algorithm": "fnv1a32-utf16"
      },
      "headword": "phrase",
      "brief_explanation": null,
      "reason": "useful_for_current_goal"
    }
  ]
}
```

Collision priority:

```text
context_gloss > phrase_gloss > vocab_highlight
```

`phrase_gloss` 可使用 `phrase_type = collocation | phrasal_verb | idiom | proper_noun | compound | other`。`context_gloss` 必须说明上下文依赖原因。三类 item 都必须通过相同的 `anchor_segment_id`、UTF-16 offset、selected text 和 text hash 校验。

D5-V1 backend facts：

- `reader_jobs.job_type = 'build_vocabulary_layer'`，`target_type = 'unit'`。
- `VocabularyWorkerService` 默认未配置时失败，不发布空 layer；测试 fake 必须显式注入。
- `VocabularyLayerPublisher` 发布 `enhancement_layers.layer_type = 'vocabulary'` 和 `reader_events.event_type = 'layer_published'`。
- D5-V1 不写 vocabulary parsed decision；是否引入 parsed milestone 留给后续 eval/projection 设计。
- D5-V1 snapshot reload 只暴露 top-level layer metadata；Plate marks/nodes 留给 D5-V2。

D5-V2 snapshot / Web projection facts：

- Published vocabulary layer 会在 snapshot reload 时从 domain facts 重建为 stable source leaf 上的 `reader_vocabulary_marks`。
- Projection 不是 layer truth；`enhancement_layers.output` 仍是正式 `VocabularyLayerOutput` typed schema。
- 三类 item 都作为 inline read-only AI marks 呈现：`vocab_highlight`、`phrase_gloss`、`context_gloss`。
- `start_offset` / `end_offset` 在 layer output 中保持 unit-local；snapshot serializer 会换算出 leaf 内的 `segment_start_utf16` / `segment_end_utf16` 供 Web 渲染。
- Web D5-V2 不实现用户编辑、Ask 修改、real vocabulary executor 或 `projection_ops` incremental applier。

D5-V3 real vocabulary executor facts：

- `reader_layer_vocabulary` route 必须显式配置 `reader_vocabulary_model_profile`；不得 fallback 到 annotation profile。
- LLM 只输出内部 candidate schema；正式 `VocabularyLayerOutput` 由后端 postprocess 生成。
- LLM 不输出 offsets、hash、raw Plate JSON 或 raw Slate ops。
- 后端在 `anchor_segment_id` 指定的 segment 内 exact-match `selected_text`，再生成 unit-local UTF-16 offsets 和 `fnv1a32-utf16` hash。
- 同一 span 冲突按 `context_gloss > phrase_gloss > vocab_highlight` 仲裁；candidate 数量、字段长度和 diagnostics 都必须有上限。
- 空有效 vocabulary output 可以发布，但必须保留 diagnostics 解释 skipped / no-op 原因。

Grammar bundle 的输出必须拆成两个 subtype：

| Subtype | Anchor | 内容形态 | 说明 |
|---|---|---|---|
| `grammar_note` | 1..4 个同 unit 内 `text_range` spans | 语法点、pattern、说明 fragment | 必须锚定到原文片段；适合 inline mark + note。跨 segment 语法关系先用多个同 unit spans 表达，不在 D5-V4 新增通用 `multi_text` contract。 |
| `sentence_analysis` | sentence/unit-bound，通常指向 Anchor Segment 或 Unit | 句型概述、analysis、chunks | 用于长难句或复杂结构拆解；不要求每句都有。 |

D5 初版可以保留一个 `grammar_bundle_worker` 同时产出两类 subtype，减少重复上下文和成本。若后续发现 sentence analysis 的触发条件、成本或质量目标与 grammar note 明显不同，再拆为独立 worker；拆 worker 不改变 layer subtype 合同。

D5-V4 grammar bundle backend facts：

- `reader_jobs.job_type = 'build_grammar_bundle'`，`target_type = 'unit'`。
- Worker output 可以同时包含 `grammar_note` 与 `sentence_analysis`，但 persisted truth 必须是两个独立 `enhancement_layers` rows。
- Layer fingerprints 必须独立：`grammar_note_unit_v1` 与 `sentence_analysis_unit_v1`，不得复用 job-level `grammar_bundle_unit_v1`。
- Empty sanitized output 是 no-op success：不写 layer、不写 `layer_published` event，但 job/run 可成功并记录 no-op diagnostics。
- 单次 bundle usage 只记一条 job-level `ai_usage_events`；不得按两个 layer 重复计费。
- `grammar_note` 任一 span 命中 `fallback_window` 时整条 item 跳过，不能部分保留 spans。
- D5-V4 snapshot 只暴露 top-level layer metadata，不投影 grammar marks/nodes 到 `snapshot.value`。

D5 vocabulary eval seed disposition：

- 评估方向是 `accepted_with_changes`：本地 deterministic seed/graders/tests 先行。
- 下一步不得按单文件 JSONL 落地；应匹配现有 `evals` dataset 目录形态，或新增专用 vocabulary loader。
- LangSmith、LLM judge runner 泛化、vocabulary fallback-window policy 和 parsed/readiness policy 后置。

Summary can be unit-level or section-level but must name its source units.

## Plate Projection

Enhancement Layer 发布后，Projection 层把 domain fact 转为 Plate nodes / marks。

约束：

- Layer Worker 不直接输出 raw Plate path operation。
- Layer Publisher 不把 Plate document 当 truth。
- Plate projection op 必须指向稳定 domain target：`unit_id`、`anchor_segment_id`、`layer_id`。
- AI 输出 Markdown / Plate fragment 时，必须经过 schema allowlist、sanitize、长度限制和 anchor/source grounding。
- `grammar_note` 和 `sentence_analysis` 应以不同 projection form 表达：
  - `grammar_note` 通常是 span-bound inline mark + expandable note。
  - `sentence_analysis` 通常是 sentence/unit-bound structured block，可包含 clause chunks、表格或分层结构。
- Projection 失败不应回滚已发布 layer；应写 projection failure diagnostic，并允许 snapshot rebuild。

## Publish Policy

Layer Publisher 发布前检查：

- content schema valid
- anchor schema valid
- target unit belongs to current Stable Base
- target Anchor Segment belongs to target unit when span-bound
- span selected text matches offsets/hash
- output is grounded in the target unit or declared source units
- run generation and record state still valid
- CAS winner not already published

Failed layer 不阻塞 Reader body。局部失败进入 `layer_failed` event，可重试或降级。

Publisher 禁止写 User Editorial Assets。需要保存用户高亮或笔记时，必须走 User Editing Surface 或 Ask action confirmation。

## Parsed Decision

`Parsed` 是 Reading Unit 状态，不是固定批注数量。

D4 写入前置：

- translation layer exists
- translation status = `published`
- unit-level anchor valid
- record not cancelled / superseded

D5+ 可加入更多 policy：

- vocabulary / grammar_note / sentence_analysis 是否对当前 reading goal 有价值
- skipped layer 必须有 rationale
- eval sampling 检查“合理跳过”和“偷懒跳过”

`coverage_complete` 必须由 aggregate 推导：

```text
all target reading_units have parsed_decision.status = parsed
```

Planner 不允许直接写 `coverage_complete`。

## D2 Spike

D2 需要验证：

- translation structured output schema。
- anchor validation 与现有 annotation contract 的一致性。
- Parsed Decision eval rubric。
- layer publish CAS 和 late worker result 拦截。
