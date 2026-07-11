# Adaptive Reader Orchestration Design

> Status: formal design draft
> Last updated: 2026-07-11 (T4.2a-V1: real-LLM Contract / Output Integrity / sample-level Semantic Quality passed; Cost/Latency baseline partial; Page UX blocked)
> Scope: Reader enhancement execution strategy, quality/cost control, progressive publishing, and longform handling.

This document consolidates the previous analysis-window design notes and temporary research reports into one business-facing architecture document. Temporary development labels are intentionally removed; future work should use the terminology in this document.

## 1. Problem Statement

Reader enhancement must produce useful translation, vocabulary, grammar notes, and sentence analysis for English reading records. The current implementation exposed two opposite failure modes:

- A short article can be over-orchestrated: many unit-level jobs, high token cost, slow completion, repeated page refreshes, and too few high-value grammar annotations.
- A long or very long document cannot safely rely on one whole-document call: context quality drops, schema failures become more likely, and eager full-document annotation wastes cost when the user only reads part of the document.

The architecture needs adaptive execution. The system should choose the simplest execution path that can produce stable, high-quality output for the document shape and reading goal.

## 2. Goals

- Preserve Stable Reading Base, Reading Units, Anchor Segments, Reader Jobs, Enhancement Layers, and publish-fence contracts.
- Recover short-article speed, cost, and quality.
- Avoid per-unit fan-out when batch or grouped processing is enough.
- Support medium and long articles through grouped/windowed execution.
- Support very long documents through outline/section/lazy enhancement.
- Publish results in a stable, reading-order-oriented way.
- Provide enough observability to compare quality, cost, latency, and user experience.

## 3. Non-Goals

- Do not expose technical strategy names to users.
- Do not stream raw LLM tokens for grammar, vocabulary, translation, or sentence-analysis annotations.
- Do not delete old AI Workflow code until comparison and fallback decisions are complete.
- Do not make one large implementation change that combines short-form recovery, full planner, SSE, patch merge, and longform execution.
- Do not let an LLM directly choose the execution strategy.

## 4. Core Concepts

### 4.1 Stable Reading Base

The stable base remains the canonical text substrate:

- `reading_bases`
- `reading_units`
- `anchor_segments`
- `stable_document_blocks`

Execution strategy changes must not alter the stable base for the same source text. Different reading goals or variants may change prompts, budgets, and density policies, but they must not produce different stable anchors for the same record generation.

### 4.2 Execution Strategy

An execution strategy is an internal runtime choice for how enhancement layers are generated and published. It is not a user-facing mode.

The main strategy families are:

| Strategy | Use case | Computation shape | Publish shape |
|---|---|---|---|
| Short article batch | Short, single-genre article | Whole-article batch per layer | Layer-level progressive, translation first |
| Structured article batch | Medium article that still fits safely in context | Whole-article batch per layer with structure hints | Layer-level progressive |
| Windowed article | Medium/long article where batch risks schema or grounding failure | Window/group calls with target/context anchors | Reading-order-oriented group/window release |
| Section-oriented longform | Long document with natural sections | Section-level grouped/windowed execution | Current/early sections first |
| Selective longform | Very long document or user reads only part | Outline/metadata first, lazy section enhancement | Current section or requested region first |

Implementation checkpoint as of 2026-07-10:

- Translation has short-article batch jobs and non-short grouped/windowed
  `translate_article` jobs. It must keep the Translation Group contract:
  batch/window compute cannot collapse display into one sentence, one anchor,
  or one whole unit.
- Deterministic three-state routing (`short_batch` / `structured_batch` /
  `grouped_windowed`) is now implemented, and `STRUCTURED_BATCH` is an
  auditable runtime mode (T4.1b): it carries a distinct
  `operation_fingerprint` base (`*_structured_v1`) and `policy_version`
  (`*_structured_bootstrap_v1`) from `SHORT_BATCH`, records
  `article_route` + `document_features` on every batch/window job, and a
  route change (short -> structured on a rebuilt base) triggers
  `_supersede_stale_fingerprint_jobs`. `SHORT_BATCH` and `GROUPED_WINDOWED`
  keep their shared `*_v1` fingerprint base to preserve their idempotency
  contracts; the three-way distinction is completed by `article_route` in
  `input_json`.
- Grammar has a compact batch path for `SHORT_BATCH` / `STRUCTURED_BATCH`
  articles (T4.1c): a single `build_grammar_bundle` / `unit_range` batch
  job covers all unpublished units in one LLM call; the batch publisher
  splits the output back into per-unit `grammar_note` /
  `sentence_analysis` layers. Route-specific fingerprints
  (`grammar_bundle_article_v1` for short,
  `grammar_bundle_article_structured_v1` for structured) maintain
  idempotency and auditability. `GROUPED_WINDOWED` keeps the existing
  Z+ analysis-window / window-publisher contract unchanged. Worker
  prompt / budget / release policy for structured batch grammar still
  reuses the short-batch compact batch path; giving structured batch
  its own grammar budget/prompt is later work.
