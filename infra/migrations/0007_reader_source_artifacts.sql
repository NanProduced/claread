-- D6-I3G Source Artifact / OSS Adapter foundation
--
-- Adds source_artifacts as the durable contract for uploaded and derived
-- input-adapter blobs. This migration is schema-only: it does not add
-- upload routes, OCR/PDF parsing, or any provider-side network behavior.

CREATE TABLE source_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reading_record_id UUID
    REFERENCES reading_records(id) ON DELETE SET NULL,
  original_input_id UUID
    REFERENCES original_inputs(id) ON DELETE SET NULL,
  user_id UUID NOT NULL
    REFERENCES users(id) ON DELETE CASCADE,
  artifact_kind TEXT NOT NULL CHECK (artifact_kind IN (
    'original_upload',
    'pdf_page_image',
    'ocr_result',
    'extracted_text',
    'webpage_snapshot',
    'derived_preview'
  )),
  storage_provider TEXT NOT NULL CHECK (storage_provider IN ('oss', 'local')),
  bucket TEXT,
  object_key TEXT NOT NULL,
  endpoint TEXT,
  content_type TEXT,
  byte_size BIGINT CHECK (byte_size IS NULL OR byte_size >= 0),
  content_sha256 TEXT
    CHECK (
      content_sha256 IS NULL
      OR content_sha256 ~ '^[0-9a-f]{64}$'
    ),
  source_filename TEXT,
  status TEXT NOT NULL DEFAULT 'available'
    CHECK (status IN ('pending', 'available', 'failed', 'deleted')),
  source_refs_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(source_refs_json) = 'object'),
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(metadata_json) = 'object'),
  quality_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(quality_json) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_source_artifacts_user_created
  ON source_artifacts (user_id, created_at DESC);

CREATE INDEX idx_source_artifacts_record_created
  ON source_artifacts (reading_record_id, created_at DESC)
  WHERE reading_record_id IS NOT NULL;

CREATE UNIQUE INDEX uq_source_artifacts_active_object
  ON source_artifacts (storage_provider, COALESCE(bucket, ''), object_key)
  WHERE deleted_at IS NULL;
