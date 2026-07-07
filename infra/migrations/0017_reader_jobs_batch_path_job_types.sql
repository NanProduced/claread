-- 0017_reader_jobs_batch_path_job_types.sql
--
-- T1.1 short-article batch path: add ``translate_article`` and
-- ``build_vocabulary_layer_article`` to the ``reader_jobs.job_type`` CHECK
-- constraint, and ``translation_batch`` / ``vocabulary_batch`` to the
-- ``reader_runtime_spans.worker_type`` CHECK constraint.
--
-- Background: the T1.1 batch path creates one record-level job per layer
-- (translation / vocabulary) for short articles (≤6000 chars). The worker
-- makes a single LLM call covering all units, then the publisher splits
-- the output into N per-unit ``enhancement_layers`` rows.
--
-- The new job types use ``target_type = 'unit_range'`` (already allowed by
-- the existing ``reader_jobs.target_type`` CHECK constraint from
-- 0001_initial_schema.sql) and are base-scoped (``base_id IS NOT NULL``),
-- so they satisfy the catch-all clause in ``ck_reader_jobs_base_scope``
-- (see 0009_reader_jobs_extracted_artifact_materialization.sql) without
-- modifying it.
--
-- The new ``worker_type`` values correspond to the ``translation_batch``
-- and ``vocabulary_batch`` dispatch slots in
-- ``ReaderEnhancementPipelineRunner.worker_order`` and are written to
-- ``reader_runtime_spans.worker_type`` by ``_run_worker_attempt``.
--
-- Tests live in:
--   services/api/tests/test_reader_orchestration_pipeline_runner.py

ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;

ALTER TABLE reader_jobs
    ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
        'build_base',
        'translate_unit',
        'build_vocabulary_layer',
        'build_grammar_bundle',
        'build_grammar_bundle_window',
        'input_artifact_extraction',
        'extracted_artifact_materialization',
        'article_rag_index_build',
        'generate_display_title_zh',
        'translate_article',
        'build_vocabulary_layer_article'
    ));

ALTER TABLE reader_runtime_spans
    DROP CONSTRAINT reader_runtime_spans_worker_type_check;

ALTER TABLE reader_runtime_spans
    ADD CONSTRAINT reader_runtime_spans_worker_type_check
    CHECK (worker_type IN (
        'display_title', 'translation', 'vocabulary', 'grammar_bundle',
        'grammar_bundle_window',
        'article_rag_index', 'artifact_extraction', 'artifact_materialization',
        'translation_batch', 'vocabulary_batch'
    ));