- Grammar also has a windowed implementation path (for `GROUPED_WINDOWED`)
  with diagnostics for raw candidates, selector decisions, budgets, and
  failure/no-op causes. A RECORD_DENSITY denominator bug has been fixed,
  but quantity and quality tuning remain open evaluation work.
- Vocabulary has short-article batch jobs, non-short grouped jobs, duplicate
  highlight policy, and conservative phrase_gloss guards. Full cross-window /
  whole-record dedup is not claimed.
- This is not yet a complete document-level short / long / very-long strategy
  planner.
- Section-oriented longform, selective longform, and semantic outline are
  design targets only. They must not be described to implementation agents as
  already available runtime modes.
- "Batch computation" is a worker/cost strategy. It must not rewrite layer
  display semantics. In particular, the translation layer remains
  group-native: a Translation Group is a semantic reading group inside a
  Reading Unit, not mechanically one sentence, one anchor segment, or one
  whole unit.
- Acceptance harness and observability closure (T4.2a-R1, 2026-07-10): the
  smoke/acceptance harness now injects `DevFakeGrammarBatchExecutor` and
  `DevFakeGrammarWindowExecutor` so that `enable_zplus_grammar=True` never
  accidentally reaches a real LLM in fake mode; the acceptance path covers
  the production `WorkerLoop` + `CompletionFinalizer` topology and verifies
  `coverage_complete` rather than only calling the pipeline runner; grammar
  batch `ai_usage_events` now carry `execution.usage_data` so usage-event
  tokens match `reader_runtime_spans` tokens; production
  translation/vocabulary batch claim and publisher now accept both
  SHORT_BATCH and STRUCTURED_BATCH fingerprints (no test-local wrappers);
  and fixed-coverage tests pin `SHORT_BATCH` / `STRUCTURED_BATCH` /
  `GROUPED_WINDOWED` across route / fingerprint / policy, job topology,
  effective calls, layer counts (including GROUPED_WINDOWED
  `sentence_analysis`), final readiness and usage attribution. The bounded
  LLM document profiler (T4.2) is intentionally deferred this round.
- **T4.2a-V1 has completed the first gated real-LLM DB/runtime
  validation.** Four records covered `SHORT_BATCH` (two),
  `STRUCTURED_BATCH` (one), and `GROUPED_WINDOWED` (one), totaling
  34 effective calls, 142,990 input tokens, 55,051 output tokens, and
  198,041 total tokens. Contract, Output Integrity, and sample-level
  Semantic Quality gates passed. Page UX remains blocked by the local
  authentication/service window, so V1 is not closed. Reliable provider
  billing and user-perceived latency remain unavailable; the run establishes
  a baseline, not a proven cost or latency reduction.
- Execution budget and cutover safety (T4.2a-R2, 2026-07-10; **T4.2a-R2-R3a
  代码级 review 通过 / deterministic acceptance complete; normal-path
  production topology exercised by T4.2a-V1**): a durable
  `ExecutionBudget` enforces a hard cost ceiling per route / layer /
  record, surviving across multiple `runner.run()` and WorkerLoop ticks.
  The budget is rebuilt at each `runner.run()` entry via
  `ExecutionBudget.load_durable()` from `reader_jobs` aggregating
  `SUM(attempt_count)` / `MAX(max_attempts)` per
  `(reading_record_id, base_id, expected_generation, budget_layer)`.
  `planned_calls` come from `max(bootstrap.job_counts,
  actual_non_terminal_counts)` per layer (translation / vocabulary /
  grammar; `display_title` is excluded). The ceiling is
  `max_effective_calls = planned_calls * max_multiplier` with the
  default `max_multiplier=3` aligning with the production
  `max_attempts=3` (the original `*2` ceiling was inconsistent with
  `max_attempts=3` and has been corrected). The durable budget provides
  a deterministic ceiling and observability/scheduling — **it does not
  by itself reduce the existing retry cost**; whether to lower
  `max_attempts` from 3 to 2 for specific routes/jobs is a separate
  data-driven decision. Only `BUDGET_CONSUMING_OUTCOMES` (`succeeded`,
  `retry_later`, `failed_terminal`) consume budget; `superseded` /
  `no_job` / `skipped` / `budget_denied` do not. `attempt_count` is
  incremented atomically at claim time (before the LLM call), so a
  claim that crashes before the LLM still counts — this is an
  intentional conservative bias. When a layer's budget is exhausted,
  the pipeline stops dispatching workers for that layer, records a
  `budget_denied` outcome (not a plain `no_job`), and reports
  `stopped_reason = budget_exhausted` (all layers exhausted) or
  `partial_budget_exhausted` (some layers exhausted while others
  continue). No new migration was added; the durable budget reuses
  existing `reader_jobs.attempt_count` / `max_attempts`.
