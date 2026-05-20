CREATE TABLE IF NOT EXISTS reader_ask_turn_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES reader_ask_messages(id) ON DELETE CASCADE,
    thread_id UUID NOT NULL REFERENCES reader_ask_threads(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    record_id UUID NOT NULL REFERENCES analysis_records(id) ON DELETE CASCADE,
    turn_id UUID NOT NULL REFERENCES reader_ask_messages(id) ON DELETE CASCADE,
    run_attempt INTEGER NOT NULL DEFAULT 1,
    supersedes_run_id UUID NULL REFERENCES reader_ask_turn_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('streaming', 'completed', 'failed')),
    resolved_intent TEXT NULL,
    user_visible_output_json JSONB NULL,
    usage_summary_json JSONB NULL,
    usage_event_id UUID NULL REFERENCES ai_usage_events(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    failed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reader_ask_turn_runs_message_created
    ON reader_ask_turn_runs (message_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_reader_ask_turn_runs_thread_started
    ON reader_ask_turn_runs (thread_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_reader_ask_turn_runs_usage_event
    ON reader_ask_turn_runs (usage_event_id)
    WHERE usage_event_id IS NOT NULL;

CREATE TRIGGER trg_reader_ask_turn_runs_set_updated_at
BEFORE UPDATE ON reader_ask_turn_runs
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS reader_ask_eval_traces (
    turn_run_id UUID PRIMARY KEY REFERENCES reader_ask_turn_runs(id) ON DELETE CASCADE,
    trace_schema_version TEXT NOT NULL,
    planning_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    capability_trace_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_audit_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    supplement_audit_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_reader_ask_eval_traces_set_updated_at
BEFORE UPDATE ON reader_ask_eval_traces
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

ALTER TABLE reader_ask_messages
    ADD COLUMN IF NOT EXISTS current_turn_run_id UUID NULL REFERENCES reader_ask_turn_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_reader_ask_messages_current_turn_run
    ON reader_ask_messages (current_turn_run_id)
    WHERE current_turn_run_id IS NOT NULL;

COMMENT ON TABLE reader_ask_turn_runs IS 'Reader Ask assistant 单轮运行记录。作为当前用户可见输出与 regenerate 历史的正式真相源。';
COMMENT ON TABLE reader_ask_eval_traces IS 'Reader Ask 单轮运行的结构化评测与审计 trace。用于 planner/capability/action/supplement 回放。';
COMMENT ON COLUMN reader_ask_messages.current_turn_run_id IS '当前 assistant message 对应的最新用户可见 turn run。';
