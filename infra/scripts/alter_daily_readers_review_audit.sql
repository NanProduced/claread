-- Incremental upgrade for existing volumes that already applied 0001
-- before review audit columns existed.
-- Fresh installs / reset_full_keep_dict + 0001 already have these columns.
--
-- Apply:
--   psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_review_audit.sql
-- Rollback:
--   psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_review_audit_down.sql
--
-- B-4 (console review-queue) reuses these columns. Do not add a second
-- copy under infra/migrations/ — that directory is pinned to 0001 only.

BEGIN;

ALTER TABLE daily_readers
    ADD COLUMN IF NOT EXISTS review_status text DEFAULT 'pending'::text NOT NULL,
    ADD COLUMN IF NOT EXISTS reviewed_by text,
    ADD COLUMN IF NOT EXISTS reviewed_at timestamp with time zone;

DO $chk$
BEGIN
    ALTER TABLE daily_readers
        ADD CONSTRAINT daily_readers_review_status_check
        CHECK ((review_status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text])));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$chk$;

UPDATE daily_readers
SET review_status = 'approved',
    reviewed_by = 'legacy',
    reviewed_at = COALESCE(published_at, NOW())
WHERE status = 'published'
  AND reviewed_by IS NULL;

COMMENT ON COLUMN daily_readers.review_status IS '日审状态：pending、approved、rejected。publish 时置 approved；retry 回 draft 时置 pending。';

COMMENT ON COLUMN daily_readers.reviewed_by IS '最近一次 publish/unpublish 的 operator 标识。旧已发布行回填 legacy。';

COMMENT ON COLUMN daily_readers.reviewed_at IS '最近一次 publish/unpublish 时间。';

COMMENT ON COLUMN daily_readers.content_sec_check IS 'DEPRECATED: 历史占位字段，pipeline 不再写入。列保留以免破坏旧行读取。';

COMMIT;