- Fingerprint model (T4.2a-R2-R2 corrected, **Approach B: conservative
  sorted fingerprint set**): existing `reader_jobs` schema fields cannot
  reliably determine a single active fingerprint. `load_durable()` no
  longer uses non-deterministic last-wins active fingerprint; instead it
  returns a sorted `non_superseded_fingerprints` set per layer. The
  budget conservatively aggregates `attempt_count` across all
  non-superseded fingerprints. SQL adds `ORDER BY operation_fingerprint
  ASC` so repeated queries are stable. `to_diagnostics()` exposes the
  fingerprint set per layer. No new schema/migration. If a unique active
  fingerprint is needed in the future, a minimal schema change must be
  designed and approved first.
- Fallback guardrail (T4.2a-R2-R1 corrected, fail-closed; T4.2a-R2-R2
  closes legacy job lifecycle): the per-unit grammar fallback is no
  longer gated on "batch job is terminal."
  `_should_suppress_grammar_per_unit_fallback` implements an explicit
  decision table: per-unit fallback is allowed only when the batch job
  is `superseded` or no batch job exists; `succeeded`, `failed_terminal`,
  `skipped`, and any non-terminal batch status all suppress fallback.
  This round is fail-closed: a `failed_terminal` batch does not
  implicitly run legacy per-unit jobs unless a future explicit fallback
  policy authorizes new fallback jobs. **T4.2a-R2-R2 adds formal
  terminal state for suppressed legacy jobs**: `_cleanup_suppressed_grammar_legacy_jobs()`
  runs once per `runner.run()` after bootstrap, calling
  `repository.supersede_conflicting_legacy_grammar_jobs()` to formally
  supersede non-superseded `build_grammar_bundle/unit` legacy jobs under
  the same base/generation, writing `reader_job_events`
  (event_type=`job_superseded`) with rationale codes:
  `batch_path_authoritative` (batch succeeded),
  `batch_fallback_not_authorized` (batch failed_terminal),
  `stale_legacy_topology` (batch superseded/skipped/stale). No
  permanently-queued/retry_later legacy job remains; WorkerLoop scanner
  no longer hot-loops on suppressed records. **T4.2a-R2-R3 wraps
  `_cleanup_suppressed_grammar_legacy_jobs()` in an explicit
  `async with conn.transaction():` block** so the `SELECT FOR UPDATE`
  lock covers the entire cleanup (read + supersede + event write),
  preventing concurrent `runner.run()` invocations from racing on the
  same legacy jobs.
- Route flip fencing: when the article route changes (e.g. short -> structured
  on a rebuilt base), the bootstrap supersede path (existing) is augmented
  by claim-time and publish-time `_validate_fence` →
  `_check_route_consistency`. The check compares
  `reader_jobs.input_json.article_route` against
  `reader_runs.envelope_json.article_route` (filtered to runs that carry
  `article_route`). A mismatch returns `stale_route_fingerprint` and
  rejects both claim and publish. All six layer publisher methods call
  the same `_validate_fence`, so stale-fingerprint results cannot be
  published. **T4.2a-R2-R2 unifies publish fence state consistency**:
  when a publisher raises `FenceViolationError`, the worker/service
  layer (which holds the claim/lease_token) calls
  `ReaderJobRuntime.transition(job_id, target_status="superseded",
  rationale_code="publish_fence_failed")` and marks the run superseded.
  Pipeline summary counts only real DB superseded transitions — **all
  `max(1, superseded_jobs)` virtual reporting removed**, replaced with
  `max(0, ...)`. translation/vocabulary/grammar unit/batch/window paths
  all share this contract. **T4.2a-R2-R3 clarifies the transition
  ownership**: the worker layer (`translation_worker` /
  `vocabulary_worker` / `grammar_worker`) already performs the real
  `transition(..., target_status="superseded",
  rationale_code="publish_fence_failed")` in its own
  `except FenceViolationError` handler before re-raising. The
  `pipeline_runner` handler only counts the DB-actual superseded delta
  (it does not perform a second transition).
