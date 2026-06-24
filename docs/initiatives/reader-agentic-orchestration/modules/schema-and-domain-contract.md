# Schema And Domain Contract

> 状态：`D6-A0 Ask / notes / highlights dependency audit`
> 最后更新：2026-06-23
> 范围：Reader agentic orchestration 的后端 schema 边界、领域对象、运行时事实源、projection DTO、旧 workflow cutover 和 reset 约束；本轮 D6-A0 在 `D6-U0 Draft: User Editorial Asset Anchor` 之后新增 `D6-A0 Ask / Notes / Highlights Dependency Audit` 子节，记录 Ask / notes / highlights / user asset 写入路径的依赖矩阵与 D6 最小实现顺序。

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
- `failed_terminal` on a run or job is runtime state, not direct UI state.
- D6-P0 product mapping is conservative and code-backed:
  - `all_workers_no_job`, `max_ticks_reached`, `max_jobs_reached` do not change `reading_records.product_state`.
  - `retry_later` does not change `reading_records.product_state`.
  - `superseded` / publish-fence attention does not change `reading_records.product_state`.
  - `failed_terminal` maps to `failed` by default.
  - `action_required` is only allowed when the failed-terminal mapper classifies `attention_code` as user-remediable; v1 currently only promotes `reader_user_confirmation_required`.
- executor/profile missing、model route missing、publisher fence and other system failures must not be collapsed into `action_required`.
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
- Span-bound layers and user selections anchor to `anchor_segment_id` plus unit-local UTF-16 offsets.
- `unit_start_utf16` / `unit_end_utf16` are segment position inside the unit. Span anchor `start_offset` / `end_offset` are positions inside the unit text and must fall within that segment range. Segment-local offsets are derived projection metadata only.

### D6-U0 Draft: User Editorial Asset Anchor

> D6-U0 仅用于本次审计的特征化记录和 schema-only draft DTO；runtime 写入仍走 legacy `user_annotations` / `reader_notes`，直到 D6-U1 接线。本节不做产品 runtime 改动。

Current legacy characterization:

- `user_annotations` still anchors to legacy `analysis_record_id` + `target_key`.
  - `sentence` target key: `record:{analysis_record_id}:sentence:{sentence_id}`.
  - `text_range` target key: `record:{analysis_record_id}:range:{sentence_id}:{start_offset}:{end_offset}:{text_hash}`.
  - `multi_text` target key hashes the ordered segment signature `(paragraph_id, sentence_id, start_offset, end_offset, text_hash)` and therefore still depends on legacy paragraph/sentence identifiers.
  - `text_range` hash mismatch is rejected at request-schema validation time.
  - render-scene quote mismatch fails later in service validation.
- `reader_notes` uses the same legacy `target_key` family and validates against legacy `render_scene`.
  - `sentence` quote mismatch fails with `selected_text does not match full sentence text`.
  - `text_range` hash mismatch fails with `text_hash does not match selected_text`.
  - `multi_text` segments must follow legacy article sentence order and cannot repeat `sentence_id`.
- Current list ordering is also legacy-shaped.
  - `user_annotations` list order is `created_at desc` only; it does not project new unit/segment order.
  - `reader_notes` list order is `anchor_sentence_id`, then `start_offset`, then `end_offset`, then `created_at`; it still depends on legacy sentence anchoring rather than new `reading_units` / `anchor_segments`.

Draft future DTO:

| Field | Meaning |
|---|---|
| `record_id` | Reading Record id |
| `base_id` | Stable Base id |
| `generation` | owning record generation |
| `unit_id` | target Reading Unit |
| `anchor_segment_id` | authority span anchor |
| `start_offset` / `end_offset` | unit-local UTF-16 offsets |
| `selected_text` / `text_hash` | content identity |
| `scope` | `stable_source`, `translation`, `system_ai_layer`, `ask_supplement` |

Rules:

- New User Editorial Asset anchors must not persist raw Plate path, Slate path or DOM range.
- `scope` is reserved now so future Ask saves / translation-bound notes can share one anchor DTO without reviving legacy `target_key`.
- D6-U0 only ships characterization tests, docs and a schema-only draft DTO; legacy `user_annotations` / `reader_notes` writes remain unchanged until D6-U1.
- D6-U1 must keep `user_annotations` / `reader_notes` as runtime writes until a read migration + dual-write reconciliation pass lands; new Reading Record anchor writes are additive only.
- The dependency inventory behind this D6-U0 characterization is recorded in `D6-A0 Ask / notes / highlights dependency audit` below. D6 product work that touches Ask / notes / highlights must update that matrix first, not after.

### D6-U2 Multi-anchor Contract Decision

Audit result:

- `UserEditorialAssetAnchor` is a single-range DTO: one `unit_id`, one `anchor_segment_id`, one unit-local UTF-16 `start_offset` / `end_offset`, one `selected_text` and one `text_hash`.
- `load_validated_reading_record_anchor(...)` in `anchor_gate.py` accepts exactly one `UserEditorialAssetAnchor`; it resolves one Reading Unit and one Anchor Segment, then validates that single span against the unit text.
- D6-A5 `UserAnnotationCreateRequest.anchor` and `ReaderNoteCreateRequest.anchor` are therefore single-range only. Their new-anchor branch correctly bypasses legacy `target_key` / `render_scene` validation and returns 409 write-pending after gate success.

Decision:

- V1c production writes are **single-range first**.
- A full-sentence user asset is represented as a single `UserEditorialAssetAnchor` covering the full Anchor Segment range; it is not a separate legacy `sentence` authority.
- `multi_text` must not be overloaded into `UserEditorialAssetAnchor` and must not fall back to legacy render-scene validation on `/app/reader-record`.
- D6-U2 adds schema-only draft DTOs `UserEditorialAssetAnchorRange` and `UserEditorialAssetAnchorSet` for future multi-range assets. They are not accepted by production write requests, routes or services in this step.
- No DB migration is introduced in D6-U2. Multi-range persistence requires a later contract covering storage shape, reload projection, ordering, conflict semantics and migration/dual-write behavior.

### D6-U3 V1c Single-range Persistence Design

> 本节是 design / contract only。当前轮次不新增 DB migration，不改 runtime 写入，不启用 `/app/reader-record` Web 写入口。

Inputs from D6-A5 / D6-U2:

