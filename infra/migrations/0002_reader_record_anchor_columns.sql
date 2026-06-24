-- D6-U4 V1c single-range persistence
--
-- Adds nullable Reading Record anchor columns to user_annotations and
-- reader_notes so new Reading Record writes can persist alongside legacy
-- analysis_record_id rows. Legacy rows keep their existing semantics;
-- new rows carry reading_record_id / base_id / generation / unit_id /
-- anchor_segment_id / unit_start_utf16 / unit_end_utf16 and leave
-- analysis_record_id NULL.
--
-- hash_algorithm is NOT added as a column: it is a code-level constant
-- (fnv1a32-utf16) shared by all rows, not per-row data.
--
-- Foreign keys to reading_bases are NOT added in V1c. Rationale:
--   1. The anchor gate (load_validated_reading_record_anchor) already
--      validates reading_record_id / base_id / generation against
--      reading_bases at runtime; FKs would be defense-in-depth, not the
--      primary validation.
--   2. reading_bases uses ON DELETE CASCADE from reading_records, so
--      hard-deleting a Reading Record cascades to its bases. Adding a
--      FK from user_annotations / reader_notes to reading_bases would
--      force a premature cascade decision (CASCADE deletes user data,
--      SET NULL leaves orphans, RESTRICT blocks base cleanup).
--   3. Product semantics for "what happens to user assets when a
--      Reading Record / Base is deleted" are not yet finalized.
--   Follow-up: revisit FKs once deletion / archival semantics are
--   decided. Candidate target: reading_bases(id, reading_record_id,
--   record_generation) via uq_reading_bases_id_record_generation.

-- ============================================================
-- user_annotations
-- ============================================================

ALTER TABLE user_annotations
    ADD COLUMN reading_record_id UUID,
    ADD COLUMN base_id UUID,
    ADD COLUMN generation INTEGER,
    ADD COLUMN unit_id TEXT,
    ADD COLUMN anchor_segment_id TEXT,
    ADD COLUMN unit_start_utf16 INTEGER,
    ADD COLUMN unit_end_utf16 INTEGER;

-- Replace the payload CHECK so anchor_type = 'text_range' accepts either
-- the legacy analysis_record_id path OR the new Reading Record path.
ALTER TABLE user_annotations
    DROP CONSTRAINT user_annotations_text_anchor_payload_check;

ALTER TABLE user_annotations
    ADD CONSTRAINT user_annotations_text_anchor_payload_check
        CHECK (
            (
                anchor_type <> 'text_range'
                OR (
                    -- legacy path
                    (
                        analysis_record_id IS NOT NULL
                        AND sentence_id IS NOT NULL
                        AND start_offset IS NOT NULL
                        AND end_offset IS NOT NULL
                        AND start_offset >= 0
                        AND end_offset > start_offset
                        AND text_hash IS NOT NULL
                    )
                    OR
                    -- Reading Record path
                    (
                        reading_record_id IS NOT NULL
                        AND base_id IS NOT NULL
                        AND generation IS NOT NULL
                        AND generation >= 1
                        AND unit_id IS NOT NULL
                        AND anchor_segment_id IS NOT NULL
                        AND unit_start_utf16 IS NOT NULL
                        AND unit_end_utf16 IS NOT NULL
                        AND unit_start_utf16 >= 0
                        AND unit_end_utf16 > unit_start_utf16
                        AND text_hash IS NOT NULL
                        AND analysis_record_id IS NULL
                    )
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

-- Reading Record family lookup index
CREATE INDEX idx_user_annotations_reading_record
    ON user_annotations (user_id, reading_record_id, base_id, generation)
    WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL;

-- Reading Record family partial unique index (dedup for active anchors)
CREATE UNIQUE INDEX uq_user_annotations_reading_record_anchor
    ON user_annotations (
        user_id, reading_record_id, base_id, anchor_segment_id,
        unit_start_utf16, unit_end_utf16, text_hash
    )
    WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL;

-- ============================================================
-- reader_notes
-- ============================================================

-- Make analysis_record_id nullable so new Reading Record rows can leave
-- it NULL. The existing UNIQUE (user_id, analysis_record_id, target_key)
-- constraint still works: PostgreSQL treats NULLs as distinct, so multiple
-- rows with analysis_record_id = NULL do not conflict.
ALTER TABLE reader_notes
    ALTER COLUMN analysis_record_id DROP NOT NULL;

-- Make anchor_sentence_id nullable so new Reading Record rows can leave
-- it NULL. New rows use anchor_segment_id as the authority.
ALTER TABLE reader_notes
    ALTER COLUMN anchor_sentence_id DROP NOT NULL;

ALTER TABLE reader_notes
    ADD COLUMN reading_record_id UUID,
    ADD COLUMN base_id UUID,
    ADD COLUMN generation INTEGER,
    ADD COLUMN unit_id TEXT,
    ADD COLUMN anchor_segment_id TEXT,
    ADD COLUMN unit_start_utf16 INTEGER,
    ADD COLUMN unit_end_utf16 INTEGER;

-- Reading Record family lookup index
CREATE INDEX idx_reader_notes_reading_record
    ON reader_notes (user_id, reading_record_id, base_id, generation)
    WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL;

-- Reading Record family partial unique index (dedup for active anchors)
CREATE UNIQUE INDEX uq_reader_notes_reading_record_anchor
    ON reader_notes (
        user_id, reading_record_id, base_id, anchor_segment_id,
        unit_start_utf16, unit_end_utf16, text_hash
    )
    WHERE reading_record_id IS NOT NULL AND deleted_at IS NULL;
