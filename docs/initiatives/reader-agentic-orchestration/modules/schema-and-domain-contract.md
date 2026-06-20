# Schema And Domain Contract

> 状态：`D4-P1 translation implemented`
> 最后更新：2026-06-21
> 范围：Reader agentic orchestration 的后端 schema 边界、领域对象、运行时事实源、projection DTO、旧 workflow cutover 和 reset 约束。

## 目标

本合同定义 D3/D4 可以实现的最小后端事实源。

它不是最终 DDL，也不是迁移说明。实现可以调整字段类型和索引名，但不能改变生命周期、所有权、事务边界、状态语义和禁止项。

## 命名和版本规则

D3 处于开发期，没有生产数据兼容需求。正式代码和文档使用以下规则：

- 核心 class、type、DTO、service 名不加版本后缀。
- 使用 `ReaderPlateSnapshot`，不使用 `ReaderPlateSnapshotV1` 或 `ReaderPlateSnapshotV2`。
- Snapshot wrapper 使用 `schema_kind = "reader_plate_snapshot"`，不用 `schema_version`。
- `schema_version` 只允许出现在 layer output、fragment、external serialized payload 等边界数据中，用于未来兼容转换。
- 当前开发期只有一个 active schema，不并行维护 V1/V2。
- 如果上线后需要 `ReaderPlateSnapshotV2`，它只能存在于 serializer / adapter 边界，不得污染 orchestration 领域逻辑。

## 全局原则

- PostgreSQL domain tables 是事实源。
- Plate document 是 Web projection，不是 truth。
- `render_scene_json` 不进入新 Web Reader API contract。
- `anchor_segment_id` 是新权威锚点；`sentence_id` 仅可作为兼容 alias。
- raw Plate path、raw Slate operation、DOM range 不持久化。
- `reader_events.sequence` 是每个 Reading Record 内的 committed UI event sequence，从 `1` 开始。
- PostgreSQL global sequence、`BIGSERIAL` 或 `nextval()` 不得作为 UI catch-up sequence。
- Worker claim、heartbeat、retry diagnostic 写 `reader_job_events`，不写 `reader_events`。
- 用户资产、词典、Daily Reader、usage/ledger、feedback、Ask 能力不能被旧 workflow 删除一起误删。

## D3 采纳结论

| 输入报告 | 采纳结果 | 写入合同的内容 |
|---|---|---|
| P1 Schema Audit | `accepted_with_changes` | 旧 workflow 可删除/重写，但保护 Ask、Daily Reader、用户资产、usage/ledger、feedback、Directus/Eval 观察面。 |
| P2 Unit Builder | `accepted_with_changes` | Anchor Segment 是一等实体；builder 必须 deterministic；UTF-16/hash/coverage 是硬 invariant。 |
| P3 Base Plate Snapshot | `accepted_with_changes` | 采纳 snapshot 字段与 node schema；拒绝 `ReaderPlateSnapshotV*` 命名；D4 通过 snapshot reload 承接 translation。 |
| P4 Runtime | `accepted_with_changes` | D4 必须包含 run/job/event sequence/job event；polling 可先行，SSE 一旦开放必须支持 `Last-Event-ID`。 |
| D3 Contract Review | `accepted_with_changes` | 固化 cutover matrix、reset manifest、状态机、usage 两阶段归因、snapshot 子类型和 vocabulary 三类 item subtype。 |
| D3-P2 Implementation | `accepted` | 低影响纯文本 builder、UTF-16/hash、Base Plate Snapshot serializer 和最小 translation projection 已实现；D3-P3 必须复用现有 builder/snapshot，不重新定义 Unit/Anchor/Snapshot。 |
| D3-P3 Implementation | `accepted` | 低风险纯文本 article_ready 持久化已实现；snapshot reload 使用 DB facts、consistent read 和公共 builder validator，不返回内存临时结果。 |
| D3-P4 Implementation | `accepted` | Job runtime、event publisher 和 polling cursor 已实现；job fence 必须校验 record 当前 active base，polling cursor 不得在 caught-up / empty stream 时误报 reload。 |
| D4-P0 Implementation | `accepted` | 最小 Reader API 已实现；plain text submit、snapshot reload 和 event polling 均复用 D3 services；新 API 不读取 `render_scene_json`；blank `client_record_id` 规范化为 `NULL`，重复 active `client_record_id` 返回 409。 |
| D4-P1 Implementation | `accepted` | Translation bootstrap、worker、publisher、usage attribution 和 snapshot projection 已实现；worker claim 必须按 job type/target type 过滤；retry 后成功必须清理 run failure 字段。 |

## Schema Groups

### D4 Hard Baseline

D4 最小纵切必须具备：

- `reading_records`
- `original_inputs`
- `reading_bases`
- `reading_units`
- `anchor_segments`
- `reader_runs`
- `reader_jobs`
- `reader_job_events`
- `reader_event_sequences`
- `reader_events`
- `enhancement_layers`
- `parsed_decisions`
- `ai_usage_events` attribution extension

### D5+ Or Deferred

D4 不阻塞以下表或能力，但 D3 schema 不得把它们设计死：

