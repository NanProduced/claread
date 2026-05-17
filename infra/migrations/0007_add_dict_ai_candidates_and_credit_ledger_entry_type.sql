ALTER TABLE user_credit_ledger
  DROP CONSTRAINT IF EXISTS user_credit_ledger_entry_type_check;

ALTER TABLE user_credit_ledger
  ADD CONSTRAINT user_credit_ledger_entry_type_check
  CHECK (entry_type IN (
    'daily_grant',
    'bonus_grant',
    'analysis_deduct',
    'ai_capability_deduct',
    'manual_adjust',
    'refund',
    'feedback_reward'
  ));

COMMENT ON COLUMN user_credit_ledger.entry_type IS '流水类型：daily_grant, bonus_grant, analysis_deduct, ai_capability_deduct, manual_adjust, refund, feedback_reward。';

CREATE TABLE dict_ai_candidate_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query TEXT NOT NULL,
  normalized_query TEXT NOT NULL,
  query_type TEXT NOT NULL CHECK (query_type IN ('word', 'phrase')),
  classification TEXT NOT NULL,
  result_kind TEXT NOT NULL CHECK (result_kind IN ('ai_entry', 'ai_unresolved')),
  confidence TEXT,
  generated_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  context_sentence TEXT NOT NULL,
  record_id UUID REFERENCES analysis_records(id) ON DELETE SET NULL,
  sentence_id TEXT,
  usage_event_id UUID REFERENCES ai_usage_events(id) ON DELETE SET NULL,
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending', 'accepted', 'rejected', 'ignored')),
  review_note TEXT,
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dict_ai_candidates_query
  ON dict_ai_candidate_entries(query);

CREATE INDEX idx_dict_ai_candidates_review_status_created
  ON dict_ai_candidate_entries(review_status, created_at DESC);

CREATE INDEX idx_dict_ai_candidates_usage_event
  ON dict_ai_candidate_entries(usage_event_id)
  WHERE usage_event_id IS NOT NULL;

CREATE TRIGGER trg_dict_ai_candidate_entries_set_updated_at
BEFORE UPDATE ON dict_ai_candidate_entries
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE dict_ai_candidate_entries IS '词典 AI 未收录兜底候选池。保存 AI 临时词条和放弃解释结果，供后续人工审核与词库补录参考。';
COMMENT ON COLUMN dict_ai_candidate_entries.generated_payload_json IS 'missing_fallback 结构化 AI 输出快照。';
COMMENT ON COLUMN dict_ai_candidate_entries.review_status IS '审核状态：pending、accepted、rejected、ignored。';