- D6-A5 already adds optional `anchor: UserEditorialAssetAnchor` to `UserAnnotationCreateRequest` and `ReaderNoteCreateRequest`.
- When `anchor` is present, services must bypass legacy `target_key` / `render_scene` validation and run the Reading Record anchor gate.
- Gate success currently returns HTTP 409 `user_editorial_asset_write_pending`; no legacy table write happens yet.
- D6-U2 fixes the V1c scope to single-range first. `multi_text` stays behind `UserEditorialAssetAnchorSet` and is not part of V1c production persistence.

Candidate persistence paths:

| Path | Shape | Pros | Risks / costs | V1c decision |
|---|---|---|---|---|
| Extend legacy `user_annotations` / `reader_notes` | Add nullable Reading Record anchor columns to both tables while keeping existing legacy columns. Suggested contract columns: `reading_record_id`, `base_id`, `generation`, `unit_id`, `anchor_segment_id`, `unit_start_utf16`, `unit_end_utf16`, plus existing `selected_text` / `text_hash`. | Minimal blast radius; reuses existing table ownership, update/delete/list services, note body storage, highlight color storage, and existing Web mental model. Allows legacy `/app/reader` rows and new `/app/reader-record` rows to coexist in the same domain tables while queries stay isolated by id family. | Requires making `reader_notes.analysis_record_id` nullable or adding a parallel new-record uniqueness path; existing `target_key` remains not-null and must become a deterministic compatibility key, not an authority. Needs partial indexes / constraints for the new id family. | **Recommended for V1c**. This is the smallest production path after the D6-A5 validation branch. |
| Add new `user_editorial_assets` unified table | Store all user highlights/notes/comments under one new table with asset type, anchor payload, note body/color payload, lifecycle and projection metadata. | Cleaner long-term domain model; avoids continuing legacy column names; easier to generalize to multi-range, Ask save, translation-bound notes and future user asset types. | Higher migration cost; creates two read sources during cutover; requires new service, route, DTO, permission checks, reload projection, update/delete semantics, migration/backfill strategy and legacy compatibility adapters. Existing note/highlight UI and old route behavior would need broader coordination. | Defer. Revisit after V1c single-range write/read is stable or when multi-range / cross-scope asset unification becomes mandatory. |

Recommended V1c table-extension contract:

- `user_annotations` and `reader_notes` stay the persistence owners for quick highlights and comment/note bodies.
- New Reading Record rows must carry `reading_record_id`, `base_id`, `generation`, `unit_id`, `anchor_segment_id`, `unit_start_utf16`, `unit_end_utf16`, `selected_text`, `text_hash` and hash metadata.
- Legacy rows keep `analysis_record_id` and legacy `sentence_id` / `target_key` semantics. New rows must not auto-fill `analysis_record_id` from `anchor.record_id`.
- Existing `target_key` may remain as a deterministic compatibility key because the current schema requires it, but `/app/reader-record` must never treat it as the authority. Suggested key family: `reading-record:{reading_record_id}:base:{base_id}:gen:{generation}:unit:{unit_id}:segment:{anchor_segment_id}:range:{unit_start_utf16}:{unit_end_utf16}:{text_hash}`.
- `reader_notes.analysis_record_id` must become nullable or be split by a new partial uniqueness constraint before new Reading Record notes can be written.
- Add new partial uniqueness / lookup indexes for the Reading Record family, for example `(user_id, reading_record_id, base_id, anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash)` where `deleted_at IS NULL`.
- The service branch remains explicit: `req.anchor is not None` means Reading Record path; `req.anchor is None` means legacy path. The Reading Record path must not call `load_render_scene(...)`, `validate_text_range_against_render_scene(...)` or `validate_multi_text_against_render_scene(...)`.

Reload projection for `/app/reader-record`:

- Query user annotations / notes by `reading_record_id`, not `analysis_record_id`.
- Filter by current active `base_id` + `generation` for normal display. Rows from stale bases should be hidden by default or returned with a typed stale-anchor state for future repair UI; they must not be revalidated through legacy render scene.
- Project each row to Plate using `unit_id`, `anchor_segment_id`, and unit-local UTF-16 offsets against the current `ReaderPlateSnapshot`.
- Sorting should use Reading Record order: Reading Unit order, Anchor Segment order, `unit_start_utf16`, `unit_end_utf16`, then `created_at`. Do not reuse `anchor_sentence_id` ordering for new rows.
- Legacy `/app/reader/{recordId}` continues querying by `analysis_record_id`; new Reading Record rows with `analysis_record_id IS NULL` remain invisible to the legacy route.

Legacy isolation rules:

- Old `/app/reader/{recordId}` routes, BFFs and services continue to accept and write legacy payloads without requiring `anchor`.
- New `/app/reader-record/{recordId}` write routes must be separate route/BFF entry points and must require `anchor`.
- There is no silent id remap between `anchor.record_id` and `analysis_record_id`.
- `render_scene_json` remains allowed only for legacy `/app/reader` behavior and old eval/directus observation surfaces. It is forbidden as a validation source for new Reading Record writes.
- No production write is enabled until a migration adds the required columns, constraints and focused tests for the table-extension path.

### D6-U4 V1c Single-range Persistence Implementation

> 本节是 D6-U3 design 的落地实现。新增 DB migration，改 runtime 写入，但只做 single-range，仍不启用 `/app/reader-record` UI 写入口。

Migration: `infra/migrations/0002_reader_record_anchor_columns.sql`

新增列（`user_annotations` 和 `reader_notes` 两表对称）：

- `reading_record_id UUID` (nullable)
- `base_id UUID` (nullable)
- `generation INTEGER` (nullable)
- `unit_id TEXT` (nullable)
- `anchor_segment_id TEXT` (nullable)
- `unit_start_utf16 INTEGER` (nullable)
- `unit_end_utf16 INTEGER` (nullable)

`hash_algorithm` 不作为列新增：它是 code-level constant `fnv1a32-utf16`，不是 per-row data。

`reader_notes.analysis_record_id` 和 `reader_notes.anchor_sentence_id` 安全迁移为 nullable。取舍说明：

- 现有 `UNIQUE (user_id, analysis_record_id, target_key)` 约束在 `analysis_record_id = NULL` 时不会冲突（PostgreSQL NULLs are distinct），因此不需要删除现有约束。
- 新 Reading Record rows 的 dedup 依赖新增 partial unique index `(user_id, reading_record_id, base_id, anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash) WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL`。
- 这比"新增并行列 + 新 constraint"方案风险更小：不改变现有 legacy rows 的约束行为，不引入双列歧义。

