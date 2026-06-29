-- Reader Orchestration reading strategy contract (T1 backend contract restore).
--
-- Restores `reading_goal` / `reading_variant` as first-class columns on
-- `reading_records`. These columns are the truth owner for Reader strategy
-- in the new orchestration; they MUST NOT be inferred from `source_metadata`.
--
-- Scope: only `daily_reading` and `exam` (with their variants) are wired into
-- the new Reader Orchestration. `academic` / `academic_general` from legacy
-- AI Workflow are intentionally excluded at the DB layer; the application
-- schema (Literal types) already fails closed for those values, and these
-- CHECK constraints provide a defense-in-depth backstop.
--
-- Backfill: existing rows receive the centralized default
-- (`daily_reading` / `intermediate_reading`) so historical records continue
-- to load. The default is a first-class persisted fact, not a worker-side
-- fallback. Future worker prompt policy must read these columns directly,
-- not reconstruct them from `source_metadata`.

ALTER TABLE reading_records
    ADD COLUMN IF NOT EXISTS reading_goal TEXT NOT NULL DEFAULT 'daily_reading';

ALTER TABLE reading_records
    ADD COLUMN IF NOT EXISTS reading_variant TEXT NOT NULL DEFAULT 'intermediate_reading';

ALTER TABLE reading_records
    DROP CONSTRAINT IF EXISTS ck_reading_records_reading_goal;

ALTER TABLE reading_records
    ADD CONSTRAINT ck_reading_records_reading_goal CHECK (
        reading_goal IN ('daily_reading', 'exam')
    );

ALTER TABLE reading_records
    DROP CONSTRAINT IF EXISTS ck_reading_records_reading_variant;

ALTER TABLE reading_records
    ADD CONSTRAINT ck_reading_records_reading_variant CHECK (
        reading_variant IN (
            'beginner_reading',
            'intermediate_reading',
            'intensive_reading',
            'gaokao',
            'cet',
            'kaoyan',
            'tem',
            'ielts_toefl'
        )
    );

-- Enforce variant-in-goal at the DB layer as defense-in-depth. The
-- application-layer validator in `app.schemas.reader_orchestration` is the
-- primary chokepoint, but this constraint makes it impossible for a stray
-- UPDATE or a backfill script to land an inconsistent pair.
ALTER TABLE reading_records
    DROP CONSTRAINT IF EXISTS ck_reading_records_reading_variant_belongs_to_goal;

ALTER TABLE reading_records
    ADD CONSTRAINT ck_reading_records_reading_variant_belongs_to_goal CHECK (
        (reading_goal = 'daily_reading' AND reading_variant IN (
            'beginner_reading', 'intermediate_reading', 'intensive_reading'
        ))
        OR (reading_goal = 'exam' AND reading_variant IN (
            'gaokao', 'cet', 'kaoyan', 'tem', 'ielts_toefl'
        ))
    );

CREATE INDEX IF NOT EXISTS idx_reading_records_user_goal_updated_at
    ON reading_records(user_id, reading_goal, updated_at DESC)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN reading_records.reading_goal IS
    'Reader strategy goal (daily_reading | exam). First-class fact; do not infer from source_metadata. academic is intentionally not wired into the new orchestration.';
COMMENT ON COLUMN reading_records.reading_variant IS
    'Reader strategy variant scoped to reading_goal. First-class fact; do not infer from source_metadata.';
