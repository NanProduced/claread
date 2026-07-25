-- 0024_reader_ask_turn_runs_reasoning_projection.sql
--
-- 背景（ASK-REASONING-R1）:
--   reader_record_ask agentic 路径需要在成功 turn 中持久化“用户可见
--   reasoning 投影”（经服务端单一 chokepoint 的确定性擦除与限额后的
--   provider reasoning），并与最终答案在同一 UPDATE 事务中原子提交,
--   以保证 热 SSE 拼接文本 ≡ DB 投影 ≡ 冷历史文本。
--
--   列形状（JSONB NULL）:
--     {
--       "projection_policy_version": "reasoning_projection_v1",
--       "text": "<用户可见投影全文，≤ 限额>",
--       "char_count": <int>,
--       "truncated": <bool>
--     }
--
--   NULL 语义:provider 未返回非空 reasoning,或 turn 非 ok
--   （cancel / validation failure / budget exhausted / persist failure
--   一律不持久化 reasoning —— fail-closed,与 resolved_evidence_json='[]'
--   的终态教义一致）。
--
--   该列绝不包含 raw provider reasoning、密钥、签名、内部 handle 或
--   未过滤文本;仅包含已投影内容与其安全元数据。
--
-- Pre-apply safety check（人工执行,确认列尚未存在）:
--   SELECT column_name
--   FROM information_schema.columns
--   WHERE table_name = 'reader_ask_turn_runs'
--     AND column_name = 'reasoning_projection_json';
--
-- Status: AUTHORED, NOT EXECUTED（需用户在本地执行 migration 并做 DB 备份）。

ALTER TABLE reader_ask_turn_runs
    ADD COLUMN IF NOT EXISTS reasoning_projection_json JSONB NULL;

COMMENT ON COLUMN reader_ask_turn_runs.reasoning_projection_json IS
    'ASK-REASONING-R1: safe user-visible reasoning projection committed '
    'atomically with the ok answer (same UPDATE). Shape: '
    '{projection_policy_version, text, char_count, truncated}. NULL when '
    'the provider returned no reasoning or the turn was not ok. Never '
    'carries raw provider reasoning, secrets, handles, or unredacted text.';
