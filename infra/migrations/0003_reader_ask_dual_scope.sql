-- F1 Ask dual-scope storage
--
-- Keep existing legacy Ask rows on the analysis_record_id path while
-- introducing an explicit Reading Record scope for new Ask threads,
-- turn runs, and supplements.

-- ============================================================
-- reader_ask_threads
-- ============================================================

ALTER TABLE reader_ask_threads
    RENAME COLUMN record_id TO analysis_record_id;

ALTER TABLE reader_ask_threads
    ADD COLUMN reading_record_id UUID REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE reader_ask_threads
    ADD CONSTRAINT reader_ask_threads_scope_check
        CHECK (
            (analysis_record_id IS NOT NULL AND reading_record_id IS NULL)
            OR
            (analysis_record_id IS NULL AND reading_record_id IS NOT NULL)
        );

CREATE UNIQUE INDEX uq_reader_ask_default_thread_reading_record
    ON reader_ask_threads (user_id, reading_record_id)
    WHERE is_default = TRUE
      AND archived_at IS NULL
      AND reading_record_id IS NOT NULL;

CREATE INDEX idx_reader_ask_threads_user_reading_record_updated
    ON reader_ask_threads (user_id, reading_record_id, updated_at DESC)
    WHERE archived_at IS NULL
      AND reading_record_id IS NOT NULL;

CREATE INDEX idx_reader_ask_threads_user_reading_record_last_message
    ON reader_ask_threads (user_id, reading_record_id, last_message_at DESC NULLS LAST)
    WHERE archived_at IS NULL
      AND reading_record_id IS NOT NULL;

-- ============================================================
-- reader_ask_turn_runs
-- ============================================================

ALTER TABLE reader_ask_turn_runs
    RENAME COLUMN record_id TO analysis_record_id;

ALTER TABLE reader_ask_turn_runs
    ADD COLUMN reading_record_id UUID REFERENCES reading_records(id) ON DELETE CASCADE,
    ADD COLUMN base_id UUID,
    ADD COLUMN generation INTEGER;

ALTER TABLE reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_scope_check
        CHECK (
            (
                analysis_record_id IS NOT NULL
                AND reading_record_id IS NULL
                AND base_id IS NULL
                AND generation IS NULL
            )
            OR
            (
                analysis_record_id IS NULL
                AND reading_record_id IS NOT NULL
                AND base_id IS NOT NULL
                AND generation IS NOT NULL
                AND generation >= 1
            )
        );

CREATE INDEX idx_reader_ask_turn_runs_reading_record_started
    ON reader_ask_turn_runs (reading_record_id, started_at DESC)
    WHERE reading_record_id IS NOT NULL;

-- ============================================================
-- reader_ask_supplements
-- ============================================================

ALTER TABLE reader_ask_supplements
    RENAME COLUMN record_id TO analysis_record_id;

ALTER TABLE reader_ask_supplements
    ALTER COLUMN target_key DROP NOT NULL,
    ALTER COLUMN sentence_id DROP NOT NULL;

ALTER TABLE reader_ask_supplements
    ADD COLUMN reading_record_id UUID REFERENCES reading_records(id) ON DELETE CASCADE,
    ADD COLUMN base_id UUID,
    ADD COLUMN generation INTEGER,
    ADD COLUMN unit_id TEXT,
    ADD COLUMN anchor_segment_id TEXT,
    ADD COLUMN start_offset INTEGER,
    ADD COLUMN end_offset INTEGER,
    ADD COLUMN text_hash TEXT,
    ADD COLUMN hash_algorithm TEXT;

ALTER TABLE reader_ask_supplements
    ADD CONSTRAINT reader_ask_supplements_scope_check
        CHECK (
            (
                analysis_record_id IS NOT NULL
                AND reading_record_id IS NULL
                AND base_id IS NULL
                AND generation IS NULL
                AND unit_id IS NULL
                AND anchor_segment_id IS NULL
                AND start_offset IS NULL
                AND end_offset IS NULL
                AND text_hash IS NULL
                AND hash_algorithm IS NULL
            )
            OR
            (
                analysis_record_id IS NULL
                AND reading_record_id IS NOT NULL
                AND base_id IS NOT NULL
                AND generation IS NOT NULL
                AND generation >= 1
                AND unit_id IS NOT NULL
                AND anchor_segment_id IS NOT NULL
                AND start_offset IS NOT NULL
                AND start_offset >= 0
                AND end_offset IS NOT NULL
                AND end_offset > start_offset
                AND text_hash IS NOT NULL
                AND hash_algorithm IS NOT NULL
            )
        );

CREATE INDEX idx_reader_ask_supplements_user_reading_record
    ON reader_ask_supplements (user_id, reading_record_id, created_at)
    WHERE deleted_at IS NULL
      AND reading_record_id IS NOT NULL;
