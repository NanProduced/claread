-- Reader display title generation contract.
--
-- Adds record-scoped Chinese display-title state and a base-scoped worker job.
-- The generated title is user-facing metadata for a Reading Record Header, not
-- stable source text, Plate projection state, DOM selection state, or a Reading
-- Base fact.

ALTER TABLE reading_records
    ADD COLUMN IF NOT EXISTS generated_title_zh TEXT,
    ADD COLUMN IF NOT EXISTS title_generation_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS title_generation_error_code TEXT,
    ADD COLUMN IF NOT EXISTS title_generation_error_message TEXT,
    ADD COLUMN IF NOT EXISTS title_generation_attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS title_generation_updated_at TIMESTAMPTZ;

ALTER TABLE reading_records
    DROP CONSTRAINT IF EXISTS ck_reading_records_title_generation_status;

ALTER TABLE reading_records
    ADD CONSTRAINT ck_reading_records_title_generation_status CHECK (
        title_generation_status IN ('pending', 'succeeded', 'failed_retryable')
    );

ALTER TABLE reading_records
    DROP CONSTRAINT IF EXISTS ck_reading_records_generated_title_zh_succeeded;

ALTER TABLE reading_records
    ADD CONSTRAINT ck_reading_records_generated_title_zh_succeeded CHECK (
        title_generation_status <> 'succeeded'
        OR (generated_title_zh IS NOT NULL AND btrim(generated_title_zh) <> '')
    );

CREATE INDEX IF NOT EXISTS idx_reading_records_title_generation_status
    ON reading_records(title_generation_status, updated_at DESC)
    WHERE deleted_at IS NULL
      AND title_generation_status IN ('pending', 'failed_retryable');

ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;

ALTER TABLE reader_jobs
    ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
        'build_base',
        'translate_unit',
        'build_vocabulary_layer',
        'build_grammar_bundle',
        'input_artifact_extraction',
        'extracted_artifact_materialization',
        'article_rag_index_build',
        'generate_display_title_zh'
    ));

COMMENT ON COLUMN reading_records.generated_title_zh IS
    'LLM-generated Simplified Chinese display title for Reader masthead. Generated from bounded stable-base preview, never by the frontend.';

COMMENT ON COLUMN reading_records.title_generation_status IS
    'State for generated_title_zh: pending, succeeded, or failed_retryable. Missing Chinese title is never represented as a successful state.';

COMMENT ON COLUMN reading_records.title_generation_error_message IS
    'Sanitized retryable failure reason for the Chinese display-title worker.';