- Budget exhaustion and final readiness: `budget_exhausted` and
  `partial_budget_exhausted` are both finalizable stopped reasons (only
  `attention_required` is non-finalizable). The completion finalizer
  reads durable state (terminal job counts from the repository), not the
  in-memory budget. **T4.2a-R2-R2 corrects partial exhaustion layer
  semantics**: the finalizer force-fails **only the exhausted layers'
  job types** (via `BUDGET_LAYER_TO_JOB_TYPES` mapping), not
  indiscriminately across `ENHANCEMENT_PIPELINE_JOB_TYPES`.
  `display_title` is not a budget layer and is never force-failed by
  budget exhaustion. Non-exhausted layers' queued/retry_later/paused
  jobs are preserved; if any non-terminal jobs survive, the finalizer
  returns `non_terminal_jobs_present` and does not prematurely finalize.
  Only when all planned work reaches a real terminal state does the
  record enter `coverage_complete`; `completed_with_failures` accurately
  reflects which layers failed due to budget. **T4.2a-R2-R3 corrects
  full `budget_exhausted` to also use `BUDGET_LAYER_TO_JOB_TYPES`** for
  force-fail computation (not `ENHANCEMENT_PIPELINE_JOB_TYPES`, which
  includes `generate_display_title_zh`). Both full and partial
  exhaustion now exclude `display_title` from force-fail.
- Usage / runtime evidence: succeeded executor calls produce
  `ai_usage_events` with record / run / job attribution and
  `reader_runtime_spans` with execution tokens. Budget-denied ticks are
  recorded as a distinct `budget_denied` outcome (not plain `no_job`)
  and surfaced via the pipeline summary (`stopped_reason`,
  `outcome_counts["budget_denied"]`, `budget_diagnostics` per layer with
  planned / max / consumed / remaining, `exhausted_layers`), so
  operators can distinguish `attempted` / `executed` / `published` /
  `budget-denied`. **T4.2a-R2-R2 persists budget denial to runtime
  spans**: `budget_denied`, `exhausted_layers`, `budget_diagnostics`,
  and `stopped_reason` are written to
  `reader_runtime_spans.metadata_json` (pipeline root span) and the
  WorkerLoop structured log, queryable from Console/runtime spans after
  the task ends — not only in the Python return value. Normal `no_job`
  scenarios have `budget_denied == 0`, distinguishable from
  budget-denied. Stale-fingerprint claim/publish rejections are
  observable via fence-violation span outcomes.
- **V1 evidence boundary:** the real-LLM records exercised the normal
  success path and confirmed route/topology/readiness consistency without
  duplicate fallback, stale publish, or superseded residue. They did not
  trigger retry, budget exhaustion, or route cutover; deterministic R2
  tests remain authoritative for those failure paths. Actual provider cost
  is unavailable; the configuration-derived theoretical range is about
  `$0.0158-$0.0354`, and no same-sample historical real-LLM baseline exists.
  Therefore the architecture must not be described as having already
  delivered a measured cost or latency reduction. Sample A also has an
  unresolved intermittent grammar usage-attribution gap, while per-job
  provider latency is unavailable because `ai_usage_events.latency_ms` is
  NULL. The next gate is page-only validation using the existing records,
  with no additional LLM calls.

### 4.3 Analysis Window

An analysis window is an internal LLM analysis scope. It is not a frontend display unit.

Durable constraints:

- A window may contain multiple reading units and anchor segments.
- LLM output candidates can come from multiple units.
- A final published grammar note or sentence analysis item must still be unit-scoped and anchor-grounded.
- Window provenance belongs in diagnostics or `quality_json`, not in user-facing output fields.
- The frontend should not need to know that a window existed.

### 4.4 Layer-Level Progressive Publish

Short-form batch computation does not mean final-only publishing. For short articles:

- Grammar, vocabulary, and translation jobs may start concurrently.
- Translation has user-visible release priority.
- Vocabulary and grammar publish after validation as stable layer-level committed results.
- The user should not wait for all layers before seeing the readable page.

This avoids both old final-only workflow behavior and current many-small-events behavior.

### 4.5 Committed Patch Streaming

Article enhancement streaming means streaming committed, validated results:

- Schema validation has passed.
- Anchor grounding has passed.
- Dedup/selector policy has run where applicable.
- Publish-fence checks have passed.

Raw LLM token streaming is reserved for Ask/chat responses. It is not appropriate for article annotations, because article annotations may be rejected or rewritten during validation.

## 5. Strategy Selection

### 5.1 Planner Ownership

The planner is deterministic code. It owns final strategy selection.

LLM involvement is optional and bounded:

- LLM may return a schema-constrained document profile.
- LLM must not return a strategy that is executed directly.
- LLM must not set budgets, skip layers, modify anchors, or decide release order.
- If LLM profiling fails or times out, deterministic routing still proceeds.

### 5.2 Planner Inputs

Primary deterministic inputs:

- Estimated token count.
- Estimated word count.
- Paragraph count.
- Heading depth and section count.
- Stable document block histogram.
- Format noise.
- Requested enhancement layers.
- Whether progressive/lazy enhancement is required.
- `reading_goal`.

Optional LLM profile fields:

