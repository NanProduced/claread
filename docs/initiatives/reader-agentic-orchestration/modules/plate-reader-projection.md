# Plate Reader Projection

> 状态：`D1 草案`
> 最后更新：2026-06-18
> 范围：Web Reader Article Body 的 Plate.js 文档投影、projection operations、document tools、owner 权限和 anchor bridge。

## 目标

Reader Article Body 使用 Plate.js 作为富文本文档和交互底座。

Plate 负责：

- 渲染 Stable Base 原文、译文、AI 批注、Ask Supplement、用户高亮和笔记。
- 承载选区、点词查询、评论、高亮和 Ask 文档工具入口。
- 渐进式接收 domain facts 的 projection operations。

Plate 不负责：

- 充当后端事实源。
- 决定 parsed coverage。
- 绕过 Layer Publisher 写 Enhancement Layer。
- 绕过用户确认写 User Editorial Assets。
- 持久化 raw node path / raw Slate path operation。

## 核心原则

后端 truth 保持 domain-first：

```text
Stable Reading Base
Reading Units / Anchor Segments
Enhancement Layers
User Editorial Assets
Ask Supplements
Parsed Decisions
```

Plate document 是这些事实的 Web projection。刷新、断线恢复、重新投影和非 Web 客户端都不能依赖 Plate document 作为唯一事实。

## D4 Base Plate Snapshot

D4 正式路径：

```text
Stable Reading Base
-> Reading Units
-> Anchor Segments
-> Base Plate Snapshot
-> Plate Reader Surface
```

Base Plate Snapshot 至少包含：

- Stable Base metadata。
- Reading Unit nodes。
- Anchor Segment nodes，携带 `unit_id`、`anchor_segment_id`、base absolute offsets、segment text hash。
- Navigation Skeleton 所需 metadata。

D4 可以用 full snapshot reload 承接第一批 translation layer。D5 再验证增量 projection operations。

旧 `renderSceneToPlateDocument` 只能作为迁移参考或 spike adapter。新 D4 contract 不经过旧 `render_scene_json`。

### Snapshot DTO Seed

