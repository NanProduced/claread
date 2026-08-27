-- Rollback for infra/scripts/alter_daily_readers_v2.sql
-- Drops the P-5A v2 landing column and restores the pre-P-5A state where
-- every INSERT had to supply footer_analysis_json explicitly.

BEGIN;

ALTER TABLE daily_readers
    ALTER COLUMN footer_analysis_json DROP DEFAULT;

ALTER TABLE daily_readers
    DROP COLUMN IF EXISTS lesson_v2;

COMMENT ON COLUMN daily_readers.footer_analysis_json IS
    'Deprecated always-empty column (kept for compatibility).';

COMMIT;