`user_annotations` 的现有 CHECK `user_annotations_text_anchor_payload_check` 被替换：`anchor_type = 'text_range'` 现在接受 legacy path（`analysis_record_id IS NOT NULL` + 旧 offsets）或 Reading Record path（`analysis_record_id IS NULL` + anchor columns set）。

新增 indexes（两表对称）：

- `idx_{table}_reading_record` on `(user_id, reading_record_id, base_id, generation) WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL` — lookup index
- `uq_{table}_reading_record_anchor` on `(user_id, reading_record_id, base_id, anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash) WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL` — partial unique index for dedup

Runtime 写入分支（`create_user_annotation` / `create_reader_note`）：

- `req.anchor is not None` → Reading Record path：gate 成功后真实 INSERT，`analysis_record_id = NULL`，anchor columns 填充，`target_key` 生成 deterministic compatibility key（不是 authority）。
- `req.anchor is None` → legacy path：完全不变，仍走 `analysis_record_id` + `load_render_scene` + `validate_*_against_render_scene`。
- Reading Record path 不调用 `load_render_scene` / `validate_text_range_against_render_scene` / `validate_multi_text_against_render_scene`。
- Reading Record id 不会被静默映射到 `analysis_record_id`（INSERT SQL 硬编码 `NULL`）。
- `user_annotations` 使用 `ON CONFLICT (user_id, target_key) DO UPDATE`（复用现有 UNIQUE 约束）。
- `reader_notes` 使用 `ON CONFLICT (user_id, reading_record_id, base_id, anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash) WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL DO UPDATE`（使用新增 partial unique index）。

Legacy isolation：

- Legacy list/update/delete 按 `analysis_record_id` 查询，新 Reading Record rows（`analysis_record_id IS NULL`）对 legacy route 不可见。
- `list_user_annotations` 的 list-all 分支（`record_id is None`）显式过滤 `AND analysis_record_id IS NOT NULL`，防止新 Reading Record rows 泄漏到 legacy 全量列表。
- Legacy `/app/reader/{recordId}` path 完全不变。
- `/app/reader-record` UI 写入口仍未启用。

FK 约束决策：

- V1c 不为 `reading_record_id` / `base_id` / `generation` 新增 FK 到 `reading_bases`。
- 原因 1：anchor gate（`load_validated_reading_record_anchor`）已在 runtime 校验 `reading_record_id` / `base_id` / `generation`，FK 是 defense-in-depth 而非 primary validation。
- 原因 2：`reading_bases` 使用 `ON DELETE CASCADE` from `reading_records`，hard-delete Reading Record 会级联删除 bases。从 `user_annotations` / `reader_notes` 加 FK 会强制提前决定 cascade 语义（CASCADE 删用户数据、SET NULL 留孤儿、RESTRICT 阻止清理）。
- 原因 3：Reading Record / Base 删除时 user assets 的归档/保留语义尚未最终确定。
- Follow-up：删除/归档语义确定后 revisit FK。候选 target：`reading_bases(id, reading_record_id, record_generation)` via `uq_reading_bases_id_record_generation`。

## D6-A0 Ask / Notes / Highlights Dependency Audit

> 本节是 D6 product hardening 进入 Ask / notes / highlights / user asset 写入前的依赖审计和迁移边界设计；不接新 Ask、不写新 API、不改产品 runtime。本节结论即 D6 最小实现顺序的输入。

### D6-A0 范围与基本立场

- 范围：`services/api/app/services/reader_ask/*`、`services/api/app/agents/reader_ask_*.py`、`services/api/app/services/user_annotations.py`、`services/api/app/services/reader_notes.py`、`services/api/app/services/reader_scene.py`、`apps/web/src/components/reader/` 中 Ask / notes / highlights / selection / citation 路径、`apps/web/src/lib/reader-plate/bridges/ask/*`，以及 D6-U0 schema-only draft `services/api/app/schemas/user_editorial_assets.py`。
- 不做：`render_scene_json` → `ReaderPlateSnapshot` 的兼容映射；旧 `analysis_record_id` / `client_record_id` → 新 `Reading Record.record_id` 的字段别名；为旧 `/app/reader/{recordId}` 静默换数据源。
- 允许：复用 `app/contracts/annotation.py` 的 UTF-16 offset + `fnv1a32-utf16` hash、`text_anchors.py` 的 anchor validation 思路、Ask "用户确认后写资产"产品约束、`reader_ask_supplements` 来源标记思路。

### D6-A0 旧依赖审计矩阵

