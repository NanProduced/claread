# Adaptive Reader Orchestration Design

> Status: formal design draft
> Last updated: 2026-07-10 (T4.2a-R1 three-mode evidence parity and observability closure landed)
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
- **The three modes are not yet accepted under real LLM.** Current
  fixed-coverage tests use fake executors and only verify code-level
  contract closure; real-LLM cost / quality / latency improvements and
  page acceptance remain a separate, later gate. The next step is
  T4.2a-R2 (execution budget / cutover safety) before any real-LLM
  acceptance run.

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
4. Unified real LLM / page validation only after short, long, and very-long
   modes have each reached code-level closure.

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
  `GROUPED_WINDOWED` keeps the existing Z+ window contract unchanged. The
  next priority is the bounded LLM document profiler (T4.2) and strategy
  planner (T4.3) before expanding the outline-first contracts for long and
  very long documents.

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
