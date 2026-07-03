-- 0014_reader_runtime_spans.sql
--
-- End-to-end actor chain span tree for reader_orchestration.
--
-- This table is the PG fact source for the reader_orchestration observability
-- gap (see docs/tmp/reader-orchestration/TMP-reader-orchestration-observability-gap-2026-07-01.md).
-- It stores one row per actor boundary span (bootstrap / pipeline_root /
-- worker_tick / llm_call / publish_fence / claim) and links to ai_usage_events
-- for token / model / cost columns and to LangSmith runs via langsmith_run_id.
--
-- Design notes:
-- * trace_id is generated in ReaderOrchestrator.submit_plain_text_and_bootstrap_translation
--   and persisted into reader_runs.envelope_json. Workers read it back from
--   the claim result and use it as parent_span_id root.
-- * ai_usage_event_id FK enables Console to JOIN tokens + latency in one query.
-- * langsmith_run_id is the dual-track linkage so Console can deep-link to
--   LangSmith's trace UI for the same span.
-- * claim_wait_ms / attempt_number / retry_class are the three fields the gap
--   report flagged as missing (#2 and #4); they live here rather than on
--   ai_usage_events to keep that table focused on token/cost facts.
-- * model_* columns are denormalized from ai_usage_events so Console panels
--   can build model cost / latency heatmaps without an extra JOIN.

CREATE TABLE reader_runtime_spans (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id             UUID NOT NULL,
    parent_span_id       UUID REFERENCES reader_runtime_spans(id) ON DELETE SET NULL,
    span_kind            TEXT NOT NULL CHECK (span_kind IN (
        'pipeline_root', 'worker_tick', 'llm_call', 'publish_fence',
        'claim', 'bootstrap'
    )),
    -- No FK on reader_run_id / reader_job_id / reading_record_id / ai_usage_event_id:
    -- observability spans must outlive the business entities they reference
    -- so Console can still query historical latency / token data after a
    -- record or job is deleted. Console queries use LEFT JOIN, so the
    -- lack of FK does not change panel behavior.
    reader_run_id        UUID,
    reader_job_id        UUID,
    -- NULLable so publish_fence spans can start before the publisher reads
    -- reader_jobs.reading_record_id inside its transaction. Non-publish
    -- spans (pipeline_root / worker_tick / claim / bootstrap) always set it.
    reading_record_id    UUID,
    worker_type          TEXT CHECK (worker_type IN (
        'display_title', 'translation', 'vocabulary', 'grammar_bundle',
        'article_rag_index', 'artifact_extraction', 'artifact_materialization'
    )),
    -- Denormalized from ai_usage_events so Console panels can group by
    -- model without an extra JOIN. NULL for non-LLM spans (claim,
    -- pipeline_root, etc.).
    model_route          TEXT,
    model_name           TEXT,
    model_provider       TEXT,
    capability_code      TEXT,
    ai_usage_event_id    UUID,
    -- Gap report #4: retry budget transparency. attempt_number is the
    -- raw reader_jobs.attempt_count; retry_class is derived from which
    -- *_attempt_count column was incremented (transient / repair / replan).
    attempt_number       INT,
    retry_class          TEXT CHECK (retry_class IN (
        'transient', 'repair', 'replan'
    )),
    status               TEXT NOT NULL CHECK (status IN (
        'started', 'succeeded', 'failed', 'superseded', 'skipped'
    )),
    failure_class        TEXT,
    failure_code         TEXT,
    -- Gap report #2: claim contention transparency. Filled by
    -- ReaderJobRuntime.claim_next_job wrapping the SKIP LOCKED SELECT.
    claim_wait_ms        INT,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at             TIMESTAMPTZ,
    duration_ms          INT,
    -- Denormalized token usage from the LLM span. NULL for non-LLM spans.
    input_tokens         INT,
    output_tokens        INT,
    total_tokens         INT,
    cache_read_tokens    INT,
    cache_write_tokens   INT,
    -- Dual-track linkage (gap report #6). Set by a custom SpanProcessor
    -- that captures LangSmith's auto-injected langsmith.trace.id /
    -- langsmith.span.id attributes on span end and UPDATEs the matching
    -- PG row identified by the claread.span_id OTel attribute.
    langsmith_run_id     TEXT,
    metadata_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_reader_runtime_spans_trace_id
    ON reader_runtime_spans(trace_id);
CREATE INDEX ix_reader_runtime_spans_run_id
    ON reader_runtime_spans(reader_run_id);
CREATE INDEX ix_reader_runtime_spans_job_id
    ON reader_runtime_spans(reader_job_id);
CREATE INDEX ix_reader_runtime_spans_record_id
    ON reader_runtime_spans(reading_record_id);
CREATE INDEX ix_reader_runtime_spans_started_at
    ON reader_runtime_spans(started_at DESC);
CREATE INDEX ix_reader_runtime_spans_worker_type
    ON reader_runtime_spans(worker_type);
CREATE INDEX ix_reader_runtime_spans_status
    ON reader_runtime_spans(status);
