-- 0018: Reading Record Ask agentic turn-run fields
--
-- Extends reader_ask_turn_runs for the independent Reading Record Ask agent
-- path (execution_version lane). Legacy turns keep NULLs and prior status
-- semantics. Does not modify analysis_records-scoped Ask history.

-- Expand status allowlist for agentic terminals.
ALTER TABLE reader_ask_turn_runs
    DROP CONSTRAINT IF EXISTS reader_ask_turn_runs_status_check;

ALTER TABLE reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_status_check
    CHECK (
        status IN (
            'streaming',
            'completed',
            'failed',
            'interrupted',
            'cancelled',
            'stale'
        )
    );

ALTER TABLE reader_ask_turn_runs
    ADD COLUMN IF NOT EXISTS execution_version TEXT NULL,
    ADD COLUMN IF NOT EXISTS envelope_fingerprint TEXT NULL,
    ADD COLUMN IF NOT EXISTS envelope_snapshot_json JSONB NULL,
    ADD COLUMN IF NOT EXISTS final_status TEXT NULL,
    ADD COLUMN IF NOT EXISTS terminal_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS resolved_evidence_json JSONB NULL;

COMMENT ON COLUMN reader_ask_turn_runs.execution_version IS
    'Agentic lane version (e.g. reader_record_ask_agentic_v1). NULL for legacy Ask turns.';
COMMENT ON COLUMN reader_ask_turn_runs.envelope_fingerprint IS
    'SHA-256 fingerprint of the immutable Context Envelope for this agentic turn.';
COMMENT ON COLUMN reader_ask_turn_runs.envelope_snapshot_json IS
    'Server-owned Context Envelope snapshot (JSON). Not client-writable.';
COMMENT ON COLUMN reader_ask_turn_runs.final_status IS
    'Finalizer status: ok | context_stale | invalid_citations | failed | cancelled.';
COMMENT ON COLUMN reader_ask_turn_runs.terminal_reason IS
    'Typed terminal failure/stale reason. No fabricated user answer.';
COMMENT ON COLUMN reader_ask_turn_runs.resolved_evidence_json IS
    'Finalizer-resolved typed evidence array for agentic turns.';

CREATE INDEX IF NOT EXISTS idx_reader_ask_turn_runs_execution_version
    ON reader_ask_turn_runs (execution_version)
    WHERE execution_version IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reader_ask_turn_runs_envelope_fingerprint
    ON reader_ask_turn_runs (envelope_fingerprint)
    WHERE envelope_fingerprint IS NOT NULL;
