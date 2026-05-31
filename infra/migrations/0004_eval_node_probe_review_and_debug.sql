ALTER TABLE IF EXISTS eval_node_probe_runs
    ADD COLUMN IF NOT EXISTS agent_instructions TEXT,
    ADD COLUMN IF NOT EXISTS rag_debug_json JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN eval_node_probe_runs.agent_instructions IS
    'Agent instructions returned by services/api node probe for prompt-packet review.';

COMMENT ON COLUMN eval_node_probe_runs.rag_debug_json IS
    'Sanitized RAG/debug evidence returned by services/api node probe.';

ALTER TABLE IF EXISTS eval_review_notes
    DROP CONSTRAINT IF EXISTS eval_review_notes_target_type_check;

ALTER TABLE IF EXISTS eval_review_notes
    ADD CONSTRAINT eval_review_notes_target_type_check CHECK (
        target_type IN ('workflow_run', 'case_artifact', 'ab_report', 'prompt_variant', 'node_probe_run')
    );

COMMENT ON TABLE eval_review_notes IS
    'Eval Center human review notes linked to workflow runs, node probe runs, case artifacts, A/B reports, or prompt variants. Notes are control-plane data and do not mutate evals/runs artifacts.';
