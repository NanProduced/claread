ALTER TABLE user_annotations
    DROP CONSTRAINT IF EXISTS user_annotations_text_anchor_payload_check;

ALTER TABLE user_annotations
    ADD CONSTRAINT user_annotations_text_anchor_payload_check
        CHECK (
            (
                anchor_type <> 'text_range'
                OR (
                    analysis_record_id IS NOT NULL
                    AND sentence_id IS NOT NULL
                    AND start_offset IS NOT NULL
                    AND end_offset IS NOT NULL
                    AND start_offset >= 0
                    AND end_offset > start_offset
                    AND text_hash IS NOT NULL
                )
            )
            AND (
                anchor_type <> 'multi_text'
                OR (
                    analysis_record_id IS NOT NULL
                    AND start_offset IS NULL
                    AND end_offset IS NULL
                    AND text_hash IS NULL
                    AND payload_json ? 'segments'
                    AND jsonb_typeof(payload_json->'segments') = 'array'
                    AND jsonb_array_length(payload_json->'segments') >= 2
                )
            )
        );

CREATE INDEX IF NOT EXISTS idx_favorite_records_user_record_updated
    ON favorite_records(user_id, analysis_record_id, updated_at DESC)
    WHERE analysis_record_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_user_annotations_user_record_updated
    ON user_annotations(user_id, analysis_record_id, updated_at DESC)
    WHERE analysis_record_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_favorite_records_user_target_updated
    ON favorite_records(user_id, target_type, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_user_annotations_user_anchor_updated
    ON user_annotations(user_id, anchor_type, updated_at DESC)
    WHERE deleted_at IS NULL;