| 路径 / 文件 | 旧 record id 语义 | 旧锚点 / scene 语义 | 写入 / 消费表 | 是否进入 D6 重写 | 备注 |
|---|---|---|---|---|---|
| `services/api/app/services/reader_ask/service.py`（约 5000 行） | `analysis_record_id` / `target_key` | `sentence_id`、`paragraph_id`、`target_key`、`render_scene` | `reader_ask_threads`、`reader_ask_turn_runs`、`reader_ask_supplements` | D6 必须重写；D6 不允许保留 service 作为 authoritative path | 旧 service 不能以"双轨长期兼容"形态继续承载新 Reading Record；D6 早期必须先冻结新写入路径并把 persistence 拆出独立层 |
| `services/api/app/services/reader_ask/supplements.py` | 同上 | 直接以 `sentence_id` / `paragraph_id` / `target_key` 写 SQL | `reader_ask_supplements` | D6 重写为 anchor_segment_id + UTF-16；先 read-only 投影，再 enable write | Ask supplement 写入当前与 `user_annotations` / `reader_notes` 在 service 层耦合（L119），D6-U1 必须先解耦 |
| `services/api/app/services/reader_ask/{repository,resolver,context_runtime,known_reference_resolver,utils}.py` | 同上 | `render_scene_json` / `render_scene` dict 作为事实源 | 仅读 | D6 重写为读 Stable Base / Reading Unit / Anchor Segment | 共享 `render_scene` 解析逻辑不能进入新 Reading Record path |
| `services/api/app/services/reader_ask/{planner,planner_runtime,planner_route_policy,post_process,runtime_contract}.py` | 同上 | `ReaderAskAnchorRef` / `target_key` / `sentence_id` | 仅 LLM/tool 上下文 | D6 重写 | Agent tool signature 必须包含 `anchor_segment_id` + unit-local UTF-16 offsets |
| `services/api/app/services/reader_ask/{capabilities,config,output_contract,stream_*}.py` | 同上 | 通过 anchor 结构传递 | 仅上下文 / 事件流 | D6 部分重写；D6 不重写 SSE / streaming 协议骨架 | streaming 协议本身可复用，只需换 anchor payload |
| `services/api/app/services/reader_ask/agent_deps_factory.py` | 同上 | `ReaderAskAnchorRef` | runtime dep 注入 | D6 重写 | 必须把"读取 `render_scene`"替换为"读取 Stable Base / Reading Unit / Anchor Segment" |
| `services/api/app/agents/reader_ask_agent.py` | `target_sentence_id` / `target_key` | `sentence_id`、`target_key` | agent tool policy | D6 重写 | 新 tool 必须接受 `anchor_segment_id` + unit-local UTF-16 offsets；旧的 `target_sentence_id` 仅可作为内部 alias |
| `services/api/app/agents/daily_vocab_agent.py` | `paragraph_id` | `paragraph_id` | agent runtime | 不进入 D6 重写范围 | 旧 daily vocab 走 legacy path；本轮不切 |
| `services/api/app/agents/repair_agent.py` | `sentence_id` | `sentence_id` | agent runtime | D6 重写为 anchor_segment_id | 与 D6-U1 anchor 切换同步 |
| `services/api/app/services/user_annotations.py` | `analysis_record_id`（D6 schema 草案称 `record_id`） | `sentence_id` / `paragraph_id` / `target_key` | `user_annotations` | D6-U1 重写 validator；U2/U3 切写入路径 | 现网 entry：`create_user_annotation` / `list_user_annotations` / `update_user_annotation` / `delete_user_annotation`；`UserAnnotationResponse` DTO 在 API 表面硬编码旧 anchor |
| `services/api/app/services/reader_notes.py` | `analysis_record_id` | `anchor_sentence_id` / `target_key` | `reader_notes` | D6-U1 重写 validator；U2/U3 切写入路径 | list 排序仍依赖 `anchor_sentence_id` + offset，D6-U1 必须重写排序键 |
| `services/api/app/services/reader_scene.py` | `client_record_id` / UUID | `render_scene_json`、`ReaderSceneResponseDto` | `analysis_records`、`reader_ask_supplements` | D6 不再作为 authoritative service；可作为 legacy read-only adapter 存在 | `merge_record_with_reader_ask_supplements` 是 ask supplement 写入旧 scene 的耦合点，必须拆分 |
| `services/api/app/schemas/user_editorial_assets.py` | `record_id: str`（Reading Record id） | `anchor_segment_id` + unit-local UTF-16 offsets + `scope` | schema-only draft | D6-U0 维持现状；D6-U1 开始接 writing path | 已含 `fnv1a32-utf16` hash + offset + selected_text 三方一致性校验，可直接作为 D6-U1 写入 DTO 的最小核心 |
| `services/api/app/schemas/{reader_ask,user_annotations,reader_notes,reader_scene,analysis}.py` + `app/schemas/internal/*` | API surface 硬编码 `sentence_id` / `paragraph_id` / `target_key` | 同上 | DTO / request schema | D6 必须引入 anchor_segment_id 字段；旧字段保留为 deprecated optional | 旧 DTO 直接改字段会破坏 legacy API；D6 必须分版本 |
| `apps/web/src/lib/reader-plate/bridges/ask/{adapters,types,index}.ts` | `targetKey` 字符串 | 通过 `targetKey` 反推 `recordId` / `sentence_id` / `paragraph_id` | Web Ask 桥接 | D6 必须改 anchor 序列化；本轮不动 | 中心 anchor serializer 是 D6 Web 切线的主要 hook |
| `apps/web/src/lib/reader-plate/primitives/selection-targets.ts` | `targetKey` 字符串 | `ReaderTextSelection` → `targetKey` | Web selection bridge | D6 必须改 | 与 bridges/ask/adapters.ts 必须同步切换；不允许一个先切 |
| `apps/web/src/types/api/{reader-ask,reader-notes,annotations,reader-scene}.ts` | `analysis_record_id` (UUID) | `sentence_id` / `paragraph_id` / `target_key` | Web DTO | D6-U1 起 DTO 加可选 anchor_segment_id；旧字段保留 | Web 端不能直接删除旧 DTO 字段，会破坏 library/command palette/Vocabulary source links |
| `apps/web/src/services/bff/{reader-ask,reader-notes,annotations}.ts` | `analysis_record_id` (UUID) | 通过 DTO 间接带旧 anchor | BFF → 上游 | D6 必须新增 BFF；但本轮不切 | 旧 BFF 仍服务于 `/app/reader/{recordId}`，不能因为 `/app/reader-record/{recordId}` 出现而被静默替换 |
| `apps/web/src/components/reader/{ReaderNotePanel,AnnotationGutter,SelectionToolbar,AiWorkspacePanel,ask-chat/*}.tsx` | UI 走旧 selection / anchor | 旧 selection / `targetKey` | UI | D6 暂不切 UI；必须等 Plate Surface UI 方案 | UI 切线依赖 Plate Surface 视觉方案，不在本轮审计范围内 |
| `apps/web/src/app/api/web/reader-{notes,ask}/**` route handlers | `analysis_record_id` / `record_id`（按 Ask 类型） | DTO 间接带旧 anchor | Web API | D6-U1 起新增 `record_id` 为 Reading Record id 的 route；旧 route 保留 | 旧 route 是当前唯一 Ask / note 写入入口，新 route 必须先有 focused tests 才能 enable |

### D6-A0 Reading Record id 进入 Ask context 的最小路径

1. Ask request 入口接收 `Reading Record.record_id`（已是新 id）；旧 `analysis_record_id` 不出现在新 path 任何字段。
2. Ask service 不再 `load_render_scene(record_id)`；改为从 `reader_plate_snapshot` 解析 record 上下文，调用 D3-P2/D3-P3 builder invariants 验证后投影 anchor。
3. Ask tool signature 改为 `(anchor_segment_id, unit_local_start_offset, unit_local_end_offset, offset_unit = "utf16", text_hash, hash_algorithm)`；旧 `target_sentence_id` / `target_key` 不再出现在 tool call payload。
4. Ask supplement 写入 (`reader_ask_supplements`) 在 D6-U1 后改为基于 `anchor_segment_id` + UTF-16 的写路径；`scope = "ask_supplement"` 与 `user_assets.scope` 共用 `UserEditorialAssetAnchor` 字段。
5. Ask thread / message 主键仍是 `reader_ask_threads`；其 `record_id` 字段在 D6 schema 中改为 `reading_record_id` (FK → `reading_records.id`)；`analysis_record_id` 列保持 nullable deprecated 直到旧 data 清空。

