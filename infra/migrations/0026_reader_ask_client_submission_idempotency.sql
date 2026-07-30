-- 0026_reader_ask_client_submission_idempotency.sql
--
-- ASK-RETRY-CONTRACT-R2/R4/R5:
--   Client-generated `client_submission_id` must be claimed atomically
--   before any model call so a network-blip resubmit cannot create a
--   second user/assistant pair or re-invoke the model.
--
--   Uniqueness is database-enforced on (thread_id, client_submission_id)
--   — never only unconstrained metadata JSON.
--
--   R5: claim_generation is a server-owned CAS token. bind/terminal
--   updates MUST match the generation returned at claim time so a
--   stale owner cannot hijack a reclaimed submission.
--
-- Status: AUTHORED, NOT EXECUTED (do not run against production DBs).

CREATE TABLE IF NOT EXISTS reader_ask_client_submissions (
    thread_id UUID NOT NULL REFERENCES reader_ask_threads (id) ON DELETE CASCADE,
    client_submission_id UUID NOT NULL,
    user_id UUID NOT NULL,
    user_message_id UUID NULL REFERENCES reader_ask_messages (id) ON DELETE SET NULL,
    assistant_message_id UUID NULL REFERENCES reader_ask_messages (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'claimed'
        CHECK (status IN (
            'claimed',
            'streaming',
            'completed',
            'failed',
            'cancelled'
        )),
    -- CAS token: increments on reclaim; bind/terminal require match.
    claim_generation BIGINT NOT NULL DEFAULT 1,
    -- Orphan reclaim window for claimed rows that never bound messages
    -- (crash after older multi-step claim). R5 prefers single-transaction
    -- claim+pair+bind so this is a safety net only.
    lease_expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, client_submission_id)
);

CREATE INDEX IF NOT EXISTS idx_reader_ask_client_submissions_assistant
    ON reader_ask_client_submissions (assistant_message_id)
    WHERE assistant_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reader_ask_client_submissions_orphan_claim
    ON reader_ask_client_submissions (status, lease_expires_at)
    WHERE status = 'claimed'
      AND user_message_id IS NULL
      AND assistant_message_id IS NULL;

COMMENT ON TABLE reader_ask_client_submissions IS
    'ASK-RETRY-CONTRACT-R5: atomic client submission claim. '
    'PK (thread_id, client_submission_id) prevents duplicate turns. '
    'status: claimed → streaming → completed|failed|cancelled. '
    'claim_generation CAS prevents stale-owner bind after reclaim.';

COMMENT ON COLUMN reader_ask_client_submissions.claim_generation IS
    'Server-generated CAS token. Increments on orphan reclaim. '
    'bind/terminal must UPDATE ... WHERE claim_generation = $token '
    'and verify affected row count.';
