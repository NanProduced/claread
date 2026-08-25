-- Incremental upgrade for existing volumes that already applied 0001
-- before the P-5A teaching-v2 landing column existed.
-- Fresh installs / reset_full_keep_dict + 0001 do not have this column.
--
-- Apply:
--   psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_v2.sql
-- Rollback:
--   psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_v2_down.sql
--
-- P-5A (teaching v2) stores the whole v2 payload (blueprint + learning
-- package + source assets + run meta) in one jsonb column; NULL means the
-- row is a pre-v2 row. Do not add a second copy under infra/migrations/ --
-- that directory is pinned to 0001 only.
--
-- footer_analysis_json is a permanently empty column: no reader exists
-- anywhere, so the INSERT side stops passing it and the column gets a
-- constant '{}' default instead. The physical DROP is deferred to the next
-- fresh baseline (content_sec_check likewise stays untouched this round).

BEGIN;

ALTER TABLE daily_readers
    ADD COLUMN IF NOT EXISTS lesson_v2 jsonb;

COMMENT ON COLUMN daily_readers.lesson_v2 IS
    'P-5A teaching v2 payload (lesson blueprint + learning package + source assets + run meta). NULL = pre-v2 row.';

ALTER TABLE daily_readers
    ALTER COLUMN footer_analysis_json SET DEFAULT '{}'::jsonb;

COMMENT ON COLUMN daily_readers.footer_analysis_json IS
    'Deprecated always-empty column. INSERT no longer supplies it; DEFAULT ''{}'' keeps old writers working. Physical DROP deferred to the next fresh baseline.';

COMMIT;