### D6-A0 anchor_segment_id + unit-local UTF-16 offsets 替代旧 sentence anchor 的契约

- 旧 `target_key = "record:{analysis_record_id}:sentence:{sentence_id}"` 拆为 `(reading_record_id, anchor_segment_id)`；不再出现 `analysis_record_id`。
- 旧 `target_key = "record:{analysis_record_id}:range:{sentence_id}:{start_offset}:{end_offset}:{text_hash}"` 拆为 `(reading_record_id, anchor_segment_id, start_offset, end_offset, text_hash, hash_algorithm)`，其中 `start_offset` / `end_offset` 改为 unit-local UTF-16 offsets（参考 `UserEditorialAssetAnchor`）；`base_id` + `generation` 通过 `reading_records.active_base_id` 推导，不直接出现在 anchor payload。
- 旧 multi-text `target_key` 改为 `(reading_record_id, [ {anchor_segment_id, start_offset, end_offset, text_hash} ])`；segment signature hash 与 legacy multi-text hash 不保证相等，但 fallback 到逐 segment 校验。
- 旧 `paragraph_id` 不再作为 anchor 主键；保留为 grouping / debug metadata，与 schema-and-domain-contract L298 的"paragraph_id is grouping/debug metadata only and must not be the sole target for new facts"一致。
- 新 anchor 验证函数沿用 `app/contracts/annotation.py` 的 `compute_text_range_hash` + `utf16_code_unit_length` + `slice_by_utf16_offsets`；与 grammar bundle / vocabulary worker 的 anchor validator 共用。

### D6-A0 Ask / propose_note / propose_highlight / write_ai_supplement 最小 D6 分层

> 命名说明：`propose_*` 仅指 Ask 通过 tool call 提议的写动作；最终落地必须经过用户确认（已有 Ask write gate 约束）。`write_ai_supplement` 指 Ask 在不经过用户再次确认的情况下直接写入 AI sidecar 内容（`reader_ask_supplements`）；必须受 Ask write gate + scope = "ask_supplement" 双重约束。

D6 最小分层只规定"按能力拆分、不可越层调用"，不规定具体文件路径：

1. **Reader context projection**（读路径）：从 `reader_plate_snapshot` 解析 `record` / `base` / `navigation.units` / `anchor_segments` / `enhancement_layers` / `ask_supplements` / `user_assets`；不直接读 `render_scene_json`。D6 不允许 service 层保留 `load_render_scene` 入口作为新 path 的 fact source。
2. **Ask Agent Tooling**（写提议）：tool signature 只接受 `(reading_record_id, anchor_segment_id, start_offset, end_offset, offset_unit, text_hash, hash_algorithm, scope)`；tool 返回值用 `UserEditorialAssetAnchor` 同形 DTO，不暴露 `target_key`。
3. **User Editorial Asset Writer**（确认后写资产）：受 Ask write gate 控制；V1c 推荐先扩展 `user_annotations` / `reader_notes` 两张 legacy 表承载 single-range Reading Record anchor columns，anchor payload 使用 `UserEditorialAssetAnchor` 形状；`user_editorial_assets` 统一表保留为后续收敛选项，不作为 V1c 最小 persistence 路径。
4. **Ask Supplement Writer**（写 AI sidecar）：scope = "ask_supplement"；可与 user asset writer 共用 anchor 校验；可与 reader_ask_supplements 表共存直到旧 scene merge 路径被替换。
5. **Anchor Validator**：集中校验 UTF-16 offsets、`fnv1a32-utf16` hash、`anchor_segment_id` ∈ 当前 base/units、`start_offset`/`end_offset` ⊂ unit 局部 span；校验失败必须 fail-fast 并返回 typed error，不静默 fallback 到 `target_key`。

### D6-A2 Anchor Validator extraction

- D6-A2 新增纯 backend 模块 `services/api/app/contracts/anchor_validation.py`，当前只提供纯函数和 focused tests，不接产品 runtime。
- 当前 API 分两层：
  - `validate_text_anchor_payload(...)`：只校验 payload 内部一致性，包括 `offset_unit == "utf16"`、`hash_algorithm == "fnv1a32-utf16"`、`end_offset > start_offset`、`selected_text` UTF-16 长度与 span 一致、`text_hash == fnv1a32-utf16(selected_text)`。
  - `validate_text_anchor_against_unit(...)`：在前者基础上再校验 `start_offset` / `end_offset` 落在给定 `anchor_segment` 的 unit-local range 内，且 `selected_text` 等于 `unit_text` 对应 UTF-16 slice。
- 异常类型为 `AnchorValidationError`，包含稳定 `code`，供后续 Ask / notes / highlights / user asset writer 映射成 typed API error。
- `UserEditorialAssetAnchor` 仅在 schema-only 层复用 `validate_text_anchor_payload(...)`；D6-A2 不改 legacy `user_annotations` / `reader_notes` 写路径，也不改 vocabulary / grammar / reader_ask runtime consumer。D6-A3 后 `app/schemas/reader_ask.py` 可通过 schema-to-schema wrapper 复用同形字段，但仍不代表写入路径已切换。

### D6-U1 / D6-A1 Backend Reading Record anchor validation gate

- D6-U1 / D6-A1 在 `services/api/app/services/reader_orchestration/anchor_gate.py` 新增只读 gate：`load_validated_reading_record_anchor(...)`。
- gate 输入当前使用 `UserEditorialAssetAnchor`，但它仍然**不**接任何 DB 写入路径；只做 Reading Record anchor 属于当前用户 / 当前 active base 的校验。
- `ReaderPlateSnapshot.record.generation` 暴露当前 `reading_records.generation`，Web read-only anchor draft 必须携带该 generation fence；后端 gate 不接受 unknown/null generation。
- gate 复用 `ReaderOrchestrationRepository.load_snapshot_facts(...)` 读取当前 record/base/unit/anchor_segment facts，并在内存中继续校验：
  - `record_id` 属于 `user_id`
  - `base_id` / `generation` 与当前 active base 一致
  - `unit_id` 属于当前 base
  - `anchor_segment_id` 属于该 unit
  - `start_offset` / `end_offset` 落在 anchor segment unit-local range 内
  - `selected_text` 与 `unit_text` UTF-16 slice 一致
  - `text_hash` 正确
