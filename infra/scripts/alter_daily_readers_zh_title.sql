-- Incremental upgrade for existing volumes that already applied 0001
-- before the A-3 Chinese title columns existed.
-- Fresh installs / reset_full_keep_dict + 0001 already have these columns.
--
-- Apply:
--   psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_zh_title.sql
-- Rollback:
--   psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_zh_title_down.sql
--
-- A-3 (Chinese headline / original_title / subtitle_zh) reuses these
-- columns. Do not add a second copy under infra/migrations/ — that
-- directory is pinned to 0001 only.

BEGIN;

ALTER TABLE daily_readers
    ADD COLUMN IF NOT EXISTS original_title text,
    ADD COLUMN IF NOT EXISTS subtitle_zh text;

-- Old rows stored the English source headline in title; keep it as
-- original_title so the retry workflow and web fallbacks keep seeing it.
UPDATE daily_readers
SET original_title = title
WHERE original_title IS NULL;

COMMENT ON COLUMN daily_readers.title IS '文章标题。A-3 起存中文主标题（takeaways.title_zh）；旧行为英文原题。';

COMMENT ON COLUMN daily_readers.subtitle IS '副标题/摘要（来源 description，英文）。';

COMMENT ON COLUMN daily_readers.original_title IS '英文原题（caption 级展示）。旧行由增量脚本回填为原 title 值。';

COMMENT ON COLUMN daily_readers.subtitle_zh IS '中文副标题（takeaways.subtitle_zh，一句话点题），可空。';

COMMENT ON COLUMN daily_readers.tags IS '文章主题标签数组。A-3 起存中文 tags（takeaways.tags_zh）；scorer tags 仅存 pipeline_meta.score_tags 作选题参考。';

COMMIT;
