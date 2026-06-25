-- D6-I1 Stable Document Block contract (schema-only landing)
--
-- Adds three new domain tables that materialize the D6 draft described in
-- docs/initiatives/reader-agentic-orchestration/modules/schema-and-domain-contract.md:
--
--   1. candidate_reading_documents  — reviewable document for
--      `needs_confirmation` before freezing a stable document.
--   2. stable_reading_documents     — immutable document truth per
--      (reading_record_id, record_generation). One active row per record.
--   3. stable_document_blocks       — ordered, addressable content blocks
--      under a stable document. Tables / images / footnotes / code blocks
--      live as first-class blocks, never silently flattened.
--
-- Canonical Text Layer (D6) is intentionally NOT added as a separate
-- table here. V1 keeps `reading_bases.text` as the transitional Canonical
-- Text Layer carrier (see schema-and-domain-contract.md "Canonical Text
-- Layer transition"). Canonical offsets on stable_document_blocks map
-- into that carrier via utf16 offsets.
--
-- This migration is a schema/domain landing only. It does NOT:
--   - connect Web or any API route,
--   - implement Candidate Document confirm flow (D6-I2 follow-up),
--   - implement input adapters, OCR/PDF/Markdown parsing (D6-I3 follow-up),
--   - implement block-scoped RAG indexing (D6-I4 follow-up),
--   - mutate any existing migration's contract.

-- ============================================================
-- candidate_reading_documents
-- ============================================================

CREATE TABLE candidate_reading_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reading_record_id UUID NOT NULL
    REFERENCES reading_records(id) ON DELETE CASCADE,
  user_id UUID NOT NULL
    REFERENCES users(id) ON DELETE CASCADE,
  record_generation INTEGER NOT NULL CHECK (record_generation >= 1),
  title TEXT,
  blocks_json JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(blocks_json) = 'array'),
  canonical_text_preview TEXT NOT NULL DEFAULT '',
  source_refs_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(source_refs_json) = 'object'),
  quality_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(quality_json) = 'object'),
  status TEXT NOT NULL DEFAULT 'ready'
    CHECK (status IN ('ready', 'confirmed', 'rejected', 'superseded')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  confirmed_at TIMESTAMPTZ,
  CONSTRAINT uq_candidate_reading_documents_id_record
    UNIQUE (id, reading_record_id),
  CONSTRAINT ck_candidate_reading_documents_confirmed_at
    CHECK (
      (status = 'confirmed' AND confirmed_at IS NOT NULL)
      OR (status <> 'confirmed')
    )
);

CREATE INDEX idx_candidate_reading_documents_record_generation
  ON candidate_reading_documents (reading_record_id, record_generation);

CREATE INDEX idx_candidate_reading_documents_user_updated
  ON candidate_reading_documents (user_id, updated_at DESC)
  WHERE status <> 'superseded';

-- ============================================================
-- stable_reading_documents
-- ============================================================

CREATE TABLE stable_reading_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reading_record_id UUID NOT NULL
    REFERENCES reading_records(id) ON DELETE CASCADE,
  record_generation INTEGER NOT NULL CHECK (record_generation >= 1),
  title TEXT,
  document_version INTEGER NOT NULL CHECK (document_version >= 1),
  source_profile_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(source_profile_json) = 'object'),
  content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'superseded')),
  frozen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_stable_reading_documents_id_record
    UNIQUE (id, reading_record_id),
  CONSTRAINT uq_stable_reading_documents_record_version
    UNIQUE (reading_record_id, document_version),
  CONSTRAINT uq_stable_reading_documents_record_generation
    UNIQUE (reading_record_id, record_generation)
);

-- One active stable document per reading record.
CREATE UNIQUE INDEX uq_stable_reading_documents_active_per_record
  ON stable_reading_documents (reading_record_id)
  WHERE status = 'active';

CREATE INDEX idx_stable_reading_documents_record_status
  ON stable_reading_documents (reading_record_id, status);

-- ============================================================
-- stable_document_blocks
-- ============================================================

