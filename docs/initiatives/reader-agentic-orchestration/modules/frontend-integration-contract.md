# Reader Frontend Integration Contract

Status: frozen baseline for the first UI/UX integration pass.

Audience: `apps/web` BFF and Reader UI agents.

This contract describes the backend surface that is ready for frontend
integration. It is intentionally limited to business-facing HTTP routes,
client-visible status values, DTO fields, polling recommendations, and truth
source rules. Internal worker/job state machines are not a frontend contract.

## Integration Scope

The frontend may integrate these flows now:

- Text / Markdown input submission and deterministic stable-ready freeze.
- OSS-backed source artifact upload, extraction, materialization, and status.
- Candidate document confirmation.
- Stable Document facts for Plate-native projection.
- Article RAG index status / ensure.
- Ask Article RAG sidecar rendering.

The frontend must remain tolerant of fail-soft states. RAG and extraction
failures must not block reading the article when `article_ready` is already
available.

## Input And Artifact Routes

### `POST /reader/records/input`

Primary input entry. Request fields:

- `source_type`
- `text`
- `filename`
- `source_metadata`
- `client_record_id`
- `language`

Response is a typed union by `outcome`:

- `stable_document_ready`
- `candidate_document_required`
- `input_rejected_or_action_required`

Client behavior:

- `stable_document_ready`: open the reader from the returned snapshot.
- `candidate_document_required`: show review/confirm UI.
- `input_rejected_or_action_required`: show suitability reasons and let the
  user revise input.

### `POST /reader/source-artifacts/init-upload`

Initializes OSS object metadata. Request fields:

- `artifact_kind` (`original_upload` only)
- `source_filename`
- `content_type`
- `byte_size`
- `content_sha256`
- `reading_record_id`
- `original_input_id`
- `source_refs`
- `metadata`
- `quality`

Response includes:

- `artifact_id`
- `bucket`
- `endpoint`
- `object_key`
- `status`
- `upload_method`
- `headers`
- `presigned_url`
- `presigned_method`
- `presigned_expires_at`

The response never includes an AccessKey secret. A presigned URL may include
the AccessKey id in the query string.

### `POST /reader/source-artifacts/{artifact_id}/complete-upload`

Marks a pending OSS upload as available. The call is idempotent only when the
completion payload matches the already-completed artifact.

### `POST /reader/source-artifacts/{artifact_id}/submit-input`

Binds an available `original_upload` artifact into a Reader input shell and
enqueues extraction. Response includes:

- `reading_record_id`
- `original_input_id`
- `artifact_id`
- `record_generation`
- `source_type`
- `input_type`
- `product_state`
- `readiness_state`
- `extraction_required`
- object metadata
- `extraction_job_id`
- `extraction_job_status`

### `GET /reader/source-artifacts/{artifact_id}/pipeline-status`

Read-only status query for uploaded artifacts.

Client-visible `outcome` values:

- `upload_pending`
- `upload_available_not_submitted`
- `extraction_queued`
- `extraction_running`
- `extraction_retry_later`
- `extraction_failed`
- `materialization_queued`
- `materialization_running`
- `materialization_retry_later`
- `materialization_failed`
- `stable_document_ready`
- `candidate_document_required`
- `input_rejected_or_action_required`

Client-visible `next_action` values:

- `complete_upload`
- `submit_input`
- `wait_for_worker`
- `retry_later`
- `show_error`
- `open_reader`
- `confirm_candidate_document`
- `revise_input`

Polling recommendation: every 3 seconds until a terminal `outcome`.

## Candidate Confirmation

### `POST /reader/records/{record_id}/candidate-documents/{candidate_document_id}/confirm`

Confirms a candidate document and returns a loaded `ReaderPlateSnapshot`.

Client behavior:

- On success, navigate to the reader view using the returned snapshot.
- On `409`, show a retry/reload message. The candidate may already be
  confirmed, stale, rejected, or superseded.

## Stable Document Projection

### `GET /reader/records/{record_id}/stable-document`

Loads the active stable document facts for Plate projection.

Response top-level fields:

- `reading_record_id`
- `record_generation`
- `active_base_id`
- `base`
- `stable_document`
- `blocks`
- `anchor_segments`

`base` fields:

- `base_id`
- `content_sha256`
- `content_utf16_length`
- `canonicalizer_version`
- `builder_version`
- `segmenter_version`
- `language`
- `title_snapshot`
- `navigation`
- `text`

`blocks[*]` fields:

