SELECT 'dict_entries' AS table_name, COUNT(*) AS rows, MIN(id) AS min_id, MAX(id) AS max_id FROM dict_entries
UNION ALL
SELECT 'dict_lookup_targets', COUNT(*), MIN(id), MAX(id) FROM dict_lookup_targets
UNION ALL
SELECT 'dict_redirects', COUNT(*), MIN(id), MAX(id) FROM dict_redirects
ORDER BY table_name;

SELECT COUNT(*) AS entries_with_exam_tags
FROM dict_entries
WHERE exam_tags IS NOT NULL AND cardinality(exam_tags) > 0;

SELECT 'dict_entries_id_seq' AS sequence_name, last_value, is_called FROM dict_entries_id_seq
UNION ALL
SELECT 'dict_lookup_targets_id_seq', last_value, is_called FROM dict_lookup_targets_id_seq
UNION ALL
SELECT 'dict_redirects_id_seq', last_value, is_called FROM dict_redirects_id_seq
ORDER BY sequence_name;
