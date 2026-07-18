-- 0021_reader_article_rag_profile_fingerprint.sql
--
-- P1-C: Durable Profile Fingerprint Migration.
--
-- Adds a durable ``profile_fingerprint`` column to
-- ``reader_article_rag_index_runs``, safely backfills recognised V1 rows
-- with the frozen V1 profile fingerprint, and atomically fails on any
-- unknown or contradictory legacy row.
--
-- The profile_fingerprint is the SHA-256 digest of the canonical
-- ArticleRagIndexProfile payload (see
-- ``app/services/reader_orchestration/article_rag_index_profile.py``).
-- It durably links each index run row to the exact profile identity
-- (embedding model, dimension, vector namespace, retrieval schema, etc.)
-- used at bootstrap time, so downstream workers / retrieval / migration
-- can detect profile drift without re-deriving the fingerprint from
-- scattered columns.
--
-- Migration design (explicit transaction, P1-C rework order):
--   1. Add ``profile_fingerprint TEXT NULL`` (nullable add).
--   2. Preflight A: reject any row whose ``profile_fingerprint`` is
--      non-NULL and not equal to the frozen V1 golden fingerprint.
--      This closes the "wrong-but-format-valid SHA-256" hole: a
--      previously partially-migrated column carrying an incorrect
--      but format-valid 64-char hex value is no longer silently
--      retained.  ``NULL`` is allowed (will be backfilled); the V1
--      golden value is allowed (safe rerun).
--   3. Preflight B: reject any row whose identity columns contradict
--      the single registered V1 profile.  Unknown ``index_version``,
--      contradictory ``chunker_version``, contradictory
--      ``embedding_model`` (non-NULL, non-v4), or contradictory
--      ``vector_collection`` (non-NULL, non-v1) abort the transaction.
--   4. Preflight C: reject any execution-active legacy row
--      (``status IN ('planned','queued','indexing')``).  Such rows
--      may have a half-frozen associated job payload (``reader_jobs``
--      ``input_json`` / ``input_hash``); the migration does not
--      attempt to repair them.  Deployers must drain / terminalize
--      execution-active Article RAG V1 runs before applying this
--      migration.  Terminal / history statuses (``indexed``,
--      ``failed``, ``superseded``) are backfillable.
--   5. Backfill: set ``profile_fingerprint`` to the frozen V1 golden
--      fingerprint for rows whose value is still NULL.
--   6. Add CHECK constraint ``NOT VALID`` (sha256 lowercase hex).
--   7. VALIDATE constraint (all rows pass after backfill).
--   8. SET NOT NULL.
--   9. Column comment.
--
-- Atomicity: the entire migration runs inside ``BEGIN ... COMMIT``.
-- If any preflight raises, the transaction rolls back and no partial
-- state (no constraint, no backfill mutation) is committed.  The
-- ``ADD COLUMN IF NOT EXISTS`` is the only statement that may have
-- been applied by a prior partial run; the preflight A explicitly
-- accepts this case and validates the pre-existing values.
--
-- Error messages are FIXED LOCAL STRINGS that do not echo fingerprint
-- values, row ids, status values, model names, collection names, or
-- any other database content.
--
-- Tests live in:
--   services/api/tests/test_migration_0021_reader_article_rag_profile_fingerprint.py

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Add profile_fingerprint as nullable TEXT
-- ---------------------------------------------------------------------------

ALTER TABLE reader_article_rag_index_runs
    ADD COLUMN IF NOT EXISTS profile_fingerprint TEXT NULL;

-- ---------------------------------------------------------------------------
-- 2. Preflight A: reject non-NULL fingerprints that are not the V1 golden
--    value.  NULL is allowed (will be backfilled at step 5); the V1
--    golden fingerprint is allowed (safe rerun).  Any other value —
--    including format-valid 64-char hex strings — aborts the migration.
--    The error message is a fixed local string and does not echo the
--    offending value, row id, or any database content.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    v1_golden CONSTANT text := 'e443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6d';
BEGIN
    IF EXISTS (
        SELECT 1 FROM reader_article_rag_index_runs
        WHERE profile_fingerprint IS NOT NULL
          AND profile_fingerprint <> v1_golden
    ) THEN
        RAISE EXCEPTION
            'preflight_a: found reader_article_rag_index_runs rows with '
            'a non-NULL profile_fingerprint that does not match the '
            'frozen V1 golden fingerprint; cannot proceed with '
            'profile_fingerprint migration';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. Preflight B: reject unknown / contradictory V1 identity rows.
