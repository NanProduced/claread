CREATE TABLE ai_usage_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  usage_scope TEXT NOT NULL CHECK (usage_scope IN (
    'user_billed',
    'system_internal',
    'anonymous_trial',
    'eval_debug'
  )),
  capability_code TEXT NOT NULL,
  billing_mode TEXT NOT NULL CHECK (billing_mode IN (
    'user_points',
    'internal_only',
    'trial',
    'no_charge'
  )),
  status TEXT NOT NULL,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  task_id UUID REFERENCES analysis_tasks(id) ON DELETE SET NULL,
  record_id UUID REFERENCES analysis_records(id) ON DELETE SET NULL,
  daily_reader_article_id TEXT REFERENCES daily_readers(id) ON DELETE SET NULL,
  client_platform TEXT,
  request_id TEXT,
  workflow_name TEXT,
  workflow_version TEXT,
  schema_version TEXT,
  prompt_version TEXT,
  model_route TEXT,
  model_profile TEXT,
  model_provider TEXT,
  model_name TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  billed_points INTEGER,
  billing_policy_version TEXT,
  error_code TEXT,
  error_message TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ai_usage_events_scope_created
  ON ai_usage_events(usage_scope, created_at DESC);
CREATE INDEX idx_ai_usage_events_capability_created
  ON ai_usage_events(capability_code, created_at DESC);
CREATE INDEX idx_ai_usage_events_user_created
  ON ai_usage_events(user_id, created_at DESC)
  WHERE user_id IS NOT NULL;
CREATE INDEX idx_ai_usage_events_task
  ON ai_usage_events(task_id)
  WHERE task_id IS NOT NULL;
CREATE INDEX idx_ai_usage_events_record
  ON ai_usage_events(record_id)
  WHERE record_id IS NOT NULL;
CREATE INDEX idx_ai_usage_events_daily_reader
  ON ai_usage_events(daily_reader_article_id, created_at DESC)
  WHERE daily_reader_article_id IS NOT NULL;

COMMENT ON TABLE ai_usage_events IS '统一 AI 使用审计事件表，记录用户计费、系统内部、匿名试用和调试评测等 AI 调用。';
COMMENT ON COLUMN ai_usage_events.usage_scope IS '调用作用域：user_billed、system_internal、anonymous_trial、eval_debug。';
COMMENT ON COLUMN ai_usage_events.capability_code IS '能力代码，如 analysis_full、dict_ai_lookup、daily_reader_pipeline。';
COMMENT ON COLUMN ai_usage_events.billing_mode IS '结算模式：user_points、internal_only、trial、no_charge。';
COMMENT ON COLUMN ai_usage_events.status IS '事件状态，建议使用 succeeded、failed、fallback、skipped，并允许后续扩展。';
COMMENT ON COLUMN ai_usage_events.model_route IS '主要模型路由；多路由工作流的完整映射放入 metadata_json。';
COMMENT ON COLUMN ai_usage_events.metadata_json IS '扩展审计上下文 JSON，包括 usage 快照、entrypoint、对象标识和多模型映射。';

COMMENT ON TABLE analysis_audit_logs IS '旧分析审计日志表。当前继续保留作兼容与排障，新的统一 AI 审计以 ai_usage_events 为准。';
COMMENT ON TABLE user_credit_ledger IS '用户积分结算账本。负责余额变动，不再承担统一 AI 调用审计职责。';
