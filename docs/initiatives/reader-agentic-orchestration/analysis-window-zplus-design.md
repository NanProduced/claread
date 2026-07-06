# Z+ Analysis Window Design Spec

> **状态**：v1 设计契约（rev 5，吸收五轮 review），待评审
> **rev 5 修正**：补充 §8.2 Window claim/preflight 阶段（pending→running 迁移时机）；修正 `_assert_lease_valid` 调用方式（模块级同步函数，不 await）；修正 `JobJobRuntime` typo 为 `ReaderJobRuntime`
> **rev 4 修正**：publish 事务明确区分 `_apply_transition` 与 `transition()` 的职责边界（手动复刻校验流程 + 调用 `_apply_transition`）；unit.char_count 改用 `reading_units.base_*_utf16` 而非 anchor 求和；修正 `_apply_transition` 不重置 attempt_count 的运行时语义描述
> **相关文档**：[concepts.md](./concepts.md) / [target-architecture.md](./target-architecture.md) / [CONTEXT.md](../../../CONTEXT.md)
> **背景**：BBC 文章 `cd6684a0` 暴露的批注密度爆炸问题（grammar 34 + sentence 26 = 60 条标注压在 37 个短 unit 上）

## 0. 设计边界（贯穿全文）

**Analysis Window 是 LLM analysis scope，不是前端展示单位。**

最终 published layer 仍必须满足现有 Reader Plate / enhancement_layers / Pydantic output contract：
- `enhancement_layers.target_scope = 'unit'`，`target_key = unit_id`
- `output_json` schema = `GrammarNoteLayerOutput` / `SentenceAnalysisLayerOutput`（`extra="forbid"`）
- `GrammarNoteItem.spans` 所有 span 必须同一 unit_id（same-unit invariant）
- 前端 contract 不变，前端不感知 window 存在

Window 是 LLM 调度 + selector + ledger 的内部机制，不暴露给前端契约。

## 1. 背景与问题

### 1.1 问题现象

文章 `cd6684a0`（BBC 新闻，6064 chars）切分为 37 个 Reading Unit / 46 个 anchor_segments。每个 unit 平均 162 chars，全是单句段。当前 grammar_bundle worker 按 per-unit 颗粒度执行 LLM 调用，导致：

- **LLM 调用爆炸**：37 个 unit × 1 grammar_bundle job = 37 次 LLM 调用
- **批注密度爆炸**：grammar_note 34 条 + sentence_analysis 26 条 = 60 条 AI 批注
- **token 开销爆炸**：每 unit 短文本 + 固定 system prompt，token 利用率极低
- **阅读体验差**：每句 2-3 条 AI 标注

### 1.2 根因

Reading Unit 同时承担了三件事：
1. 阅读结构（discourse 边界）
2. LLM 调度颗粒度
3. 批注预算分配

三者耦合导致：Unit 切碎 → LLM 调用爆炸 → 每 Unit 独立预算 → 批注密度爆炸。

### 1.3 不能回退到旧 AI Workflow

旧 AI Workflow（`services/api/app/services/analysis/`）全篇一次 LLM 调用无法渐进式输出，长文 LLM 格式错误率高，与产品定位冲突。**Z+ 必须在 agentic orchestration 架构内解决问题。**

### 1.4 Z+ 解决思路

三段式分离：

```
Stable Base（Reading Units + Anchor Segments）—— 阅读结构、导航、回源
  ↓
LayerAnalysisPlan + AnalysisWindows —— LLM 调度窗口（内部机制）
  ↓
Online Selector + Plan Ledger —— 批注质量与预算控制（内部机制）
  ↓
Publisher —— 发布 unit-scoped enhancement_layers（现有 contract 不变）
```

## 2. 核心概念定义

### 2.1 LayerAnalysisPlan（解析计划）

**定义**：per-record-per-layer 的全篇解析计划，持有 ledger（预算账本），是 SELECT FOR UPDATE 锁的物理载体。

**生命周期**：从创建到所有 window 完成（含 no-op）或被 supersede。

### 2.2 AnalysisWindow（分析窗口）

**定义**：单次 LLM 调用的输入范围，覆盖 N 个相邻 Reading Unit / M 个相邻 Anchor Segment。

**关键约束**：
- Window 可以包含多个 unit 的 anchor
- LLM output（candidate）可以来自多个 unit
- 但单条最终 published grammar_note 仍锚定到**单 unit**（兼容 `GrammarNoteItem.validate_same_unit_spans`）
- multi-unit span 的 candidate 在 v1 被 reject 或拆分

**与 reader_jobs 的关系**：1 window : 1 reader_job（复用现有 job 状态机和 attempt_count retry 机制，不创建新 job 表）。

### 2.3 Online Selector（在线选择器）

**核心原则**：软排序（window 内按 candidate quality 排序）+ 硬边界（hard gates 决定发布）。

### 2.4 Plan Ledger（计划账本）

`layer_analysis_plans` 中的 JSONB 字段集合，记录全篇预算与已用情况。

## 3. Implementation Contract

### 3.1 AnalysisAnchorView（planner 输入视图）

Window planner 的输入不是 raw `anchor_segments` 行，而是一个派生视图 `AnalysisAnchorView`，由三张现有表构造：