- 所有失败都统一为 `AnchorValidationError` + 稳定 `code`；D6-U1 / D6-A1 当前引入的 gate-level code 包括 record/base UUID 非法、record 不属于用户、stale base/generation、unit 缺失、anchor segment 缺失和 anchor segment 不属于目标 unit。
- 本轮不新增 API route，不改变 `/app/reader-record` read-only 状态，也不切换 `user_annotations` / `reader_notes` / `reader_ask_supplements` 的 runtime 写路径。
- static guard allowlist 仅放行 `app/services/reader_orchestration/anchor_gate.py` import `app.schemas.user_editorial_assets`。原因：这是一条专用只读 gate；除它之外，其他 runtime service 继续禁止直接依赖 schema-only draft，避免在 D6-U2 之前扩散成隐式写路径依赖。D6-A3 允许 `app/schemas/reader_ask.py` 做 schema-to-schema 复用；agent / service 侧仍不得直接 import `user_editorial_assets`。

### D6-A3 Ask tool signature / write-proposal anchor contract

- D6-A3 在 `services/api/app/schemas/reader_ask.py` 新增 Ask write proposal payload schema：`ReaderAskReadingRecordAnchor` 继承 `UserEditorialAssetAnchor` 字段与 payload-only validator，`ReaderAskWriteProposalPayload` 允许 `save_note` / `save_highlight` proposal 携带同形 `anchor`。
- `ReaderAskActionProposal` 对 `save_note` / `save_highlight` 的 `payload_json` 做 focused schema 校验：新 Reading Record anchor payload 可用；legacy `ReaderAskAnchorRef`、`target_key`、`target_sentence_id` 仍保留兼容；malformed anchor fail-fast。
- `services/api/app/agents/reader_ask_agent.py` 的 `propose_save_note` / `propose_save_highlight` tool signature 增加可选 Reading Record anchor 参数。传入新 anchor 时只写入 action request / action proposal payload；未传入时继续使用 legacy `primary_anchor` payload。
- D6-A3 不调用 `load_validated_reading_record_anchor(...)` 做 DB 校验，不写 `user_annotations` / `reader_notes` / `reader_ask_supplements`，不启用 `/app/reader-record` Ask，也不改旧 `/api/web/reader-ask/*` route / confirm 行为。

### D6-A0 哪些能力先 read-only、哪些必须等 Plate Surface UI 方案

**先 read-only（不依赖 Plate Surface 视觉方案）**：

- Ask thread 创建、Ask message 发送、Ask citation 渲染（D6 仅改 anchor 序列化，不改 chat shell 视觉）。
- Ask supplement 列表展示（read-only，不写）。
- `user_annotations` / `reader_notes` 列表展示（read-only，不写）；按 `anchor_segment_id` + UTF-16 offsets 排序。
- 旧 `user_annotations` / `reader_notes` 写入路径继续走 `/app/reader/{recordId}`；不切换为 `/app/reader-record/{recordId}` 写入入口。

**必须等 Plate Surface UI 方案（D6 不在本轮实现）**：

- `/app/reader-record/{recordId}` 内的 notes/highlights 写入入口（composer / inline mark）。
- SelectionToolbar 的 ask / highlight / note 动作在 `/app/reader-record/{recordId}` 启用。
- AnnotationGutter 在 `/app/reader-record/{recordId}` 内的交互。
- AiWorkspacePanel 在 `/app/reader-record/{recordId}` 内的接入（含 action confirm UI）。
- `/app/reader-record/{recordId}` 与 `/app/reader/{recordId}` 的并存策略或合并策略。

理由：Plate Surface 视觉方案决定 selection-to-anchor、inline mark、right-rail、bottom-sheet、action confirmation 等 UX 形状；这些形状反过来决定 Ask / notes / highlights 写入的最小切面。在形状未确定前切写入只会做出"形状错位的中间态"。

### D6-A0 D6 最小实现顺序

按"读先于写、低风险先于高风险、独立 surface 先于耦合 surface"分组：

1. **D6-A1 Read-only anchor 接入**：`UserEditorialAssetAnchor` schema 不动；先新增 legacy-to-new anchor adapter，把旧 `target_key` / `sentence_id` / sentence-local offsets 映射为 `anchor_segment_id` + unit-local UTF-16 anchor，再做只读 projection / grouping；旧 `user_annotations` / `reader_notes` / `reader_ask_supplements` 当前没有 `anchor_segment_id`，不能直接按新 anchor 读取；不写 DB。Touched areas：`services/api/app/services/reader_ask/{supplements,repository}.py`、`apps/web/src/lib/reader-plate/bridges/ask/adapters.ts`、`apps/web/src/lib/reader-plate/primitives/selection-targets.ts`（只读路径）；不接新 API。
2. **D6-A2 Anchor Validator 抽离**：把现有 `app/services/text_anchors.py` 的 anchor validation 思路抽到 `app/contracts/anchor_validation.py`（或等价位置），使 Ask tool、user_annotations、reader_notes、grammar bundle、vocabulary worker 共用同一 validator；不引入新 contract 字段。
3. **D6-A3 Ask tool signature 切换（write-proposal only，已落地）**：agent tool signature 已可接受 `UserEditorialAssetAnchor` 同形 Reading Record anchor payload；tool 返回的 action proposal payload 可携带新 `anchor`，同时保留 legacy `ReaderAskAnchorRef` / `target_key` / `target_sentence_id` 兼容；tool 调用仍受 Ask write gate 控制，不写 DB；Ask message / citation / stream 协议不动。
4. **D6-A4 Ask supplement 写入切线**：保留 `reader_ask_supplements` 表与现有 schema；把 `supplements.py` 中所有 `sentence_id` / `paragraph_id` / `target_key` 写 SQL 改为基于 anchor_segment_id + UTF-16；写前必须经过 Ask write gate + Anchor Validator；新写入不影响 `/scene` 旧读取。
5. **D6-A5 `user_annotations` / `reader_notes` 双合同 spike（D6-U1 前置，D6-U2 决策后收窄为 single-range first）**：保留两张表；引入 `UserEditorialAssetAnchor` 作为 request body 的可选 `anchor` 字段；旧 `target_key` 字段 deprecated optional；`analysis_record_id` 改为 nullable deprecated optional。当前 spike 不新增 DB migration，不写 legacy 表；收到 `anchor` 时先校验 `selected_text == anchor.selected_text`，再走 Reading Record anchor gate。gate 失败返回 typed HTTP 400；gate 成功返回 HTTP 409 + `code = "user_editorial_asset_write_pending"`，表示已验证但 persistence deferred。`multi_text` 不进入该 production branch；后续必须走 `UserEditorialAssetAnchorSet` / multi-range DTO。D6-U3 design 结论是 V1c 先扩展 `user_annotations` / `reader_notes`，不先引入统一 `user_editorial_assets` 表。
6. **D6-A6 Web BFF / route 切线**：新增 `/api/web/reader-records/{recordId}/reader-ask/threads` 等新 route handler；旧 `/api/web/reader-ask/threads` 与 `/api/web/reader-notes` 保留为 legacy；新 route 不复用 BFF `confirmReaderAskActionForWeb` 中旧 `target_key` 分支。
7. **D6-A7 Plate Surface UI 接入（不在本轮审计范围）**：必须等 Plate Surface 视觉方案落地后再切；本轮不做。

