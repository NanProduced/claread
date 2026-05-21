-- ============================================================
-- reset_dev_keep_dict.sql
-- 重置开发库：清空所有业务表，保留词典数据（dict_*）
--
-- 词典三表数据量约 205 万行 / 1.25 GB，重新导入需 20+ 分钟，
-- 且 exam_tags 字段需额外脚本标注，因此重置时必须保留。
--
-- 保留的表：dict_entries, dict_lookup_targets, dict_redirects
-- 保留的函数/触发器：set_updated_at() 及各 trg_*_set_updated_at
-- ============================================================

BEGIN;

TRUNCATE TABLE
  reader_ask_eval_traces,
  reader_ask_turn_runs,
  reader_ask_supplements,
  reader_ask_messages,
  reader_ask_threads,
  reader_notes,
  user_annotations,
  dict_ai_candidate_entries,
  ai_usage_events,
  analysis_task_events,
  analysis_tasks,
  analysis_results,
  favorite_records,
  vocabulary_book,
  feedback,
  user_credit_ledger,
  user_credit_accounts,
  daily_readers,
  pipeline_runs,
  user_sessions,
  user_identities,
  analysis_records,
  anonymous_quotas,
  users
RESTART IDENTITY CASCADE;

COMMIT;
