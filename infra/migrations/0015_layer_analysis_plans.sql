-- Z+ Analysis Window: plan + windows + ledger
-- See docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md §4

CREATE TABLE layer_analysis_plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reading_record_id UUID NOT NULL REFERENCES reading_records(id) ON DELETE CASCADE,
  base_id UUID NOT NULL REFERENCES reading_bases(id) ON DELETE CASCADE,
  layer_type TEXT NOT NULL,  -- v1: 'grammar_bundle'
  policy_version TEXT NOT NULL,
  generation INT NOT NULL CHECK (generation >= 1),
  budget_total JSONB NOT NULL,
  -- typed counters: {grammar_note: {...}, sentence_analysis: {...}}
  budget_used JSONB NOT NULL DEFAULT '{"grammar_note":{}, "sentence_analysis":{}}'::jsonb,
  published_anchor_counts_by_type JSONB NOT NULL DEFAULT '{"grammar_note":{}, "sentence_analysis":{}}'::jsonb,
  published_dedup_keys_by_type JSONB NOT NULL DEFAULT '{"grammar_note":[], "sentence_analysis":[]}'::jsonb,
  published_pattern_keys_by_type JSONB NOT NULL DEFAULT '{"grammar_note":[], "sentence_analysis":[]}'::jsonb,
  density_by_record JSONB NOT NULL DEFAULT '{"grammar_note":0, "sentence_analysis":0}'::jsonb,
  covered_window_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  no_op_windows JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL CHECK (status IN (
    'planning', 'active', 'completed', 'completed_with_failures', 'superseded'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_layer_analysis_plans_active
  ON layer_analysis_plans(reading_record_id, base_id, layer_type)
  WHERE status IN ('planning', 'active');

CREATE TABLE analysis_windows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id UUID NOT NULL REFERENCES layer_analysis_plans(id) ON DELETE CASCADE,
  window_index INT NOT NULL,
  target_anchor_ids JSONB NOT NULL,
  context_anchor_prev JSONB NOT NULL DEFAULT '[]'::jsonb,
  context_anchor_next JSONB NOT NULL DEFAULT '[]'::jsonb,
  target_unit_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  target_block_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  char_count INT NOT NULL,
  anchor_count INT NOT NULL,
  window_budget JSONB NOT NULL,
  coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL CHECK (status IN (
    'pending', 'running', 'completed', 'no_op', 'failed'
  )),
  job_id UUID,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(plan_id, window_index)
);

CREATE INDEX idx_analysis_windows_plan_status ON analysis_windows(plan_id, status);
CREATE INDEX idx_analysis_windows_job ON analysis_windows(job_id) WHERE job_id IS NOT NULL;

-- reader_jobs.job_type CHECK 追加 build_grammar_bundle_window
-- NOTE: ck_reader_jobs_base_scope is NOT modified here.
-- build_grammar_bundle_window is base-scoped (base_id IS NOT NULL). The
-- existing catch-all clause in ck_reader_jobs_base_scope already enforces
-- base_id IS NOT NULL for any job_type not in the build_base /
-- input_artifact_extraction / extracted_artifact_materialization allow-list.
ALTER TABLE reader_jobs
  DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;
ALTER TABLE reader_jobs
  ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
    'build_base',
    'translate_unit',
    'build_vocabulary_layer',
    'build_grammar_bundle',
    'build_grammar_bundle_window',
    'input_artifact_extraction',
    'extracted_artifact_materialization',
    'article_rag_index_build',
    'generate_display_title_zh'
  ));