- `block_id`
- `parent_block_id`
- `order_index`
- `block_type`
- `text_content`
- `payload`
- `source_refs`
- `quality`
- `canonical_text_start_utf16`
- `canonical_text_end_utf16`
- `interpretation_policy`

`anchor_segments[*]` fields:

- `anchor_segment_id`
- `unit_id`
- `order_index`
- `segment_type`
- `base_start_utf16`
- `base_end_utf16`
- `text_hash`

Plate projection rule:

- Plate JSON is display state only.
- `base.text`, `blocks[*].text_content`, `canonical_text_*`, and
  `anchor_segments` are the source of citation and anchor truth.
- The frontend may group `list_item` blocks into visual `ul` / `ol`
  containers, but list grouping is UI projection, not truth.

## Snapshot And Events

### `GET /reader/records/{record_id}/snapshot`

Loads the current `ReaderPlateSnapshot`. Use this for the initial reader
screen and for post-confirm / post-submit navigation.

### `GET /reader/records/{record_id}/events?after_sequence=...`

Polls committed reader events. Polling recommendation: every 2-3 seconds while
an input or article-ready workflow is active.

## Article RAG Index

### `GET /reader/records/{record_id}/article-rag-index/status`

Read-only status query. Client-visible `status` values:

- `not_ready`
- `not_indexed`
- `queued`
- `indexing`
- `indexed`
- `failed`
- `superseded_or_stale`
- `unavailable`

Client behavior:

- `indexed`: Ask may show "article context available".
- `queued` / `indexing`: show subtle background progress if useful.
- `failed`: show a retry affordance only in diagnostics or settings UI.
- `not_ready`, `not_indexed`, `superseded_or_stale`, `unavailable`: hide RAG
  affordances or show passive unavailable copy. Do not block reading.

Polling recommendation: every 5-10 seconds until `indexed`, `failed`,
`superseded_or_stale`, or `unavailable`.

### `POST /reader/records/{record_id}/article-rag-index/ensure`

Request fields:

- `expected_generation` (required, `>= 1`)
- `index_version` (optional)

The request body does not accept `user_id`, `chunker_version`, provider config,
or vector config.

Response `status` values:

- `enqueued`
- `idempotent_noop`
- `record_not_found`
- `generation_mismatch`
- `not_ready`
- `no_active_base`
- `plan_hash_mismatch`
- `bootstrap_inconsistent`
- `error`

Client behavior:

- `enqueued` / `idempotent_noop`: poll status route.
- `generation_mismatch`: reload record.
- `not_ready` / `no_active_base`: hide RAG affordance until article is ready.
- `plan_hash_mismatch` / `bootstrap_inconsistent` / `error`: show diagnostic
  retry only in developer/ops UI.

## Ask Article RAG Sidecar

Ask responses may include:

- `article_rag`
- `article_rag_citations` (legacy compatibility)

`article_rag.status` values:

- `available`
- `empty`
- `not_indexed_or_unavailable`
- `composer_rejected`
- `disabled`
- `stale_due_to_repair`

`article_rag.citations[*]` shape:

- `context_id`
- `chunk_id`
- `citation`

`citation` is the 9-key truth pointer:

- `reading_record_id`
- `stable_document_id`
- `base_id`
- `record_generation`
- `block_ids`
- `unit_ids`
- `anchor_segment_ids`
- `canonical_text_start_utf16`
- `canonical_text_end_utf16`

Client behavior:

- `available`: show citations and allow navigation/highlight against the
  stable document truth.
- `empty`: optional quiet copy, e.g. "No relevant article citation found."
- `not_indexed_or_unavailable`, `composer_rejected`, `disabled`: hide RAG
  citation UI and use normal Ask output.
- `stale_due_to_repair`: do not show stale citations. Suggested copy:
  "This answer was repaired, so previous article citations were cleared."

## Forbidden Truth Sources

The frontend must never use these as citation or anchor truth:

- Plate JSON.
- Slate path.
- DOM selection.
- UI display group.
- Markdown syntax.
- `original_inputs.source_text`.
- Vector payload from Zilliz.

Vector results may carry `chunk_id` pointers only. Citation text and offsets
must be resolved through Postgres-backed stable document facts.

## Boundary Tolerance

Frontend code must treat any unknown status as a safe unavailable state:

- Artifact pipeline: show a generic pipeline failure / retry message.
- Article RAG index: treat as `unavailable`.
- Ask `article_rag`: treat as `not_indexed_or_unavailable`.

Do not render raw `failure_code`, `reason_code`, exception text, provider URI,
tokens, query text, or chunk text from diagnostics. Those fields are for logs
and developer diagnostics only.
