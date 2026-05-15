ALTER TABLE favorite_records
  DROP CONSTRAINT IF EXISTS favorite_records_target_type_check;

ALTER TABLE favorite_records
  ADD CONSTRAINT favorite_records_target_type_check
  CHECK (target_type IN (
    'analysis_record',
    'sentence',
    'paragraph',
    'phrase',
    'vocab',
    'text_range'
  ));