- `source_artifacts`
- `extraction_results`
- `candidate_reading_bases`
- `rag_substrates`
- vector store adapter metadata
- richer User Editorial Assets consolidation
- Directus/Eval replacement views
- separate navigation table
- projection snapshot cache
- independent outbox or DLQ

## Core Record And Base

### `reading_records`

Reading Record 是用户面对的长期阅读对象。

Required fields:

| Field | Meaning |
|---|---|
| `id` | record UUID |
| `user_id` | owner |
| `client_record_id` | optional client alias |
| `source_type` | `text`, `markdown`, `file`, `url`, `pdf`, `ocr`, etc. |
| `title` | display title |
| `language` | detected or user-selected language |
| `lifecycle_status` | `active`, `cancelled`, `superseded`, `deleted` |
| `product_state` | Reader / Library visible state |
| `readiness_state` | domain milestone state |
| `generation` | current record generation fence |
| `active_base_id` | current Stable Base |
| `superseded_by_record_id` | replacement record if superseded |
| `deleted_at` | soft delete |
| `created_at`, `updated_at` | audit timestamps |

Required constraints:

- partial unique index on `(user_id, client_record_id)` where `client_record_id is not null and deleted_at is null`;
- index `(user_id, updated_at desc)` for Library listing;
- index `(user_id, product_state, updated_at desc)`;
- `generation >= 1`.

Rules:

- `reading_records.generation` owns the current generation.
- `reader_runs.record_generation` and `reader_jobs.expected_generation` are snapshots.
- `active_base_id` must point to a base owned by the same record and current generation.
- Supersede creates or points to another Reading Record; it does not mutate an existing Stable Base.
- `active_base_id` can be enforced by nullable FK + transaction check, deferrable FK, or equivalent service-level invariant. The contract requires the invariant, not a single DDL shape.
- D3-P1 does not require a DB trigger to enforce `active_base_id -> reading_bases.status = 'active'`. Service / publisher code must enforce this invariant when setting active base, superseding base, and publishing layers/jobs.

### Record State Ownership

The three state layers must not collapse into one task status.

| State field | Owner | Meaning | D4 allowed values |
|---|---|---|---|
| `lifecycle_status` | record lifecycle service | Long-lived existence and replacement status. | `active`, `cancelled`, `superseded`, `deleted` |
| `readiness_state` | domain milestone aggregate | Stable milestones that downstream systems can rely on. | `submitted`, `candidate_base_ready`, `article_ready`, `initial_enhancement_ready`, `coverage_complete` |
| `product_state` | Reader/Library domain gate | User-visible state derived from readiness plus action/error/quota needs. | `processing`, `needs_confirmation`, `readable_enhancing`, `action_required`, `failed`, `deleted` |

Rules:

- `readiness_state` is monotonic for an active generation, except supersede/delete.
- `product_state` may move forward or backward when quota, user action, or retry state changes.
- Workers do not write `coverage_complete` directly. Coverage aggregates from `parsed_decisions`.

### `original_inputs`

Original Input stores what the user submitted.

Required fields:

| Field | Meaning |
|---|---|
| `id` | input UUID |
| `reading_record_id` | parent record |
| `user_id` | owner |
| `input_type` | `plain_text`, `markdown`, `file_ref`, `url`, `image_ref`, etc. |
| `source_text` | D4 plain text source |
| `source_ref_json` | future file/url/object reference |
| `metadata_json` | input metadata |
| `content_sha256` | original content hash |
| `created_at` | timestamp |

D4 pure text uses one row. Future URL/PDF/OCR may append artifacts and extraction results.

### `reading_bases`

Stable Reading Base is the immutable text truth for a record generation.

Required fields:

| Field | Meaning |
|---|---|
| `id` | base UUID |
| `reading_record_id` | parent record |
| `base_version` | record-local base version |
| `record_generation` | record generation this base belongs to |
| `text` | stable readable English text |
| `content_sha256` | full text hash |
| `content_utf16_length` | UTF-16 code unit length |
| `canonicalizer_version` | low-impact adaptation policy version |
| `builder_version` | unit builder version |
| `segmenter_version` | segmenter version |
| `language` | stable base language |
| `title_snapshot` | title used at freeze time |
| `navigation_json` | D4 Navigation Skeleton JSON |
| `status` | `active`, `superseded` |
| `frozen_at`, `created_at` | audit timestamps |

Required constraints:

- unique `(reading_record_id, base_version)`;
- unique `(reading_record_id, record_generation)`;
- one active base per record;
- active base `record_generation` equals `reading_records.generation`;
- `content_utf16_length` equals UTF-16 length of `text`;
- `content_sha256` matches `text`.

`navigation_json` D4 shape:

```json
{
  "units": [
    {
      "unit_id": "u1",
      "order_index": 1,
      "unit_type": "body",
      "label": null,
      "base_start_utf16": 0,
      "base_end_utf16": 120,
      "boundary_quality": "normal"
    }
  ]
}
```

Rules:

- Enhancement Layers, Ask and user assets never edit `reading_bases.text`.
- High-impact input adaptation must go through Candidate Base before freezing.
- D4 low-impact pure text path may freeze directly.

### `reading_units`

Reading Unit is the stable scheduling and display chunk.

Required fields:

| Field | Meaning |
|---|---|
| `id` | row UUID |
| `reading_record_id` | parent record |
| `base_id` | Stable Base |
| `unit_id` | record/base-local id such as `u1` |
| `order_index` | numeric order, 1-based |
| `unit_type` | `body`, `heading`, `list`, `quote`, `unknown`, `fallback` |
| `boundary_quality` | `normal`, `low`, or structured JSON |
| `base_start_utf16`, `base_end_utf16` | absolute offsets in Stable Base |
| `text_hash` | 8-char `fnv1a32-utf16` hash of unit text |
| `metadata_json` | diagnostics and future structure metadata |

Required constraints:

- unique `(base_id, unit_id)`;
- unique `(base_id, order_index)`;
- index `(reading_record_id, base_id, order_index)`;
- `0 <= base_start_utf16 < base_end_utf16`;
- adjacent units cannot overlap;
- gaps between adjacent units may contain only whitespace.

Rules:

- Unit is not guaranteed to be a semantic paragraph.
- Unit is not the minimum user selection coordinate.
- Sorting relies on `order_index`, not string sorting of `unit_id`.

### `anchor_segments`

Anchor Segment is the stable span anchor entity.

Required fields:

| Field | Meaning |
|---|---|
| `id` | row UUID |
| `reading_record_id` | parent record |
| `base_id` | Stable Base |
| `unit_id` | owning Reading Unit |
| `anchor_segment_id` | authority id such as `s1` |
| `sentence_id` | optional compatibility alias |
| `paragraph_id` | optional base-local structural group id such as `p1` |
| `order_index` | record/base-level order, 1-based |
| `unit_order_index` | order inside unit |
| `segment_type` | `sentence`, `clause`, `fallback_window` |
| `base_start_utf16`, `base_end_utf16` | absolute offsets in Stable Base |
| `unit_start_utf16`, `unit_end_utf16` | local offsets inside unit |
| `text_hash` | 8-char `fnv1a32-utf16` hash |
| `boundary_quality` | `normal`, `low`, or structured JSON |

Required constraints:

- unique `(base_id, anchor_segment_id)`;
- unique `(base_id, sentence_id)` where `sentence_id` is not null;
- unique `(unit_id, unit_order_index)`;
- index `(reading_record_id, base_id, order_index)`;
- `anchor_segment_id` is never null.

Rules:

- Segment text must equal `slice_by_utf16_offsets(reading_bases.text, base_start_utf16, base_end_utf16)`.
- Hash must match that slice.
- `sentence_id` is not a new authority target. New targets, Ask tools, RAG citations, projection ops and user assets must include `anchor_segment_id`.
- `paragraph_id` is grouping/debug metadata only and must not be the sole target for new facts.
- `segment_type = clause` or `fallback_window` must not be presented as a real sentence.
- `segment_type` describes shape. `boundary_quality` describes reliability.
- Span-bound layers and user selections anchor to `anchor_segment_id` plus segment-local UTF-16 offsets.
- `unit_start_utf16` / `unit_end_utf16` are segment position inside the unit. Span anchor `start_offset` / `end_offset` are positions inside the anchor segment text.

## Builder Invariants

Reading Base Builder must expose a deterministic, side-effect-free build boundary. Actual class names are implementation details.

Hard invariants:

- non-empty Stable Base creates at least one Unit and one Anchor Segment;
- Unit and Segment IDs are 1-based: `u1`, `p1`, `s1`;
- all stored offsets are UTF-16 code unit offsets;
- all visible non-whitespace text is covered by units and anchor segments;
- gaps are whitespace only;
- unit and segment order is strictly monotonic;
- hash algorithm is `fnv1a32-utf16`;
- `text_hash` stores raw 8-char hex, without algorithm prefix;
- Python and JavaScript hash implementations must match the parity corpus;
- builder never rewrites visible author text in D4 low-impact path;
- Unicode line endings and invisible characters may be normalized only under the recorded `canonicalizer_version`.

D4 segment fallback order:

1. paragraph / structure block;
2. English sentence boundary;
3. clause boundary;
4. word-window `fallback_window`;
5. mark `boundary_quality = low` when fallback is used or segment length is abnormal.

D3-P2 implementation baseline:

- low-impact canonicalization may normalize line endings, remove zero-width/control characters, normalize common Unicode spaces, compress 3+ blank lines to 2, and trim outer whitespace;
- low-impact canonicalization must not rewrite smart quotes, dash, ellipsis or other visible author text;
- current Unit formation is `1 structure block -> 1 reading unit`; structure blocks are split by blank lines;
- target-length aggregation and LLM-assisted boundary refinement are not part of D3-P2 or D3-P3;
- Anchor Segment formation uses sentence punctuation with abbreviation guard, then clause boundaries, then 24-word `fallback_window`;
- any `fallback_window` segment is `boundary_quality = low`, and a Unit containing a low-quality segment is also low quality.

Boundary mapping:

| `segment_type` | Default `boundary_quality` | Notes |
|---|---|---|
| `sentence` | `normal` | May become `low` if length or punctuation diagnostics are abnormal. |
| `clause` | `normal` or `low` | Use when sentence boundary is unavailable but punctuation/grammar gives a usable local span. |
| `fallback_window` | `low` | Mechanical recovery only. Never call it a real sentence. |

Fallback layer policy:

- D4 translation can run at unit scope even when some internal segments are low quality.
- D5 `grammar_note` and `sentence_analysis` default to skip `fallback_window` spans with rationale `boundary_low_fallback_window`, unless a boundary refiner/reviewer produces acceptable segments.

Hash parity corpus must cover at least:

- ASCII sentence;
- smart quotes, em dash and ellipsis;
- emoji and surrogate pairs;
- mixed CJK + emoji;
- single space;
- empty string.

## Runtime Schema

### `reader_runs`

Reader Run is a bounded orchestration run.

Required fields:

| Field | Meaning |
|---|---|
| `id` | run UUID |
| `reading_record_id` | parent record |
| `user_id` | owner |
| `run_type` | `initial_build`, `translation_layer`, `ask_sidecar`, etc. |
| `status` | run status |
| `record_generation` | record generation snapshot |
| `envelope_json` | immutable Authorization Envelope |
| `policy_version` | deterministic policy version |
| `trigger_kind` | user/system/source trigger |
| `failure_class`, `failure_code` | failure taxonomy |
| `created_at`, `started_at`, `finished_at`, `updated_at` | audit timestamps |

D4 statuses:

- `queued`
- `running`
- `waiting_user`
- `waiting_quota`
- `paused`
- `completed`
- `failed_retryable`
- `failed_terminal`
- `cancelled`
- `superseded`

Required constraints:

- index `(reading_record_id, created_at desc)`;
- index `(status, created_at)`;
- active mutating runs for the same record/generation must be bounded by policy.

Run status transitions:

| From | To |
|---|---|
| `queued` | `running`, `cancelled`, `superseded` |
| `running` | `waiting_user`, `waiting_quota`, `paused`, `completed`, `failed_retryable`, `failed_terminal`, `cancelled`, `superseded` |
| `waiting_user` | `running`, `cancelled`, `superseded` |
| `waiting_quota` | `running`, `cancelled`, `superseded` |
| `paused` | `running`, `cancelled`, `superseded` |
| `failed_retryable` | `running`, `failed_terminal`, `cancelled`, `superseded` |
| terminal states | no transition except explicit dev repair tool |

Rules:

- D4 cannot omit `reader_runs`.
- `envelope_json` is immutable after run creation.
- Complete envelope counters can be D5, but run/generation/envelope cannot be D5.

### `reader_jobs`

Reader Job is the durable claimable execution unit.

Required fields:

| Field | Meaning |
|---|---|
| `id` | job UUID |
| `reading_record_id` | parent record |
| `base_id` | Stable Base for base-scoped jobs; null only for record-level jobs before base exists |
| `run_id` | owning run |
| `user_id` | owner |
| `job_type` | D4 at least `build_base`, `translate_unit` |
| `target_type` | `record`, `unit`, `anchor_segment`, `unit_range` |
| `target_key` | domain target id |
| `status` | job status |
| `priority` | claim ordering |
| `available_at` | due time |
| `lease_owner` | worker identity |
| `lease_token` | UUID token per claim |
| `lease_expires_at` | absolute lease expiry timestamp |
| `claimed_at` | claim timestamp |
| `pause_owner` | optional `user`, `quota`, `system`, `policy` |
| `attempt_count` | successful claims |
| `transient_attempt_count` | transient retry budget |
| `repair_attempt_count` | repair retry budget |
| `replan_attempt_count` | replan retry budget |
| `max_attempts` | hard cap |
| `expected_generation` | record generation fence |
| `operation_fingerprint` | business intent fingerprint |
| `idempotency_key` | enqueue idempotency |
| `input_hash` | worker input hash |
| `input_json` | typed input envelope |
| `output_ref_json` | output reference or diagnostics |
| `rationale_code` | skip/pause/reject rationale |
| `failure_class`, `failure_code`, `failure_message` | failure details |
| `created_at`, `updated_at` | audit timestamps |

D4 statuses:

- `queued`
- `claimed`
- `retry_later`
- `paused`
- `skipped`
- `succeeded`
- `failed_terminal`
- `cancelled`
- `superseded`

`failed_retryable` is not a durable claimable job status. Retryable failure is represented by `failure_class` plus transition to `retry_later`.

Target key formats:

| `target_type` | `target_key` |
|---|---|
| `record` | record UUID |
| `unit` | `unit_id` such as `u1` |
| `anchor_segment` | `anchor_segment_id` such as `s3` |
| `unit_range` | inclusive range such as `u1:u3` |

Required constraints:

- partial claim index on `(priority desc, available_at asc, created_at asc, id asc)` where status is `queued` or due `retry_later`;
- partial lease expiry index where status is `claimed`;
- unique `(run_id, idempotency_key)`;
- active unique fingerprint on `(reading_record_id, base_id, job_type, target_type, target_key, expected_generation, operation_fingerprint)` where status in `queued`, `claimed`, `retry_later`, `paused`;
- base-scoped jobs should enforce `(base_id, reading_record_id, expected_generation)` against `reading_bases(id, reading_record_id, record_generation)` or an equivalent generation-aware guard;
- heartbeat and publish must match `lease_token`.

Job status transitions:

| From | To |
|---|---|
| `queued` | `claimed`, `paused`, `skipped`, `cancelled`, `superseded` |
| `claimed` | `succeeded`, `retry_later`, `paused`, `skipped`, `failed_terminal`, `cancelled`, `superseded` |
| `retry_later` | `claimed`, `paused`, `cancelled`, `superseded`, `failed_terminal` |
| `paused` | `queued`, `cancelled`, `superseded`, `failed_terminal` |
| terminal states | no transition except explicit dev repair tool |

Rules:

- Claim and publish are short transactions.
- LLM calls never run inside a DB transaction.
- `attempt_count` increments on successful claim only.
- Skip, quota pause or future retry scheduling must not consume an attempt.
- `operation_fingerprint` includes `base_id` and expresses business intent; it does not include transient fallback provider/model.
- Only record-level `build_base` may have `base_id = null`; all other jobs must have `base_id`, including any non-`build_base` job whose `target_type = record`.
- Claim and publish fence must reject stale generation, inactive target base, target base not owned by the record, and target base not equal to `reading_records.active_base_id`.

### `reader_job_events`

Reader Job Event is internal diagnostics.

Required fields:

| Field | Meaning |
|---|---|
| `id` | event UUID |
| `reading_record_id` | parent record |
| `run_id` | optional run |
| `job_id` | job |
| `event_type` | `job_claimed`, `heartbeat_lost`, `job_requeued`, `job_succeeded`, etc. |
| `payload_json` | diagnostics |
| `created_at` | timestamp |

Rules:

- Claim, heartbeat, retry and stale recovery diagnostics go here.
- These events do not enter Reader SSE / polling stream.

### `reader_event_sequences`

This table provides the record-scoped transactional counter.

Required fields:

| Field | Meaning |
|---|---|
| `reading_record_id` | primary key |
| `next_sequence` | next committed UI sequence, starts at `1` |
| `updated_at` | timestamp |

Allocation algorithm:

```sql
UPDATE reader_event_sequences
SET next_sequence = next_sequence + 1,
    updated_at = now()
WHERE reading_record_id = $1
RETURNING next_sequence - 1 AS sequence;
```

Rules:

- Counter update and `reader_events` insert occur in the same transaction.
- Rollback reverts the counter update and event insert.
- The next committed event reuses the rolled-back number.
- Counter row must be created in the same transaction as the Reading Record or repaired with `INSERT ... ON CONFLICT DO NOTHING` before allocation.

### `reader_events`

Reader Event is the committed UI domain event stream.

Required fields:

| Field | Meaning |
|---|---|
| `id` | event UUID |
| `reading_record_id` | parent record |
| `sequence` | per-record committed sequence |
| `event_type` | user-visible domain/projection event |
| `payload_json` | event payload |
| `source_run_id` | optional run |
| `source_job_id` | optional job |
| `source_layer_id` | optional layer |
| `created_at` | timestamp |

Required constraints:

- unique `(reading_record_id, sequence)`;
- index `(reading_record_id, sequence asc)`;
- index `(reading_record_id, created_at desc)`;
- optional index `(source_job_id)` where not null.

D4 event types:

- `article_ready`
- `layer_published`
- `layer_failed`
- `parsed_decision_updated`
- `record_state_changed`
- `action_required`
- `run_completed`
- `record_superseded`
- `projection_reset_required`

`projection_ops` DTO may be defined in schema and emitted by a disabled skeleton, but D4 does not require frontend applier end to end.

## Layer And Parsed Contracts

### `enhancement_layers`

Enhancement Layer is domain truth for translation and later AI annotation layers.

Required fields:

| Field | Meaning |
|---|---|
| `id` | layer UUID |
| `reading_record_id` | parent record |
| `base_id` | Stable Base |
| `layer_type` | `translation`, `vocabulary`, `grammar_note`, `sentence_analysis`, etc. |
| `layer_subtype` | optional subtype |
| `target_scope` | `unit`, `anchor_segment`, `unit_range`, `record` |
| `target_key` | stable domain target |
| `generation` | record generation |
| `status` | `draft`, `published`, `superseded`, `failed`, `hidden` |
| `operation_fingerprint` | idempotency fingerprint |
| `schema_version` | layer output payload schema marker |
| `output_json` | typed layer output, not arbitrary Plate JSON |
| `coverage_json` | coverage and parsed contribution |
| `quality_json` | validation and confidence diagnostics |
| `source_run_id`, `source_job_id` | producer |
| `published_at`, `superseded_at` | timestamps |

Required constraints:

- unique active `(reading_record_id, base_id, layer_type, target_scope, target_key)` where status is `published`;
- unique `(source_job_id, operation_fingerprint)`;
- layer target must belong to the same record/base.
- layer `generation` must match the target base `record_generation`, enforced by composite FK or equivalent publish guard.

Rules:

- D4 requires `translation` layer.
- `grammar_note` and `sentence_analysis` are separate layer types even if one Grammar Bundle Worker generates both.
- Layer output is business data. Projection converts it to Plate nodes or marks.

### Vocabulary Layer Item Types

Old AI Workflow generated three vocabulary annotation shapes. New orchestration keeps those semantics as `output_json.items[].item_type` inside one `vocabulary` layer.