| 字段 | 来源 | 说明 |
|---|---|---|
| `anchor_segment_id` | `anchor_segments.anchor_segment_id`（**TEXT**，非 UUID） | LLM prompt / selector / publisher 全链路使用的 anchor 标识，与 [grammar_worker.py:135 `GrammarCandidateSpan.anchor_segment_id`](../../../services/api/app/services/reader_orchestration/grammar_worker.py#L135) 一致 |
| `anchor_row_id` | `anchor_segments.id`（UUID 主键） | 内部行引用，仅用于 debug / log，不进入 prompt |
| `unit_id` | `anchor_segments.unit_id` | TEXT |
| `unit_order_index` | `anchor_segments.unit_order_index` | unit 内排序 |
| `base_id` | `anchor_segments.base_id` | UUID |
| `order_index` | `anchor_segments.order_index` | 全局排序 |
| `base_start_utf16` | `anchor_segments.base_start_utf16` | anchor base-absolute 起始 |
| `base_end_utf16` | `anchor_segments.base_end_utf16` | anchor base-absolute 结束 |
| `unit_base_start_utf16` | `reading_units.base_start_utf16`（[0001 schema line 816](../../../infra/migrations/0001_initial_schema.sql#L816)） | 所属 unit 的 base-absolute 起始 |
| `unit_base_end_utf16` | `reading_units.base_end_utf16`（[0001 schema line 817](../../../infra/migrations/0001_initial_schema.sql#L817)） | 所属 unit 的 base-absolute 结束 |
| `unit_char_count` | `unit_base_end_utf16 - unit_base_start_utf16` | **unit 长度（用于切分算法），不是 anchor 长度求和** |
| `block_id` | `stable_document_blocks.block_id`（**range intersection**，非 FK） | 见下文“block 映射规则” |
| `block_type` | `stable_document_blocks.block_type` | 来自 intersected block |
| `canonical_text_start_utf16` | `stable_document_blocks.canonical_text_start_utf16` | block 在 canonical text 中的起点 |
| `canonical_text_end_utf16` | `stable_document_blocks.canonical_text_end_utf16` | block 在 canonical text 中的终点 |
| `anchor_char_count` | `base_end_utf16 - base_start_utf16` | anchor 自身长度（仅用于诊断，不用于切分） |

**不要在伪代码里假设 `anchor.block_type / anchor.block_id / anchor.unit_char_count` 是 `anchor_segments` 的原生字段。** 它们是 `AnalysisAnchorView` 的派生字段。

**切分算法的 char_count 来源**：必须用 `unit_char_count`（来自 `reading_units` 表的 base-absolute range），**不能用 `sum(anchor_char_count)`**。理由：
- `reading_units.base_start_utf16 / base_end_utf16` 是 unit 的完整范围（含未被 anchor 覆盖的空白/标题/异常片段）
- anchor 在 clause / fallback_window 模式下可能不连续，求和会漏掉范围
- `reading_units` 是 deterministic source of truth，与 Base Builder 切分逻辑一致

#### block 映射规则（range intersection）

当前 schema 没有 `reading_units` → `stable_document_blocks` 的 FK，`stable_document_blocks` 通过 `canonical_text_start_utf16 / canonical_text_end_utf16`（可空）记录其在 Canonical Text Layer 中的位置（见 [0004_reader_document_blocks.sql:137-140](../../../infra/migrations/0004_reader_document_blocks.sql#L137-L140)）。

**派生规则**：
- 对每个 `anchor_segment`，取其 `[base_start_utf16, base_end_utf16)` 与同 `stable_document_id` 下所有 `stable_document_blocks.canonical_text_*` 区间求交
- 严格包含：`block.canonical_text_start_utf16 <= anchor.base_start_utf16` AND `block.canonical_text_end_utf16 >= anchor.base_end_utf16` → `block_id` 取该 block
- 跨 block 边界的 anchor（罕见）：取起点所在 block 作为主 `block_id`，并标记 `crosses_block_boundary = true`
- `canonical_text_*` 为 NULL 的 block（如 image / image_ocr 无 text）：不参与映射，对应 anchor 退化到 `unknown` 类型
- 一个 anchor 对应多个候选 block 时，取 `order_index` 最小的 block

### 3.2 Window Job 与 reader_jobs 的契约

**复用现有 reader_jobs 表，不新增 status enum 值。**

现有 reader_jobs status（来自 `app/schemas/reader_orchestration.py:52-62`）：
```
queued / claimed / retry_later / paused / skipped / succeeded / failed_terminal / cancelled / superseded
```

**Window job 配置**：

| 字段 | 值 |
|---|---|
| `job_type` | `build_grammar_bundle_window`（**已定 contract：新增 job_type，不复用 `build_grammar_bundle`**） |
| `target_type` | `unit_range`（现有 enum 值） |
| `target_key` | window_id（UUID 字符串） |
| `input_json` | `{plan_id, window_index, target_unit_ids, target_anchor_ids, context_anchor_prev, context_anchor_next, window_budget}` |
| `operation_fingerprint` | `grammar_bundle_window_v1` |
| `max_attempts` | 复用现有 retry 配置 |

**新增 job_type 的同步要求**（必须全部完成，否则 window job 不进入进度聚合 / claim / publish 路径）：

| # | 文件 / 符号 | 修改 |
|---|---|---|
| 1 | `infra/migrations/*.sql` `reader_jobs.job_type` CHECK | 追加 `build_grammar_bundle_window` |
| 2 | [job_bootstrap.py:52-56](../../../services/api/app/services/reader_orchestration/job_bootstrap.py#L52-L56) `_LAYER_NAME_BY_JOB_TYPE` | 追加 `"build_grammar_bundle_window": "grammar_bundle"` |
| 3 | [repository.py:77-81](../../../services/api/app/services/reader_orchestration/repository.py#L77-L81) `_JOB_CAPABILITY_BY_TYPE` | 追加 `"build_grammar_bundle_window": "grammar"`，否则 progress capability 聚合漏 window job |
| 4 | [repository.py:82-86](../../../services/api/app/services/reader_orchestration/repository.py#L82-L86) `_JOB_LAYER_TYPE_BY_TYPE` | 追加 `"build_grammar_bundle_window": None`（grammar_bundle window job 不直接产出单 layer） |
| 5 | [repository.py:918-922](../../../services/api/app/services/reader_orchestration/repository.py#L918-L922) progress job query `job_type IN (...)` | 追加 `'build_grammar_bundle_window'`，否则 reader snapshot 缺 window job 状态 |
| 6 | `pipeline_runner.py` / `worker_loop.py` job type routing | 确认 window job 被 route 到 `grammar_window_worker`，不被旧 `grammar_worker` claim |
| 7 | `layer_publisher.py` | 新增 `publish_window_grammar_bundle` method（替代 `publish_unit_grammar_bundle`），沿用 publisher 现有 transaction / fence / event 模式（见 §8.3） |

**window 自身状态**（`analysis_windows.status`，独立于 reader_jobs.status）：
```
pending / running / completed / no_op / failed
```

`analysis_windows.job_id` 存储 reader_jobs.id 引用，不做 FK 约束（避免 migration 复杂度，与现有 `enhancement_layers.source_job_id` 一致使用软引用）。

### 3.3 Publisher 输出策略

**v1 采用 unit-scoped multi-layer publish**：

1. Window job 一次 LLM 分析多个 units，产出 candidates
2. Selector 接受 candidates 后，按 `unit_id` 分组
3. 对每个有 accepted candidate 的 unit，分别构建 `GrammarNoteLayerOutput` / `SentenceAnalysisLayerOutput`
4. 一个 window 事务内可以发布多个 unit-targeted layer 行
5. `enhancement_layers.target_scope = 'unit'`，`target_key = unit_id`（与现有 contract 一致）

#### Window Target Ownership（强约束）

**`unit` 是 window target 的最小不可拆单位。** 一个 `unit_id` 的所有 anchor 必须落在同一个 window 中，不允许在 unit 内部切分 window。

理由：现有唯一索引 [`uq_enhancement_layers_active_published`](../../../infra/migrations/0001_initial_schema.sql#L1054) ON `(record_id, base_id, layer_type, target_scope, target_key) WHERE status = 'published'`。如果两个 window 都接受同一 unit 的 candidate 并各自 publish，第二个 INSERT 会因唯一索引冲突而失败，破坏 publish 事务原子性。

**单 unit 超 `safety_max` 的处理**：
- v1 不在 window planner 中拆 unit
- 若单 unit `char_count > safety_max`（3000 UTF-16 chars），planner 仍将其整体放入一个 window，并标记 `oversized_unit = true`
- 该 window 的 LLM 调用按 oversized 处理（prompt 注入额外上下文，budget 按 unit 内 anchor 数自适应）
- 长期方案：Base Builder v2（§13 v0.5）通过 hard_min/target_max/safety_max 在 Reading Unit 层面控制大小，从源头避免 oversized unit

#### Provenance 字段（不进 output_json）

- `source_window_id` / `plan_id` / `window_index` 写入 `enhancement_layers.quality_json`
- `source_job_id` 已有字段，写入 window job 的 reader_jobs.id

#### 兼容现有唯一索引

`uq_enhancement_layers_active_published ON (record_id, base_id, layer_type, target_scope, target_key) WHERE status = 'published'`。**由于 unit 不可拆且 windows 之间 unit 非重叠**，每个 unit 最多被一个 window 覆盖，所以一个 unit 最多一条 published grammar_note layer + 一条 published sentence_analysis layer。

**No-op 处理**：如果某 unit 在 window 中 0 个 candidate 被 accepted，不发布该 unit 的 layer。该 unit 的"已覆盖"状态由 `analysis_windows.coverage` 持久化（`no_op_units` 列表），防止 bootstrap 重新排队。

### 3.4 Published item 的局部锚定约束

- Window 可以包含多个 unit
- Candidate 可以来自多个 unit
- 但**单条最终 published grammar_note 仍锚定到单 unit**（兼容 `GrammarNoteItem.validate_same_unit_spans`）
- `GrammarNoteItem.spans` 所有 span 必须同一 `unit_id`
- Multi-unit span 的 candidate 在 v1 被 reject（selector gate），不作为正常输出
- 这符合"减少密集批注"的产品目标

### 3.5 Self-rating 不进入前端 output_json

**Sidecar / Candidate schema**（worker 内部，不持久化到 enhancement_layers）：
```json
{
  "anchor_segment_id": "u5_a1",
  "unit_id": "u5",
  "span_start_utf16": 12,
  "span_end_utf16": 28,
  "grammar_pattern": "though + clause",
  "explanation_zh": "...",
  "quality_score": 4,
  "reading_blocker": false,
  "reason_code": "grammar_pattern",
  "confidence": 0.85,
  "dedup_hint": "though_concession"
}
```

**Published layer schema**（写入 `enhancement_layers.output_json`，schema 不变）：
```json
{
  "schema_version": 1,
  "items": [
    {
      "item_type": "grammar_note",
      "spans": [ReaderTextRangeAnchor],
      "grammar_point": "...",
      "pattern": "...",
      "note": "..."
    }
  ]
}
```

`GrammarNoteItem` / `SentenceAnalysisItem` 的 `extra="forbid"`，**不能**加 self-rating 字段。Self-rating 字段只存在于：
- Candidate DTO（worker 内存）
- Diagnostics（job output / log）
- `enhancement_layers.quality_json`（provenance，部分字段）

## 4. Plan / Window Schema

### 4.1 layer_analysis_plans 表

ledger counters **按 item_type 拆分**（grammar_note 与 sentence_analysis 的预算、cap、dedup 语义不同，不能共用单一集合）。

```sql
CREATE TABLE layer_analysis_plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reading_record_id UUID NOT NULL REFERENCES reading_records(id) ON DELETE CASCADE,
  base_id UUID NOT NULL REFERENCES reading_bases(id) ON DELETE CASCADE,
  layer_type TEXT NOT NULL,  -- v1: 'grammar_bundle'
  policy_version TEXT NOT NULL,
  generation INT NOT NULL CHECK (generation >= 1),
  budget_total JSONB NOT NULL,
  -- typed counters: {grammar_note: {...}, sentence_analysis: {...}}
  budget_used JSONB NOT NULL DEFAULT '{"grammar_note":{}, "sentence_analysis":{}}'::jsonb,
  published_anchor_counts_by_type JSONB NOT NULL DEFAULT '{"grammar_note":{}, "sentence_analysis":{}}'::jsonb,
  -- {item_type: {anchor_segment_id: count}}
  published_dedup_keys_by_type JSONB NOT NULL DEFAULT '{"grammar_note":[], "sentence_analysis":[]}'::jsonb,
  -- {item_type: [semantic_dedup_key, ...]}
  published_pattern_keys_by_type JSONB NOT NULL DEFAULT '{"grammar_note":[], "sentence_analysis":[]}'::jsonb,
  -- {item_type: [pattern_key, ...]}（仅 grammar_note 使用，sentence_analysis 退化为空）
  density_by_record JSONB NOT NULL DEFAULT '{"grammar_note":0, "sentence_analysis":0}'::jsonb,
  -- {item_type: int} 当前 record 已 publish 的该类型总数
  covered_window_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  no_op_windows JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL CHECK (status IN (
    'planning', 'active', 'completed', 'completed_with_failures', 'superseded'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial unique: 同一 record/base/layer 只能有一个 active plan
CREATE UNIQUE INDEX uq_layer_analysis_plans_active
  ON layer_analysis_plans(reading_record_id, base_id, layer_type)
  WHERE status IN ('planning', 'active');
```

**JSONB 形状示例**（publish 一条 grammar_note 后）：
```json
{
  "budget_used": {
    "grammar_note": {"count": 1, "anchor_ids": ["u5_a1"]},
    "sentence_analysis": {"count": 0, "anchor_ids": []}
  },
  "published_anchor_counts_by_type": {
    "grammar_note": {"u5_a1": 1},
    "sentence_analysis": {}
  },
  "published_dedup_keys_by_type": {
    "grammar_note": ["though_concession:adverbial_clause"],
    "sentence_analysis": []
  },
  "published_pattern_keys_by_type": {
    "grammar_note": ["though_concession"],
    "sentence_analysis": []
  },
  "density_by_record": {
    "grammar_note": 1,
    "sentence_analysis": 0
  }
}
```

**Status enum**：
- `planning`：plan 创建中，windows 未切分完
- `active`：plan 已激活，windows 可执行
- `completed`：所有 windows 完成（含 no-op），无 failed window
- `completed_with_failures`：部分 window failed 且 retry 耗尽，plan 终态（允许部分 unit 无 layer）
- `superseded`：被新 generation plan 替代

### 4.2 analysis_windows 表

```sql
CREATE TABLE analysis_windows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id UUID NOT NULL REFERENCES layer_analysis_plans(id) ON DELETE CASCADE,
  window_index INT NOT NULL,
  target_anchor_ids JSONB NOT NULL,
  context_anchor_prev JSONB NOT NULL DEFAULT '[]'::jsonb,
  context_anchor_next JSONB NOT NULL DEFAULT '[]'::jsonb,
  target_unit_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  target_block_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  char_count INT NOT NULL,
  anchor_count INT NOT NULL,
  window_budget JSONB NOT NULL,
  coverage JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {covered_unit_ids, no_op_unit_ids}
  status TEXT NOT NULL CHECK (status IN (
    'pending', 'running', 'completed', 'no_op', 'failed'
  )),
  job_id UUID,  -- 软引用 reader_jobs.id，不做 FK
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(plan_id, window_index)
);

CREATE INDEX idx_analysis_windows_plan_status ON analysis_windows(plan_id, status);
CREATE INDEX idx_analysis_windows_job ON analysis_windows(job_id) WHERE job_id IS NOT NULL;
```

**Retry 模型**：复用现有 `reader_jobs.attempt_count` / `max_attempts` / `transient_attempt_count`，不另建 1 window : N jobs。Retry 时 reader_jobs 重新 claim 同一 job，window worker 重新执行 LLM + publish。`analysis_windows.job_id` 不变。

## 5. Window Formation Policy

### 5.1 边界类型

| Block 类型 | 边界性质 | Window 行为 |
|---|---|---|
| `paragraph` / `list_item` | soft boundary | 可跨，自由组合进 window |
| `heading` | hard boundary + section context | 切分当前 window，heading 进入下一 window 的 context，不作为 target |
| `blockquote` / `caption` / `table` / `table_row` / `table_cell` / `footnote` / `image` / `image_ocr` | isolation boundary | 独立成 window，作为 isolated target |
| `code_block` | skip（grammar_bundle v1） | 不进入常规 window |
| `unknown` | soft boundary | 按 paragraph 处理 |

### 5.2 切分算法（修正 heading context 覆盖 + unit 不可拆）

**核心约束**：以 `unit` 为最小不可拆单位（§3.3 Window Target Ownership）。算法先按 `unit_id` 分组 anchor，再以 unit 整体加入 window，**禁止在 unit 内部切分 window**。

```
参数：
  target_max = 1500 UTF-16 chars
  safety_max = 3000 UTF-16 chars
  context_anchor_count = 2

输入：sorted_anchor_views（AnalysisAnchorView list，按 order_index 排序）
输出：windows[]

预处理：
  # 按 unit_id 分组，保留每组内 anchor 按 order_index 升序
  unit_groups = group_by_unit_id(sorted_anchor_views)
  # 每个 unit_group 直接取 AnalysisAnchorView 的 unit-level 字段：
  #   unit.char_count = anchors[0].unit_char_count  # 来自 reading_units.base_*_utf16
  #     （不是 sum(anchor.anchor_char_count)，避免漏掉未 anchor 覆盖的范围）
  #   unit.unit_base_start_utf16 = anchors[0].unit_base_start_utf16
  #   unit.unit_base_end_utf16 = anchors[0].unit_base_end_utf16
  #   unit.dominant_block_type = 多数 anchor 的 block_type（用于边界判定）
  #   unit.contains_isolation = 任一 anchor block_type ∈ isolation set
  #   unit.contains_code_block_only = 所有 anchor block_type == 'code_block'

算法：
  windows = []
  current_window = new_window(index=0)
  prev_context_anchors = []        # 前一 window 末尾 N 个 target anchor
  pending_section_context = []    # 累积的 heading（等待加入下一 window 的 context）

  for unit in unit_groups:
    # 整个 unit 是 code_block：skip（grammar_bundle v1）
    if unit.contains_code_block_only:
      continue

    # 整个 unit 主导类型是 isolation block：unit 独立成 window
    if unit.contains_isolation:
      if current_window.target_anchors not empty:
        finalize_window(current_window,
                        prev_context = prev_context_anchors + pending_section_context)
        prev_context_anchors = current_window.target_anchors[-context_anchor_count:]
        pending_section_context = []
        current_window = new_window(index=len(windows))

      isolated_window = new_window(index=len(windows))
      isolated_window.add_unit(unit)  # 整 unit 加入
      finalize_window(isolated_window,
                      prev_context = prev_context_anchors + pending_section_context)
      prev_context_anchors = unit.anchors[-context_anchor_count:]
      pending_section_context = []
      current_window = new_window(index=len(windows))
      continue

    # unit 主导类型是 heading：hard boundary，unit 整体进入下一 window context
    if unit.dominant_block_type == 'heading':
      if current_window.target_anchors not empty:
        finalize_window(current_window,
                        prev_context = prev_context_anchors + pending_section_context)
        prev_context_anchors = current_window.target_anchors[-context_anchor_count:]
        pending_section_context = []
        current_window = new_window(index=len(windows))
      # 整个 unit 的 anchor 累积到 pending_section_context
      pending_section_context.extend(unit.anchors)
      continue

    # normal unit（paragraph / list_item / unknown）
    # 检查加入后是否超 safety_max —— 若超，先 finalize 当前 window
    # 注意：即使加入后超 safety_max，也必须整个 unit 加入（unit 不可拆）
    if current_window.char_count + unit.char_count > safety_max \
       and current_window.target_anchors not empty:
      finalize_window(current_window,
                      prev_context = prev_context_anchors + pending_section_context)
      prev_context_anchors = current_window.target_anchors[-context_anchor_count:]
      pending_section_context = []
      current_window = new_window(index=len(windows))

    current_window.add_unit(unit)
    # 标记 oversized（不阻止加入，仅作 budget / prompt 调整信号）
    if unit.char_count > safety_max:
      current_window.oversized_units.append(unit.unit_id)

    if current_window.char_count >= target_max:
      finalize_window(current_window,
                      prev_context = prev_context_anchors + pending_section_context)
      prev_context_anchors = current_window.target_anchors[-context_anchor_count:]
      pending_section_context = []
      current_window = new_window(index=len(windows))

  if current_window.target_anchors not empty:
    finalize_window(current_window,
                    prev_context = prev_context_anchors + pending_section_context)

  # 第二次遍历：填充 context_anchor_next
  for i, window in enumerate(windows):
    if i + 1 < len(windows):
      window.context_anchor_next = windows[i + 1].target_anchors[:context_anchor_count]

finalize_window(window, prev_context):
  window.context_anchor_prev = prev_context[-context_anchor_count:]
  window.window_budget = compute_window_budget(window)
  windows.append(window)
```

**关键差异（与按 anchor 切分对比）**：
- 切分粒度从 anchor 升到 unit，保证 `target_unit_ids` 在 windows 间互斥
- `safety_max` 不再是硬截断，而是触发 finalize 当前 window + 新 window 接收整个 unit
- 单 unit 超 `safety_max` 时仍整体放入 window，仅标记 `oversized_unit` 由 prompt / budget 处理

### 5.3 Target / Context anchor 分离

- Target anchors：window 内的 paragraph/list/blockquote 等 anchor，LLM output 必须落在这里
- Context anchors：前 1-2 个 + 后 1-2 个 target anchor + heading section context
- context_chars_cap = 400-600 UTF-16 chars
- LLM output 只能落在 target anchors，selector 验证 `anchor_segment_id ∈ target_anchor_ids`

## 6. Prompt Schema

### 6.1 允许修改的 prompt 文件

- `services/api/prompts/agents/reader_layer_grammar_bundle.yaml`

### 6.2 Prompt 结构

```yaml
system: |
  You are an expert English reading tutor. Analyze a window of English text
  and produce high-quality grammar notes and sentence analyses.

  ## Critical Rules

  1. OUTPUT ANCHOR CONSTRAINT: Every output item MUST have an `anchor_segment_id`
     that is in the TARGET anchors list. Items targeting CONTEXT_ONLY anchors
     will be rejected.

  2. CONTEXT-ONLY ANCHORS: Anchors marked [CONTEXT_ONLY] are for context only.
     You MUST NOT output items targeting CONTEXT_ONLY anchors.

  3. BUDGET CONSTRAINT: This window has a budget of
     {grammar_note_per_window} grammar_notes and {sentence_analysis_per_window}
     sentence_analyses. Producing fewer is better than producing low-value items.

  4. NO-OP IS VALID: If no anchor warrants annotation, return empty arrays.
     This is a successful result, not a failure.

  5. QUALITY OVER QUANTITY: Annotate only when the anchor has:
     - A grammar pattern worth teaching
     - A long/complex sentence worth parsing
     - A meaning-blocking construct
     - An exam-relevant point

  6. SELF-RATING REQUIRED: Each output item MUST include self-rating fields
     (quality_score, reading_blocker, reason_code, confidence, dedup_hint).

  7. SAME-UNIT SPANS: Each item's spans must all be within the same unit_id.

user: |
  ## Target Anchors (output must target these)
  [TARGET] anchor_id=u5_a1, unit_id=u5
  Text: "Though Madison Square Garden is putting on a refreshingly ambitious
  and aesthetically pleasing display..."
  [/TARGET]

  [TARGET] anchor_id=u5_a2, unit_id=u5
  Text: "It was nonetheless an evening that felt a little too neat and tidy."
  [/TARGET]

  ## Context Anchors (for understanding only, do not target)
  [CONTEXT_ONLY] anchor_id=u4_a3, unit_id=u4
  Text: "..."
  [/CONTEXT_ONLY]

  ## Window Budget
  - grammar_note: max {grammar_note_per_window}
  - sentence_analysis: max {sentence_analysis_per_window}
```

### 6.3 Candidate DTO（LLM 输出 schema）

```json
{
  "grammar_notes": [
    {
      "anchor_segment_id": "u5_a1",
      "unit_id": "u5",
      "span_start_utf16": 12,
      "span_end_utf16": 28,
      "grammar_pattern": "though + clause",
      "grammar_point": "让步状语从句",
      "explanation_zh": "though 引导让步状语从句...",
      "quality_score": 4,
      "reading_blocker": false,
      "reason_code": "grammar_pattern",
      "confidence": 0.85,
      "dedup_hint": "though_concession"
    }
  ],
  "sentence_analyses": [
    {
      "anchor_segment_id": "u5_a2",
      "unit_id": "u5",
      "span_start_utf16": 0,
      "span_end_utf16": 56,
      "label": "长难句分析",
      "analysis_zh": "这句话的主语是 It，谓语是 was...",
      "chunks": [{"order": 1, "label": "主干", "text": "It was an evening"}],
      "quality_score": 5,
      "reading_blocker": true,
      "reason_code": "long_sentence",
      "confidence": 0.92,
      "dedup_hint": "long_sentence_inversion"
    }
  ]
}
```

### 6.4 Self-Rating 字段语义

| 字段 | 类型 | 说明 | 用途 |
|---|---|---|---|
| `quality_score` | int 1-5 | LLM 自评质量 | window-local 排序 |
| `reading_blocker` | bool | 是否阅读理解障碍 | 排序（true 优先） |
| `reason_code` | enum | `grammar_pattern` / `long_sentence` / `exam_relevant` / `meaning_blocker` / `discourse_signal` / `low_value` | 排序 + diagnostics |
| `confidence` | float 0-1 | LLM 置信度 | v1 仅记录 |
| `dedup_hint` | string | 去重提示 | 参考（selector 计算实际 key） |

**这些字段不进入 `enhancement_layers.output_json`**（`GrammarNoteItem` / `SentenceAnalysisItem` 的 `extra="forbid"`）。它们只存在于 candidate DTO 和 diagnostics。

## 7. Selector Hard Gates

### 7.1 Dedup Key 设计（两层 + 按 item_type 拆分）

ledger counters 按 `item_type` 拆分（§4.1）。dedup key / pattern_key 也必须分桶存储和查询，避免 `sentence_analysis` 占掉 `grammar_note` 的 per-anchor quota，或 `sentence_analysis` 被 `grammar_pattern` 误 dedup。

**candidate_identity**（防同 anchor 重复，per-window-local）：
```
candidate_identity = f"{item_type}:{anchor_segment_id}:{span_start_utf16}:{span_end_utf16}:{grammar_pattern_canonical}"
```
用于：同一 window 内 candidate 去重（防止 LLM 输出重复 item）。仅 worker 内存使用，不持久化。

**semantic_dedup_key**（防跨 window 重复，typed）：
```
semantic_dedup_key(item_type) = f"{item_type}:{grammar_pattern_canonical}:{construction_family}"
```
**不包含 `anchor_segment_id`**，用于跨 window 去重。

- `grammar_note` 的 dedup key：`grammar_note:though_concession:adverbial_clause`（所有 "though + clause" 让步从句共享）
- `sentence_analysis` 的 dedup key：`sentence_analysis:long_sentence:inversion`（长难句倒装结构）
- 两种 item_type 各自独立 dedup，互不影响

查询：`ledger.published_dedup_keys_by_type[item_type]` 是否包含该 key。

**pattern_key**（密度控制，仅 grammar_note 使用）：
```
pattern_key = grammar_pattern_canonical
```
用于：`published_pattern_keys_by_type['grammar_note']` 密度检查（同一 pattern 在 record 内最多 3 次）。`sentence_analysis` 不使用 pattern_key（其 `published_pattern_keys_by_type['sentence_analysis']` 退化为空数组）。

**写入位置**：
- `semantic_dedup_key` 写入 `layer_analysis_plans.published_dedup_keys_by_type[item_type]`（JSONB array per type）
- `pattern_key` 写入 `layer_analysis_plans.published_pattern_keys_by_type[item_type]`（JSONB array per type，仅 grammar_note 真实使用）
- 部分 provenance 可写入 `enhancement_layers.quality_json`，**不进** `output_json`

### 7.2 处理流程

```
1. 收到当前 window candidates
2. schema / anchor / span validation
   - anchor_segment_id ∈ target_anchor_ids
   - span offsets 在 anchor 范围内
   - spans 必须同一 unit_id（兼容 GrammarNoteItem same-unit invariant）
   - 不合法 -> reject, 写 diagnostics
3. 计算 candidate_identity / semantic_dedup_key(item_type) / pattern_key(item_type)
4. window 内按 candidate_identity 去重（保留第一个）
5. 按排序键排序:
   - quality_score desc
   - reading_blocker true first
   - candidate_type priority (sentence_analysis > grammar_note)
   - anchor_order asc
6. 逐个跑 hard gates（按顺序，第一个 reject 即停止）。
   所有 counters 按 candidate.item_type 查询 ledger 的对应分桶：
   - gate 1 (DUP): semantic_dedup_key(item_type) 已在
                   ledger.published_dedup_keys_by_type[item_type] -> reject
   - gate 2 (PATTERN_DENSE): pattern_key 在
                   ledger.published_pattern_keys_by_type[item_type] 出现 >= 3 次 -> reject
                   （仅 grammar_note 真实生效；sentence_analysis pattern_keys 退化为空）
   - gate 3 (ANCHOR_CAP): anchor 已达 cap
                   ledger.published_anchor_counts_by_type[item_type][anchor_segment_id] >= per_anchor_cap=1
                   -> reject
   - gate 4 (WINDOW_CAP): window 已达 cap
                   window_count_by_type[item_type] >= window_budget[item_type]
                   -> reject
   - gate 5 (RECORD_DENSITY): record density 超阈值
                   ledger.density_by_record[item_type] >= density_cap[item_type] -> reject
   - gate 6 (RECORD_BUDGET): record budget 已满
                   ledger.budget_used[item_type].count >= budget_total[item_type] -> reject
   - gate 7 (ANCHOR_RATIO): annotated_anchor_ratio 超阈值
                   total_annotated_anchors / total_record_anchors > 0.30 -> reject
                   （此 gate 跨 item_type 聚合）
   - gate 8 (MULTI_UNIT_SPAN): candidate spans 跨 unit -> reject
                   （违反 same-unit invariant，防御性 gate）
7. accepted candidates 按 unit_id 分组
8. 对每个 unit 构建 GrammarNoteLayerOutput / SentenceAnalysisLayerOutput
9. 进入 publish 事务
```

### 7.3 Hard Gates 数值

**Per-anchor caps**：grammar_note=1, sentence_analysis=1
**Per-window caps**：grammar_note=2, sentence_analysis=1
**Per-record caps**：
- grammar_note = `min(ceil(content_chars / 1000) * 2, 18)`
- sentence_analysis = `min(max(round(content_chars / 2000), 1), 5)`

**Density caps**（v1 record-level，不真正按 section）：
- grammar_note = 3 / 1000 UTF-16 chars
- sentence_analysis = 1 / 1000 UTF-16 chars

**Annotated anchor ratio**：per_record <= 30%

### 7.4 BBC 6064 chars 预算验证

- grammar_note cap = `min(ceil(6.064) * 2, 18) = 14`
- sentence_analysis cap = `min(max(round(3.032), 1), 5) = 3`
- 当前 60 条 → 降到 grammar <=14 + sentence <=3，降幅约 70%

## 8. Ledger Transaction Model

### 8.1 并发模型

**正确性基础**：PostgreSQL row-level lock（SELECT FOR UPDATE）。
**性能策略**：per-record-per-layer concurrency cap。

限流不是正确性保障。即使并发=1，仍有 retry / lease 恢复 / worker crash 重入。Ledger 必须由 DB 事务兜底。

### 8.2 Window claim / preflight 阶段（running 标记）

Window worker 通过 `claim_next_job` 拿到 `job_type = build_grammar_bundle_window` 的 reader_job 后，必须在 LLM 调用前完成 analysis_windows 的 `pending -> running` 状态迁移。否则 §8.3 步骤 2 的 `window_locked.status != 'running'` 检查会挡住 publish。

```
async def preflight_window_job(conn, *, job_row, lease_token):
    # 全程在外层 conn.transaction() 中
    # 1. 从 reader_jobs.input_json 解析 window_id
    window_id = job_row.input_json['window_id']

    # 2. 锁定 analysis_windows 行
    window_locked = SELECT * FROM analysis_windows
                    WHERE id = window_id FOR UPDATE
    if window_locked is None:
        raise LookupError(f"analysis_window {window_id} not found")

    # 3. 根据 window.status 决定动作
    if window_locked.status == 'pending':
        # 首次执行：标记 running，写 started_at / job_id
        UPDATE analysis_windows SET
            status = 'running',
            started_at = NOW(),
            job_id = $job_row.id,
            updated_at = NOW()
        WHERE id = window_id
        return "proceed"  # 继续 LLM 调用

    elif window_locked.status == 'running':
        # retry 情况：同一 job_id 重跑允许继续
        # （reader_jobs attempt_count < max_attempts，retry_later 后重新 claim）
        if window_locked.job_id != job_row.id:
            raise IllegalStateError(
                f"window {window_id} is running by job {window_locked.job_id}, "
                f"current job is {job_row.id}")
        return "proceed"  # retry 同一 job，继续 LLM 调用

    elif window_locked.status in ('completed', 'no_op', 'failed'):
        # 已终态：不重复执行
        # reader_jobs 也要标记为 skipped / superseded，避免重复 claim
        # （具体策略见 §8.7 失败处理）
        return "already_terminal"

    else:
        raise IllegalStateError(f"unexpected window status: {window_locked.status}")
```

**关键点**：
- `pending -> running` 必须在 LLM 调用前完成，否则 publish 阶段的防重复检查会挡住
- retry 时允许 `running` 状态继续（同一 job_id），不强制重置为 `pending`
- 已终态（`completed / no_op / failed`）的 window 不重复执行，对应的 reader_jobs 也要进入终态
- `started_at` 和 `job_id` 在此阶段写入，retry 时不更新（保留首次执行时间）

### 8.3 LLM 调用阶段（不持锁）

```
Window Job (reader_jobs.status = 'claimed', analysis_windows.status = 'running'):
  - 读取 analysis_window.target_anchor_ids / context_anchor_ids / window_budget
  - 构造 prompt
  - 调用 LLM（不持锁，需 heartbeat 续租，见 §8.5）
  - 收到 candidates，暂存 job output
  - 进入 publish 阶段
```

### 8.4 Publish 阶段事务（遵循现有 publisher pattern）

**核心要求**：publish 事务必须复用现有 `GrammarBundleLayerPublisher._publish_unit_grammar_bundle_inner` 的事务模式（见 [layer_publisher.py:973-996](../../../services/api/app/services/reader_orchestration/layer_publisher.py#L973-L996)），不能直接 `UPDATE reader_jobs SET status='succeeded'`。直接 UPDATE 会漏清 lease 字段、漏 reader_job_events、漏 publish fence 校验。

**关键运行时语义**（避免混淆 `_apply_transition` 与 `transition`）：

- `ReaderJobRuntime.transition()`（[job_runtime.py:447-553](../../../services/api/app/services/reader_orchestration/job_runtime.py#L447-L553)）是 public wrapper：SELECT FOR UPDATE → `_ALLOWED_TRANSITIONS` 校验 → `_assert_lease_valid()` → `_validate_fence()` → 调用 `_apply_transition()` → `_insert_job_event()`。
- `ReaderJobRuntime._apply_transition()`（[job_runtime.py:753-790](../../../services/api/app/services/reader_orchestration/job_runtime.py#L753-L790)）是 private：**只做状态字段更新 + lease 字段清理**（清 `lease_owner / lease_token / lease_expires_at / claimed_at`），**不校验 lease/fence，不重置 attempt_count**。
- `_assert_lease_valid()`（[job_runtime.py:918-932](../../../services/api/app/services/reader_orchestration/job_runtime.py#L918-L932)）是 **job_runtime 模块级同步函数**，不是 `ReaderJobRuntime` 实例方法，**不需要 await**。现有 publisher 通过 `from .job_runtime import _assert_lease_valid` 直接调用（见 [layer_publisher.py:33-37](../../../services/api/app/services/reader_orchestration/layer_publisher.py#L33-L37)）。window publisher 必须采用相同 import 方式。
- `_validate_fence()` 是 `ReaderJobRuntime` 实例方法（async，需 await），通过 `self._job_runtime._validate_fence(conn, job_locked)` 调用。
- Window publisher 必须在同一事务里操作 ledger + layers + job，**不能直接调用外层 `transition()`**（它会自己开事务并写 job_event，与 publish 事务分离）。因此 window publisher 要手动复刻 `transition()` 的校验流程，然后调用 `_apply_transition()` 完成 job 字段更新。

#### publish_window_grammar_bundle 事务流程

```
async def publish_window_grammar_bundle(conn, *, job_id, plan_id, window_id,
                                         candidates, lease_token):
    # 全程在外层 conn.transaction() 中（由 worker 调用方开启）

    # 1. 锁定 plan ledger（FOR UPDATE）
    plan_locked = SELECT * FROM layer_analysis_plans
                  WHERE id = plan_id FOR UPDATE

    # 2. 锁定 window（FOR UPDATE，防重复 publish）
    window_locked = SELECT * FROM analysis_windows
                    WHERE id = window_id FOR UPDATE
    if window_locked.status != 'running':
        return  # 防重复 publish

    # 3. 锁定 reader_jobs 行（FOR UPDATE，沿用 publisher 模式）
    job_locked = SELECT * FROM reader_jobs WHERE id = job_id FOR UPDATE

    # 4. 手动复刻 transition() 的校验流程（必须在 _apply_transition 之前完成）
    #    4a. status 校验
    if job_locked['status'] != 'claimed':
        raise IllegalTransitionError(
            f"expected status='claimed', got {job_locked['status']!r}")

    #    4b. job_type / target_type / fingerprint 校验（防误 publish 其他 job）
    if job_locked['job_type'] != 'build_grammar_bundle_window':
        raise IllegalTransitionError("job_type mismatch")
    if job_locked['target_type'] != 'unit_range':
        raise IllegalTransitionError("target_type mismatch")
    if job_locked['operation_fingerprint'] != 'grammar_bundle_window_v1':
        raise IllegalTransitionError("operation_fingerprint mismatch")

    #    4c. lease_token 校验（防被其他 worker 抢占）
    #        _assert_lease_valid 是 job_runtime 模块级同步函数，不 await
    #        import: from .job_runtime import _assert_lease_valid
    #        不通过会抛 LeaseTokenMismatchError / LeaseExpiredError
    _assert_lease_valid(job_locked, job_id, lease_token)

    #    4d. publish fence 校验（generation / active base / base_id presence）
    #        调用 runtime._validate_fence(conn, job_locked)
    #        不通过会抛 FenceViolationError
    fence_error = await self._job_runtime._validate_fence(conn, job_locked)
    if fence_error is not None:
        raise FenceViolationError(f"publish fence failed: {fence_error}")

    # 5. Application-level selector:
    #    - schema/anchor/span validation
    #    - 计算 semantic_dedup_key / pattern_key（按 item_type 拆分，见 §7.1）
    #    - 排序 candidates
    #    - 跑 hard gates（读 plan_locked 的 typed counters）
    #    - accepted candidates 按 unit_id 分组
    #    - 构建 GrammarNoteLayerOutput / SentenceAnalysisLayerOutput

    # 6. INSERT accepted layers（per-unit, target_scope='unit'）
    #    复用 _insert_published_grammar_layer / _insert_published_sentence_layer
    for unit_id, grammar_layer in accepted_grammar_by_unit:
        INSERT INTO enhancement_layers (..., target_scope='unit', target_key=unit_id,
                                         quality_json={plan_id, window_id, window_index,
                                                        semantic_dedup_key, pattern_key}, ...)
    for unit_id, sentence_layer in accepted_sentence_by_unit:
        INSERT INTO enhancement_layers (...)  # 同理

    # 7. 更新 ledger（JSONB 完整覆盖，typed counters，见 §4.1 / §7.1）
    UPDATE layer_analysis_plans SET
        budget_used = $new_budget_used_jsonb,
        published_anchor_counts_by_type = $new_anchor_counts_jsonb,
        published_dedup_keys_by_type = $new_dedup_keys_jsonb,
        published_pattern_keys_by_type = $new_pattern_keys_jsonb,
        density_by_record = $new_density_by_record_jsonb,
        covered_window_ids = $new_covered_window_ids_jsonb,
        updated_at = NOW()
    WHERE id = plan_id

    # 8. 更新 window status + coverage
    UPDATE analysis_windows SET
        status = CASE WHEN $accepted_count = 0 THEN 'no_op' ELSE 'completed' END,
        coverage = $coverage_jsonb,
        completed_at = NOW()
    WHERE id = window_id

    # 9. 通过 job_runtime 完成 reader_jobs 字段更新（沿用 publisher 现有模式）
    #    _apply_transition 只做状态字段更新 + lease 字段清理，不校验 lease/fence
    #    lease/fence 校验已在步骤 4c/4d 完成
    updated_job = await self._job_runtime._apply_transition(
        conn,
        job_row=job_locked,
        target_status='succeeded',
        available_at=None,
        pause_owner=None,
        output_ref=output_ref,           # {grammar_note_layer_ids, sentence_analysis_layer_ids, ...}
        failure_class=None,
        failure_code=None,
        failure_message=None,
        rationale_code='grammar_bundle_window_published' if accepted_count > 0
                       else 'grammar_bundle_window_no_op',
    )
    # _apply_transition 自动清理：lease_owner / lease_token / lease_expires_at / claimed_at
    # _apply_transition 不重置 attempt_count（attempt counters 跨 retry 累积，由 retry_later 路径使用）

    # 10. 写 reader_job_events（沿用 publisher 现有模式，与 _apply_transition 在同一事务）
    await self._job_runtime._insert_job_event(
        conn,
        reading_record_id=updated_job['reading_record_id'],
        run_id=updated_job['run_id'],
        job_id=updated_job['id'],
        event_type='job_succeeded',
        payload={
            'previous_status': 'claimed',
            'target_status': 'succeeded',
            'rationale_code': rationale_code,
        },
    )

    # 11. 更新 reader_runs（沿用 publisher 现有模式）
    UPDATE reader_runs SET status='completed', finished_at=NOW(), updated_at=NOW()
    WHERE id = updated_job['run_id']
```

**关键约束**：
- 步骤 4（手动校验）**必须**在步骤 9（`_apply_transition`）之前完成。不能省略，也不能依赖 `_apply_transition` 来做这些校验。
- 步骤 4c `_assert_lease_valid`（模块级同步函数，`from .job_runtime import _assert_lease_valid`，**不 await**）：防被其他 worker 抢占
- 步骤 4d `_validate_fence`（`ReaderJobRuntime` 实例方法，需 await）：校验 generation / active base / base_id presence
- 步骤 9 `_apply_transition`（`ReaderJobRuntime` 实例方法，需 await）：只更新状态字段 + 清 lease 字段（`lease_owner / lease_token / lease_expires_at / claimed_at`），写 `output_ref / rationale_code`，**不校验 lease/fence，不重置 `attempt_count`**
- 步骤 10 必须写 `reader_job_events`，否则 traceability 缺失
- ledger 字段是 JSONB，更新时用完整 JSONB 值覆盖（`$new_*_jsonb`），**不用** `array_append`
- `enhancement_layers.target_scope = 'unit'`，`target_key = unit_id`，与现有 contract 一致
- `quality_json` 存储 provenance（plan_id / window_id / window_index / dedup_key / pattern_key），**不进** `output_json`

**为什么不能直接调用外层 `transition()`**：
- `transition()` 内部会自己开 `conn.transaction()` 并写 `_insert_job_event`
- Window publisher 必须在同一事务内完成 ledger + layers + job 三件事
- 如果调用 `transition()`，job 状态变化与 ledger/layers 不在同一事务，原子性破坏

**与现有 publisher 模式的对齐**：现有 `GrammarBundleLayerPublisher._publish_unit_grammar_bundle_inner`（[layer_publisher.py:973-996](../../../services/api/app/services/reader_orchestration/layer_publisher.py#L973-L996)）正是手动调用 `_apply_transition` + `_insert_job_event` + `UPDATE reader_runs`，window publisher 完全沿用此模式，只是把单 unit publish 换成多 unit publish + ledger update。

#### 不复用 reader_jobs `ready_to_publish` 状态

reader_jobs 没有 `ready_to_publish` status（见 §3.2 真实 status enum）。Window LLM 调用完成后直接在同事务内 publish，reader_jobs 直接从 `claimed` → `succeeded`，不经过中间状态。LLM 调用结果暂存在 worker 内存（不持久化），retry 时重新调用 LLM。

### 8.5 锁覆盖范围

- **锁覆盖**：selector + publish + ledger update + window status + reader_jobs status
- **锁不覆盖**：LLM 调用、prompt 构造、candidates 暂存
- **锁粒度**：per-record-per-layer（grammar ledger 与 vocabulary ledger 不互相阻塞）
- **锁时长**：仅 publish 阶段，毫秒级

### 8.6 Lease Heartbeat 要求（新增）

当前 lease 默认 120s（[worker_loop.py:38](../../../services/api/app/services/reader_orchestration/worker_loop.py#L38) `DEFAULT_READER_WORKER_LEASE_DURATION`），且 worker 在 LLM 调用期间**不自动续租**。Window LLM 调用比 per-unit 更长（多 unit 文本），必须显式 heartbeat，否则 `recover_stale_leases`（[job_runtime.py:559-641](../../../services/api/app/services/reader_orchestration/job_runtime.py#L559-L641)）会回收正在执行的 window job，导致重复 publish 风险。

**要求**：
- Window worker 在 LLM 调用期间，每约 30s 调用一次 `heartbeat`（[job_runtime.py:383-441](../../../services/api/app/services/reader_orchestration/job_runtime.py#L383-L441)）
- 或 lease duration 设置明显大于 worst-case window call（如 300s），但仍推荐 heartbeat
- 实现方式：在 `grammar_window_worker.py` 中包装 LLM call，启动 asyncio task 周期性 heartbeat，LLM 完成后取消

### 8.7 失败处理

- LLM 调用失败：reader_jobs.status → `retry_later`（如 attempt_count < max_attempts）或 `failed_terminal`。analysis_windows.status 保持 `running`，retry 时重新 claim 同一 job。
- Publish 事务失败：事务回滚，ledger 不变，window 状态不变。reader_jobs.status → `retry_later`。
- Retry 次数耗尽：reader_jobs.status → `failed_terminal`，analysis_windows.status → `failed`，plan.status → `completed_with_failures`。

## 9. Worker Migration Path

### 9.1 删除的 per-unit active path

- `grammar_worker.py` 中 per-unit 逻辑：`_load_job_context`（按单 unit 加载）、`_build_grammar_prompt`（单 unit prompt）、`_build_grammar_output_from_candidates`（per-unit 输出）
- `job_bootstrap.py` 中 `_bootstrap_grammar_jobs` 的 per-unit 入队逻辑

### 9.2 可复用部分迁入 window worker

- LLM 调用封装（API client / retry / streaming）
- Schema validation（Pydantic models）
- Span grounding validation（anchor_segment_id / offset 校验）
- Layer publisher 调用（新增 `publish_window_grammar_bundle` method）
- Usage attribution（ai_usage_events）
- Diagnostics 写入

### 9.3 新增文件结构

```
services/api/app/services/reader_orchestration/
  grammar_window_worker.py      # 新增：window-level grammar worker（含 heartbeat）
  analysis_window_planner.py   # 新增：deterministic window 切分
  online_selector.py            # 新增：selector + hard gates
  plan_ledger.py                 # 新增：ledger 读写封装
  grammar_worker.py              # 删除 per-unit 逻辑，保留可复用 helper 或整体替换
  layer_publisher.py             # 扩展：新增 publish_window_grammar_bundle
  job_bootstrap.py               # 修改：_bootstrap_grammar_jobs 改为创建 plan + window jobs
```

### 9.4 测试迁移

- 删除 per-unit behavior 测试
- 改写为 Z+ window behavior 测试：
  - Window formation 测试（4 类边界、target/context 分离、heading context 不覆盖）
  - Selector hard gates 测试（每个 gate 单独测 + dedup_key 两层）
  - Ledger 事务测试（JSONB 更新、并发、retry、crash recovery）
  - Prompt schema 测试（target/context 标记、self-rating 输出不进 output_json）
  - Publisher 测试（unit-scoped multi-layer publish、quality_json provenance）
  - Lease heartbeat 测试
  - End-to-end：BBC regression

### 9.5 不在删除范围

- `services/api/app/services/analysis/` 旧 AI Workflow 代码
- `services/api/app/services/reader_orchestration/` 其他 worker（translation / vocabulary）

## 10. Test / Eval Cases

### 10.1 BBC Regression Fixture

**要求**：创建 BBC 文章 fixture（input text + expected stable base），存放在测试 fixtures 目录。不能只口头引用 `cd6684a0`。

**Fixture 内容**：
- BBC 新闻原文（"Today is the start of it all"，6064 chars）
- 期望 stable base：37 个 unit / 46 个 anchor
- 验收只针对 grammar_bundle Z+（不暗示 translation/vocabulary 已 window 化）

**验收指标**：
- grammar_bundle LLM calls 从 37 降到 3-5 次
- grammar_note 数量 <= 14 条
- sentence_analysis 数量 <= 3 条
- 跨 window 近重复率 <= 10%
- 带 grammar_note 的 anchor 占比 <= 30%
- 首批输出时间：第一个 window 完成即 publish（< 10s 级别）

### 10.2 Window Formation 测试

| 用例 | 输入 | 期望 |
|---|---|---|
| 全 paragraph | 37 个 paragraph block | 3-5 个 window，每个 1000-1500 chars |
| 含 heading | heading + paragraph | heading 进入下一 window context，不作为 target |
| 含 blockquote | paragraph + blockquote + paragraph | 3 个 window，blockquote 独立 |
| 含 code_block | paragraph + code_block + paragraph | 2 个 window，code_block skip |
| 超长段 | 1 个 3000+ chars paragraph | 按 anchor 拆分到多个 window |
| heading context 累积 | heading1 + heading2 + paragraph | 两个 heading 都进入下一 window context |

### 10.3 Selector Hard Gate 测试

| Gate | 测试场景 | 期望 |
|---|---|---|
| semantic_dedup_key | 两个 window 的 candidate 共享 dedup_key | 后到的 reject |
| pattern_key | 同一 grammar_pattern 在 record 内出现 3 次 | 第 3 次 reject |
| anchor cap | 同一 anchor 已有 1 grammar_note | 第 2 个 reject |
| window cap | window 已有 2 grammar_note | 第 3 个 reject |
| record budget | record 已有 14 grammar_note | 第 15 个 reject |
| anchor ratio | 30% anchor 已有批注 | 第 31% reject |
| multi-unit span | candidate spans 跨 unit | reject（违反 same-unit invariant） |

### 10.4 Ledger 并发测试

| 用例 | 场景 | 期望 |
|---|---|---|
| 串行 publish | window 0 → publish → window 1 → publish | ledger JSONB 正确累加 |
| 并发 publish | window 0 和 1 同时 ready | 后到的等待 row lock，最终两个都成功 |
| retry 后 publish | window 0 publish 失败 → retry → publish | ledger 不重复，window 状态正确 |
| crash recovery | window 0 publish 中 worker crash → recover_stale_leases | 不重复 publish |

## 11. 范围与约束

### 11.1 v1 Scope（收窄）

**包含**：
- Analysis Window planner（deterministic 切分，基于 `AnalysisAnchorView`）
- `layer_analysis_plans` + `analysis_windows` 表
- Online selector + plan ledger
- Window grammar worker（含 heartbeat）
- Publisher unit-scoped multi-layer publish（`publish_window_grammar_bundle`）
- grammar_bundle prompt 改造
- 删除 per-unit grammar worker active path
- BBC regression fixture + 测试

**不包含（移到 future）**：
- vocabulary Z+ 化（future v1.1）
- translation Z+ 化（future v1.2）
- semantic_outline（long-term）
- candidate 持久化表（v2）
- 跨 window 全局 selector（v2）
- section-level density（v1 退化为 record-level）
- Base Builder v2（hard_min/target_max/safety_max）—— **不与 Z+ 强绑定**，可作为独立前置优化（v0.5），Z+ 设计基于现有 Stable Base 工作

### 11.2 约束

- **不修改** `apps/web/**`
- **不修改** `services/api/app/services/analysis/`
- **不修改** legacy `/app/reader/{recordId}` path
- **允许修改** `services/api/prompts/agents/reader_layer_grammar_bundle.yaml`
- **允许修改** `services/api/app/services/reader_orchestration/`（删除 per-unit + 新增 window-based）
- **允许修改** `services/api/app/schemas/reader_orchestration.py`（新增 job_type / window status enum）
- **允许新增** `infra/migrations/` schema migration
- **允许修改** `infra/scripts/reset_dev_keep_dict.sql`（新增表加入 TRUNCATE）
- **不 stage / commit** 任何变更

### 11.3 数据迁移

开发阶段，本地数据库可随时重置（[reset_dev_keep_dict.sql](../../../infra/scripts/reset_dev_keep_dict.sql)）。新增表加入 TRUNCATE 列表即可。无需兼容旧 record。

## 12. 验证标准

### 12.1 功能验证

- [ ] BBC fixture 解析后 grammar_note <= 14，sentence_analysis <= 3
- [ ] grammar_bundle LLM 调用从 37 降到 3-5 次（仅 grammar_bundle，translation/vocabulary 仍 per-unit）
- [ ] 跨 window 近重复率 <= 10%
- [ ] 带 grammar_note 的 anchor 占比 <= 30%
- [ ] 渐进式输出保留（window 完成一个，publish 一个）
- [ ] 前端 layer 渲染正常（grammar_note / sentence_analysis 仍按 anchor 投影，output_json schema 不变）

### 12.2 架构验证

- [ ] `layer_analysis_plans` / `analysis_windows` 表正确创建
- [ ] `reader_jobs.job_type` 新增 `build_grammar_bundle_window`
- [ ] `_LAYER_NAME_BY_JOB_TYPE` 包含新映射
- [ ] Ledger 事务正确串行化（JSONB 更新，无 race condition）
- [ ] Window worker 在 LLM 调用期间 heartbeat 续租
- [ ] Retry 复用同一 reader_jobs.id 和 analysis_windows.id

### 12.3 代码质量验证

- [ ] per-unit grammar worker active path 已删除
- [ ] per-unit 测试已删除或改写
- [ ] 可复用部分已迁入 window worker
- [ ] 无新增 dead code
- [ ] `output_json` schema 未变（`GrammarNoteItem` / `SentenceAnalysisItem` 无新字段）

## 13. 未来演进路径

- **v0.5（可选前置）**：Base Builder v2（hard_min/target_max/safety_max），与 Z+ 解耦
- **v1.1**：vocabulary Z+ 化（lexical online selector）
- **v1.2**：translation Z+ 化（coverage ledger）
- **v2**：AnnotationCandidate 持久化表、跨 window 全局 selector（Y-lite）、section-level density
- **v3**：全篇 batch selector（Y）、semantic_outline

## 14. Open Implementation Decisions

以下仅列**真正还没定**的工程选择，已确认的 contract 不在此列。

1. **`analysis_windows.job_id` 是否做 FK**：FK 更强一致但 migration 复杂；软引用（不做 FK）与现有 `enhancement_layers.source_job_id` 一致。倾向软引用。

2. **Heartbeat 实现方式**：asyncio task 周期性调用 vs 在 LLM call wrapper 中插入 checkpoint。倾向前者（更通用）。

3. **GrammarNoteLayerOutput min_length=1 与 no-op unit 的处理**：如果一个 unit 在 window 中 0 个 candidate accepted，不发布该 unit 的 grammar_note layer。这是否需要 bootstrap 逻辑调整（从"有 published layer 即 skip"改为"window coverage 即 skip"）？倾向需要调整 bootstrap。

4. **`enhancement_layers.quality_json` 的 provenance schema**：是标准化（所有 layer 都写 plan_id/window_id/window_index）还是 layer-specific？倾向标准化。

5. **Oversized unit 的 budget 自适应算法**：单 unit 超 `safety_max` 时，window_budget 应按 unit 内 anchor 数等比例放大，还是固定为 max budget？倾向按 anchor 数等比例放大，但放大系数需 eval 校准。

## 15. 参考资料

- [CONTEXT.md](../../../CONTEXT.md) — Z+ grilling 决策完整记录
- [concepts.md](./concepts.md) — Reading Unit / Anchor Segment 基础概念
- [target-architecture.md](./target-architecture.md) — agentic orchestration 目标架构
- [reading-base-and-units.md](./modules/reading-base-and-units.md) — Reading Base 与 Unit 设计
- [enhancement-layers-and-parsed.md](./modules/enhancement-layers-and-parsed.md) — Enhancement Layer 设计
- [infra/scripts/reset_dev_keep_dict.sql](../../../infra/scripts/reset_dev_keep_dict.sql) — 开发库重置规范
- [infra/migrations/0001_initial_schema.sql](../../../infra/migrations/0001_initial_schema.sql) — reader_jobs / enhancement_layers schema
- [infra/migrations/0004_reader_document_blocks.sql](../../../infra/migrations/0004_reader_document_blocks.sql) — stable_document_blocks schema
- [services/api/app/schemas/reader_orchestration.py](../../../services/api/app/schemas/reader_orchestration.py) — Pydantic output contract
- [services/api/app/services/reader_orchestration/job_bootstrap.py](../../../services/api/app/services/reader_orchestration/job_bootstrap.py) — _LAYER_NAME_BY_JOB_TYPE
- [services/api/app/services/reader_orchestration/job_runtime.py](../../../services/api/app/services/reader_orchestration/job_runtime.py) — lease / recover_stale_leases / heartbeat
- [services/api/app/services/reader_orchestration/layer_publisher.py](../../../services/api/app/services/reader_orchestration/layer_publisher.py) — publish_unit_grammar_bundle
