CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS eval_node_lab_candidate_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    candidate_id TEXT NOT NULL UNIQUE CHECK (candidate_id ~ '^[A-Za-z0-9._-]+$'),
    node_name TEXT NOT NULL CHECK (node_name IN ('grammar', 'vocabulary', 'translation')),
    label TEXT NOT NULL,
    description TEXT,
    source_kind TEXT NOT NULL DEFAULT 'baseline_clone' CHECK (
        source_kind IN ('baseline_clone', 'draft', 'ad_hoc', 'file_import')
    ),
    edit_mode TEXT NOT NULL DEFAULT 'structured' CHECK (edit_mode IN ('structured', 'raw')),
    instruction_layer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_layer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    few_shot_layer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_layer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized_manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    draft_hash TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready', 'archived')),
    notes TEXT,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_candidate_drafts_node_updated
    ON eval_node_lab_candidate_drafts (node_name, date_updated DESC NULLS LAST, date_created DESC);

CREATE TABLE IF NOT EXISTS eval_node_lab_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    session_id TEXT NOT NULL UNIQUE CHECK (session_id ~ '^[A-Za-z0-9._-]+$'),
    node_name TEXT NOT NULL CHECK (node_name IN ('grammar', 'vocabulary', 'translation')),
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'drafting' CHECK (
        status IN ('drafting', 'active', 'paused', 'reviewed', 'archived')
    ),
    allowed_workspace_types_json JSONB NOT NULL DEFAULT '["single_run","baseline_compare","judge_compare"]'::jsonb,
    baseline_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    baseline_snapshot_hash TEXT,
    candidate_registry_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    judge_config_snapshot_json JSONB,
    judge_config_snapshot_hash TEXT,
    aggregate_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_sessions_node_updated
    ON eval_node_lab_sessions (node_name, date_updated DESC NULLS LAST, date_created DESC);

CREATE TABLE IF NOT EXISTS eval_node_lab_trials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    trial_id TEXT NOT NULL UNIQUE CHECK (trial_id ~ '^[A-Za-z0-9._-]+$'),
    session_id TEXT REFERENCES eval_node_lab_sessions(session_id) ON DELETE SET NULL,
    node_name TEXT NOT NULL CHECK (node_name IN ('grammar', 'vocabulary', 'translation')),
    workspace_type TEXT NOT NULL CHECK (workspace_type IN ('single_run', 'baseline_compare', 'judge_compare')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    execution_mode TEXT NOT NULL DEFAULT 'sync' CHECK (execution_mode IN ('sync', 'background')),
    input_text_hash TEXT NOT NULL,
    input_excerpt TEXT NOT NULL,
    reading_goal TEXT NOT NULL,
    reading_variant TEXT NOT NULL,
    source_type TEXT NOT NULL,
    baseline_snapshot_hash TEXT,
    candidate_snapshot_hashes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    judge_config_snapshot_hash TEXT,
    result_kind TEXT NOT NULL CHECK (
        result_kind IN ('single_run_result', 'compare_result', 'judge_compare_result')
    ),
    result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_path TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_json JSONB,
    review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (
        review_state IN ('unreviewed', 'needs_followup', 'accepted', 'rejected')
    ),
    decision_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_trials_session_created
    ON eval_node_lab_trials (session_id, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_trials_node_created
    ON eval_node_lab_trials (node_name, date_created DESC);

CREATE TABLE IF NOT EXISTS eval_node_lab_judge_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    judge_config_id TEXT NOT NULL UNIQUE CHECK (judge_config_id ~ '^[A-Za-z0-9._-]+$'),
    node_name TEXT NOT NULL CHECK (node_name IN ('grammar', 'vocabulary', 'translation')),
    label TEXT NOT NULL,
    description TEXT,
    judge_mode TEXT NOT NULL CHECK (
        judge_mode IN ('rubric_score_only', 'rubric_plus_pairwise', 'persona_pairwise', 'anti_template_probe', 'raw')
    ),
    rubric_source_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    persona_json JSONB,
    prompt_templates_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_schema_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    judger_models_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    normalized_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    draft_hash TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready', 'archived')),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_judge_configs_node_updated
    ON eval_node_lab_judge_configs (node_name, date_updated DESC NULLS LAST, date_created DESC);

CREATE TABLE IF NOT EXISTS eval_node_lab_judge_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    judge_request_id TEXT NOT NULL UNIQUE CHECK (judge_request_id ~ '^[A-Za-z0-9._-]+$'),
    trial_id TEXT NOT NULL REFERENCES eval_node_lab_trials(trial_id) ON DELETE CASCADE,
    session_id TEXT REFERENCES eval_node_lab_sessions(session_id) ON DELETE SET NULL,
    node_name TEXT NOT NULL CHECK (node_name IN ('grammar', 'vocabulary', 'translation')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    judge_config_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    judge_config_snapshot_hash TEXT,
    participants_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_path TEXT,
    source_request_id UUID REFERENCES eval_node_lab_judge_requests(id),
    attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= attempt_no),
    retry_reason TEXT,
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_json JSONB,
    notes TEXT,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_judge_requests_status_created
    ON eval_node_lab_judge_requests (status, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_judge_requests_session_created
    ON eval_node_lab_judge_requests (session_id, date_created DESC);

CREATE TABLE IF NOT EXISTS eval_node_lab_review_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    target_type TEXT NOT NULL CHECK (
        target_type IN ('session', 'trial', 'judge_request', 'candidate', 'judge_config')
    ),
    target_id TEXT NOT NULL,
    session_id TEXT,
    trial_id TEXT,
    judge_request_id TEXT,
    candidate_id TEXT,
    judge_config_id TEXT,
    verdict TEXT CHECK (
        verdict IS NULL
        OR verdict IN ('good', 'bad', 'mixed', 'needs_review', 'win', 'loss', 'tie', 'blocked')
    ),
    note TEXT NOT NULL DEFAULT '',
    promote_candidate BOOLEAN NOT NULL DEFAULT false,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_eval_node_lab_review_notes_target
    ON eval_node_lab_review_notes (target_type, target_id, date_created DESC);