| `item_type` | Meaning | Required fields | Projection |
|---|---|---|---|
| `vocab_highlight` | Useful word highlight with optional brief explanation. | `anchor`, `headword`, optional `brief_explanation`, optional `reason` | Inline AI mark; may show tooltip only when explanation exists. |
| `phrase_gloss` | Multi-word phrase explanation. | `anchor`, `phrase`, `phrase_type`, `gloss`, optional `example` | Inline mark plus note. |
| `context_gloss` | Context-dependent expression or meaning that depends on the current passage. | `anchor`, `display`, `gloss`, `reason` | Inline mark plus contextual note. |

D5 seed schema:

```json
{
  "schema_version": 1,
  "items": [
    {
      "item_type": "vocab_highlight",
      "anchor": {
        "anchor_type": "text_range",
        "base_id": "base_...",
        "unit_id": "u1",
        "anchor_segment_id": "s1",
        "offset_unit": "utf16",
        "start_offset": 10,
        "end_offset": 16,
        "selected_text": "word",
        "text_hash": "1a2b3c4d",
        "hash_algorithm": "fnv1a32-utf16"
      },
      "headword": "word",
      "brief_explanation": null,
      "reason": "useful_for_current_goal"
    }
  ]
}
```

Rules:

- `vocab_highlight`, `phrase_gloss` and `context_gloss` are not top-level layer types.
- Collision priority is `context_gloss > phrase_gloss > vocab_highlight`.
- A vocabulary layer can contain mixed item types if they share target scope and schema version.
- Anchors must pass the same `anchor_segment_id` and UTF-16 hash validation as grammar/user assets.

### `parsed_decisions`

Parsed Decision records whether a Unit satisfies the current parsing policy.

Required fields:

| Field | Meaning |
|---|---|
| `id` | decision UUID |
| `reading_record_id` | parent record |
| `base_id` | Stable Base |
| `unit_id` | target unit |
| `policy_code` | policy name/version |
| `parsed_state` | `not_started`, `partial`, `parsed`, `skipped`, `failed` |
| `rationale_code` | deterministic reason |
| `coverage_json` | coverage detail |
| `source_layer_id`, `source_job_id` | producer |
| `decision_json` | audit details |
| `created_at` | timestamp |

Required constraints:

- unique `(reading_record_id, base_id, unit_id, policy_code)`;
- index `(reading_record_id, parsed_state)`;
- index `(source_layer_id)`.

Rules:

- Parsed is not annotation-count threshold.
- Worker success does not automatically mean parsed.
- Coverage can increase progressively and must survive refresh.

## Plate Snapshot Contract

The Reader API returns a wrapper DTO named `ReaderPlateSnapshot`.

It is a projection payload, not domain truth.

```ts
type ReaderPlateSnapshot = {
  schema_kind: "reader_plate_snapshot";
  snapshot_id: string;
  snapshot_taken_at: string;
  last_event_sequence: number;
  record_id: string;
  base: {
    base_id: string;
    content_sha256: string;
    canonicalizer_version: string;
    builder_version: string;
    segmenter_version: string;
    text_length_utf16: number;
    hash_algorithm: "fnv1a32-utf16";
  };
  navigation: {
    units: Array<{
      unit_id: string;
      order_index: number;
      unit_type: "body" | "heading" | "list" | "quote" | "unknown" | "fallback";
      boundary_quality?: "normal" | "low";
      label?: string;
      base_start_utf16: number;
      base_end_utf16: number;
    }>;
  };
  enhancement_layers: Array<ReaderSnapshotLayer>;
  ask_supplements: Array<ReaderSnapshotAskSupplement>;
  user_assets: Array<ReaderSnapshotUserAsset>;
  parsed_decisions: Array<ReaderSnapshotParsedDecision>;
  value: ReaderPlateValue;
};

type ReaderSnapshotLayer = {
  layer_id: string;
  layer_type: "translation" | "vocabulary" | "grammar_note" | "sentence_analysis" | string;
  layer_subtype?: string | null;
  base_id: string;
  target_scope: "unit" | "anchor_segment" | "unit_range" | "record";
  target_key: string;
  status: "published";
  schema_version: number;
  output: unknown;
  published_at: string;
};

type ReaderSnapshotParsedDecision = {
  unit_id: string;
  policy_code: string;
  parsed_state: "not_started" | "partial" | "parsed" | "skipped" | "failed";
  rationale_code?: string | null;
};

type ReaderSnapshotAskSupplement = {
  supplement_id: string;
  owner: "ask_supplement";
  anchor?: DomainAnchor;
  content: unknown;
  created_at: string;
};

type ReaderSnapshotUserAsset = {
  asset_id: string;
  asset_type: "highlight" | "reader_note" | "saved_ask_note" | "saved_ask_highlight" | string;
  owner: "user";
  anchor: DomainAnchor;
  deleted_at?: string | null;
  updated_at: string;
};

type ReaderPlateValue = Array<ReaderPlateNode>;
type ReaderPlateNode = Record<string, unknown>;
```

D4 values:

- `enhancement_layers` contains published translation layers after they exist.
- `ask_supplements` is empty.
- `user_assets` is empty.
- `parsed_decisions` may be empty or contain translation parsed decisions.
- `value` contains Plate nodes for source and any published translation.

Rules:

