CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- Eval Center / Directus 控制面表
-- 说明：
-- - 本文件只负责 Eval Center 控制面 PostgreSQL 物理表
-- - 不负责 Directus metadata（directus_collections / fields / permissions）
-- - 不负责 Claread 业务表
-- - 本文件描述当前 Eval Center 最终态控制面表
-- ============================================================

-- ------------------------------------------------------------
-- Workflow / 通用 eval
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS eval_prompt_variant_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    variant_id TEXT NOT NULL UNIQUE CHECK (variant_id ~ '^[A-Za-z0-9._-]+$'),
    target TEXT NOT NULL DEFAULT 'article_analysis' CHECK (target IN ('article_analysis')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready_for_eval', 'archived')),
    scope TEXT NOT NULL DEFAULT 'workflow_lab' CHECK (scope IN ('workflow_lab')),
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
    'Eval-only prompt/workflow variant drafts. These records must not directly mutate business prompt YAML.';

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
    adapter_kind TEXT NOT NULL DEFAULT 'in_process' CHECK (adapter_kind IN ('fake', 'in_process', 'http', 'directus_async')),
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
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    custom_title TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_run_requests_status_created
    ON eval_workflow_run_requests (status, date_created ASC);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_run_requests_prompt_variant
    ON eval_workflow_run_requests (prompt_variant_id)
    WHERE prompt_variant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_eval_workflow_run_requests_source_request
    ON eval_workflow_run_requests (source_request_id)
    WHERE source_request_id IS NOT NULL;

COMMENT ON TABLE eval_workflow_run_requests IS
    'Workflow dataset run request queue and control-plane records.';

CREATE TABLE IF NOT EXISTS eval_judge_run_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    judge_run_id TEXT NOT NULL CHECK (judge_run_id ~ '^[A-Za-z0-9._-]+$'),
    run_id TEXT NOT NULL CHECK (run_id ~ '^[A-Za-z0-9._-]+$'),
    rubric_id TEXT NOT NULL CHECK (rubric_id ~ '^[A-Za-z0-9._-]+$'),
    rubric_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'partial_failure')
    ),
    judge_adapter_kind TEXT NOT NULL DEFAULT 'llm' CHECK (
        judge_adapter_kind IN ('fake', 'llm')
    ),
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_path TEXT,
    source_request_id UUID REFERENCES eval_judge_run_requests(id),
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
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,

    UNIQUE (run_id, judge_run_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_judge_run_requests_status_created
    ON eval_judge_run_requests (status, date_created ASC);

CREATE INDEX IF NOT EXISTS idx_eval_judge_run_requests_run
    ON eval_judge_run_requests (run_id, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_judge_run_requests_source_request
    ON eval_judge_run_requests (source_request_id)
    WHERE source_request_id IS NOT NULL;

COMMENT ON TABLE eval_judge_run_requests IS
    'Workflow judge request queue and immutable judge artifact index.';

CREATE TABLE IF NOT EXISTS eval_review_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    target_type TEXT NOT NULL CHECK (
        target_type IN ('workflow_run', 'case_artifact', 'workflow_compare', 'prompt_variant')
    ),
    target_id TEXT NOT NULL,
    run_id TEXT,
    case_id TEXT,
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
    'Workflow / prompt compare human review notes. Control-plane only.';

CREATE TABLE IF NOT EXISTS eval_workflow_compares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    compare_id TEXT NOT NULL UNIQUE CHECK (compare_id ~ '^[A-Za-z0-9._-]+$'),
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('single_run_compare', 'history_compare')
    ),
    status TEXT NOT NULL DEFAULT 'complete' CHECK (
        status IN ('complete', 'partial_failure', 'failed')
    ),
    baseline_run_id TEXT NOT NULL CHECK (baseline_run_id ~ '^[A-Za-z0-9._-]+$'),
    candidate_run_id TEXT NOT NULL CHECK (candidate_run_id ~ '^[A-Za-z0-9._-]+$'),
    input_hash TEXT,
    experiment_fingerprint TEXT
        CHECK (experiment_fingerprint IS NULL OR experiment_fingerprint ~ '^[0-9a-f]{16}$'),
    reading_goal TEXT,
    reading_variant TEXT,
    source_type TEXT,
    artifact_path TEXT NOT NULL,
    report_id TEXT NOT NULL CHECK (report_id ~ '^[A-Za-z0-9._-]+$'),
    case_count INTEGER NOT NULL DEFAULT 0 CHECK (case_count >= 0),
    wins INTEGER NOT NULL DEFAULT 0 CHECK (wins >= 0),
    losses INTEGER NOT NULL DEFAULT 0 CHECK (losses >= 0),
    ties INTEGER NOT NULL DEFAULT 0 CHECK (ties >= 0),
    identity_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT,
    custom_title TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_compares_created
    ON eval_workflow_compares (date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_compares_candidate
    ON eval_workflow_compares (candidate_run_id, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_compares_fingerprint_created
    ON eval_workflow_compares (experiment_fingerprint, date_created DESC)
    WHERE experiment_fingerprint IS NOT NULL;

COMMENT ON TABLE eval_workflow_compares IS
    'Workflow Lab compare control-plane records. This is the only user-facing Workflow history object.';

COMMENT ON COLUMN eval_workflow_compares.experiment_fingerprint IS
    'Stable sha1(16hex) over workflow compare experiment conditions. Used for cross-experiment grouping only; never used to deduplicate or reuse compare rows.';

CREATE TABLE IF NOT EXISTS eval_workflow_compare_judge_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    judge_run_id TEXT NOT NULL CHECK (judge_run_id ~ '^[A-Za-z0-9._-]+$'),
    compare_id TEXT NOT NULL REFERENCES eval_workflow_compares(compare_id) ON DELETE CASCADE,
    baseline_run_id TEXT NOT NULL CHECK (baseline_run_id ~ '^[A-Za-z0-9._-]+$'),
    candidate_run_id TEXT NOT NULL CHECK (candidate_run_id ~ '^[A-Za-z0-9._-]+$'),
    rubric_id TEXT NOT NULL CHECK (rubric_id ~ '^[A-Za-z0-9._-]+$'),
    rubric_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'partial_failure')
    ),
    judge_adapter_kind TEXT NOT NULL DEFAULT 'llm' CHECK (
        judge_adapter_kind IN ('fake', 'llm')
    ),
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_path TEXT,
    source_request_id UUID REFERENCES eval_workflow_compare_judge_requests(id),
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
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,

    UNIQUE (compare_id, judge_run_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_compare_judge_requests_status_created
    ON eval_workflow_compare_judge_requests (status, date_created ASC);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_compare_judge_requests_compare
    ON eval_workflow_compare_judge_requests (compare_id, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_workflow_compare_judge_requests_source_request
    ON eval_workflow_compare_judge_requests (source_request_id)
    WHERE source_request_id IS NOT NULL;

COMMENT ON TABLE eval_workflow_compare_judge_requests IS
    'Workflow Lab compare-level pairwise judge queue and immutable artifact index.';

-- ------------------------------------------------------------
-- Node Lab
-- ------------------------------------------------------------

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
    allowed_workspace_types_json JSONB NOT NULL DEFAULT '["single_run","baseline_compare"]'::jsonb,
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
    workspace_type TEXT NOT NULL CHECK (workspace_type IN ('single_run', 'baseline_compare')),
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
        result_kind IN ('single_run_result', 'compare_result')
    ),
    result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_path TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_json JSONB,
    review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (
        review_state IN ('unreviewed', 'needs_followup', 'accepted', 'rejected')
    ),
    decision_note TEXT,
    custom_title TEXT
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
        judge_mode IN ('rubric_score_only', 'rubric_plus_pairwise', 'anti_template_probe', 'raw')
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

-- Legacy cleanup: tighten live DB defaults/checks so judge_compare exits the main path.
ALTER TABLE eval_node_lab_sessions
    ALTER COLUMN allowed_workspace_types_json
    SET DEFAULT '["single_run","baseline_compare"]'::jsonb;

UPDATE eval_node_lab_sessions
SET allowed_workspace_types_json = allowed_workspace_types_json - 'judge_compare'
WHERE allowed_workspace_types_json @> '["judge_compare"]'::jsonb;

ALTER TABLE eval_node_lab_trials
    DROP CONSTRAINT IF EXISTS eval_node_lab_trials_workspace_type_check;

ALTER TABLE eval_node_lab_trials
    ADD CONSTRAINT eval_node_lab_trials_workspace_type_check
    CHECK (workspace_type IN ('single_run', 'baseline_compare'));

ALTER TABLE eval_node_lab_trials
    DROP CONSTRAINT IF EXISTS eval_node_lab_trials_result_kind_check;

ALTER TABLE eval_node_lab_trials
    ADD CONSTRAINT eval_node_lab_trials_result_kind_check
    CHECK (result_kind IN ('single_run_result', 'compare_result'));

-- ------------------------------------------------------------
-- Example Lab
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS eval_example_lab_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    -- 核心标识
    example_id TEXT NOT NULL UNIQUE CHECK (example_id ~ '^[A-Za-z0-9._-]+$'),
    example_type TEXT NOT NULL CHECK (example_type IN (
        'vocab', 'phrase', 'context', 'grammar', 'sentence_analysis', 'translation'
    )),

    -- 基础内容（所有 type 共有）
    sentence_text TEXT NOT NULL,
    output_fragment JSONB NOT NULL DEFAULT '{}'::jsonb,
    label TEXT NOT NULL DEFAULT '',

    -- 来源与分类
    source_kind TEXT NOT NULL DEFAULT 'manual' CHECK (
        source_kind IN ('manual', 'run_capture', 'yaml_import', 'seed_import', 'other')
    ),
    source_ref TEXT,
    reading_variant TEXT,
    target_node TEXT CHECK (target_node IN ('grammar', 'vocabulary', 'translation', 'academic')),

    -- 向量库预备字段（仅 grammar_note / sentence_analysis 有意义）
    -- RAG 准入由 eval_example_lab_entries_approved_rag_eligible_check
    -- 强制：example_type 必须 ∈ {grammar, sentence_analysis} 才能 approved=true。
    -- 命名沿用历史（约束原本对应已移除的 rag_eligible 列），语义现在直接由
    -- example_type 决定；保留旧名以避免对 live DB 做 rename 迁移。
    grammar_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieval_text TEXT,
    -- 派生时间 / 来源：Directus hook 在 AI regenerate-rag-fields 时写入；
    -- 真实 DB 列以匹配 sync-eval-center-metadata 中的字段声明。
    derived_at TIMESTAMPTZ,
    derived_by TEXT,

    -- 质量与审批
    quality_score REAL NOT NULL DEFAULT 0.0 CHECK (quality_score >= 0.0 AND quality_score <= 1.0),
    approved BOOLEAN NOT NULL DEFAULT false,

    -- 备注
    notes TEXT,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,

    CONSTRAINT eval_example_lab_entries_approved_rag_eligible_check
        CHECK (approved = false OR example_type IN ('grammar', 'sentence_analysis')),

    CONSTRAINT eval_example_lab_entries_fragment_type_check
        CHECK (
            output_fragment->>'type' IS NULL
            OR output_fragment = '{}'::jsonb
            OR (example_type = 'grammar' AND output_fragment->>'type' = 'grammar_note')
            OR (example_type = 'sentence_analysis' AND output_fragment->>'type' = 'sentence_analysis')
            OR (example_type = 'vocab' AND output_fragment->>'type' IN ('vocab_highlight', 'term_note', 'logic_note'))
            OR (example_type = 'phrase' AND output_fragment->>'type' = 'phrase_gloss')
            OR (example_type = 'context' AND output_fragment->>'type' = 'context_gloss')
            OR (example_type = 'translation' AND output_fragment->>'type' IN ('translation', 'academic_translation'))
        )
);

CREATE INDEX IF NOT EXISTS idx_eval_example_lab_entries_type_created
    ON eval_example_lab_entries (example_type, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_example_lab_entries_variant
    ON eval_example_lab_entries (reading_variant, example_type);

COMMENT ON TABLE eval_example_lab_entries IS
    'Example Lab few-shot example entries. Stores manually curated examples with RAG-ready metadata. Only grammar / sentence_analysis entries may be approved (approved=true requires example_type in that set).';

-- Live DB cleanup: keep RAG-eligible types on a canonical 1:1 mapping.
-- (历史保留：在 0001 之前已存在 example_type='grammar' 但 output_fragment.type='sentence_analysis' 的行；
--  上线时统一迁到 example_type='sentence_analysis'。在已应用过 0001 的 live DB 上是 no-op。)
UPDATE eval_example_lab_entries
SET example_type = 'sentence_analysis'
WHERE example_type = 'grammar'
  AND output_fragment->>'type' = 'sentence_analysis';

COMMENT ON COLUMN eval_workflow_compares.custom_title IS
    'User-defined display title for the compare record. Nullable; when NULL the UI should fall back to compare_id or a generated summary.';

COMMENT ON COLUMN eval_workflow_run_requests.custom_title IS
    'User-defined display title for the run request. Nullable; when NULL the UI should fall back to run_id or a generated summary.';

COMMENT ON COLUMN eval_node_lab_trials.custom_title IS
    'User-defined display title for the trial. Nullable; when NULL the UI should fall back to node_name / reading_goal / reading_variant.';
