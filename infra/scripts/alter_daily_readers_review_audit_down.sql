-- Rollback for infra/scripts/alter_daily_readers_review_audit.sql
-- Does not restore pre-backfill reviewed_* values.

BEGIN;

ALTER TABLE daily_readers
    DROP CONSTRAINT IF EXISTS daily_readers_review_status_check;

ALTER TABLE daily_readers
    DROP COLUMN IF EXISTS review_status,
    DROP COLUMN IF EXISTS reviewed_by,
    DROP COLUMN IF EXISTS reviewed_at;

COMMENT ON COLUMN daily_readers.content_sec_check IS '微信内容安全检测结果，含 trace_id、suggest、label 等。';

COMMIT;