### D6-A0 暂不切的旧能力与原因

- **`reader_scene.py` 作为 authoritative service**：D6 不替换；它仍是 `/app/reader/{recordId}` 的事实源；直到 Plate Surface 决定 `/app/reader-record/{recordId}` 是否合并 `/app/reader/{recordId}`，否则两个 service 并存。理由：合并策略取决于 UI 方案。
- **`reader_ask_threads` 表 + Ask thread UI**：D6 不重写主键结构；Ask thread 仍是独立于 Reading Record 的子资源。理由：Ask thread 跨 Reading Record 的合并/迁移策略未确定。
- **Ask "用户跨 Reading Record 引文"**：known_reference_resolver 仍以 `render_scene` 解析；D6 不在本轮重写。理由：跨 record citation 涉及 candidate base / RAG substrate，不属于 D6 切线范围。
- **`daily_vocab_agent.py`**：daily vocab 走 legacy path，不进入 D6 切线。理由：daily vocab 与 Daily Reader 边界对齐，本轮 daily_reader_workflow 不进入 runtime conversion。
- **`/app/reader/{recordId}` 路由与 ReaderWorkbench 视觉**：D6 不在本轮替换；旧 route 继续承载 Ask / notes / highlights / dictionary / user asset 写入。理由：本轮 D6-A0 明确不处理 Plate Surface 视觉改造。
- **Ask supplement 旧 `/scene` merge 路径**：`merge_record_with_reader_ask_supplements` 暂时保留；Ask supplement 写入新路径 D6-A4 之后，旧 `/scene` merge 不再承担 Ask supplement 写入。理由：旧 `/scene` 仍有 library 与 command palette recent 等 consumer。
- **`render_scene_json` 在旧 Directus / Eval 观察面**：D6 不切；观察面切换必须在 cutover matrix 中单独评估。理由：观察面是隔离 spike，不属于 D6 product hardening 主路径。
- **`@target_sentence_id` 在 agent tool 内 alias**：D6 允许其作为内部 alias 存在，但禁止对外 DTO / persistence 出现。理由：避免一次大改引入回归。

### D6-A0 Static Guard 建议

本轮建议补的 static guard（不写新代码，只新增 guard 文件）：

- `apps/web/src/lib/reader-plate/bridges/ask/` 不引用 `targetKey` 之外对旧 `target_key` / `sentence_id` / `paragraph_id` 的 hardcoded 字符串（仅允许在 `adapters.ts` 的 `targetKey` 兼容层内部出现）。
- `services/api/app/services/reader_ask/service.py` 中任何新增文件不允许 `import render_scene`；现有 `load_render_scene` 调用逐步收口到 read-only legacy adapter。
- `services/api/app/schemas/{user_annotations,reader_notes,reader_ask,reader_scene,analysis}.py` 在 D6 schema 演进中必须保留 deprecated optional 字段；不允许直接删除 `sentence_id` / `paragraph_id` / `target_key` / `analysis_record_id` / `client_record_id` 字段。
- `apps/web/src/components/reader/{ReaderNotePanel,AnnotationGutter,SelectionToolbar,AiWorkspacePanel,ask-chat/*}.tsx` 暂不切 UI；本轮不允许把它们的 import / state / props 切到新 anchor schema。

### D6-A0 Done Criteria

- 本节矩阵、D6 最小分层、D6 最小实现顺序全部进入 tracked 正式文档。
- 没有改任何产品 runtime；没有接新 Ask；没有写新 API；没有改 `/app/reader-record/{recordId}` 视觉。
- 旧 `reader_ask.service`、`user_annotations`、`reader_notes`、`reader_scene` 行为保持不变；旧 ask supplement / annotation / note 写入路径仍走 `/app/reader/{recordId}`。
- 新 `user_editorial_assets.py` schema-only draft 与本节 D6 最小分层一致；`UserEditorialAssetAnchor` 字段就是 D6 anchor 写入的最小核心。
- D6-A1 / A2 / A3 后续任务以本节矩阵为起点；D6-A7 仍依赖 Plate Surface 视觉方案，不在本轮审计范围。

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
- D5 `grammar_note`, `sentence_analysis` and `vocabulary` default to skip `fallback_window` spans with rationale `boundary_low_fallback_window`, unless a boundary refiner/reviewer produces acceptable segments. Translation can still run on the parent unit even if some internal segments are low quality.

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
| `job_type` | `build_base`, `translate_unit`, `build_vocabulary_layer` |
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
- D5-V2 projects published vocabulary layers as `reader_vocabulary_marks` on stable source leaves during snapshot rebuild. The durable layer output remains `VocabularyLayerOutput`; Plate marks are projection payload, not truth.

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
  record: {
    title: string;
    created_at: string;
    source_type: string;
    source_metadata: Record<string, unknown>;
    product_state:
      | "processing"
      | "needs_confirmation"
      | "readable_enhancing"
      | "action_required"
      | "failed"
      | "deleted";
    readiness_state:
      | "submitted"
      | "candidate_base_ready"
      | "article_ready"
      | "initial_enhancement_ready"
      | "coverage_complete";
  };
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
      text_hash: string;
      hash_algorithm: "fnv1a32-utf16";
    }>;
  };
  anchor_segments: Array<{
    anchor_segment_id: string;
    sentence_id: string;
    paragraph_id: string;
    unit_id: string;
    order_index: number;
    unit_order_index: number;
    segment_type: "sentence" | "clause" | "fallback_window";
    boundary_quality?: "normal" | "low";
    base_start_utf16: number;
    base_end_utf16: number;
    unit_start_utf16: number;
    unit_end_utf16: number;
    text_hash: string;
    hash_algorithm: "fnv1a32-utf16";
  }>;
  enhancement_layers: Array<ReaderSnapshotLayer>;
  enhancement_progress: ReaderEnhancementProgress;
  ask_supplements: Array<ReaderSnapshotAskSupplement>;
  user_assets: Array<ReaderSnapshotUserAsset>;
  parsed_decisions: Array<ReaderSnapshotParsedDecision>;
  value: ReaderPlateValue;
};

