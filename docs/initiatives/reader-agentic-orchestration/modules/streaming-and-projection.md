# Streaming 与 Projection

> 状态：`D5 主链路使用 snapshot reload；projection_ops 增量 applier 仍 D5+ 后置；T4.2a-PUX-R2 已闭合 runtime integration gate`
> 最后更新：2026-07-13（DOC-R2：本文成为 `projection_ops` envelope、sequence contract、gap detection、polling cursor 与 snapshot reload fallback 的权威归宿；同步 T4.2a-PUX-R1/R2 状态）
> 范围：Reader Events、snapshot、SSE、polling fallback、Plate projection operations 和刷新恢复。

## Owner 归属

本文是以下事实的唯一权威归宿（其他文档引用时只写简短约束 + 链接）：

- `reader_events` envelope 结构与字段语义
- `reader_event_sequences` 分配规则（事务内分配、rollback no-gap、record-scoped）
- `projection_ops` payload envelope JSON 示例
- `op_type` 概念列表与说明
- Sequence contract、gap detection、polling cursor 规则
- Snapshot 策略（实时聚合 vs `reader_snapshots` cache）
- Plate Recovery 流程与 reload fallback 触发条件

Plate 侧 op_type 语义（Target / Owner / Use 列）、Projection Applier 行为、Owner 权限表归 [`plate-reader-projection.md`](./plate-reader-projection.md)；本文不复制这些表。

## 目标

Reader 渐进渲染必须来自持久业务状态和持久 events。

禁止：

- 用 LLM token stream 直接替换 Reader body。
- 用 worker 内存消息作为唯一实时来源。
- 把 worker diagnostics 混入 UI event stream。
- 把 raw Plate path / raw Slate path operation 持久化为后端 API contract。

## Event 分层

| 类型 | 表 | 用途 | 是否进入 SSE |
|---|---|---|---|
| Reader Event | `reader_events` | UI domain events、snapshot catch-up、polling fallback | 是 |
| Projection Event | `reader_events` | Web Plate projection operations、snapshot rebuild signal | 是，仅 Web Reader 消费 |
| Reader Job Event | `reader_job_events` 或 debug log | claim、heartbeat、attempt、requeue、diagnostics | 否 |

`reader_events` 只记录用户界面需要理解的领域事件。

Projection Event 是 Reader Event 的子类。它不表达新的业务事实，只表达 Web Reader Article Body 如何从当前 domain facts 增量更新。

## Event Envelope

每条 `reader_events` 至少包含：

```json
{
  "id": "evt_...",
  "reading_record_id": "rec_...",
  "sequence": 123,
  "event_type": "layer_published",
  "payload": {},
  "created_at": "2026-06-18T00:00:00Z"
}
```

规则：

- `id` 是全局唯一事件 id，用于客户端去重。
- `sequence` 在单个 Reading Record 内对 committed UI events 单调连续，从 `1` 开始。
- event 与业务发布同事务写入。
- SSE 与 polling 返回相同 envelope。
- 投递语义是 at-least-once，客户端必须按 `sequence` 和 `id` 去重。

如果使用数据库 sequence 会产生 rollback gap，不能直接作为 UI catch-up sequence。D3/D4 使用 record-scoped transactional counter 或等价方案，例如 `reader_event_sequences(reading_record_id, next_sequence)` 在同一事务中分配 sequence；事务回滚时 counter 也回滚。

## Sequence Contract

`reader_event_sequences` 是推荐实现：

