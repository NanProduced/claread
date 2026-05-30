CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS eval_node_probe_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'timeout')),
    node_name TEXT NOT NULL CHECK (node_name IN ('grammar', 'vocabulary', 'translation')),
    dry_run BOOLEAN NOT NULL DEFAULT false,

    reading_goal TEXT NOT NULL,
    reading_variant TEXT NOT NULL,
    source_type TEXT NOT NULL,
    input_text_hash TEXT NOT NULL,
    input_excerpt TEXT NOT NULL,
    input_text TEXT,

    prompt_mode TEXT NOT NULL CHECK (prompt_mode IN ('baseline', 'no_few_shot', 'variant')),
    prompt_variant_id TEXT,
    prompt_identity_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    prompt_preview TEXT,

    model_profile TEXT,
    model_identity_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    workflow_identity_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_identity_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    prepared_sentences_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    example_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    preprocess_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    node_output_json JSONB,
    warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    runtime_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_refs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_json JSONB,

    human_verdict TEXT CHECK (
        human_verdict IS NULL
        OR human_verdict IN ('good', 'bad', 'mixed', 'needs_review')
    ),
    human_notes TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    promote_candidate BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_eval_node_probe_runs_created
    ON eval_node_probe_runs (date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_node_probe_runs_node_created
    ON eval_node_probe_runs (node_name, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_node_probe_runs_prompt_variant
    ON eval_node_probe_runs (prompt_variant_id)
    WHERE prompt_variant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_eval_node_probe_runs_input_hash
    ON eval_node_probe_runs (input_text_hash);

COMMENT ON TABLE eval_node_probe_runs IS
    'Eval Center Node Probe interaction records. This is control-plane data, not formal workflow eval artifact storage.';
