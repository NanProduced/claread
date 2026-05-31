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

CREATE TABLE IF NOT EXISTS eval_prompt_variant_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    variant_id TEXT NOT NULL UNIQUE CHECK (variant_id ~ '^[A-Za-z0-9._-]+$'),
    target TEXT NOT NULL DEFAULT 'article_analysis' CHECK (target IN ('article_analysis')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready_for_eval', 'archived')),
    scope TEXT NOT NULL DEFAULT 'workflow_eval' CHECK (scope IN ('node_probe', 'workflow_eval')),
    few_shot_mode TEXT NOT NULL DEFAULT 'off' CHECK (few_shot_mode IN ('off', 'baseline', 'variant', 'settings')),
    policies_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    examples_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    snapshot_hash TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_prompt_variant_drafts_status
    ON eval_prompt_variant_drafts (status, date_updated DESC);

COMMENT ON TABLE eval_prompt_variant_drafts IS
    'Eval-only prompt variant drafts. These records must not directly modify services/api business prompt YAML.';

CREATE TABLE IF NOT EXISTS eval_workflow_run_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    run_id TEXT NOT NULL UNIQUE CHECK (run_id ~ '^[A-Za-z0-9._-]+$'),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    dataset_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'workflow' CHECK (mode IN ('workflow')),
    eval_purpose TEXT NOT NULL DEFAULT 'dataset_regression' CHECK (
        eval_purpose IN ('dataset_regression', 'prompt_experiment', 'manual_debug')
    ),
    adapter_kind TEXT NOT NULL DEFAULT 'in_process' CHECK (adapter_kind IN ('fake', 'in_process', 'http')),
    runner_kind TEXT NOT NULL DEFAULT 'external_worker',
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    prompt_variant_id TEXT,
    prompt_variant_snapshot_hash TEXT,
    artifact_run_id TEXT,
    artifact_path TEXT,
    source_request_id UUID REFERENCES eval_workflow_run_requests(id),
    attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= attempt_no),
    retry_reason TEXT,
    max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency >= 1),
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_json JSONB,
    notes TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb
);

ALTER TABLE IF EXISTS eval_workflow_run_requests
    ADD COLUMN IF NOT EXISTS source_request_id UUID REFERENCES eval_workflow_run_requests(id);

ALTER TABLE IF EXISTS eval_workflow_run_requests
    ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1);

ALTER TABLE IF EXISTS eval_workflow_run_requests
    ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= attempt_no);

ALTER TABLE IF EXISTS eval_workflow_run_requests
    ADD COLUMN IF NOT EXISTS retry_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_eval_workflow_run_requests_status_created
    ON eval_workflow_run_requests (status, date_created ASC);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_run_requests_prompt_variant
    ON eval_workflow_run_requests (prompt_variant_id)
    WHERE prompt_variant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_eval_workflow_run_requests_source_request
    ON eval_workflow_run_requests (source_request_id)
    WHERE source_request_id IS NOT NULL;

COMMENT ON TABLE eval_workflow_run_requests IS
    'Eval Center runner bridge request queue. Directus creates requests; an external worker performs execution and writes evals/runs artifacts.';

CREATE TABLE IF NOT EXISTS eval_review_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    target_type TEXT NOT NULL CHECK (
        target_type IN ('workflow_run', 'case_artifact', 'ab_report', 'prompt_variant')
    ),
    target_id TEXT NOT NULL,
    run_id TEXT,
    case_id TEXT,
    ab_report_id TEXT,
    prompt_variant_id TEXT,
    verdict TEXT CHECK (
        verdict IS NULL
        OR verdict IN ('good', 'bad', 'mixed', 'needs_review', 'win', 'loss', 'tie', 'blocked')
    ),
    note TEXT NOT NULL DEFAULT '',
    promote_candidate BOOLEAN NOT NULL DEFAULT false,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_eval_review_notes_target
    ON eval_review_notes (target_type, target_id, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_review_notes_run
    ON eval_review_notes (run_id, date_created DESC)
    WHERE run_id IS NOT NULL;

COMMENT ON TABLE eval_review_notes IS
    'Eval Center human review notes linked to workflow runs, case artifacts, A/B reports, or prompt variants. Notes are control-plane data and do not mutate evals/runs artifacts.';