- `genre`: news, essay, academic, literary, technical, mixed.
- `structure_coherence`: single article, fragmented news, noisy extract, multi-document.
- `schema_risk`: low, medium, high.
- `selective_analysis_recommended`: boolean.
- `key_sections`: only for long/very long documents.

### 5.3 Reading Goal And Variant

`reading_goal` can affect routing at a coarse level:

- Daily reading can prefer lighter and faster execution.
- Exam-oriented goals can prefer deeper coverage near strategy boundaries.
- Intensive reading can prefer section/window depth for medium and long documents.

`reading_variant` should mostly tune:

- Prompt strategy.
- Few-shot examples.
- Grammar/vocabulary focus.
- Density and budget.
- Vocabulary difficulty threshold.
- Translation style.

Do not multiply the route matrix by every reading variant unless evaluation data proves the need.

## 6. Short Article Recovery Path

Short article recovery is the immediate priority.

Implementation status as of 2026-07-09: the first M1 slice is implemented for
the current Reader orchestration path. Short articles route translation and
vocabulary through whole-article batch jobs with per-unit layer publishing;
grammar window execution receives reading strategy metadata and aligned window
budgets. The short-article batch translation regression that collapsed groups
to whole Reading Units has been repaired by backend group planning / hydration
contracts. Future grouped/windowed translation must reuse the same Translation
Group contract.

This also does not close the UX stability work: page reload behavior, panel
state preservation, and reading-order release remain M2 responsibilities.

### 6.1 Entry Criteria

Initial short-path criteria:

- `estimated_token_count < 2000`, or equivalent word-count fallback.
- Paragraph count below roughly 25.
- Single-genre article or low mixed-content risk.
- No strong requirement for window-level progressive output.

Do not compare raw UTF-16 character length directly with token thresholds.
Raw `content_utf16_length` may remain a coarse guardrail, but it must not be
the sole router between short batch, structured batch, and grouped/windowed
longform modes.

### 6.2 Execution

Short article path:

1. Build stable base and render source text.
2. Start translation, vocabulary, and grammar batch jobs concurrently when capacity allows.
3. Publish translation first when validated.
4. Publish vocabulary and grammar as stable layer-level committed results.
5. Avoid per-unit translation and vocabulary fan-out.
6. Deduplicate vocabulary across the whole record.

Translation-specific guardrail:

- A short-article `translate_article` batch job may analyze and translate all
  units in one worker call, but the published `translation` layer must still
  use semantic Translation Groups.
- The batch path must not replace semantic groups with one group per anchor
  segment, one sentence per group, or one whole Reading Unit per group.
- If the translation step needs backend-predefined `group_id` values to avoid
  anchor remapping, use a bounded group-planning step first: the planner
  returns schema-constrained contiguous anchor ranges; backend code validates
  coverage/contiguity/no-overlap and hydrates `group_id`, `source_text_hash`,
  and source text; the translator then returns only `group_id` and
  `translated_text`.

### 6.3 Structured Article Batch

Structured article batch path:

1. Use when the article is beyond the initial short-path threshold, but still
   fits safely in a single structured batch and does not require longform
   progress UX.
2. Keep translation and vocabulary whole-article batch compute when safe, with
   schema-bound output and grounded per-unit publish.
3. Prefer a compact grammar candidate path over blindly running three largely
   independent longform sweeps on medium documents.
4. Publish translation first; vocabulary and grammar can follow once validated.
5. This is the intended landing zone for medium articles that are too large for
   `short article batch` but too small to justify full grouped/windowed
   longform execution.

### 6.4 Acceptance

Short-form recovery is not complete unless:

- Translation and vocabulary no longer create one job per short reading unit.
- Translation Groups remain semantic reading groups in the published layer.
  A short-article batch implementation that collapses groups to whole units
  is not accepted.
- Translation becomes visible before grammar/vocabulary completion.
- Grammar output materially recovers against the old workflow baseline for exam samples.
- Total token usage is near the old workflow range or any excess has a documented quality reason.
- The page does not repeatedly flash or collapse open annotations during normal publishing.

## 7. Windowed And Grouped Execution

Windowed/grouped execution is for documents where batch is unsafe or insufficient.

### 7.1 Window Formation

Window formation should use an analysis-anchor view derived from existing stable base facts:

- Anchor id and unit id.
- Unit order.
- Base offsets.
- Unit range.
- Block type and block range.

The planner must not assume these derived fields are native `anchor_segments` columns.

### 7.2 Target And Context

Each window has:

- Target anchors: candidates may be published only against these anchors.
- Context anchors: LLM may read them, but output cannot anchor to them.

This keeps windows coherent without expanding the published target scope.

### 7.3 Ledger And Selector

For windowed grammar/sentence analysis:

