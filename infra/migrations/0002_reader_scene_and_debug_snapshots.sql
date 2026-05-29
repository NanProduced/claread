ALTER TABLE analysis_records
ADD COLUMN IF NOT EXISTS request_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS analysis_debug_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  record_id UUID NOT NULL REFERENCES analysis_records(id) ON DELETE CASCADE,
  task_id UUID NOT NULL UNIQUE REFERENCES analysis_tasks(id) ON DELETE CASCADE,
  workflow_name TEXT,
  workflow_version TEXT,
  schema_version TEXT,
  prompt_version TEXT,
  task_status TEXT NOT NULL,
  user_facing_state TEXT,
  failure_code TEXT,
  failure_message TEXT,
  preprocess_summary_json JSONB,
  normalize_summary_json JSONB,
  drop_log_summary_json JSONB,
  runtime_summary_json JSONB,
  academic_quality_json JSONB,
  rag_debug_json JSONB,
  trace_refs_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analysis_debug_snapshots_record_created
  ON analysis_debug_snapshots(record_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_debug_snapshots_task_status
  ON analysis_debug_snapshots(task_status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_debug_snapshots_updated
  ON analysis_debug_snapshots(updated_at DESC);