- D4 translation uses snapshot reload or simple projection refresh.
- D4 does not persist durable raw Slate ops.
- D4 does not require `projection_ops` end to end.
- `snapshot_id` supports reload dedupe and debugging.
- `last_event_sequence` tells the client where polling/SSE should resume after snapshot load.
- `last_event_sequence` is the max committed Reader Event sequence observed by the same consistent read used to serialize the snapshot facts.
- D3-P3 implementation uses a read-only `repeatable_read` transaction for snapshot reload. Equivalent implementations are allowed only if they guarantee the same consistent view across record/base/unit/anchor/layer/parsed/event reads.
- Snapshot `value` is generated from domain facts on GET. It is not persisted Plate editor state in D4.
- Snapshot serializer must reject facts that do not belong to the current base / unit / anchor. This applies to enhancement layers, parsed decisions, ask supplements and user assets.
- DB-hydrated `ReadingBaseBuildResult` must pass `validate_reading_base_build_result` or an equivalent public builder invariant validator before snapshot serialization.
- Top-level `enhancement_layers` and `value` must be produced by the same projection builder. Focused tests must verify a published translation layer appears both in `enhancement_layers` and in matching Plate nodes by `layer_id`.
- D4 minimal translation projection only covers published `translation` layers whose output validates as `TranslationLayerOutput` and whose target scope is `unit` or `anchor_segment`.
- Non-translation `unit_range` / `record` membership checks are reserved for D5 Layer Publisher and must not be silently accepted into a D4 snapshot if they cannot be grounded to the current base.
- D4 snapshot does not expose `projection_version`. D5 may add non-cursor projection metadata if projection cache or op applier needs it.

### Plate Node Schema

D4 source nodes:

| Node | Role | Required metadata |
|---|---|---|
| `reader_unit` | top-level block | `owner=stable`, `base_id`, `unit_id`, order, unit type, base offsets, unit hash |
| `reader_source_block` | source container inside unit | `owner=stable`, `base_id`, `unit_id`, base offsets |
| `reader_anchor_segment` | inline sentence-like anchor | `owner=stable`, `base_id`, `unit_id`, `anchor_segment_id`, optional `sentence_id`, `segment_type`, base offsets, text hash |
| stable source leaf | text leaf | `owner=stable`, `lock_source=true`, `source_role=segment_text|separator`, base offsets, optional segment-local offsets |
| `reader_translation` | D4 translation block | `owner=system_ai`, `layer_id`, `layer_version`, `unit_id` or `anchor_segment_id`, typed text payload |

Source validation:

1. Collect stable source leaves in order.
2. Concatenate their `text`.
3. Compare with `reading_bases.text` slice for the unit.
4. Verify unit and anchor hash with `fnv1a32-utf16`.
5. On mismatch, reload snapshot.

## Reader Event API Contract

Polling endpoint:

```text
GET /reader/records/{record_id}/events?after_sequence=123&limit=100
```

Response:

```json
{
  "reading_record_id": "rec_...",
  "after_sequence": 123,
  "last_event_sequence": 150,
  "next_after_sequence": 140,
  "has_more": true,
  "events": []
}
```

Rules:

- Return events with `sequence > after_sequence`.
- Sort ascending by sequence.
- `last_event_sequence` is server observed max committed sequence.
- `next_after_sequence` is the last returned event sequence.
- If the response is truncated, client must not jump cursor to `last_event_sequence`.
- If no events are returned, `next_after_sequence` remains `after_sequence`.
- If `after_sequence == last_event_sequence`, return empty events and do not require reload.
- If there are no committed events and `after_sequence = 0`, return empty events and do not require reload.
- If `after_sequence > last_event_sequence`, return empty events and keep `next_after_sequence = after_sequence`; client may reload snapshot if this persists.
- Client advances cursor only after applying events.
- If a sequence gap appears, client reloads snapshot and resumes from snapshot `last_event_sequence`.

SSE endpoint may be deferred. If implemented:

```text
GET /reader/records/{record_id}/stream?after_sequence=123
Last-Event-ID: 123
Accept: text/event-stream
```

Rules:

- `Last-Event-ID` wins over query param when parseable.
- SSE `id:` is the per-record numeric sequence.
- Payload includes event UUID for debugging and dedupe.
- Heartbeat comments do not advance sequence.
- Reconnect resumes with `sequence > Last-Event-ID`.

## Publish Guard Contract

Layer Publisher writes domain truth and UI events in one transaction.

Required transaction steps:

1. Lock job by `id`, `lease_token`, status and non-expired lease.
2. Validate run, record lifecycle, product state and record generation.
3. Validate target base belongs to the record and is the active base for the expected generation.
4. Validate target unit/anchor belongs to the same record/base.
5. Validate owner policy: system publisher writes only system-owned layers; Ask/user asset writes use their own guarded paths.
6. Validate schema, anchor, source grounding and operation fingerprint.
7. CAS publish `enhancement_layers`.
8. Mark job `succeeded` or idempotent `skipped`.
9. Allocate record-scoped event sequence.
10. Insert `reader_events`.
11. Link known successful usage and ledger attribution idempotently.
12. Insert `reader_job_events`.

Post-commit work:

- SSE delivery;
- polling visibility;
- metrics;
- trace flush/linking when best effort;
- snapshot cache rebuild;
- future PG LISTEN/NOTIFY;
- future TTL or DLQ.