type ReaderSnapshotLayer = {
  layer_id: string;
  layer_type: "translation" | "vocabulary" | "grammar_note" | "sentence_analysis" | string;
  layer_subtype?: string | null;
  owner: "system_ai";
  base_id: string;
  target_scope: "unit" | "anchor_segment" | "unit_range" | "record";
  target_key: string;
  status: "published";
  schema_version: number;
  output: unknown;
  published_at: string;
};

type ReaderEnhancementProgress = {
  overall_status:
    | "processing"
    | "readable_enhancing"
    | "ready"
    | "failed"
    | "action_required";
  layers: Array<ReaderEnhancementProgressLayer>;
};

type ReaderEnhancementProgressLayer = {
  capability: "translation" | "vocabulary" | "grammar";
  layer_type?: "translation" | "vocabulary" | "grammar_note" | "sentence_analysis" | null;
  status:
    | "not_started"
    | "queued"
    | "processing"
    | "succeeded"
    | "failed"
    | "action_required";
  job_status?:
    | "queued"
    | "claimed"
    | "retry_later"
    | "paused"
    | "skipped"
    | "succeeded"
    | "failed_terminal"
    | "cancelled"
    | "superseded"
    | null;
  job_type?: "translate_unit" | "build_vocabulary_layer" | "build_grammar_bundle" | string | null;
  layer_id?: string | null;
  job_id?: string | null;
  target_type?: "record" | "unit" | "anchor_segment" | "unit_range" | string | null;
  target_scope?: "unit" | "anchor_segment" | "unit_range" | "record" | null;
  target_key?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  failure_code?: string | null;
  failure_message?: string | null;
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
- `enhancement_progress` is a UI observability projection from current-base jobs and layers.
- `ask_supplements` is empty.
- `user_assets` is empty.
- `parsed_decisions` may be empty or contain translation parsed decisions.
- `value` contains Plate nodes for source and any published translation.

D5-V2 values:

- Published `vocabulary` layers remain present in top-level `enhancement_layers`.
- Snapshot `value` may include `reader_vocabulary_marks` on stable source leaves.
- A mark includes `mark_id`, `layer_id`, `item_type`, `anchor_segment_id`, unit-local `start_offset` / `end_offset`, `selected_text`, derived `segment_start_utf16` / `segment_end_utf16`, and `starts_here` / `ends_here` for split leaves.
- Web renders these marks read-only; it does not persist or replay raw Plate/Slate operations.

W3-C2 alignment additions:

- Snapshot top-level `record` is the minimum ReaderWorkbench shell metadata contract for title, created time, source metadata, current `product_state`, and current `readiness_state`.
- `/app/reader-record/{recordId}` may surface `product_state` as the primary reader-facing status and `readiness_state` as auxiliary milestone text after snapshot reloads.
- D6-P7A adds `enhancement_progress` so Reader UI can distinguish queued, processing, published and failed enhancement work. It is derived from `reading_records`, current-base/current-generation `reader_jobs`, and `enhancement_layers`; it is not a new source of truth and does not create new DB tables.
- `enhancement_progress.layers[*].capability` groups existing facts into `translation`, `vocabulary`, or `grammar`. Grammar jobs may have no single `layer_type`; published grammar outputs continue to use existing `grammar_note` and `sentence_analysis` layer types.
- `reader_jobs.status` maps to progress as follows: `queued` / `retry_later` / `paused` -> `queued`, `claimed` -> `processing`, `succeeded` / `skipped` -> `succeeded`, terminal/cancelled/superseded states -> `failed` unless the D6-P4 user-actionable policy classifies the condition as `action_required`.
- Published enhancement layers map to `succeeded`; draft layers map to `processing`; failed layers map to `failed` or `action_required` by the same D6-P4 rule.
- `record.product_state` remains the record-level product state. Snapshot progress must not rewrite `product_state`; a `failed_terminal` job can be visible in `enhancement_progress` while `record.product_state` remains `readable_enhancing` until the worker product-state update path decides otherwise.
- Snapshot top-level `anchor_segments` and `navigation.units[*].text_hash` are stable interaction anchors. Frontends must not infer them only from Plate tree shape.
- `enhancement_layers.owner`, `ask_supplements.owner` and `user_assets.owner` distinguish projection ownership. Only `enhancement_layers` uses `target_scope` / `target_key` as publish targeting; ask supplements and user assets continue to ground themselves through explicit anchors.
- `reading_goal` is intentionally not in `ReaderPlateSnapshot` yet. The new Reader orchestration domain does not have a first-class persisted `reading_goal` truth owner, so adding a nullable placeholder now would create false contract stability.
- `summary` / `semantic_outline` are intentionally not formalized as typed snapshot layer schemas in D5-W3-C2. `layer_type: string` keeps room for future experimentation, but production layer contracts must wait until owner, target scope, publish policy and ReaderWorkbench rendering shape are decided.

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
- Snapshot reload remains the source of truth for `record`, `base`, `navigation`, `anchor_segments`, published layers, ask supplements, user assets and parsed decisions. Future `projection_ops` must not become an alternate truth source for these facts.
- D4 minimal translation projection only covers published `translation` layers whose output validates as `TranslationLayerOutput` and whose target scope is `unit` or `anchor_segment`.
- D5-V2 vocabulary projection only covers published `vocabulary` layers whose output validates as `VocabularyLayerOutput`, whose layer target scope is `unit`, and whose item anchors belong to the current base/unit/anchor segment.
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

Product-state event contract:

- Worker-driven `product_state` changes publish `record_product_state_updated`.
- Event payload must include:
  - `product_state`
  - `reason_code`
  - `user_visible`
  - `attention_code` (nullable)
  - `stopped_reason`
  - `stopped_outcome` (nullable)
- D6-P1 minimal wiring publishes this event only when the `reading_records.product_state` update succeeds.
- When the runtime already owns an open write connection for the state change, the `product_state` update and `record_product_state_updated` event must commit in the same transaction.

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
