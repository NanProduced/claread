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
END $$;