```sql
CREATE TABLE reader_event_sequences (
  reading_record_id UUID PRIMARY KEY REFERENCES reading_records(id) ON DELETE CASCADE,
  next_sequence BIGINT NOT NULL DEFAULT 1,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

分配规则：

- 第一个 committed event sequence 是 `1`。
- 分配 counter 和插入 `reader_events` 必须在同一事务。
- 事务 rollback 不推进 sequence。
- 不同 Reading Record 的 sequence 独立。
- 同一 record 高并发 publish 由 row-level lock 串行化。

实现可以使用预创建 row + `UPDATE ... RETURNING`，或等价的 `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`，但必须用 focused test 覆盖 first sequence、rollback no-gap 和 concurrent publish。

## Reader Event Types

- `input_received`
- `extraction_progressed`
- `candidate_base_ready`
- `article_ready`
- `substrate_progressed`
- `substrate_ready`
- `layer_started`
- `layer_published`
- `layer_failed`
- `parsed_decision_updated`
- `projection_ops`
- `projection_reset_required`
- `user_editorial_asset_changed`
- `ask_supplement_published`
- `ask_supplement_deleted`
- `run_paused`
- `action_required`
- `run_completed`
- `record_superseded`

`layer_started` 只用于用户可见的局部 pending，不用于 worker heartbeat。

`projection_ops` 只用于 Web Plate projection。非 Web 客户端可以忽略它，并继续通过 snapshot / polling 获得最新可读状态。

## Projection Operation Envelope

`projection_ops` payload 使用稳定 domain target。

示例：

```json
{
  "base_id": "base_...",
  "projection_version": 12,
  "ops": [
    {
      "op_id": "op_...",
      "op_type": "add_ai_mark",
      "target": {
        "unit_id": "u1",
        "anchor_segment_id": "s3",
        "layer_id": "layer_..."
      },
      "owner": "system_ai",
      "fragment": {
        "format": "plate_fragment",
        "schema_version": 1,
        "content": []
      }
    }
  ],
  "source_event_id": "evt_...",
  "source_layer_id": "layer_..."
}
```

Rules:

- `op_id` is stable for idempotent replay.
- `target` must use `unit_id`, `anchor_segment_id`, `layer_id`, `asset_id`, or `supplement_id`.
- `fragment` must be sanitized and schema-allowlisted.
- No raw Plate path or raw Slate path operation is durable.
- If the frontend cannot resolve target to current Plate path, it reloads snapshot.

Allowed `op_type` seed list:

| op_type | 说明 |
|---|---|
| `upsert_translation_node` | 插入或替换 unit / Anchor Segment 的译文投影 |
| `add_ai_mark` | 添加 vocabulary / grammar 等 AI inline mark |
| `upsert_ai_note_node` | 插入 grammar note 或 sentence analysis block |
| `upsert_ask_supplement_node` | 插入用户确认或预授权的 Ask Supplement |
| `upsert_user_highlight` | 插入或更新用户高亮投影 |
| `upsert_user_note` | 插入或更新用户笔记投影 |
| `remove_projection_node` | 移除 hidden / dismissed / deleted item 的投影，不删除 system AI truth |

## 恢复流程

```text
GET /reader/records/{id}
-> receive snapshot/projection + Base Plate Snapshot + last_event_sequence
-> subscribe /reader/records/{id}/stream after last_event_sequence
-> if SSE unavailable, poll /reader/records/{id}/events?after=
-> if gap detected, reload snapshot
```

Gap 定义：

```text
next_event.sequence != last_seen_sequence + 1
```

触发 gap 后，客户端丢弃未确认局部 projection，重新拉 snapshot。

Polling cursor 规则：

- `GET /reader/records/{id}/events?after=N` 返回 `sequence > N` 的 events。
- response 可以包含 server 当前 `last_event_sequence` 作为观测值。
- 客户端 cursor 只能前进到最后一个已经成功处理的 event sequence。
- 如果 response 被 `limit` 截断，客户端不得直接把 cursor 跳到 server `last_event_sequence`，否则会跳过未处理事件。
- 如果 `after=N` 等于 server 当前 `last_event_sequence`，返回空 events，不要求 reload。
- 如果当前没有 committed events 且 `after=0`，返回空 events，不要求 reload。
- 如果 `after=N` 大于 server 当前 `last_event_sequence`，返回空 events，并保持 `next_after_sequence = N`。客户端如果持续观察到该状态，应 reload snapshot，而不是猜测 sequence。

## Snapshot 策略

D1 不强制 snapshot 必须落表。

允许两种实现：

1. `GET /reader/records/{id}` 从业务表实时聚合 projection。
2. `reader_snapshots` 保存压缩 projection，并可从业务表重建。

D2-S5 结论：D4 默认使用实时聚合。`reader_snapshots` cache、write-through rebuild、PG LISTEN/NOTIFY fan-out 和 event TTL 都后置到 D5+，除非 D4 focused perf test 证明实时聚合不可用。

无论是否落表，snapshot 必须等价于：

```text
reading_record product state
+ reading_record shell metadata (title, created_at, source_type, source_metadata)
+ stable base metadata
+ reading_units
+ anchor_segments
+ stable interaction anchor descriptors (unit text hash, anchor text hash, UTF-16 offsets)
+ base_plate_snapshot
+ latest published enhancement_layers per unit/layer_type
+ ask_supplements
+ user_editorial_assets
+ parsed_decisions
+ rag_substrate status
+ available actions
+ last_event_sequence
```

Snapshot is rebuilt from domain facts. It is not persisted Plate editor state unless a future spike proves a cached projection is needed and rebuild equivalence is tested.

D3-P3 snapshot reload uses a read-only `repeatable_read` transaction so `last_event_sequence` and all snapshot facts come from the same consistent view. Future cached projection or materialized read-model implementations must preserve the same rebuild-equivalence and cursor semantics.

D4 snapshot wrapper 使用 `schema_kind = "reader_plate_snapshot"` 和 `last_event_sequence`。D4 不在 snapshot 上暴露 `projection_version`；`last_event_sequence` 是唯一恢复 cursor。`projection_ops.payload.projection_version` 如在 D5 启用，只能用于 projection cache/applier 内部一致性，不替代 Reader Event sequence。

W3-C2-BE 进一步固定：snapshot reload 仍是 `record` 元数据、`product_state`、`navigation`、`anchor_segments` 和已发布 layer ownership/targeting 的唯一 truth reload path。即使未来启用 `projection_ops`，它也只能增量更新前端投影，不得单独定义或修补这些顶层事实。

## Plate Recovery

Web Reader recovery:

```text
load snapshot
-> set Base Plate Snapshot / latest projected document
-> apply missed projection_ops in sequence
-> dedupe by event id and op_id
-> reload snapshot on sequence gap, unresolved target, policy failure, or anchor hash mismatch
```

Projection ops are an optimization for progressive rendering, not a source of truth.

D4 不要求 `projection_ops` 端到端。D4 translation layer 可以先通过 snapshot reload 或 simple projection refresh 呈现。D3 仍可定义 event type、DTO 和 projection emitter skeleton，D5 再启用前端增量 applier。

Snapshot reload fallback is mandatory when:

- `next_event.sequence != last_seen_sequence + 1`
- op `target` cannot resolve to a current `unit_id` / `anchor_segment_id` / artifact node
- anchor text hash mismatches current Stable Base projection
- owner policy rejects the requested projection
- frontend detects duplicate-but-conflicting `op_id`
- backend emits `projection_reset_required`

### T4.2a-PUX-R2 runtime integration

`reader-record` 页面 `reloadSnapshot` 已接入 progressive transition 校验后才应用 snapshot（T4.2a-PUX-R2 已闭合 runtime integration gate）：

- canonical replay 与 stale/layer 单调 helpers 来自 T4.2a-PUX-R1 fixture 合同。
- stale 拒绝时 cursor hold，不覆盖 UI；layer regression 同样不覆盖 UI。
- 底部 progressive status strip 显示「正文可读 → 译文先到 → 批注逐步丰富 → 完整解析」状态。
- Plate generation-scoped clear + scroll restore 在 reload 时保留用户阅读位置。
- 详细测试与运行态约束见 [`implementation-plan.md`](../implementation-plan.md) T4.2a-PUX-R1 / T4.2a-PUX-R2 章节。

## D2 Spike

D2 需要验证：

- at-least-once SSE + client dedupe。
- polling fallback 与 SSE 共用 envelope。
- gap detection reload snapshot。
- event 与 layer publish 同事务。
- snapshot 落表和实时聚合的成本差异。
- domain-targeted projection ops replay。
- Base Plate Snapshot rebuild equivalence。
