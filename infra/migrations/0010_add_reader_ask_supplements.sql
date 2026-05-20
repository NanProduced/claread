CREATE TABLE IF NOT EXISTS reader_ask_supplements (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    record_id UUID NOT NULL REFERENCES analysis_records(id) ON DELETE CASCADE,
    supplement_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    sentence_id TEXT NOT NULL,
    paragraph_id TEXT NULL,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL,
    anchor_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_version TEXT NOT NULL,
    created_from_turn_run_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_reader_ask_supplements_user_record
    ON reader_ask_supplements (user_id, record_id, created_at)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_reader_ask_supplements_target_key
    ON reader_ask_supplements (user_id, target_key)
    WHERE deleted_at IS NULL;
