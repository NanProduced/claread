-- B2: Make reader_ask_supplements.analysis_record_id nullable.
--
-- Migration 0003 added the dual-scope CHECK constraint that allows
-- analysis_record_id IS NULL for Reading Record rows, but did not drop
-- the inherited NOT NULL constraint from migration 0001 (when the column
-- was named `record_id`). This made the Reading Record branch of the
-- CHECK constraint unreachable and caused INSERT failures for any
-- supplement created via the Reading Record path.
--
-- This migration completes the dual-scope contract: NULL is now allowed
-- for analysis_record_id so Reading Record supplements can persist.

ALTER TABLE reader_ask_supplements
    ALTER COLUMN analysis_record_id DROP NOT NULL;
