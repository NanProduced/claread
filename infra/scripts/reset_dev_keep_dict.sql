-- ============================================================
-- reset_dev_keep_dict.sql
-- 开发库软重置:TRUNCATE 全部业务表(保留结构与受保护数据)
-- DATA-SCHEMA-BASELINE D2: 表清单与单一基线
-- infra/migrations/0001_initial.sql 精确对齐,但排除两类受保护数据:
--   - 词典三表 dict_entries/dict_lookup_targets/dict_redirects(约 205 万行)
--   - eval_example_lab_entries(受保护 Directus Collection,Example Lab 数据)
-- ============================================================

BEGIN;

TRUNCATE TABLE
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
  RESTART IDENTITY CASCADE;

COMMIT;
