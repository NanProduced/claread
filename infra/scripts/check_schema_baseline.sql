DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_notes'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_notes';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'user_annotations'
  ) THEN
    RAISE EXCEPTION 'missing table: user_annotations';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'ai_usage_events'
  ) THEN
    RAISE EXCEPTION 'missing table: ai_usage_events';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'dict_ai_candidate_entries'
  ) THEN
    RAISE EXCEPTION 'missing table: dict_ai_candidate_entries';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_ask_threads'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_ask_threads';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_ask_messages'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_ask_messages';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_ask_supplements'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_ask_supplements';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_ask_turn_runs'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_ask_turn_runs';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_ask_eval_traces'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_ask_eval_traces';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'analysis_overview_tasks'
  ) THEN
    RAISE EXCEPTION 'missing table: analysis_overview_tasks';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'analysis_overview_task_events'
  ) THEN
    RAISE EXCEPTION 'missing table: analysis_overview_task_events';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reading_records'
  ) THEN
    RAISE EXCEPTION 'missing table: reading_records';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reading_bases'
  ) THEN
    RAISE EXCEPTION 'missing table: reading_bases';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'original_inputs'
  ) THEN
    RAISE EXCEPTION 'missing table: original_inputs';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reading_units'
  ) THEN
    RAISE EXCEPTION 'missing table: reading_units';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'anchor_segments'
  ) THEN
    RAISE EXCEPTION 'missing table: anchor_segments';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_runs'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_runs';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_jobs'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_jobs';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_job_events'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_job_events';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_event_sequences'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_event_sequences';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reader_events'
  ) THEN
    RAISE EXCEPTION 'missing table: reader_events';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'enhancement_layers'
  ) THEN
    RAISE EXCEPTION 'missing table: enhancement_layers';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'parsed_decisions'
  ) THEN
    RAISE EXCEPTION 'missing table: parsed_decisions';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'reader_notes'
      AND column_name = 'anchor_sentence_id'
  ) THEN
    RAISE EXCEPTION 'missing column: reader_notes.anchor_sentence_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'reader_notes'
      AND column_name = 'quote_mode'
  ) THEN
    RAISE EXCEPTION 'missing column: reader_notes.quote_mode';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'reader_notes'
      AND column_name = 'note_text'
  ) THEN
    RAISE EXCEPTION 'missing column: reader_notes.note_text';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'user_annotations'
      AND column_name IN ('note', 'annotation_type')
  ) THEN
    RAISE EXCEPTION 'unexpected legacy columns remain on user_annotations';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'reader_ask_messages'
      AND column_name = 'metadata_json'
  ) THEN
    RAISE EXCEPTION 'missing column: reader_ask_messages.metadata_json';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'reader_ask_messages'
      AND column_name = 'current_turn_run_id'
  ) THEN
    RAISE EXCEPTION 'missing column: reader_ask_messages.current_turn_run_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'ai_usage_events'
      AND column_name = 'reading_record_id'
  ) THEN
    RAISE EXCEPTION 'missing column: ai_usage_events.reading_record_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'ai_usage_events'
      AND column_name = 'reader_run_id'
  ) THEN
    RAISE EXCEPTION 'missing column: ai_usage_events.reader_run_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'ai_usage_events'
      AND column_name = 'reader_job_id'
  ) THEN
    RAISE EXCEPTION 'missing column: ai_usage_events.reader_job_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'ai_usage_events'
      AND column_name = 'enhancement_layer_id'
  ) THEN
    RAISE EXCEPTION 'missing column: ai_usage_events.enhancement_layer_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'ai_usage_events'
      AND column_name = 'operation_fingerprint'
  ) THEN
    RAISE EXCEPTION 'missing column: ai_usage_events.operation_fingerprint';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'user_credit_ledger'
      AND column_name = 'subject_type'
  ) THEN
    RAISE EXCEPTION 'missing column: user_credit_ledger.subject_type';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'user_credit_ledger'
      AND column_name = 'subject_id'
  ) THEN
    RAISE EXCEPTION 'missing column: user_credit_ledger.subject_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'user_credit_ledger'
      AND column_name = 'reading_record_id'
  ) THEN
    RAISE EXCEPTION 'missing column: user_credit_ledger.reading_record_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'user_credit_ledger'
      AND column_name = 'reader_run_id'
  ) THEN
    RAISE EXCEPTION 'missing column: user_credit_ledger.reader_run_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'user_credit_ledger'
      AND column_name = 'reader_job_id'
  ) THEN
    RAISE EXCEPTION 'missing column: user_credit_ledger.reader_job_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'reading_records'
      AND column_name = 'generation'
  ) THEN
    RAISE EXCEPTION 'missing column: reading_records.generation';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_reader_notes_record_created'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_reader_notes_record_created';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_reader_notes_anchor_sentence'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_reader_notes_anchor_sentence';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_ai_usage_events_record'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_ai_usage_events_record';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_ai_usage_events_reading_record'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_ai_usage_events_reading_record';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_ai_usage_events_reader_run'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_ai_usage_events_reader_run';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_ai_usage_events_reader_job'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_ai_usage_events_reader_job';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_ai_usage_events_enhancement_layer'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_ai_usage_events_enhancement_layer';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'idx_ai_usage_events_operation_fingerprint'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_ai_usage_events_operation_fingerprint';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_credit_ledger_subject'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_credit_ledger_subject';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_credit_ledger_reading_record'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_credit_ledger_reading_record';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_credit_ledger_reader_run'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_credit_ledger_reader_run';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_credit_ledger_reader_job'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_credit_ledger_reader_job';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_dict_ai_candidates_usage_event'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_dict_ai_candidates_usage_event';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_reader_ask_default_thread'
  ) THEN
    RAISE EXCEPTION 'missing index: uq_reader_ask_default_thread';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_reader_ask_messages_current_turn_run'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_reader_ask_messages_current_turn_run';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_reader_ask_turn_runs_usage_event'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_reader_ask_turn_runs_usage_event';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_analysis_overview_tasks_record_active'
  ) THEN
    RAISE EXCEPTION 'missing index: uq_analysis_overview_tasks_record_active';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_vocabulary_book_dict_entry_id'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_vocabulary_book_dict_entry_id';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_dict_entries_source_entry_key'
  ) THEN
    RAISE EXCEPTION 'missing index: idx_dict_entries_source_entry_key';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_reading_records_user_client_active'
  ) THEN
    RAISE EXCEPTION 'missing index: uq_reading_records_user_client_active';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_reader_jobs_active_fingerprint'
  ) THEN
    RAISE EXCEPTION 'missing index: uq_reader_jobs_active_fingerprint';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'uq_enhancement_layers_active_published'
  ) THEN
    RAISE EXCEPTION 'missing index: uq_enhancement_layers_active_published';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'ai_usage_events'
      AND c.conname = 'fk_ai_usage_events_reading_record'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_ai_usage_events_reading_record';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'ai_usage_events'
      AND c.conname = 'fk_ai_usage_events_reader_run'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_ai_usage_events_reader_run';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'ai_usage_events'
      AND c.conname = 'fk_ai_usage_events_reader_job'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_ai_usage_events_reader_job';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'ai_usage_events'
      AND c.conname = 'fk_ai_usage_events_enhancement_layer'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_ai_usage_events_enhancement_layer';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'user_credit_ledger'
      AND c.conname = 'fk_user_credit_ledger_reading_record'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_user_credit_ledger_reading_record';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'user_credit_ledger'
      AND c.conname = 'fk_user_credit_ledger_reader_run'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_user_credit_ledger_reader_run';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'user_credit_ledger'
      AND c.conname = 'fk_user_credit_ledger_reader_job'
  ) THEN
    RAISE EXCEPTION 'missing constraint: fk_user_credit_ledger_reader_job';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'reader_jobs'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%build_grammar_bundle%'
  ) THEN
    RAISE EXCEPTION 'reader_jobs job_type check missing build_grammar_bundle';
  END IF;
END $$;
