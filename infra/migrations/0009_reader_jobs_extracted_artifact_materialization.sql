-- D6-I3O: Allow reader_jobs to host extracted artifact materialization jobs.
--
-- After D6-I3L extraction succeeds (original_inputs.source_text is populated),
-- the extraction worker enqueues a materialization job that runs the I3N
-- service to freeze a stable document / create a candidate / mark action_required.
-- Like extraction, materialization runs BEFORE a reading base exists — the
-- stable path itself creates the first base via persist_stable_document_freeze_plan.
--
-- This migration is minimal and mirrors 0008:
--   1. Extend the job_type CHECK to include 'extracted_artifact_materialization'.
--   2. Relax ck_reader_jobs_base_scope to allow extracted_artifact_materialization
--      with target_type='record' and base_id IS NULL (same shape as build_base
--      and input_artifact_extraction).
--
-- NOTE: job_runtime._validate_fence allows build_base, input_artifact_extraction,
-- and extracted_artifact_materialization record-level jobs to proceed with a
-- null base_id at claim/publish time. Both extraction and materialization are
-- superseded if active_base_id is already set (they must run before any base
-- exists). All other non-build_base jobs still require a non-null base_id.
--
-- Tests live in:
--   services/api/tests/test_reader_orchestration_job_runtime.py
--   services/api/tests/test_d6_i3o_materialization_job_runtime.py

ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;

ALTER TABLE reader_jobs
    ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
        'build_base',
        'translate_unit',
        'build_vocabulary_layer',
        'build_grammar_bundle',
        'input_artifact_extraction',
        'extracted_artifact_materialization'
    ));

ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS ck_reader_jobs_base_scope;

ALTER TABLE reader_jobs
    ADD CONSTRAINT ck_reader_jobs_base_scope CHECK (
        (
            job_type = 'build_base'
            AND target_type = 'record'
            AND base_id IS NULL
        )
        OR (
            job_type = 'input_artifact_extraction'
            AND target_type = 'record'
            AND base_id IS NULL
        )
        OR (
            job_type = 'extracted_artifact_materialization'
            AND target_type = 'record'
            AND base_id IS NULL
        )
        OR (
            NOT (
                job_type IN (
                    'build_base',
                    'input_artifact_extraction',
                    'extracted_artifact_materialization'
                )
                AND target_type = 'record'
            )
            AND base_id IS NOT NULL
        )
    );
