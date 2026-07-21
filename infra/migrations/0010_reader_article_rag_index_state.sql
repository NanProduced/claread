-- D6-I4B: Article RAG Index Job Bootstrap + Index State Foundation.
--
-- Establishes the persistent index state table and extends reader_jobs to
-- host article_rag_index_build jobs. This migration does NOT:
--   * create vector store tables (Zilliz / Milvus integration is deferred)
--   * define embedding model columns beyond a nullable text placeholder
--   * modify ArticleRagIndexPlanService truth / citation rules
--   * add API routes or event types
--
-- 1. Create reader_article_rag_index_runs state table.
--    One row per stable_document_id index attempt.  The Article RAG
--    index is a single path.  Stores only truth-layer hashes and
--    counts — never chunk text, Plate JSON, Markdown syntax, DOM
--    selections, or Slate paths.  A partial unique index enforces
--    at most one active row per ``stable_document_id`` so duplicate
--    queued jobs cannot accumulate.
--
-- 2. Extend reader_jobs.job_type CHECK to include 'article_rag_index_build'.
--    This job is base-scoped: base_id IS NOT NULL. The existing
--    ck_reader_jobs_base_scope catch-all clause already enforces this
--    for any job_type not in the build_base / extraction / materialization
--    allow-list, so no modification to that constraint is needed here.
--
-- Tests live in:
--   services/api/tests/test_d6_i4b_article_rag_index_bootstrap.py

-- ---------------------------------------------------------------------------
-- 1. reader_article_rag_index_runs
-- ---------------------------------------------------------------------------

CREATE TABLE reader_article_rag_index_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reading_record_id UUID NOT NULL REFERENCES reading_records(id) ON DELETE CASCADE,
    stable_document_id UUID NOT NULL REFERENCES stable_reading_documents(id) ON DELETE CASCADE,
    base_id UUID NOT NULL REFERENCES reading_bases(id) ON DELETE CASCADE,
    record_generation INTEGER NOT NULL CHECK (record_generation >= 1),

    -- Truth-layer hashes (never payload / projection hashes).
    stable_document_content_sha256 TEXT NOT NULL,
    canonical_text_sha256 TEXT NOT NULL,
    plan_content_sha256 TEXT NOT NULL,

    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),

    status TEXT NOT NULL CHECK (status IN (
        'planned', 'queued', 'indexing', 'indexed', 'failed', 'superseded'
    )),

    -- Embedding / vector store fields are nullable placeholders. They are
    -- populated only when a later milestone actually calls an embedding
    -- provider and writes to a vector store. D6-I4B leaves them NULL.
    embedding_model TEXT NULL,
    vector_store_provider TEXT NULL,
    vector_collection TEXT NULL,

    -- Linkage to reader_jobs / reader_runs once enqueued. Nullable because
    -- the index state row is inserted before the job is enqueued within
    -- the same transaction; a committed row always has both populated.
    job_id UUID NULL,
    reader_run_id UUID NULL,

    error_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,

    CONSTRAINT ck_reader_article_rag_index_runs_sha256_format CHECK (
        stable_document_content_sha256 ~ '^[0-9a-f]{64}$'
        AND canonical_text_sha256 ~ '^[0-9a-f]{64}$'
        AND plan_content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_reader_article_rag_index_runs_jsonb_object CHECK (
        jsonb_typeof(error_json) = 'object'
        AND jsonb_typeof(metadata_json) = 'object'
    )
);

-- At most one active / planned / queued / indexing / indexed row per
-- stable_document_id.  The Article RAG index is a single path — there
-- is no version dimension.  'failed' and 'superseded' rows are excluded
-- so a re-index after failure / supersession can insert a fresh row.
CREATE UNIQUE INDEX uq_reader_article_rag_index_runs_active
    ON reader_article_rag_index_runs (stable_document_id)
    WHERE status IN ('planned', 'queued', 'indexing', 'indexed');

CREATE INDEX idx_reader_article_rag_index_runs_record
    ON reader_article_rag_index_runs (reading_record_id, status)
    WHERE status IN ('planned', 'queued', 'indexing', 'indexed');

COMMENT ON TABLE reader_article_rag_index_runs IS
    'Persistent state for Article RAG index builds. One row per stable_document_id index attempt. The Article RAG index is a single path. Stores only truth-layer hashes and counts; never chunk text, Plate JSON, Markdown syntax, DOM selections, or Slate paths.';

COMMENT ON COLUMN reader_article_rag_index_runs.plan_content_sha256 IS
    'SHA-256 of the deterministic plan content (chunk ids, content hashes, citation refs). Computed by compute_plan_content_sha256 in article_rag_index_plan.py.';

COMMENT ON COLUMN reader_article_rag_index_runs.embedding_model IS
    'Nullable placeholder. Populated only when a later milestone calls an embedding provider. D6-I4B leaves this NULL.';

COMMENT ON COLUMN reader_article_rag_index_runs.vector_store_provider IS
    'Nullable placeholder. Populated only when a later milestone writes to Zilliz / Milvus. D6-I4B leaves this NULL.';

-- ---------------------------------------------------------------------------
-- 2. Extend reader_jobs.job_type CHECK
-- ---------------------------------------------------------------------------

ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;

ALTER TABLE reader_jobs
    ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
        'build_base',
        'translate_unit',
        'build_vocabulary_layer',
        'build_grammar_bundle',
        'input_artifact_extraction',
        'extracted_artifact_materialization',
        'article_rag_index_build'
    ));

-- NOTE: ck_reader_jobs_base_scope is NOT modified here.
-- article_rag_index_build is base-scoped (base_id IS NOT NULL). The
-- existing catch-all clause in ck_reader_jobs_base_scope already
-- enforces base_id IS NOT NULL for any job_type not in the
-- build_base / input_artifact_extraction / extracted_artifact_materialization
-- allow-list. Since article_rag_index_build is not in that allow-list,
-- inserting it with base_id IS NULL will raise a CheckViolationError.