Failure rule:

- If any required step fails, rollback all domain writes and event sequence allocation.
- If projection event generation is enabled and fails, write `projection_reset_required` in the same transaction or rollback.
- Never leave a published layer that cannot recover by snapshot reload or event catch-up.

## Usage And Cost Attribution

Existing `ai_usage_events` must stay. D3/D4 adds nullable attribution fields:

- `reading_record_id`
- `reader_run_id`
- `reader_job_id`
- `enhancement_layer_id`
- `operation_fingerprint`
- `model_profile_id`
- `model_route`
- normalized cache status fields

Usage attribution is two-stage:

1. Raw provider usage is append-only after a model call returns. It uses an idempotency key and may exist even when publish fails.
2. Publish transaction links known usage rows to job/layer/event and writes ledger debit idempotently. It must not duplicate usage rows.

Rules:

- Failed LLM calls may write usage without a UI event.
- Usage audit is not optional for D4 LLM workers.
- Old workflow FKs from usage/ledger to `analysis_tasks` or `analysis_records` must be dropped or made legacy-nullable before deleting old tables.

`user_credit_ledger` must keep accounting history. It should gain generic attribution fields such as `subject_type`, `reading_record_id`, `reader_run_id`, `reader_job_id` and title snapshot metadata.

## Cutover Dependency Matrix

D3 reset can delete old development records, but must not erase protected capabilities by accident.

| Area / Table group | D3 handling |
|---|---|
| Dictionary: `dict_entries`, `dict_lookup_targets`, `dict_redirects` | Preserve. These are not learning workflow data. |
| Daily Reader tables/runtime | Preserve or isolate. Do not fold into D4 learning Reader schema. |
| `ai_usage_events` | Add new nullable attribution fields; drop or neutralize old workflow FK constraints before old table removal. |
| `user_credit_ledger` / credit accounts | Preserve history; add generic subject attribution; old task refs become legacy nullable. |
| User annotations / reader notes | Rewrite validation to Stable Base and Anchor Segments before routing new Reader writes. |
| Favorites | Add `reading_record` target type or isolate old target until rewritten. |
| Vocabulary book | Keep `dict_entry_id`; rewrite source refs to `reading_record_id` / `anchor_segment_id`. |
| Ask threads/messages/supplements/turn runs | Preserve ability; rewrite context and write targets to Reading Record / Anchor Segment / layers. |
| Feedback | Rewrite scope to record/layer/user asset/Ask/Daily targets. |
| `dict_ai_candidate_entries` | Preserve; add reading record / anchor / job attribution if used by Reader. |
| Directus / Eval old views | Hide or rewrite to runs/jobs/layers/events/usage before deleting old backing tables. |
| Old `analysis_*` workflow tables | Safe to delete only after protected dependencies are neutralized or rewritten. |

## Old Workflow Handling

Safe delete or rewrite after replacement exists:

- `analysis_tasks`
- `analysis_task_events`
- `analysis_results.render_scene_json`
- `analysis_results.page_state_json`
- `analysis_overview_tasks`
- old `/reader/.../scene`
- `reader_scene.py` as authoritative service
- render-scene Directus inspector
- eval adapters that treat render scene as output truth

Isolation required:

- academic workflow;
- old Directus parse-run dashboards;
- old eval suites;
- old `/analysis-tasks` routes if still needed during transition.

## Reset Manifest

Reset scripts must require an explicit profile. Current local reset scripts are not automatically considered D3-protected reset profiles until audited.

| Profile | Allowed deletion | Must preserve |
|---|---|---|
| `drop_old_learning_workflow_keep_product_assets` | Old learning workflow rows, render-scene data, parse-run debug artifacts tied only to old learning workflow. | Dictionary, Daily Reader, user annotations, reader notes, favorites, vocabulary book, Ask history, usage/ledger, feedback, users/auth. |
| `full_dev_wipe_keep_dict` | All development business data if explicitly requested. | Dictionary seed tables and other explicitly named local seed data. |

Any new destructive reset script must list exact table groups before implementation.

## Focused Tests Required Before D4

Schema and builder:

- unit and anchor coverage;
- whitespace-only gaps;
- UTF-16 slicing with emoji and smart punctuation;
- hash parity corpus shared by Python and JS;
- no overlap and monotonic order;
- active base matches record generation;
- source leaf to Stable Base validation;
- Base Plate Snapshot rebuild equivalence.

Runtime:

- claim skips locked rows;
- lease token is UUID and heartbeat only updates current token;
- stale recovery requeues or terminally fails;
- publish guard rejects wrong token, expired lease and generation mismatch;
- `base_id` and active base mismatch are rejected;
- first event sequence is `1`;
- rollback creates no visible sequence gap;
- concurrent publish sequences are contiguous;
- polling cursor does not skip truncated pages;
- snapshot includes `last_event_sequence`;
- SSE `Last-Event-ID` resume if SSE is exposed.

Cutover:

- protected dictionary tests pass;
- Daily Reader tests pass or are explicitly isolated;
- Ask write gate still requires user confirmation;
- usage/ledger attribution works with new fields;
- no new Web Reader path reads `render_scene_json`.
