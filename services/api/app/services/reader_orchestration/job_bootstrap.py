from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

TRANSLATION_RUN_TYPE = "translation_layer"
TRANSLATION_JOB_TYPE = "translate_unit"
TRANSLATION_TARGET_SCOPE = "unit"
TRANSLATION_TRIGGER_KIND = "system"
TRANSLATION_POLICY_VERSION = "reader_translation_bootstrap_v1"
TRANSLATION_OPERATION_FINGERPRINT = "translation_unit_v1"
DEFAULT_TRANSLATION_TARGET_LANGUAGE = "zh-CN"
DEFAULT_TRANSLATION_MAX_ATTEMPTS = 3
VOCABULARY_RUN_TYPE = "vocabulary_layer"
VOCABULARY_JOB_TYPE = "build_vocabulary_layer"
VOCABULARY_TARGET_SCOPE = "unit"
VOCABULARY_TRIGGER_KIND = "system"
VOCABULARY_POLICY_VERSION = "reader_vocabulary_bootstrap_v1"
VOCABULARY_OPERATION_FINGERPRINT = "vocabulary_unit_v1"
DEFAULT_VOCABULARY_MAX_ATTEMPTS = 3
GRAMMAR_RUN_TYPE = "grammar_bundle"
GRAMMAR_JOB_TYPE = "build_grammar_bundle"
GRAMMAR_TARGET_SCOPE = "unit"
GRAMMAR_TRIGGER_KIND = "system"
GRAMMAR_POLICY_VERSION = "reader_grammar_bundle_bootstrap_v1"
GRAMMAR_OPERATION_FINGERPRINT = "grammar_bundle_unit_v1"
DEFAULT_GRAMMAR_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class TranslationBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class VocabularyBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class GrammarBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    expected_generation: int
    operation_fingerprint: str


class TranslationJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_translation_run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> TranslationBootstrapResult:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                record_row = await conn.fetchrow(
                    """
                    SELECT id, generation, active_base_id, lifecycle_status, product_state
                    FROM reading_records
                    WHERE id = $1
                      AND user_id = $2
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    record_id,
                    user_id,
                )
                if record_row is None:
                    raise LookupError(f"reading record {record_id} not found for user {user_id}")
                if record_row["lifecycle_status"] != "active":
                    raise ValueError("translation bootstrap requires an active reading record")
                if record_row["product_state"] not in {"readable_enhancing", "processing"}:
                    raise ValueError("reading record is not ready for translation bootstrap")

                base_id = record_row["active_base_id"]
                if base_id is None:
                    raise ValueError("translation bootstrap requires an active base")

                base_row = await conn.fetchrow(
                    """
                    SELECT id, record_generation, status, text, language
                    FROM reading_bases
                    WHERE id = $1
                      AND reading_record_id = $2
                    """,
                    base_id,
                    record_id,
                )
                if base_row is None:
                    raise ValueError("active base does not belong to the requested record")

                expected_generation = int(record_row["generation"])
                if int(base_row["record_generation"]) != expected_generation:
                    raise ValueError(
                        "active base generation does not match "
                        "the reading record generation"
                    )
                if base_row["status"] != "active":
                    raise ValueError("translation bootstrap requires status='active' base")

                unit_row = await conn.fetchrow(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.base_start_utf16,
                        u.base_end_utf16,
                        u.text_hash
                    FROM reading_units u
                    WHERE u.reading_record_id = $1
                      AND u.base_id = $2
                      AND NOT EXISTS (
                          SELECT 1
                          FROM enhancement_layers layer
                          WHERE layer.reading_record_id = u.reading_record_id
                            AND layer.base_id = u.base_id
                            AND layer.generation = $3
                            AND layer.layer_type = 'translation'
                            AND layer.target_scope = 'unit'
                            AND layer.target_key = u.unit_id
                            AND layer.status = 'published'
                      )
                    ORDER BY u.order_index ASC
                    LIMIT 1
                    """,
                    record_id,
                    base_id,
                    expected_generation,
                )
                if unit_row is None:
                    raise ValueError("no untranslated reading unit is available")

                existing_job = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = 'translate_unit'
                      AND target_type = 'unit'
                      AND target_key = $3
                      AND expected_generation = $4
                      AND operation_fingerprint = $5
                      AND status IN ('queued', 'claimed', 'retry_later', 'paused')
                    LIMIT 1
                    """,
                    record_id,
                    base_id,
                    unit_row["unit_id"],
                    expected_generation,
                    TRANSLATION_OPERATION_FINGERPRINT,
                )
                if existing_job is not None:
                    return TranslationBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=record_id,
                        base_id=base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=expected_generation,
                        operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
                    )

                run_row = await conn.fetchrow(
                    """
                    INSERT INTO reader_runs (
                        reading_record_id,
                        user_id,
                        run_type,
                        status,
                        record_generation,
                        envelope_json,
                        policy_version,
                        trigger_kind
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        'queued',
                        $4,
                        $5::jsonb,
                        $6,
                        $7
                    )
                    RETURNING id
                    """,
                    record_id,
                    user_id,
                    TRANSLATION_RUN_TYPE,
                    expected_generation,
                    jsonb_param(
                        {
                            "record_id": str(record_id),
                            "base_id": str(base_id),
                            "target_scope": TRANSLATION_TARGET_SCOPE,
                            "target_unit_id": str(unit_row["unit_id"]),
                            "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                        }
                    ),
                    TRANSLATION_POLICY_VERSION,
                    TRANSLATION_TRIGGER_KIND,
                )
                if run_row is None:
                    raise RuntimeError("reader_runs insert did not return a row")

                unit_text_signature = (
                    f"{base_id}:{unit_row['unit_id']}:{unit_row['text_hash']}:"
                    f"{base_row['language'] or 'en'}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}"
                )
                input_hash = hashlib.sha256(unit_text_signature.encode("utf-8")).hexdigest()

                job_row = await conn.fetchrow(
                    """
                    INSERT INTO reader_jobs (
                        reading_record_id,
                        base_id,
                        run_id,
                        user_id,
                        job_type,
                        target_type,
                        target_key,
                        status,
                        priority,
                        expected_generation,
                        operation_fingerprint,
                        idempotency_key,
                        input_hash,
                        input_json,
                        max_attempts
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5,
                        $6,
                        $7,
                        'queued',
                        0,
                        $8,
                        $9,
                        $10,
                        $11,
                        $12::jsonb,
                        $13
                    )
                    RETURNING id
                    """,
                    record_id,
                    base_id,
                    run_row["id"],
                    user_id,
                    TRANSLATION_JOB_TYPE,
                    TRANSLATION_TARGET_SCOPE,
                    unit_row["unit_id"],
                    expected_generation,
                    TRANSLATION_OPERATION_FINGERPRINT,
                    f"{TRANSLATION_OPERATION_FINGERPRINT}:{unit_row['unit_id']}",
                    input_hash,
                    jsonb_param(
                        {
                            "unit_id": str(unit_row["unit_id"]),
                            "unit_order_index": int(unit_row["order_index"]),
                            "unit_text_hash": str(unit_row["text_hash"]),
                            "base_language": str(base_row["language"] or "en"),
                            "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                        }
                    ),
                    DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                )
                if job_row is None:
                    raise RuntimeError("reader_jobs insert did not return a row")

                return TranslationBootstrapResult(
                    run_id=run_row["id"],
                    job_id=job_row["id"],
                    reading_record_id=record_id,
                    base_id=base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=expected_generation,
                    operation_fingerprint=TRANSLATION_OPERATION_FINGERPRINT,
                )


class VocabularyJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_vocabulary_run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> VocabularyBootstrapResult:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                record_row = await conn.fetchrow(
                    """
                    SELECT id, generation, active_base_id, lifecycle_status, product_state
                    FROM reading_records
                    WHERE id = $1
                      AND user_id = $2
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    record_id,
                    user_id,
                )
                if record_row is None:
                    raise LookupError(f"reading record {record_id} not found for user {user_id}")
                if record_row["lifecycle_status"] != "active":
                    raise ValueError("vocabulary bootstrap requires an active reading record")
                if record_row["product_state"] not in {"readable_enhancing", "processing"}:
                    raise ValueError("reading record is not ready for vocabulary bootstrap")

                base_id = record_row["active_base_id"]
                if base_id is None:
                    raise ValueError("vocabulary bootstrap requires an active base")

                base_row = await conn.fetchrow(
                    """
                    SELECT id, record_generation, status, language
                    FROM reading_bases
                    WHERE id = $1
                      AND reading_record_id = $2
                    """,
                    base_id,
                    record_id,
                )
                if base_row is None:
                    raise ValueError("active base does not belong to the requested record")

                expected_generation = int(record_row["generation"])
                if int(base_row["record_generation"]) != expected_generation:
                    raise ValueError(
                        "active base generation does not match "
                        "the reading record generation"
                    )
                if base_row["status"] != "active":
                    raise ValueError("vocabulary bootstrap requires status='active' base")

                unit_row = await conn.fetchrow(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.text_hash
                    FROM reading_units u
                    WHERE u.reading_record_id = $1
                      AND u.base_id = $2
                      AND NOT EXISTS (
                          SELECT 1
                          FROM enhancement_layers layer
                          WHERE layer.reading_record_id = u.reading_record_id
                            AND layer.base_id = u.base_id
                            AND layer.generation = $3
                            AND layer.layer_type = 'vocabulary'
                            AND layer.target_scope = 'unit'
                            AND layer.target_key = u.unit_id
                            AND layer.status = 'published'
                      )
                    ORDER BY u.order_index ASC
                    LIMIT 1
                    """,
                    record_id,
                    base_id,
                    expected_generation,
                )
                if unit_row is None:
                    raise ValueError("no unprocessed vocabulary reading unit is available")

                existing_job = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = $3
                      AND target_type = $4
                      AND target_key = $5
                      AND expected_generation = $6
                      AND operation_fingerprint = $7
                      AND status IN ('queued', 'claimed', 'retry_later', 'paused')
                    LIMIT 1
                    """,
                    record_id,
                    base_id,
                    VOCABULARY_JOB_TYPE,
                    VOCABULARY_TARGET_SCOPE,
                    unit_row["unit_id"],
                    expected_generation,
                    VOCABULARY_OPERATION_FINGERPRINT,
                )
                if existing_job is not None:
                    return VocabularyBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=record_id,
                        base_id=base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=expected_generation,
                        operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
                    )

                run_row = await conn.fetchrow(
                    """
                    INSERT INTO reader_runs (
                        reading_record_id,
                        user_id,
                        run_type,
                        status,
                        record_generation,
                        envelope_json,
                        policy_version,
                        trigger_kind
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        'queued',
                        $4,
                        $5::jsonb,
                        $6,
                        $7
                    )
                    RETURNING id
                    """,
                    record_id,
                    user_id,
                    VOCABULARY_RUN_TYPE,
                    expected_generation,
                    jsonb_param(
                        {
                            "record_id": str(record_id),
                            "base_id": str(base_id),
                            "target_scope": VOCABULARY_TARGET_SCOPE,
                            "target_unit_id": str(unit_row["unit_id"]),
                            "layer_type": "vocabulary",
                        }
                    ),
                    VOCABULARY_POLICY_VERSION,
                    VOCABULARY_TRIGGER_KIND,
                )
                if run_row is None:
                    raise RuntimeError("reader_runs insert did not return a row")

                unit_text_signature = (
                    f"{base_id}:{unit_row['unit_id']}:{unit_row['text_hash']}:"
                    f"{base_row['language'] or 'en'}:vocabulary:1"
                )
                input_hash = hashlib.sha256(unit_text_signature.encode("utf-8")).hexdigest()

                job_row = await conn.fetchrow(
                    """
                    INSERT INTO reader_jobs (
                        reading_record_id,
                        base_id,
                        run_id,
                        user_id,
                        job_type,
                        target_type,
                        target_key,
                        status,
                        priority,
                        expected_generation,
                        operation_fingerprint,
                        idempotency_key,
                        input_hash,
                        input_json,
                        max_attempts
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5,
                        $6,
                        $7,
                        'queued',
                        0,
                        $8,
                        $9,
                        $10,
                        $11,
                        $12::jsonb,
                        $13
                    )
                    RETURNING id
                    """,
                    record_id,
                    base_id,
                    run_row["id"],
                    user_id,
                    VOCABULARY_JOB_TYPE,
                    VOCABULARY_TARGET_SCOPE,
                    unit_row["unit_id"],
                    expected_generation,
                    VOCABULARY_OPERATION_FINGERPRINT,
                    f"{VOCABULARY_OPERATION_FINGERPRINT}:{unit_row['unit_id']}",
                    input_hash,
                    jsonb_param(
                        {
                            "unit_id": str(unit_row["unit_id"]),
                            "unit_order_index": int(unit_row["order_index"]),
                            "unit_text_hash": str(unit_row["text_hash"]),
                            "base_language": str(base_row["language"] or "en"),
                            "layer_type": "vocabulary",
                        }
                    ),
                    DEFAULT_VOCABULARY_MAX_ATTEMPTS,
                )
                if job_row is None:
                    raise RuntimeError("reader_jobs insert did not return a row")

                return VocabularyBootstrapResult(
                    run_id=run_row["id"],
                    job_id=job_row["id"],
                    reading_record_id=record_id,
                    base_id=base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=expected_generation,
                    operation_fingerprint=VOCABULARY_OPERATION_FINGERPRINT,
                )


class GrammarJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_grammar_run(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> GrammarBootstrapResult:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                record_row = await conn.fetchrow(
                    """
                    SELECT id, generation, active_base_id, lifecycle_status, product_state
                    FROM reading_records
                    WHERE id = $1
                      AND user_id = $2
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    record_id,
                    user_id,
                )
                if record_row is None:
                    raise LookupError(f"reading record {record_id} not found for user {user_id}")
                if record_row["lifecycle_status"] != "active":
                    raise ValueError("grammar bootstrap requires an active reading record")
                if record_row["product_state"] not in {"readable_enhancing", "processing"}:
                    raise ValueError("reading record is not ready for grammar bootstrap")

                base_id = record_row["active_base_id"]
                if base_id is None:
                    raise ValueError("grammar bootstrap requires an active base")

                base_row = await conn.fetchrow(
                    """
                    SELECT id, record_generation, status, language
                    FROM reading_bases
                    WHERE id = $1
                      AND reading_record_id = $2
                    """,
                    base_id,
                    record_id,
                )
                if base_row is None:
                    raise ValueError("active base does not belong to the requested record")

                expected_generation = int(record_row["generation"])
                if int(base_row["record_generation"]) != expected_generation:
                    raise ValueError(
                        "active base generation does not match "
                        "the reading record generation"
                    )
                if base_row["status"] != "active":
                    raise ValueError("grammar bootstrap requires status='active' base")

                unit_row = await conn.fetchrow(
                    """
                    SELECT
                        u.unit_id,
                        u.order_index,
                        u.text_hash
                    FROM reading_units u
                    WHERE u.reading_record_id = $1
                      AND u.base_id = $2
                      AND NOT EXISTS (
                          SELECT 1
                          FROM enhancement_layers layer
                          WHERE layer.reading_record_id = u.reading_record_id
                            AND layer.base_id = u.base_id
                            AND layer.generation = $3
                            AND layer.layer_type IN ('grammar_note', 'sentence_analysis')
                            AND layer.target_scope = 'unit'
                            AND layer.target_key = u.unit_id
                            AND layer.status = 'published'
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM reader_jobs job
                          WHERE job.reading_record_id = u.reading_record_id
                            AND job.base_id = u.base_id
                            AND job.job_type = $4
                            AND job.target_type = $5
                            AND job.target_key = u.unit_id
                            AND job.expected_generation = $3
                            AND job.operation_fingerprint = $6
                            AND job.status = 'succeeded'
                      )
                    ORDER BY u.order_index ASC
                    LIMIT 1
                    """,
                    record_id,
                    base_id,
                    expected_generation,
                    GRAMMAR_JOB_TYPE,
                    GRAMMAR_TARGET_SCOPE,
                    GRAMMAR_OPERATION_FINGERPRINT,
                )
                if unit_row is None:
                    raise ValueError("no unprocessed grammar reading unit is available")

                existing_job = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = $3
                      AND target_type = $4
                      AND target_key = $5
                      AND expected_generation = $6
                      AND operation_fingerprint = $7
                      AND status IN ('queued', 'claimed', 'retry_later', 'paused')
                    LIMIT 1
                    """,
                    record_id,
                    base_id,
                    GRAMMAR_JOB_TYPE,
                    GRAMMAR_TARGET_SCOPE,
                    unit_row["unit_id"],
                    expected_generation,
                    GRAMMAR_OPERATION_FINGERPRINT,
                )
                if existing_job is not None:
                    return GrammarBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=record_id,
                        base_id=base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=expected_generation,
                        operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
                    )

                run_row = await conn.fetchrow(
                    """
                    INSERT INTO reader_runs (
                        reading_record_id,
                        user_id,
                        run_type,
                        status,
                        record_generation,
                        envelope_json,
                        policy_version,
                        trigger_kind
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        'queued',
                        $4,
                        $5::jsonb,
                        $6,
                        $7
                    )
                    RETURNING id
                    """,
                    record_id,
                    user_id,
                    GRAMMAR_RUN_TYPE,
                    expected_generation,
                    jsonb_param(
                        {
                            "record_id": str(record_id),
                            "base_id": str(base_id),
                            "target_scope": GRAMMAR_TARGET_SCOPE,
                            "target_unit_id": str(unit_row["unit_id"]),
                            "layer_types": ["grammar_note", "sentence_analysis"],
                        }
                    ),
                    GRAMMAR_POLICY_VERSION,
                    GRAMMAR_TRIGGER_KIND,
                )
                if run_row is None:
                    raise RuntimeError("reader_runs insert did not return a row")

                unit_text_signature = (
                    f"{base_id}:{unit_row['unit_id']}:{unit_row['text_hash']}:"
                    f"{base_row['language'] or 'en'}:grammar_bundle:1"
                )
                input_hash = hashlib.sha256(unit_text_signature.encode("utf-8")).hexdigest()

                job_row = await conn.fetchrow(
                    """
                    INSERT INTO reader_jobs (
                        reading_record_id,
                        base_id,
                        run_id,
                        user_id,
                        job_type,
                        target_type,
                        target_key,
                        status,
                        priority,
                        expected_generation,
                        operation_fingerprint,
                        idempotency_key,
                        input_hash,
                        input_json,
                        max_attempts
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5,
                        $6,
                        $7,
                        'queued',
                        0,
                        $8,
                        $9,
                        $10,
                        $11,
                        $12::jsonb,
                        $13
                    )
                    RETURNING id
                    """,
                    record_id,
                    base_id,
                    run_row["id"],
                    user_id,
                    GRAMMAR_JOB_TYPE,
                    GRAMMAR_TARGET_SCOPE,
                    unit_row["unit_id"],
                    expected_generation,
                    GRAMMAR_OPERATION_FINGERPRINT,
                    f"{GRAMMAR_OPERATION_FINGERPRINT}:{unit_row['unit_id']}",
                    input_hash,
                    jsonb_param(
                        {
                            "unit_id": str(unit_row["unit_id"]),
                            "unit_order_index": int(unit_row["order_index"]),
                            "unit_text_hash": str(unit_row["text_hash"]),
                            "base_language": str(base_row["language"] or "en"),
                            "layer_types": ["grammar_note", "sentence_analysis"],
                        }
                    ),
                    DEFAULT_GRAMMAR_MAX_ATTEMPTS,
                )
                if job_row is None:
                    raise RuntimeError("reader_jobs insert did not return a row")

                return GrammarBootstrapResult(
                    run_id=run_row["id"],
                    job_id=job_row["id"],
                    reading_record_id=record_id,
                    base_id=base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=expected_generation,
                    operation_fingerprint=GRAMMAR_OPERATION_FINGERPRINT,
                )
