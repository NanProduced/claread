-- Rollback for infra/scripts/alter_daily_readers_zh_title.sql
-- Drops the A-3 columns. Old rows keep their English headline in title;
-- rows created after A-3 lose their English original (title stays Chinese).

BEGIN;

ALTER TABLE daily_readers
    DROP COLUMN IF EXISTS original_title,
    DROP COLUMN IF EXISTS subtitle_zh;

COMMIT;
