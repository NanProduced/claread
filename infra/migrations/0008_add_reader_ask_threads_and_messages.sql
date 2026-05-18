CREATE TABLE reader_ask_threads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  record_id UUID NOT NULL REFERENCES analysis_records(id) ON DELETE CASCADE,
  title TEXT,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  archived_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_message_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_reader_ask_default_thread
  ON reader_ask_threads(user_id, record_id)
  WHERE is_default = TRUE AND archived_at IS NULL;

CREATE INDEX idx_reader_ask_threads_user_record_updated
  ON reader_ask_threads(user_id, record_id, updated_at DESC)
  WHERE archived_at IS NULL;

CREATE INDEX idx_reader_ask_threads_user_record_last_message
  ON reader_ask_threads(user_id, record_id, last_message_at DESC NULLS LAST)
  WHERE archived_at IS NULL;

CREATE TABLE reader_ask_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id UUID NOT NULL REFERENCES reader_ask_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  status TEXT NOT NULL DEFAULT 'completed'
    CHECK (status IN ('pending', 'streaming', 'completed', 'failed')),
  content_md TEXT NOT NULL DEFAULT '',
  context_anchors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  action_proposals_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  tool_trace_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  usage_event_id UUID REFERENCES ai_usage_events(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reader_ask_messages_thread_created
  ON reader_ask_messages(thread_id, created_at ASC);

CREATE INDEX idx_reader_ask_messages_usage_event
  ON reader_ask_messages(usage_event_id)
  WHERE usage_event_id IS NOT NULL;

CREATE TRIGGER trg_reader_ask_threads_set_updated_at
BEFORE UPDATE ON reader_ask_threads
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_ask_messages_set_updated_at
BEFORE UPDATE ON reader_ask_messages
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE reader_ask_threads IS 'Reader 内 Ask Claread 会话线程。按用户和文章绑定，支持默认线程与 New chat。';
COMMENT ON COLUMN reader_ask_threads.is_default IS '是否为当前文章的默认线程。一个用户在同一篇文章下仅允许一个未归档默认线程。';
COMMENT ON COLUMN reader_ask_threads.last_message_at IS '最近一条消息时间，用于线程排序与续聊。';

COMMENT ON TABLE reader_ask_messages IS 'Reader Ask 会话消息表。保存 Markdown 输出、上下文锚点、引用来源、动作提议和工具轨迹。';
COMMENT ON COLUMN reader_ask_messages.context_anchors_json IS '本条消息显式挂载或解析出的上下文锚点列表。';
COMMENT ON COLUMN reader_ask_messages.citations_json IS '本条消息的引用来源列表，包括正文锚点、摘录资产、跨文章来源等。';
COMMENT ON COLUMN reader_ask_messages.action_proposals_json IS 'AI 提议的待确认动作列表。修改性动作需通过 HITL confirm endpoint 执行。';
COMMENT ON COLUMN reader_ask_messages.tool_trace_json IS 'Reader Ask 内部工具调用轨迹，用于前端调试与审计。';