CREATE TABLE stable_document_blocks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  stable_document_id UUID NOT NULL
    REFERENCES stable_reading_documents(id) ON DELETE CASCADE,
  block_id TEXT NOT NULL,
  -- parent_block_id is the document-local stable block_id string of the
  -- parent block (NOT the row UUID). It is kept aligned with the
  -- StableDocumentBlock Python contract, which uses block_id (a string)
  -- as the parent reference.
  parent_block_id TEXT
    CHECK (parent_block_id IS NULL OR parent_block_id <> block_id),
  order_index INTEGER NOT NULL CHECK (order_index >= 0),
  block_type TEXT NOT NULL CHECK (block_type IN (
    'paragraph',
    'heading',
    'list_item',
    'blockquote',
    'table',
    'table_row',
    'table_cell',
    'footnote',
    'image',
    'image_ocr',
    'caption',
    'code_block',
    'unknown'
  )),
  text_content TEXT,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload_json) = 'object'),
  source_refs_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(source_refs_json) = 'object'),
  canonical_text_start_utf16 INTEGER
    CHECK (canonical_text_start_utf16 IS NULL OR canonical_text_start_utf16 >= 0),
  canonical_text_end_utf16 INTEGER
    CHECK (canonical_text_end_utf16 IS NULL OR canonical_text_end_utf16 >= 0),
  -- The `'{}'::jsonb` default below is a STORAGE PLACEHOLDER ONLY.
  -- StableDocumentBlock's Python contract always materializes a
  -- per-block-type default policy via
  -- `default_interpretation_policy_for(block_type)` (see
  -- app/schemas/reader_documents.py). The D6-I2 Candidate Document
  -- confirm flow that persists frozen Stable Reading Documents MUST
  -- write that Python-model-generated policy into this column; the DB
  -- default is never relied on at runtime. An empty `{}` would
  -- silently route the block as main_reading / main_reading_text and
  -- contradict the D6 projection rules (tables / images / code /
  -- footnotes would leak into the main grammar pass).
  interpretation_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(interpretation_policy_json) = 'object'),
  quality_json JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(quality_json) = 'object'),
  CONSTRAINT uq_stable_document_blocks_doc_block
    UNIQUE (stable_document_id, block_id),
  CONSTRAINT uq_stable_document_blocks_doc_order
    UNIQUE (stable_document_id, order_index),
  CONSTRAINT ck_stable_document_blocks_canonical_text_offsets
    CHECK (
      (canonical_text_start_utf16 IS NULL AND canonical_text_end_utf16 IS NULL)
      OR (
        canonical_text_start_utf16 IS NOT NULL
        AND canonical_text_end_utf16 IS NOT NULL
        AND canonical_text_end_utf16 > canonical_text_start_utf16
      )
    ),
  CONSTRAINT ck_stable_document_blocks_text_for_textual_types
    CHECK (
      block_type IN ('table', 'table_row', 'table_cell', 'image', 'code_block', 'unknown')
      OR (text_content IS NOT NULL AND length(text_content) > 0)
    ),
  -- Composite FK so that parent_block_id refers to a sibling block_id
  -- inside the SAME stable_document_id. We cannot reference a single
  -- TEXT column directly, so the standard Postgres pattern is to use a
  -- composite (stable_document_id, block_id) UNIQUE (already declared
  -- above as uq_stable_document_blocks_doc_block) and FK the parent
  -- pair against it.
  CONSTRAINT fk_stable_document_blocks_parent
    FOREIGN KEY (stable_document_id, parent_block_id)
    REFERENCES stable_document_blocks(stable_document_id, block_id)
    DEFERRABLE INITIALLY DEFERRED
);

-- Partial index for "find children of parent X" lookups; we cannot
-- index parent_block_id alone because two different stable documents
-- may legitimately reuse the same document-local block_id strings.
CREATE INDEX idx_stable_document_blocks_parent
  ON stable_document_blocks (stable_document_id, parent_block_id)
  WHERE parent_block_id IS NOT NULL;

CREATE INDEX idx_stable_document_blocks_doc_order
  ON stable_document_blocks (stable_document_id, order_index);

CREATE INDEX idx_stable_document_blocks_type
  ON stable_document_blocks (stable_document_id, block_type);