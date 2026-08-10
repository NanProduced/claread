-- ============================================================
-- reset_full_keep_dict.sql
-- 完整重置开发库:删除所有业务表后重建,保留受保护数据
--
-- 适用场景:表结构变更后需要重建所有表,但不想重新导入词典
-- 使用方式:
--   1. 执行本脚本(DROP 业务表)
--   2. 执行 infra/migrations/0001_initial.sql(重建所有表)
--      dict_* 三表与 eval_example_lab_entries 使用 IF NOT EXISTS,
--      已存在时安全跳过
--
-- 词典三表数据量约 205 万行 / 1.25 GB,重新导入需 20+ 分钟,
-- 且 exam_tags 字段需额外脚本标注,因此重置时必须保留。
-- eval_example_lab_entries 是受保护的 Example Lab 数据,同样保留。
-- DATA-SCHEMA-BASELINE D2: DROP 清单与单一基线精确对齐
-- (49 张非保护表;无 legacy analysis / Eval 表残留)。
-- ============================================================

BEGIN;

DROP TABLE IF EXISTS
  ai_model_execution_journal,
  ai_usage_events,
  analysis_windows,
  anchor_segments,
  anonymous_quotas,
  candidate_reading_documents,
  confirmed_source_documents,
  daily_readers,
  dict_ai_candidate_entries,
  enhancement_layers,
  favorite_records,
  feedback,
  layer_analysis_plans,
  llm_ask_config,
  llm_ask_options,
  llm_models,
  llm_presets,
  llm_profiles,
  llm_providers,
  original_inputs,
  parsed_decisions,
  pipeline_runs,
  reader_article_rag_index_runs,
  reader_ask_client_submissions,
  reader_ask_messages,
  reader_ask_supplements,
  reader_ask_thread_memory,
  reader_ask_threads,
  reader_ask_turn_runs,
  reader_event_sequences,
  reader_events,
  reader_job_events,
  reader_jobs,
  reader_notes,
  reader_runs,
  reader_runtime_spans,
  reading_bases,
  reading_records,
  reading_units,
  source_artifacts,
  stable_document_blocks,
  stable_reading_documents,
  user_annotations,
  user_credit_accounts,
  user_credit_ledger,
  user_identities,
  user_sessions,
  users,
  vocabulary_book
  CASCADE;

COMMIT;
