# Frontend Integration Status / Reason-code Map

Audience: frontend BFF / Web app developers integrating with Reader Orchestration.

This document is the canonical user-facing contract for status fields and
reason_code values returned by the Reader Orchestration HTTP surface.
It does NOT document internal worker status machines (those are private
implementation detail and may change without notice).

> Source-of-truth rule: every status field returned to the client is a
> closed enum. Unknown values are NOT forwarded — the boundary layer
> coerces them to a safe fallback (see "Coercion contract" below).

---

## Artifact Pipeline Status — `/reader/source-artifacts/{id}/pipeline-status`

`outcome` (Literal):
- `upload_pending` — user must upload the bytes (or `complete_upload`).
- `upload_available_not_submitted` — bytes are in OSS but not yet bound to a record.
- `extraction_queued` / `extraction_running` / `extraction_retry_later` — in flight; poll again.
- `extraction_failed` — terminal; show "上传的文件无法解析，请改用粘贴文本".
- `materialization_queued` / `materialization_running` / `materialization_retry_later` — in flight.
- `materialization_failed` — terminal; show "内部错误，请重试或联系支持".
- `stable_document_ready` — happy path; UI may `open_reader`.
- `candidate_document_required` — user must `confirm_candidate_document` first.
- `input_rejected_or_action_required` — show reason list from `suitability.reasons`.

`next_action` (Literal):
- `complete_upload` — call `POST .../complete-upload`.
- `submit_input` — call `POST .../submit-input`.
- `wait_for_worker` — UI shows skeleton + spinner; poll again.
- `retry_later` — UI shows "稍后重试"; poll again after `poll_interval_seconds`.
- `show_error` — surface `failure_message` (≤240 chars).
- `open_reader` — navigate to reader surface.
- `confirm_candidate_document` — open candidate editor.
- `revise_input` — back to input editor; show `suitability.flags`.

Reason codes (`failure_code` / `rationale_code`) are DEBUG ONLY. Frontend
MUST NOT render them to end users; ops dashboard can surface them.

---

## Article RAG Index Status — `/reader/records/{id}/article-rag-index/status`

`status` (Literal):
- `not_ready` — record still has no `article_ready`. UI may hide RAG panel.
- `not_indexed` — `article_ready` reached but no index exists yet.
- `queued` / `indexing` — worker running; poll.
- `indexed` — happy path.
- `failed` — terminal. UI shows "RAG 索引失败，可重试"; reason_code is debug-only.
- `superseded_or_stale` — index was superseded by a newer generation; UI hides RAG panel for stale data only.
- `unavailable` — provider missing / unreachable; UI hides RAG panel; do NOT show "broken".

`article_ready` event payload's `article_rag_index` block is INFORMATIONAL
ONLY. It MUST NOT block reading UI rendering. RAG indexing runs in parallel
to reading.

---

## Ask Article RAG Sidecar — `ReaderAskUserVisibleOutput.article_rag.status`

`status` (Literal):
- `available` — provider returned chunks; show citation list.
- `empty` — provider reachable, returned 0 chunks; show "没有找到相关引用".
- `not_indexed_or_unavailable` — index missing OR provider down; hide RAG UI.
- `composer_rejected` — shape rejected; hide RAG UI; logs explain.
- `disabled` — feature flag off; hide RAG UI; this is expected in dev.
- `stale_due_to_repair` — repair branch ran; previous citations cleared.
  Frontend should show "上次回答触发了修复，引用的支持片段已重置" rather than
  treating this as a RAG-system failure.

`failure_code` / `retryable` / `fallback_allowed` are debug-only.

---

## Truth-source rule (binding)

Frontend MUST source citation truth from:
1. Postgres `reading_records` / `reading_bases` / `anchor_segments` /
   `stable_document_blocks` (served via `/records/{id}/snapshot`,
   `/records/{id}/stable-document`, `/records/{id}/events`).
2. Ask sidecar `article_rag.citations[*].chunk_id` — these are pointers to
   the Postgres truth; the frontend looks up the chunk text + anchor via
   `GET /records/{id}/stable-document` and `base.text`.

Frontend MUST NOT source citation truth from:
- Plate JSON, Slate path, DOM selection.
- Markdown source text (`original_inputs.source_text`).
- Vector payload (anything from Zilliz). Vector payloads may contain a
  `chunk_id` pointer; they never contain authoritative chunk text.
- UI display grouping (paragraph color, heading style, etc.).

---

## Coercion contract (boundary)

When the boundary layer receives an unknown `status` value (regression,
hostile fake, schema drift) it coerces to:
- Article RAG sidecar → `not_indexed_or_unavailable`
- Article RAG index status → `unavailable`
- Artifact pipeline outcome → `extraction_failed`

Coerced responses include `failure_code="article_rag_status_coerced"` (or
the pipeline equivalent) and a server-side warning log.

---

## Polling recommendations

- Events stream: `GET /records/{id}/events?after_sequence=X` at 2-3s.
- Pipeline status (file upload only): 3s until terminal outcome.
- RAG index status: 5-10s until `indexed` / `failed` / `unavailable`.
- Ask stream: SSE; never poll.