- The plan ledger tracks per-record budgets, published anchors, semantic dedup keys, pattern keys, density, and window coverage.
- The selector applies hard gates before publish.
- Window-local score can order candidates inside one window, but hard gates decide what is published.
- Raw candidate counts and selector rejection reasons must be observable.

### 7.4 Release Order

Window processing may be concurrent, but user-visible release should be reading-order-oriented:

- Prefer the earliest unread or currently visible region.
- Later windows may become background-ready.
- Later results should not aggressively appear in the tail while early paragraphs remain empty, unless the user explicitly jumps there.

### 7.5 Completion State Finalization

After grouped/windowed jobs and analysis windows reach terminal status, a
completion finalizer advances `readiness_state` to `coverage_complete` and
publishes a `record_state_changed` event so the record exits the candidate
scan. The finalizer is the only writer of the `coverage_complete` readiness
state.

Scope of writes (v1): the finalizer writes **only** `reading_records.readiness_state`
and `reader_events`. It does **not** update `reading_records.product_state` or
`layer_analysis_plans.status`. `product_state` is intentionally left at
`readable_enhancing` on the clean / no_op / completed_with_failures paths so
users are not locked out of articles whose translation + vocabulary succeeded;
`failed` grammar window outcomes are surfaced via T3.4a diagnostics instead.
Plan status continues to be owned by the existing grammar window publisher /
pipeline runner paths.

Decision source: the finalizer reads **durable state** (terminal job/window
counts from the repository), not the in-memory pipeline summary. The pipeline
summary's stopped reason is only a gate, not a count source.

Cap behavior: `max_ticks_reached` and `max_jobs_reached` do **not** auto-block
finalization. The pipeline runner checks caps **after** incrementing the
processed count, so the last succeeding tick can land exactly on the budget.
Both caps are symmetric and finalizable; only `attention_required` is treated
as non-finalizable.

Budget exhaustion behavior (T4.2a-R2; T4.2a-R2-R3a 代码级 review 通过):
`budget_exhausted` and `partial_budget_exhausted` are both finalizable
stopped reasons, symmetric with the cap behavior. The budget is durable
across `runner.run()` calls (rebuilt from `reader_jobs.attempt_count`),
so a retried job cannot reset the ceiling. When a per-layer
`ExecutionBudget` is exhausted, the pipeline stops dispatching workers
for that layer and records a `budget_denied` outcome (not a plain
`no_job`). If all layers are exhausted the stopped reason is
`budget_exhausted`; if only some layers are exhausted while others
continue, the stopped reason is `partial_budget_exhausted`, which is
never disguised as `all_workers_no_job`. **T4.2a-R2-R2 corrects
per-layer force-fail semantics**: the finalizer force-fails only the
exhausted layers' job types (via `BUDGET_LAYER_TO_JOB_TYPES`), not
indiscriminately across `ENHANCEMENT_PIPELINE_JOB_TYPES`;
`display_title` is not a budget layer and is never force-failed by
budget exhaustion. Non-exhausted layers' queued/retry_later/paused jobs
are preserved; if any non-terminal jobs survive, the finalizer returns
`non_terminal_jobs_present` and does not prematurely finalize. Only
when all planned work reaches a real terminal state does the record
enter `coverage_complete`. Budget exhaustion therefore never produces
`completed_clean` when a layer failed due to budget; it is always
surfaced as `completed_with_failures` with an explicit diagnostic, so
operators can distinguish it from a clean run. **T4.2a-R2-R3 ensures
`display_title` is never force-failed by any budget exhaustion (full
or partial)**: both exhaustion paths now route through
`BUDGET_LAYER_TO_JOB_TYPES`, which excludes
`generate_display_title_zh`.

Outcomes:

- `completed_clean`: all enhancement jobs succeeded and no `failed` / `no_op`
  analysis windows.
- `completed_with_no_op`: no `failed` windows but some `no_op` windows.
- `completed_with_failures`: some `failed` windows or `failed_terminal` jobs.

Stuck window policy (v1): when all enhancement jobs are terminal but analysis
windows remain `pending` or `running`, the finalizer force-fails those windows
(`failure_code=finalizer_forced_window_failure`, `forced_by=completion_finalizer`)
and finalizes with `completed_with_failures`. This avoids a permanent wedge:
candidate scan only re-selects records with `runnable_job_count > 0`, so a
record with all-terminal jobs but stuck windows would otherwise never be
scanned again and `readiness_state` would stay at `article_ready` /
`initial_enhancement_ready` forever. The v1 finalizer does not retry
force-failed windows; retry / action-required UX is deferred to T4+.

Coverage scope: the finalizer covers `ENHANCEMENT_PIPELINE_JOB_TYPES` only.
`article_rag_index_build` and other substrate jobs do not participate in the
completion closure.

## 8. Progressive Delivery And Frontend Stability

The reader experience requires stable progressive delivery.

