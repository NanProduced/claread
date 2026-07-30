-- 0027_reader_ask_client_submission_claim_generation.sql
--
-- ASK-RETRY-CONTRACT-R6: additive migration for environments that already
-- applied an earlier 0026 without claim_generation / lease columns.
-- Safe to re-run (IF NOT EXISTS). Does not drop or rewrite 0026.
--
-- Status: AUTHORED, NOT EXECUTED (Owner applies separately).

ALTER TABLE reader_ask_client_submissions
    ADD COLUMN IF NOT EXISTS claim_generation BIGINT NOT NULL DEFAULT 1;

ALTER TABLE reader_ask_client_submissions
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_reader_ask_client_submissions_assistant
    ON reader_ask_client_submissions (assistant_message_id)
    WHERE assistant_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reader_ask_client_submissions_orphan_claim
    ON reader_ask_client_submissions (status, lease_expires_at)
    WHERE status = 'claimed'
      AND user_message_id IS NULL
      AND assistant_message_id IS NULL;

COMMENT ON COLUMN reader_ask_client_submissions.claim_generation IS
    'R5/R6 CAS token. bind/terminal UPDATE must match generation.';
