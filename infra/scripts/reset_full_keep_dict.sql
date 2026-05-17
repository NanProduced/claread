-- ============================================================
-- reset_full_keep_dict.sql
-- 完整重置开发库：删除所有业务表后重建，保留词典数据（dict_*）
--
-- 适用场景：表结构变更后需要重建所有表，但不想重新导入词典
-- 使用方式：
--   1. 执行本脚本（DROP 业务表）
--   2. 执行 initial.sql（重建所有表）
--      dict_* 三表使用 IF NOT EXISTS，已存在时安全跳过
--
-- 词典三表数据量约 205 万行 / 1.25 GB，重新导入需 20+ 分钟，
-- 且 exam_tags 字段需额外脚本标注，因此重置时必须保留。
-- ============================================================

BEGIN;

DROP TABLE IF EXISTS
  user_annotations,
  dict_ai_candidate_entries,
  ai_usage_events,
  analysis_audit_logs,
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
CASCADE;

COMMIT;
