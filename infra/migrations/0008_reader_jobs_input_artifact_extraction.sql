-- D6-I3K: Allow reader_jobs to host artifact extraction jobs.
--
-- The existing reader_jobs table enforces that only build_base/record jobs
-- may have a NULL base_id. Artifact extraction jobs run BEFORE a reading
-- base exists (the whole point is to extract text/content to eventually
-- build a base), so they also need target_type='record' with base_id IS NULL.
--
-- No new queue table is introduced; extraction jobs reuse the existing
-- reader_jobs claim/heartbeat/transition runtime. The migration is minimal:
--   1. Extend the job_type CHECK to include 'input_artifact_extraction'.
--   2. Relax ck_reader_jobs_base_scope to allow input_artifact_extraction
--      with target_type='record' and base_id IS NULL (same shape as
--      build_base).
--
-- NOTE: job_runtime._validate_fence allows build_base and
-- input_artifact_extraction record-level jobs to proceed with a null base_id
-- at claim/publish time. build_base creates the first reading base;
-- input_artifact_extraction runs before any base exists (it extracts text
-- from the uploaded artifact into original_inputs) and is superseded if
-- active_base_id is already set. All other non-build_base jobs still require
-- a non-null base_id.
--
-- Tests live in services/api/tests/test_reader_orchestration_job_runtime.py:
--   test_claim_allows_input_artifact_extraction_job_with_null_base
--   test_db_constraint_rejects_non_build_base_null_base_job_insert
--   test_validate_fence_supersedes_non_build_base_null_base_job_row
--   test_claim_supersedes_extraction_job_when_active_base_already_exists

ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;

ALTER TABLE reader_jobs
    ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
        'build_base',
        'translate_unit',
        'build_vocabulary_layer',
        'build_grammar_bundle',
        'input_artifact_extraction'
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
            NOT (
                job_type IN ('build_base', 'input_artifact_extraction')
                AND target_type = 'record'
            )
            AND base_id IS NOT NULL
        )
    );