### 8.1 Current Problem To Avoid

Publishing a small layer row and forcing a full snapshot reload on every `layer_published` event causes:

- Page flashing.
- Collapsed annotation panels.
- Lost active selection.
- Tail annotations appearing before early paragraphs.

### 8.2 Event Transport

SSE is a good target transport for Reader events, because the system already has:

- Persistent reader events.
- Per-record event sequence.
- Ask SSE precedent.
- Cursor/reconnect semantics.

However, SSE is not sufficient by itself. SSE must carry stable event envelopes or projection patches. Replacing polling with SSE while still forcing a full snapshot reload for every event would make the current flashing problem more immediate, not better.

### 8.3 Update Model

Implementation should evolve in two steps:

1. V1: SSE or polling events with debounced/batched reload and preserved expanded/selected/scroll state.
2. V2: true projection patch merge without whole-snapshot replacement.

## 9. Longform And Very Long Documents

Long and very long documents should not eagerly generate every annotation upfront.

Implementation note: current long-article support is still incomplete at the
strategy level. Grouped translation and vocabulary are implemented, and grammar
diagnostics/density fixes are in place, but the system still eagerly runs
translation, vocabulary, and grammar as mostly independent layer passes. For
long and very long documents, the next design question is whether a bounded
enhancement planner can select translation groups and high-value enhancement
targets before specialized structured workers run. That planner must remain
schema-bound and must not own publishing, budgets, anchors, or control flow.
Near-term planning constraint: the first bounded-planner cut should prioritize
high-value vocabulary/grammar targets for long and very long documents. The
existing translation semantic group planner remains the primary translation
contract unless later evidence justifies expanding planner scope.

### 9.1 Section-Oriented Longform

For long documents with clear sections:

- Build a section map from headings and stable blocks.
- Process current and early sections first.
- Keep section-level budgets and density controls.
- Let later sections be background-ready when useful.

### 9.2 Selective Longform

For very long documents:

- Build metadata and outline first.
- Do not eagerly annotate the whole document.
- Enhance the current section, explicit user selection, Ask-relevant region, or user-jumped section.
- Keep cost bounded by section/lazy policy.

### 9.3 Progress UX

Very long documents need visible progress affordances:

- Outline readiness.
- Current section enhancement status.
- Queued/background sections.
- Reading-order progress.

Do not expose technical execution strategy names in UI.

## 10. Observability And Evaluation

Every strategy must produce enough evidence for review:

- Strategy name, complexity class, and planner rationale.
- Reading goal and variant.
- Per-layer call count, latency, tokens, model route/profile/provider/name.
- Cache hit/miss or cached-input attribution when the provider exposes it.
- First-useful-output time.
- Full-completion time.
- Raw candidate count, selector rejection count, accepted count where applicable.
- No-op/empty outcome cause: raw candidate empty, selector rejected all, publish failed, background-ready hidden.
- Position-sensitive quality checks: beginning/middle/end relevance and section-jump cases.

Evaluation should compare:

- Old AI Workflow baseline.
- Current orchestration baseline.
- New adaptive strategy.

Quality review should check:

- Grammar-note relevance.
- Sentence-analysis usefulness.
- Vocabulary dedup and difficulty fit.
- Translation readability.
- Density and visual balance.

### 10.1 Evaluation Cadence

During implementation, do not repeatedly run expensive real LLM longform tests
after every small patch. Use this cadence instead:

1. Contract tests for planner output, window/group boundaries, publish fences,
   source hashes, layer schemas, and completion states.
2. Fake executor tests for job counts, layer counts, reading-order publishing,
   usage attribution, and no-op/failure paths.
3. Recorded LLM response fixtures for hydration, selector, diagnostics, and
   projection behavior.
4. Unified gated real-LLM validation only after each target mode reaches
   code-level closure; reuse completed records for page-only validation instead
   of repeating model calls.

Real LLM checks remain necessary, but they are integration gates, not the
default feedback loop for every intermediate patch.

## 11. Roadmap

### Phase 0: Documentation And Baseline Closure

- Freeze this design.
- Define baseline samples.
- Record old workflow and current orchestration metrics.
- Define provisional P0 acceptance thresholds.
- Status 2026-07-08: baseline harness and golden samples are in place for fake-mode comparison.

### P0: Short-Form Recovery

- Restore grammar strategy/few-shot.
- Add short article batch execution.
- Batch grammar/vocabulary/translation by layer.
- Publish translation first.
- Reduce short-path per-unit fan-out.
- Add minimum UX stabilization and observability.
- Status 2026-07-08: translation/vocabulary batch execution and grammar strategy/budget recovery are implemented; UX stabilization remains open and is tracked under M2 in `implementation-plan.md`.

### P1: Adaptive Planner And Grouped Non-Short Paths

