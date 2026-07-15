-- 0019: Complete Reader Ask dual-scope nullability.
--
-- Migration 0003 added mutually-exclusive analysis/Reading Record scope
-- checks, but left the NOT NULL constraints inherited from the legacy
-- record_id columns. This made the Reading Record branch unreachable for
-- reader_ask_threads and reader_ask_turn_runs.
--
-- reader_ask_supplements was repaired separately in migration 0006.

ALTER TABLE reader_ask_threads
    ALTER COLUMN analysis_record_id DROP NOT NULL;

ALTER TABLE reader_ask_turn_runs
    ALTER COLUMN analysis_record_id DROP NOT NULL;