--    Identity columns must be either NULL or exactly the V1 expected
--    value.  Unknown ``index_version`` / ``chunker_version`` always
--    fail (they are NOT NULL columns).  ``embedding_model`` and
--    ``vector_collection`` are nullable; non-NULL values must match
--    the V1 expected value.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    -- Reject any row whose index_version is not the single registered V1.
    IF EXISTS (
        SELECT 1 FROM reader_article_rag_index_runs
        WHERE index_version <> 'article_rag_index_v1'
    ) THEN
        RAISE EXCEPTION
            'preflight_b: found reader_article_rag_index_runs rows with '
            'index_version other than article_rag_index_v1; '
            'cannot backfill profile_fingerprint';
    END IF;

    -- Reject any row whose chunker_version contradicts the V1 profile.
    IF EXISTS (
        SELECT 1 FROM reader_article_rag_index_runs
        WHERE chunker_version <> 'article_rag_index_plan_v1'
    ) THEN
        RAISE EXCEPTION
            'preflight_b: found reader_article_rag_index_runs rows with '
            'chunker_version other than article_rag_index_plan_v1; '
            'cannot backfill profile_fingerprint';
    END IF;

    -- Reject any row whose embedding_model is non-NULL and not v4.
    IF EXISTS (
        SELECT 1 FROM reader_article_rag_index_runs
        WHERE embedding_model IS NOT NULL
          AND embedding_model <> 'text-embedding-v4'
    ) THEN
        RAISE EXCEPTION
            'preflight_b: found reader_article_rag_index_runs rows with '
            'embedding_model other than text-embedding-v4; '
            'cannot backfill profile_fingerprint';
    END IF;

    -- Reject any row whose vector_collection is non-NULL and not v1.
    IF EXISTS (
        SELECT 1 FROM reader_article_rag_index_runs
        WHERE vector_collection IS NOT NULL
          AND vector_collection <> 'article_rag_index_v1'
    ) THEN
        RAISE EXCEPTION
            'preflight_b: found reader_article_rag_index_runs rows with '
            'vector_collection other than article_rag_index_v1; '
            'cannot backfill profile_fingerprint';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 4. Preflight C: reject execution-active legacy rows.  Rows in
--    ``planned`` / ``queued`` / ``indexing`` may have half-frozen
--    associated ``reader_jobs`` payloads (``input_json`` /
--    ``input_hash``); the migration does not attempt to repair them.
--    Deployers must drain / terminalize execution-active Article RAG
--    V1 runs before applying this migration.  Terminal / history
--    statuses (``indexed``, ``failed``, ``superseded``) are
--    backfillable.  The error message is a fixed local string and
--    does not echo row id, status, or any database content.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM reader_article_rag_index_runs
        WHERE status IN ('planned', 'queued', 'indexing')
    ) THEN
        RAISE EXCEPTION
            'preflight_c: found reader_article_rag_index_runs rows in '
            'an execution-active status (planned / queued / indexing); '
            'drain or terminalize execution-active Article RAG V1 runs '
            'before applying migration 0021';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 5. Backfill: set the frozen V1 profile fingerprint for NULL rows only.
--    Rows already carrying the V1 golden value (safe rerun) are left
--    untouched; rows carrying any other value would have failed at
--    preflight A.
-- ---------------------------------------------------------------------------

UPDATE reader_article_rag_index_runs
SET profile_fingerprint = 'e443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6d'
WHERE profile_fingerprint IS NULL;

-- ---------------------------------------------------------------------------
-- 6. Add CHECK constraint NOT VALID (sha256 lowercase hex + non-null)
-- ---------------------------------------------------------------------------

ALTER TABLE reader_article_rag_index_runs
    DROP CONSTRAINT IF EXISTS ck_reader_article_rag_index_runs_profile_fingerprint_sha256;

ALTER TABLE reader_article_rag_index_runs
    ADD CONSTRAINT ck_reader_article_rag_index_runs_profile_fingerprint_sha256
    CHECK (profile_fingerprint IS NOT NULL
           AND profile_fingerprint ~ '^[0-9a-f]{64}$') NOT VALID;

-- ---------------------------------------------------------------------------
-- 7. VALIDATE constraint (all rows pass after backfill)
-- ---------------------------------------------------------------------------

ALTER TABLE reader_article_rag_index_runs
    VALIDATE CONSTRAINT ck_reader_article_rag_index_runs_profile_fingerprint_sha256;

-- ---------------------------------------------------------------------------
-- 8. SET NOT NULL
-- ---------------------------------------------------------------------------

ALTER TABLE reader_article_rag_index_runs
    ALTER COLUMN profile_fingerprint SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 9. Column comment
-- ---------------------------------------------------------------------------

COMMENT ON COLUMN reader_article_rag_index_runs.profile_fingerprint IS
    'P1-C: SHA-256 fingerprint of the canonical ArticleRagIndexProfile payload (index_version, plan_version, chunker_version, embedding model/dimension/text_type, vector_namespace, retrieval_schema_version, citation_mode_version). Durably links each index run to the exact profile identity used at bootstrap time.';

COMMIT;