- Add complexity classifier.
- Add mutually exclusive strategy planner.
- Group/window non-short translation and vocabulary.
- Add reading-order-oriented release for windowed/grouped paths.
- Status 2026-07-10: non-short translation and vocabulary grouped execution,
  vocabulary phrase_gloss guards, grammar window diagnostics, the grammar
  RECORD_DENSITY denominator fix, completion state finalization, and
  deterministic short/medium route hardening are implemented. The current
  router uses `estimated_word_count` as the primary signal, `content_utf16_length`
  only as a structured-tier guardrail, and recognizes a `STRUCTURED_BATCH`
  landing zone for medium articles. `STRUCTURED_BATCH` is now an auditable
  runtime mode (T4.1b landed): it carries a distinct `operation_fingerprint`
  base and `policy_version` from `SHORT_BATCH`, records `article_route` +
  `document_features` on every batch/window job, and a route change triggers
  `_supersede_stale_fingerprint_jobs`. The compact grammar batch path
  (T4.1c landed) now gives `SHORT_BATCH` / `STRUCTURED_BATCH` articles a
  single whole-article `build_grammar_bundle` / `unit_range` batch job
  instead of the heavy Z+ analysis-window path; the batch publisher splits
  output back into per-unit `grammar_note` / `sentence_analysis` layers.
  `GROUPED_WINDOWED` keeps the existing Z+ window contract unchanged.
  Acceptance harness and observability closure (T4.2a-R1 landed) faithfully
  reproduce the production `WorkerLoop` + `CompletionFinalizer` topology,
  inject fake executors so `enable_zplus_grammar=True` never reaches a real
  LLM in fake mode, and pin three-mode fixed-coverage tests across route /
  fingerprint / policy, job topology, effective calls, layer counts, final
  readiness and usage attribution. Execution budget and cutover safety
  (T4.2a-R2; **T4.2a-R2-R3a 代码级 review 通过 / deterministic acceptance complete**)
  add a durable per-layer `ExecutionBudget`
  (`max_effective_calls = planned * 3`, aligned with `max_attempts=3`;
  rebuilt from `reader_jobs.attempt_count` across `runner.run()` calls;
  the durable budget provides a deterministic ceiling and
  observability/scheduling but does not by itself reduce the existing
  retry cost), a fail-closed fallback decision table (`superseded` or
  no batch only) **with formal terminal state for suppressed legacy
  jobs** (rationale codes: `batch_path_authoritative` /
  `batch_fallback_not_authorized` / `stale_legacy_topology`), claim-time
  + publish-time route-flip fencing via `_validate_fence` →
  `_check_route_consistency` **with unified publish fence state
  consistency (no `max(1,...)` virtual reporting)**, `budget_exhausted`
  and `partial_budget_exhausted` finalizable stopped reasons with
  **per-layer force-fail (only exhausted layers' job types;
  `display_title` excluded)** to avoid permanent wedges, `budget_denied`
  as a distinct observable outcome **persisted to
  `reader_runtime_spans.metadata_json`**, and a **conservative sorted
  fingerprint set (Approach B)** for deterministic budget aggregation.
  T4.2a-V1 has exercised all three routes with real LLM calls and passed
  Contract / Output Integrity / sample-level Semantic Quality gates. Its
  Page UX gate remains blocked, and Cost/Latency remains a baseline rather
  than a measured improvement. The immediate priority is page-only validation
  with existing records. The bounded LLM document profiler (T4.2) stays
  deferred until deterministic routing shows repeatable boundary errors;
  strategy planning and outline-first contracts remain later phases.

### P2: SSE And Interaction-Preserving Updates

- Add Reader event SSE endpoint.
- Change event semantics toward batch/group/window readiness.
- Preserve interaction state during updates.
- Move toward projection patch merge.

### P3: Longform And Very Long Documents

- Add section-oriented longform execution.
- Add selective longform execution.
- Add very-long progress UX.
- Real very-long validation should wait until outline-first planning and lazy
  section enhancement have contract/fake coverage.

## 12. Review Gates

Every implementation slice must pass:

1. Contract gate: stable base, anchors, layers, publish fence, and projection remain valid.
2. Quality gate: output is reviewed against old workflow and real reading needs.
3. Cost gate: token/call/latency changes are measured and justified.
4. UX stability gate: progressive output does not destabilize reading.
5. Observability gate: traces and usage data explain what happened.

Do not merge multiple phases into one implementation prompt unless the previous phase has passed review.

## 13. Consolidated Sources

This document consolidates:

- The prior analysis-window design contract.
- The adaptive reader orchestration research reports.
- The cost/quality diagnosis from the short Reuters/BBC-style article comparison.
- The implementation brief and roadmap discussion from 2026-07-07.

Temporary reports under `tmp/` are no longer canonical facts after this consolidation.
