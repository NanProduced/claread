-- 0005: Add Reading Record anchor columns to dict_ai_candidate_entries
--
-- 词典 AI candidates 原先只通过 record_id 关联 analysis_records（旧 AI Workflow）。
-- 新 agentic orchestration 架构下，需要同时支持 Reading Record anchor，
-- 以便未来读取路径能按 Reading Record 分组、排除纯旧记录行。
--
-- 保留 record_id 列不删除（旧 AI Workflow 对照期），新增 reading_record_id / base_id / generation。

ALTER TABLE dict_ai_candidate_entries
    ADD COLUMN IF NOT EXISTS reading_record_id UUID REFERENCES reading_records(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS base_id UUID REFERENCES reading_bases(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS generation INT;

CREATE INDEX IF NOT EXISTS idx_dict_ai_candidates_reading_record
    ON dict_ai_candidate_entries(reading_record_id, created_at DESC)
    WHERE reading_record_id IS NOT NULL;
