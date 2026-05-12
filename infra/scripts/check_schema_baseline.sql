SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'daily_readers'
  AND column_name IN ('paragraph_notes_json', 'takeaways_json')
ORDER BY column_name;

SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
    'idx_vocabulary_book_dict_entry_id',
    'idx_dict_entries_source_entry_key',
    'idx_dict_entries_display_headword_lower',
    'idx_dict_entries_base_headword_lower',
    'idx_dict_lookup_targets_form_rank',
    'idx_dict_lookup_targets_entry_id',
    'idx_dict_redirects_key',
    'idx_dict_redirects_target'
  )
ORDER BY indexname;
