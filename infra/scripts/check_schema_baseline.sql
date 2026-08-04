-- DATA-SCHEMA-BASELINE D2 fail-closed guard for the single fresh baseline.
-- Run with: psql -v ON_ERROR_STOP=1 -f check_schema_baseline.sql
-- Verifies infra/migrations/0001_initial.sql end state:
--   1. exactly the 52 baseline tables exist,
--   2. legacy analysis / Eval control-plane tables are absent,
--   3. the confirmed legacy columns on protected shared tables are absent,
--   4. the contract CHECKs/indexes of the exited contracts are present.

DO $guard$
DECLARE
    expected_tables text[] := ARRAY[
        'ai_usage_events',
        'analysis_windows',
        'anchor_segments',
        'anonymous_quotas',
        'candidate_reading_documents',
        'confirmed_source_documents',
        'daily_readers',
        'dict_ai_candidate_entries',
        'dict_entries',
        'dict_lookup_targets',
        'dict_redirects',
        'enhancement_layers',
        'eval_example_lab_entries',
        'favorite_records',
        'feedback',
        'layer_analysis_plans',
        'llm_ask_config',
        'llm_ask_options',
        'llm_models',
        'llm_presets',
        'llm_profiles',
        'llm_providers',
        'original_inputs',
        'parsed_decisions',
        'pipeline_runs',
        'reader_article_rag_index_runs',
        'reader_ask_client_submissions',
        'reader_ask_messages',
        'reader_ask_supplements',
        'reader_ask_thread_memory',
        'reader_ask_threads',
        'reader_ask_turn_runs',
        'reader_event_sequences',
        'reader_events',
        'reader_job_events',
        'reader_jobs',
        'reader_notes',
        'reader_runs',
        'reader_runtime_spans',
        'reading_bases',
        'reading_records',
        'reading_units',
        'source_artifacts',
        'stable_document_blocks',
        'stable_reading_documents',
        'user_annotations',
        'user_credit_accounts',
        'user_credit_ledger',
        'user_identities',
        'user_sessions',
        'users',
        'vocabulary_book'
    ];
    tbl text;
    missing text[] := '{}';
    extra text[] := '{}';
    actual text;
BEGIN
    FOREACH tbl IN ARRAY expected_tables LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
              AND table_name = tbl
        ) THEN
            missing := array_append(missing, tbl);
        END IF;
    END LOOP;
    IF cardinality(missing) > 0 THEN
        RAISE EXCEPTION 'baseline tables missing: %', missing;
    END IF;

    FOREACH actual IN ARRAY ARRAY(
        SELECT t.table_name FROM information_schema.tables t
        WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
    ) LOOP
        IF NOT (actual = ANY (expected_tables)) THEN
            extra := array_append(extra, actual);
        END IF;
    END LOOP;
    IF cardinality(extra) > 0 THEN
        RAISE EXCEPTION 'tables outside the baseline present: %', extra;
    END IF;
END
$guard$;

DO $guard$
DECLARE
    banned_tables text[] := ARRAY[
        'analysis_records',
        'analysis_results',
        'analysis_tasks',
        'analysis_task_events',
        'analysis_overview_tasks',
        'analysis_overview_task_events',
        'analysis_debug_snapshots',
        'eval_prompt_variant_drafts',
        'eval_workflow_run_requests',
        'eval_workflow_compares',
        'eval_workflow_compare_judge_requests',
        'eval_judge_run_requests',
        'eval_review_notes',
        'eval_node_lab_candidate_drafts',
        'eval_node_lab_sessions',
        'eval_node_lab_trials',
        'eval_node_lab_judge_configs',
        'eval_node_lab_judge_requests',
        'eval_node_lab_review_notes',
        'reader_ask_eval_traces'
    ];
    tbl text;
    found text[] := '{}';
BEGIN
    FOREACH tbl IN ARRAY banned_tables LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            found := array_append(found, tbl);
        END IF;
    END LOOP;
    IF cardinality(found) > 0 THEN
        RAISE EXCEPTION 'legacy tables must not exist in the baseline: %', found;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (table_name, column_name) IN (
              ('user_annotations', 'analysis_record_id'),
              ('reader_notes', 'analysis_record_id'),
              ('reader_notes', 'anchor_sentence_id'),
              ('favorite_records', 'analysis_record_id'),
              ('feedback', 'analysis_record_id'),
              ('feedback', 'annotation_type'),
              ('dict_ai_candidate_entries', 'record_id'),
              ('ai_usage_events', 'record_id'),
              ('ai_usage_events', 'task_id'),
              ('user_credit_ledger', 'task_id'),
              ('reader_ask_threads', 'analysis_record_id'),
              ('reader_ask_turn_runs', 'analysis_record_id'),
              ('reader_ask_supplements', 'analysis_record_id')
          )
    ) THEN
        RAISE EXCEPTION 'legacy identity columns must be dropped from protected shared tables';
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'uq_ai_usage_events_invocation_key'
    ) THEN
        RAISE EXCEPTION 'missing uq_ai_usage_events_invocation_key index';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'reader_jobs_job_type_check'
          AND pg_get_constraintdef(oid) LIKE '%build_semantic_outline%'
    ) THEN
        RAISE EXCEPTION 'reader_jobs_job_type_check must carry the final 12-value job type set';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'stable_document_blocks_block_type_check'
          AND pg_get_constraintdef(oid) LIKE '%thematic_break%'
    ) THEN
        RAISE EXCEPTION 'stable_document_blocks_block_type_check must carry the final 15-value block type set';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'favorite_records_target_type_check'
          AND pg_get_constraintdef(oid) LIKE '%reading_record%'
          AND pg_get_constraintdef(oid) LIKE '%daily_reader_article%'
    ) THEN
        RAISE EXCEPTION 'favorite_records_target_type_check must be the exact daily_reader_article/reading_record union';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'reader_ask_threads_scope_check'
          AND pg_get_constraintdef(oid) LIKE '%reading_record_id IS NOT NULL%'
    ) THEN
        RAISE EXCEPTION 'reader_ask_threads_scope_check must be Reading Record only';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'user_annotations_text_anchor_payload_check'
          AND pg_get_constraintdef(oid) LIKE '%reading_record_id IS NOT NULL%'
          AND pg_get_constraintdef(oid) LIKE '%unit_end_utf16%'
    ) THEN
        RAISE EXCEPTION 'user_annotations_text_anchor_payload_check must be Reading Record anchor only';
    END IF;
END
$guard$;

SELECT 'schema baseline OK' AS check_schema_baseline;
