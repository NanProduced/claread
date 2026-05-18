ALTER TABLE reader_ask_messages
ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN reader_ask_messages.metadata_json IS 'Reader Ask 消息扩展元数据，包含 task_mode、resolved_context 和结构化卡片等。';
