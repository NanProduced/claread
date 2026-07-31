-- 0028_reader_ask_thread_memory.sql
--
-- ASK-CONTEXT-COMPACTION-R1: Ask thread memory snapshot 表。
--
-- 背景:
--   reader_record_ask agentic 路径引入"同线程上下文记忆"——按 episode 组织的
--   派生只读视图，由小模型在严格 Typed JSON 契约下增量生成（R2），由确定性
--   Host 抽取兜底（R1 emergency）。snapshot 按 thread_id 单行存储，version
--   自增（CAS 守卫防并发轮竞争）。
--
--   snapshot_json 形状见 R0.1 §6（ThreadMemorySnapshot Pydantic 镜像，
--   app/services/reader_record_ask/thread_memory/schema.py）。
--   真相源永远是 reader_ask_messages + reader_ask_turn_runs(final_status='ok')；
--   本表是派生只读视图，可凭 canonical messages 完全重建（R0.1 §4.2(e)）。
--   丢失不造成任何事实损失。
--
--   不依赖 0024（reasoning_projection_json）。
--
-- Pre-apply safety check（人工执行,确认表尚未存在）:
--   SELECT to_regclass('public.reader_ask_thread_memory');
--
CREATE TABLE IF NOT EXISTS reader_ask_thread_memory (
    thread_id UUID PRIMARY KEY REFERENCES reader_ask_threads(id) ON DELETE CASCADE,
    snapshot_json JSONB NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reader_ask_thread_memory_updated_on
    ON reader_ask_thread_memory (updated_at);

COMMENT ON TABLE reader_ask_thread_memory IS
    'ASK-CONTEXT-COMPACTION-R1: Ask thread memory snapshot——派生只读视图，'
    '可凭 canonical messages (reader_ask_messages + reader_ask_turn_runs '
    'final_status=ok) 完全重建（R0.1 §4.2e）。snapshot_json 形状见 R0.1 §6 '
    'ThreadMemorySnapshot。version 自增用于 CAS 守卫（防并发轮竞争）。'
    '本表不替代 canonical messages 作为真相源；丢失不造成事实损失。';
