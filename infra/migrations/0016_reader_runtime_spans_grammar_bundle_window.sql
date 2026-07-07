-- 0016_reader_runtime_spans_grammar_bundle_window.sql
--
-- Add ``grammar_bundle_window`` to the ``reader_runtime_spans.worker_type``
-- CHECK constraint so the Z+ Analysis Window worker can write worker_tick
-- spans (requirement 5 of the Z+ observability adaptation).
--
-- Background: migration 0014 enumerated the original 4 workers
-- (display_title / translation / vocabulary / grammar_bundle) plus the
-- artifact workers. The Z+ window worker is a distinct worker_type
-- dispatched ahead of legacy ``grammar_bundle`` (see
-- ``pipeline_runner._run_grammar_window_attempt``), so it needs its own
-- enum value for Console latency / token panels to group Z+ spans
-- separately from legacy per-unit grammar spans.

ALTER TABLE reader_runtime_spans
    DROP CONSTRAINT reader_runtime_spans_worker_type_check;

ALTER TABLE reader_runtime_spans
    ADD CONSTRAINT reader_runtime_spans_worker_type_check
    CHECK (worker_type IN (
        'display_title', 'translation', 'vocabulary', 'grammar_bundle',
        'grammar_bundle_window',
        'article_rag_index', 'artifact_extraction', 'artifact_materialization'
    ));
