# Grammar Candidate Selection 合同

> change-id: `reader-grammar-candidate-selection`
> 状态：开发阶段 **breaking contract**。不处理迁移、Eval、RAG、few-shot。
> 事实来源：当前 `grammar_candidate_policy.py` / `window_selector.py` /
> `grammar_window_publisher.py` 生产代码与对应测试。

## Candidate 必填三字段

`window_selector.CandidateItem` 必填且 `__post_init__` 真实校验：

- `quality_score: int`，范围 `1..5`；拒绝 `bool`（`type(x) is not int`）与 `float`
- `reading_blocker: bool`；拒绝非 `bool` 类型
- `dedup_hint: str`；经 `validate_dedup_hint` trim + normalize 后非空、
  ≤120 字符，并写回 normalized 值

四路径（per-unit / batch / window worker / window selector）的 Pydantic
schema 与 `CandidateItem` 构造点 MUST 显式提供这三字段。

## 三路径共享排序

per-unit / batch / window 三路径 MUST 调用
`grammar_candidate_policy.grammar_candidate_sort_key`，排序键：

```
(-quality_score, 0 if reading_blocker else 1, 0 if grammar_note else 1)
```

即 `quality_score` 降序 → `reading_blocker=true` 优先 → 同分时
`grammar_note` 优先于 `sentence_analysis`。

## Dedup identity

去重身份为二元组：

```
(anchor_segment_id, normalize_dedup_hint(dedup_hint))
```

- 同 anchor + 同 hint：跨 `grammar_note` / `sentence_analysis` 只保留一个
  （winner 由 sort order 决定）
- 不同 anchor + 同 hint：**不**触发 DUP，可继续进入后续 gates
- 全文重复控制由 `PATTERN_DENSE` / `ANCHOR_CAP` / `RECORD_DENSITY` /
  `RECORD_BUDGET` 负责

`scoped_dedup_key` 内部调用 `validate_dedup_hint`，非法 hint 抛
`ValueError`（fail-closed），不静默返回半合法元组。

## DUP diagnostic

DUP rejection 携带结构化字段，与人类可读的 `reason` 分离：

- `RejectedCandidate.reason_code: str | None`：独立结构化字段。DUP gate
  设置为 `DEDUP_HINT_DUPLICATE_REASON_CODE`（`"dedup_hint_duplicate"`）；
  其他 gate 为 `None`。`reason` 仅保留人类可读详情，不再承担 code 合同。
- `RejectedCandidate.dedup_metadata: DedupRejectionMetadata | None`：
  仅 DUP gate 填充，包含：
  - `normalized_hint: str`
  - `winner_item_type: str`
  - `winner_anchor_segment_id: str`
  - `winner_item_index: int | None`（current_window winner 为真实 index；
    published_ledger winner 为 `None`，不得伪造）
  - `winner_source: Literal["current_window", "published_ledger"]`

Publisher 的 `_aggregate_rejected` 直接从 `dedup_metadata` 读取结构化字段
并输出独立 `reason_code`，MUST NOT 从 `reason` 字符串解析。
`rejected_breakdown` 每条 entry MUST 包含 `reason_code`：DUP 为
`"dedup_hint_duplicate"`，其他 gate 为 `None`。

## Window ledger 只接受当前 scoped key

`grammar_window_publisher._load_ledger_from_plan` 只接受二元素 scoped key：

```
[anchor_segment_id, normalized_dedup_hint]
```

严格 canonical-content 校验：

- entry 必须是长度为 2 的 `list` 或 `tuple`
- `anchor_segment_id` 必须是 `str`，且 `strip()` 后非空
- `hint` 必须是 `str`，且必须通过 `validate_dedup_hint`（非空、≤120）
- 存储的 `hint` 必须已经等于 `validate_dedup_hint(hint)` 的返回值
  （即必须是已 normalized 的形式）
- 非法内容抛 `ValueError`，不兼容旧格式、不修复 normalization、不静默跳过

### Ledger 场景

以下场景 MUST 抛 `ValueError`：

- 非法长度 / 形状：`"a-string"` / `{"anchor": "a1"}` / `[1, 2, 3]` / `null`
  / 长度 ≠ 2
- 非 str anchor：`[123, "hint"]` / `[null, "hint"]`
- 空白 anchor：`["", "hint"]` / `["   ", "hint"]`
- 非 str hint：`["a1", 123]` / `["a1", null]` / `["a1", ["hint"]]`
- 空 hint：`["a1", ""]`
- 纯空白 hint：`["a1", "   "]`
- 超长 hint：hint 长度 > 120
- 未规范化 hint：`["a1", "  Foo   BAR  "]`（不等于 `"foo bar"`）

合法 canonical scoped key 正常加载：
`["a1", "though_concession:adverbial_clause"]` →
`("a1", "though_concession:adverbial_clause")`，不抛异常。

## 不在范围

- 不处理数据库迁移、Eval、Article RAG、few-shot
- 不修改教学 prompt 主体
- 不调用真实 LLM、不重置数据库、不 commit
