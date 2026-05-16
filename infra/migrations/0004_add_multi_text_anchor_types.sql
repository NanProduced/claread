ALTER TABLE user_annotations
    DROP CONSTRAINT IF EXISTS user_annotations_anchor_type_check;

ALTER TABLE user_annotations
    ADD CONSTRAINT user_annotations_anchor_type_check
        CHECK (anchor_type IN ('sentence', 'paragraph', 'text_range', 'multi_text'));

ALTER TABLE favorite_records
    DROP CONSTRAINT IF EXISTS favorite_records_target_type_check;

ALTER TABLE favorite_records
    ADD CONSTRAINT favorite_records_target_type_check
        CHECK (target_type IN ('analysis_record', 'sentence', 'paragraph', 'phrase', 'vocab', 'text_range', 'multi_text'));
