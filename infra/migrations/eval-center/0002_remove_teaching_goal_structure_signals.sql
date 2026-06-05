-- 0002: Remove teaching_goal, structure_signals from eval_example_lab_entries
--
-- These fields are removed as part of the grammar RAG reconstruction:
-- - teaching_goal: overlaps with variant (variant is the hard boundary)
-- - structure_signals: not curator-maintained data, should be runtime-derived
--
-- Also adds retrieval_text column if not exists (machine-derived embedding text).

-- 1. Drop CHECK constraint on teaching_goal
ALTER TABLE eval_example_lab_entries
    DROP CONSTRAINT IF EXISTS eval_example_lab_entries_teaching_goal_check;

-- 2. Drop columns
ALTER TABLE eval_example_lab_entries
    DROP COLUMN IF EXISTS teaching_goal,
    DROP COLUMN IF EXISTS structure_signals;

-- 3. Add retrieval_text column if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'eval_example_lab_entries'
        AND column_name = 'retrieval_text'
    ) THEN
        ALTER TABLE eval_example_lab_entries
            ADD COLUMN retrieval_text TEXT;
    END IF;
END $$;