API 返回 wrapper DTO，而不是把 Claread metadata 强塞进 Plate root：

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
```

`value` 是 Plate 可消费的 document value；`ReaderPlateSnapshot` 本身不是业务 truth。

开发期不创建 `ReaderPlateSnapshotV1` / `ReaderPlateSnapshotV2` 类型。未来如需要兼容转换，版本化类型只能存在于 serializer / adapter 边界，不能泄漏到 orchestration 核心逻辑。

D4 translation 可通过 snapshot reload 或 simple projection refresh 呈现。`enhancement_layers` 在 D4 至少包含已发布的 translation layer；D4-P2 起 snapshot reload 可包含最小 `parsed_decisions`。`ask_supplements` 和 `user_assets` 仍可以为空数组。

D4 snapshot 不暴露 `projection_version`。客户端恢复 cursor 只使用 `last_event_sequence`；它表示 snapshot 序列化时同一次一致性读取到的 max committed Reader Event sequence。D5 如启用 projection cache 或增量 applier，再单独加入非 cursor 的 projection metadata。

Snapshot wrapper 使用 `schema_kind`，不是 `schema_version`。`schema_version` 只保留给 layer output、fragment 等 serialized boundary payload。

D4 Web slice：

- Web BFF 通过 `/api/web/reader-plate/*` 调用后端 Reader API，不回退到旧 `/scene`。
- Web Reader 只读 surface 使用 `ReaderPlateSnapshot.value` 渲染 article body。
- D4 polling 收到 `layer_published`、`projection_reset_required` 或 server reload signal 后 reload snapshot；不应用 `projection_ops`。
- 用户可见页面不暴露 Plate/Slate path、event cursor、sequence 或 snapshot internals。
- BFF unit tests 覆盖 Reader Plate auth/error mapping；mock phone / anonymous session 不允许提交。
- Browser smoke 可以使用 mocked BFF routes 验证 read-only surface、source text、translation 和 caught-up polling，但它不等价于真实 authenticated backend E2E。

### Base Node Seed

D4 最小 schema 使用三个 source node：

| Node | 角色 | 关键字段 |
|---|---|---|
| `reader_unit` | top-level block，承载 Unit 渲染、导航、translation target | `owner=stable`、`unit_id`、order、base offsets、unit hash |
| `reader_source_block` | unit 内原文容器 | `owner=stable`、`unit_id`、base offsets |
| `reader_anchor_segment` | inline element，承载 sentence-like anchor | `owner=stable`、`anchor_segment_id`、兼容 `sentence_id`、`segment_type`、base offsets、text hash |

Stable source leaf 必须携带 `owner=stable`、`lock_source=true`、base offsets 和可选 segment-local offsets。相邻 Anchor Segments 之间的空白必须作为 stable separator leaf 保留，不能静默丢失。

`sentence_id` 只作为兼容 alias。新代码 target、projection op、Ask tool、RAG citation 和 user asset 均应优先使用 `anchor_segment_id`。

## Projection Operation Contract

`reader_events` 支持 `event_type = projection_ops`。Projection op 使用稳定 domain target。

示例：

```json
{
  "event_type": "projection_ops",
  "payload": {
    "base_id": "base_...",
    "projection_version": 12,
    "ops": [
      {
        "op_id": "op_...",
        "op_type": "upsert_translation_node",
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
}
```

Allowed `op_type` seed list:

| op_type | Target | Owner | Use |
|---|---|---|---|
| `upsert_translation_node` | `unit_id` or `anchor_segment_id` + `layer_id` | `system_ai` | Insert or replace unit/segment translation. |
| `add_ai_mark` | anchor + `layer_id` | `system_ai` | Add vocabulary or grammar inline mark. |
| `upsert_ai_note_node` | anchor + `layer_id` | `system_ai` | Insert grammar note or sentence analysis block. |
| `upsert_ask_supplement_node` | anchor + `supplement_id` | `ask_supplement` | Insert Ask-confirmed supplement. |
| `upsert_user_highlight` | anchor + `asset_id` | `user` | Insert or update user highlight projection. |
| `upsert_user_note` | anchor + `asset_id` | `user` | Insert or update user note projection. |
| `remove_projection_node` | `layer_id` / `supplement_id` / `asset_id` | owner-specific | Remove projection for hidden/dismissed/deleted item. |

Raw Slate operations may be generated inside the frontend applier, but they are not part of the durable API.

## Projection Applier

The frontend applier:

1. Receives snapshot first.
2. Builds `anchor_segment_id -> Plate path` and `unit_id -> Plate path` caches.
3. Applies `projection_ops` in event sequence order.
4. Resolves each op target to the current Plate path.
5. Runs owner policy checks.
6. Converts `fragment` to allowed Plate nodes / marks.
7. Applies Plate transforms.
8. Invalidates path caches after structural edits.

If an op target cannot be resolved, the client must reload snapshot rather than guessing a path.

## Owner Policy

| Owner | Content | User Action | System / AI Action |
|---|---|---|---|
| `stable` | Original source content | select, lookup, highlight, note; no edit/delete | no edit/delete; supersede record for source fix |
| `system_ai` | Translation and system AI annotation layers | hide, collapse, feedback; no direct edit/delete of truth | versioned replace through Layer Publisher |
| `ask_supplement` | Ask-confirmed AI supplement | delete/dismiss display; audit remains | append/revise via Ask tools |
| `user` | Highlight, comment, note, saved Ask note/highlight | edit/delete | AI can propose only; cannot overwrite |
| `ephemeral` | selection focus, transient citation, pending suggestion | close/reject | not durable |

The backend is authoritative. Frontend policy exists to prevent confusing interactions and early-block invalid commands.

## Anchor Bridge

Durable anchors remain domain anchors:

```json
{
  "base_id": "base_...",
  "unit_id": "u1",
  "anchor_segment_id": "s3",
  "offset_unit": "utf16",
  "start_offset": 10,
  "end_offset": 24,
  "selected_text": "selected words",
  "text_hash": "1a2b3c4d",
  "hash_algorithm": "fnv1a32-utf16"
}
```

Plate path is only a transient rendering address.

Required adapters:

- `unitIdToPath(editor, unitId)`
- `anchorSegmentIdToPath(editor, anchorSegmentId)`
- `pathToAnchorSegment(editor, path)`
- `selectionToDomainAnchor(editor, selection)`

`selectionToDomainAnchor` must compute UTF-16 offsets and `fnv1a32-utf16` hash, then the backend validates selected text and hash before writing facts.

## AI And Markdown

AI workers and Ask tools must not output arbitrary Plate JSON.

Allowed output forms:

- typed layer result
- document tool call
- sanitized Markdown fragment
- sanitized Plate fragment that matches an allowlisted schema

Markdown or Plate fragments must pass:

- typed schema validation before projection
- strict node/mark allowlist
- length limits by fragment type
- no raw scriptable HTML
- link protocol allowlist
- anchor/source grounding validation

Provider or Plate plugin AI features may be used only after D2 validates license and API availability. The architecture cannot depend on unverified commercial plugins.

D5 default allowlist:

| Feature | Policy |
|---|---|
| paragraph / heading / list / code block / inline code / blockquote / strong / em / text | allowed |
| link | allowed only with `http:` / `https:` / `mailto:` and non-private host |
| image / table / inline HTML / math / frontmatter / definition / footnote | denied |

Implementation should prefer `allowedNodes` plus fine-grained `allowNode`; do not rely on broad defaults. `allowedNodes` and `disallowedNodes` should not both be treated as the primary safety mechanism.

LLM output must not be persisted as arbitrary Plate JSON. Workers store typed layer results or sanitized fragment payloads that can be regenerated from domain facts.

## Ask Document Tools

Ask uses document tools to read and propose changes:

| Tool | Writes Domain Fact | Confirmation |
|---|---|---|
| `read_range` | No | Not required |
| `propose_highlight` | User Editorial Asset | Required |
| `propose_note` | User Editorial Asset | Required |
| `write_ai_supplement` | Ask Supplement | Required unless explicitly pre-authorized |
| `revise_ai_annotation` | Ask Supplement revision or System Annotation revision proposal | Required by policy for user-visible overwrite |

Ask cannot directly edit Stable Base or overwrite User Editorial Assets.

Ask cannot directly overwrite System Annotation Layer truth. If Ask identifies a bad system annotation, it may create a revision proposal or an Ask Supplement correction; a system worker / Layer Publisher path owns versioned replacement.

## Snapshot And Recovery

Recovery flow:

```text
GET Reader snapshot
-> render Base Plate Snapshot + latest domain facts
-> subscribe events after last_event_sequence
-> apply projection_ops in sequence
-> reload snapshot on gap, unresolved target, hash mismatch, or policy violation
```

`reader_events` remain at-least-once. The frontend dedupes by event id and op id.

## D2 Spike Requirements

D2 Plate spikes must verify:

- Plate dependency, license, and API availability.
- Base Plate Snapshot without old `render_scene_json`.
- domain-targeted projection ops and replay.
- selection -> domain anchor round trip.
- owner policy enforcement.
- long document performance.
- Markdown / Plate fragment sanitize policy.
- Ask document tools with user confirmation.
