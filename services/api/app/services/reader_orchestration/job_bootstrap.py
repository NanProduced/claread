from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.reading_strategy import (
    ReaderVariantStrategy,
    resolve_reader_variant_strategy,
)

TRANSLATION_RUN_TYPE = "translation_layer"
TRANSLATION_JOB_TYPE = "translate_unit"
TRANSLATION_TARGET_SCOPE = "unit"
TRANSLATION_TRIGGER_KIND = "system"
TRANSLATION_POLICY_VERSION = "reader_translation_bootstrap_v1"
TRANSLATION_OPERATION_FINGERPRINT = "translation_unit"
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
DISPLAY_TITLE_RUN_TYPE = "display_title_generation"
DISPLAY_TITLE_JOB_TYPE = "generate_display_title_zh"
DISPLAY_TITLE_TARGET_SCOPE = "record"
DISPLAY_TITLE_TRIGGER_KIND = "system"
DISPLAY_TITLE_POLICY_VERSION = "reader_display_title_bootstrap_v1"
DISPLAY_TITLE_OPERATION_FINGERPRINT = "display_title_zh_v1"
DEFAULT_DISPLAY_TITLE_MAX_ATTEMPTS = 5
_BOOTSTRAP_READY_PRODUCT_STATES = frozenset({"readable_enhancing", "processing"})

# Maps each enhancement job_type to the variant policy layer name it belongs
# to. ``generate_display_title_zh`` has no entry because the display title job
# does not consume a per-layer prompt policy; T5 only records strategy metadata
# and fingerprint coverage. T6/T7/T8 will wire layer prompts into the workers.
_LAYER_NAME_BY_JOB_TYPE: dict[str, str] = {
    TRANSLATION_JOB_TYPE: "translation",
    VOCABULARY_JOB_TYPE: "vocabulary",
    GRAMMAR_JOB_TYPE: "grammar_bundle",
}


def _compose_operation_fingerprint(
    base: str,
    strategy: ReaderVariantStrategy,
) -> str:
    """Compose a job operation fingerprint that covers the strategy hash.

    Any change to the resolved variant strategy (goal, variant, profile_id,
    annotation_density, strategy_version, or any layer prompt line) changes
    ``strategy_hash`` and therefore changes the composed fingerprint. This
    ensures that a policy text change does not silently reuse old job output:
    the ``reader_jobs.operation_fingerprint`` column differs, so the
    idempotency NOT EXISTS check treats the new fingerprint as a missing job.
    """
    return f"{base}:{strategy.strategy_hash}"


def _build_strategy_metadata(
    strategy: ReaderVariantStrategy,
    layer_name: str | None,
) -> dict[str, Any]:
    """Build the strategy metadata block recorded on job input/envelope JSON.

    T5 only persists metadata for audit and fingerprinting. It does NOT inject
    ``prompt_lines`` into worker prompts; T6/T7/T8 will read this block to
    resolve the per-layer prompt policy.
    """
    layer_policy_hash: str | None
    if layer_name is not None:
        layer = strategy.layers.get(layer_name)
        if layer is None:
            # Defensive: layer_name comes from _LAYER_NAME_BY_JOB_TYPE and the
            # resolver guarantees all REQUIRED_LAYERS are present. Fail closed
            # if a future code path violates that invariant.
            raise RuntimeError(
                f"resolved strategy has no layer {layer_name!r}"
            )
        layer_policy_hash = layer.policy_hash
    else:
        layer_policy_hash = None
    return {
        "reading_goal": strategy.reading_goal,
        "reading_variant": strategy.reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer_policy_hash,
    }


def _fingerprint_matches_base(fingerprint: str, base: str) -> bool:
    """Check if ``fingerprint`` is exactly ``base`` or ``base + ':' + hash``.

    Boundary-aware matching: ``v1`` must NOT match ``v10`` or ``v1abc``.
    Only an exact match (legacy constant fingerprint) or a ``base:hash``
    composed fingerprint (T5 strategy-aware) is accepted.
    """
    return fingerprint == base or fingerprint.startswith(base + ":")


# rationale_code written when a queued/retry_later/paused job is superseded
# because its operation_fingerprint no longer matches the current strategy
# fingerprint. Consumed by diagnostics and the pipeline runner's superseded
# counter.
_STRATEGY_FINGERPRINT_SUPERSEDED_RATIONALE = "strategy_fingerprint_superseded"


async def _supersede_stale_fingerprint_jobs(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    base_id: UUID,
    expected_generation: int,
    job_type: str,
    target_scope: str,
    current_fingerprint: str,
) -> int:
    """Mark active stale-fingerprint jobs as superseded.

    Before bootstrapping jobs with the current strategy fingerprint, any
    pre-existing ``queued`` / ``retry_later`` / ``paused`` job of the same
    record / base / generation / job_type / target_scope whose
    ``operation_fingerprint`` differs from ``current_fingerprint`` is marked
    ``superseded`` with rationale_code
    ``strategy_fingerprint_superseded``.

    ``claimed`` and ``succeeded`` jobs are intentionally left untouched:
    a claimed job is being actively processed by a worker, and a succeeded
    job has already published its layer (superseding it would not unpublish
    the layer).

    Returns the number of rows superseded.
    """
    result = await conn.execute(
        """
        UPDATE reader_jobs
        SET status = 'superseded',
            rationale_code = $7,
            lease_owner = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            updated_at = NOW()
        WHERE reading_record_id = $1
          AND base_id = $2
          AND expected_generation = $3
          AND job_type = $4
          AND target_type = $5
          AND operation_fingerprint <> $6
          AND status IN ('queued', 'retry_later', 'paused')
        """,
        record_id,
        base_id,
        expected_generation,
        job_type,
        target_scope,
        current_fingerprint,
        _STRATEGY_FINGERPRINT_SUPERSEDED_RATIONALE,
    )
    # asyncpg execute returns "UPDATE N" where N is the row count.
    count_str = result.split()[-1] if result else "0"
    try:
        return int(count_str)
    except ValueError:
        return 0


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


@dataclass(frozen=True, slots=True)
class DisplayTitleBootstrapResult:
    run_id: UUID
    job_id: UUID
    reading_record_id: UUID
    base_id: UUID
    expected_generation: int
    operation_fingerprint: str


@dataclass(frozen=True, slots=True)
class EnhancementBootstrapJobCounts:
    display_title: int = 0
    translation: int = 0
    vocabulary: int = 0
    grammar_bundle: int = 0


@dataclass(frozen=True, slots=True)
class EnhancementBootstrapSummary:
    record_id: UUID
    base_id: UUID
    expected_generation: int
    last_event_sequence: int
    job_counts: EnhancementBootstrapJobCounts
    display_title_results: tuple[DisplayTitleBootstrapResult, ...] = ()
    translation_results: tuple[TranslationBootstrapResult, ...] = ()
    vocabulary_results: tuple[VocabularyBootstrapResult, ...] = ()
    grammar_results: tuple[GrammarBootstrapResult, ...] = ()


@dataclass(frozen=True, slots=True)
class _LockedActiveBaseState:
    record_id: UUID
    user_id: UUID
    base_id: UUID
    expected_generation: int
    base_language: str
    last_event_sequence: int
    strategy: ReaderVariantStrategy


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
        """Enqueue the first translation job for the active base.

        Uses the shared strategy-aware helpers so the created job carries
        the same strategy metadata (reading_goal / reading_variant /
        strategy_version / strategy_hash / layer_policy_hash) and composed
        operation_fingerprint as ``EnhancementJobBootstrapService``. Any
        stale queued / retry_later / paused translation job whose
        operation_fingerprint no longer matches the current strategy is
        marked superseded before the new job is created.
        """
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                operation_fingerprint = _compose_operation_fingerprint(
                    TRANSLATION_OPERATION_FINGERPRINT, state.strategy
                )
                await _supersede_stale_fingerprint_jobs(
                    conn,
                    record_id=state.record_id,
                    base_id=state.base_id,
                    expected_generation=state.expected_generation,
                    job_type=TRANSLATION_JOB_TYPE,
                    target_scope=TRANSLATION_TARGET_SCOPE,
                    current_fingerprint=operation_fingerprint,
                )

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
                    state.record_id,
                    state.base_id,
                    state.expected_generation,
                )
                if unit_row is None:
                    raise ValueError("no untranslated reading unit is available")

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
                    state.record_id,
                    state.base_id,
                    TRANSLATION_JOB_TYPE,
                    TRANSLATION_TARGET_SCOPE,
                    unit_row["unit_id"],
                    state.expected_generation,
                    operation_fingerprint,
                )
                if existing_job is not None:
                    return TranslationBootstrapResult(
                        run_id=existing_job["run_id"],
                        job_id=existing_job["id"],
                        reading_record_id=state.record_id,
                        base_id=state.base_id,
                        unit_id=str(unit_row["unit_id"]),
                        expected_generation=state.expected_generation,
                        operation_fingerprint=operation_fingerprint,
                    )

                run_id, job_id = await _insert_unit_job(
                    conn,
                    state=state,
                    unit_id=str(unit_row["unit_id"]),
                    unit_order_index=int(unit_row["order_index"]),
                    unit_text_hash=str(unit_row["text_hash"]),
                    run_type=TRANSLATION_RUN_TYPE,
                    job_type=TRANSLATION_JOB_TYPE,
                    target_scope=TRANSLATION_TARGET_SCOPE,
                    policy_version=TRANSLATION_POLICY_VERSION,
                    trigger_kind=TRANSLATION_TRIGGER_KIND,
                    operation_fingerprint=operation_fingerprint,
                    max_attempts=DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                    envelope_json={
                        "record_id": str(state.record_id),
                        "base_id": str(state.base_id),
                        "target_scope": TRANSLATION_TARGET_SCOPE,
                        "target_unit_id": str(unit_row["unit_id"]),
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                    },
                    input_signature_suffix=(
                        f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}"
                    ),
                    input_json={
                        "base_language": state.base_language,
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                    },
                    layer_name=_LAYER_NAME_BY_JOB_TYPE[TRANSLATION_JOB_TYPE],
                )

                return TranslationBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(unit_row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
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


class DisplayTitleJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_display_title_job(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> DisplayTitleBootstrapResult | None:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                results = await _bootstrap_display_title_job(conn, state=state)
        return results[0] if results else None


class EnhancementJobBootstrapService:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def bootstrap_missing_jobs(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> EnhancementBootstrapSummary:
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                display_title_results = await _bootstrap_display_title_job(
                    conn,
                    state=state,
                )
                translation_results = await self._bootstrap_translation_jobs(
                    conn,
                    state=state,
                )
                vocabulary_results = await self._bootstrap_vocabulary_jobs(
                    conn,
                    state=state,
                )
                grammar_results = await self._bootstrap_grammar_jobs(
                    conn,
                    state=state,
                )

        return EnhancementBootstrapSummary(
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            last_event_sequence=state.last_event_sequence,
            job_counts=EnhancementBootstrapJobCounts(
                display_title=len(display_title_results),
                translation=len(translation_results),
                vocabulary=len(vocabulary_results),
                grammar_bundle=len(grammar_results),
            ),
            display_title_results=tuple(display_title_results),
            translation_results=tuple(translation_results),
            vocabulary_results=tuple(vocabulary_results),
            grammar_results=tuple(grammar_results),
        )

    async def _bootstrap_translation_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
    ) -> list[TranslationBootstrapResult]:
        operation_fingerprint = _compose_operation_fingerprint(
            TRANSLATION_OPERATION_FINGERPRINT, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=TRANSLATION_JOB_TYPE,
            target_scope=TRANSLATION_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
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
                    AND layer.layer_type = 'translation'
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
                    AND job.status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            TRANSLATION_JOB_TYPE,
            TRANSLATION_TARGET_SCOPE,
            operation_fingerprint,
        )
        results: list[TranslationBootstrapResult] = []
        for row in rows:
            run_id, job_id = await _insert_unit_job(
                conn,
                state=state,
                unit_id=str(row["unit_id"]),
                unit_order_index=int(row["order_index"]),
                unit_text_hash=str(row["text_hash"]),
                run_type=TRANSLATION_RUN_TYPE,
                job_type=TRANSLATION_JOB_TYPE,
                target_scope=TRANSLATION_TARGET_SCOPE,
                policy_version=TRANSLATION_POLICY_VERSION,
                trigger_kind=TRANSLATION_TRIGGER_KIND,
                operation_fingerprint=operation_fingerprint,
                max_attempts=DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                envelope_json={
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": TRANSLATION_TARGET_SCOPE,
                    "target_unit_id": str(row["unit_id"]),
                    "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                },
                input_signature_suffix=(
                    f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}"
                ),
                input_json={
                    "unit_id": str(row["unit_id"]),
                    "unit_order_index": int(row["order_index"]),
                    "unit_text_hash": str(row["text_hash"]),
                    "base_language": state.base_language,
                    "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                },
                layer_name=_LAYER_NAME_BY_JOB_TYPE[TRANSLATION_JOB_TYPE],
            )
            results.append(
                TranslationBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )
            )
        return results

    async def _bootstrap_vocabulary_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
    ) -> list[VocabularyBootstrapResult]:
        operation_fingerprint = _compose_operation_fingerprint(
            VOCABULARY_OPERATION_FINGERPRINT, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=VOCABULARY_JOB_TYPE,
            target_scope=VOCABULARY_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
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
                    AND job.status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            VOCABULARY_JOB_TYPE,
            VOCABULARY_TARGET_SCOPE,
            operation_fingerprint,
        )
        results: list[VocabularyBootstrapResult] = []
        for row in rows:
            run_id, job_id = await _insert_unit_job(
                conn,
                state=state,
                unit_id=str(row["unit_id"]),
                unit_order_index=int(row["order_index"]),
                unit_text_hash=str(row["text_hash"]),
                run_type=VOCABULARY_RUN_TYPE,
                job_type=VOCABULARY_JOB_TYPE,
                target_scope=VOCABULARY_TARGET_SCOPE,
                policy_version=VOCABULARY_POLICY_VERSION,
                trigger_kind=VOCABULARY_TRIGGER_KIND,
                operation_fingerprint=operation_fingerprint,
                max_attempts=DEFAULT_VOCABULARY_MAX_ATTEMPTS,
                envelope_json={
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": VOCABULARY_TARGET_SCOPE,
                    "target_unit_id": str(row["unit_id"]),
                    "layer_type": "vocabulary",
                },
                input_signature_suffix=f"{state.base_language}:vocabulary:1",
                input_json={
                    "unit_id": str(row["unit_id"]),
                    "unit_order_index": int(row["order_index"]),
                    "unit_text_hash": str(row["text_hash"]),
                    "base_language": state.base_language,
                    "layer_type": "vocabulary",
                },
                layer_name=_LAYER_NAME_BY_JOB_TYPE[VOCABULARY_JOB_TYPE],
            )
            results.append(
                VocabularyBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )
            )
        return results

    async def _bootstrap_grammar_jobs(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
    ) -> list[GrammarBootstrapResult]:
        operation_fingerprint = _compose_operation_fingerprint(
            GRAMMAR_OPERATION_FINGERPRINT, state.strategy
        )
        await _supersede_stale_fingerprint_jobs(
            conn,
            record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            job_type=GRAMMAR_JOB_TYPE,
            target_scope=GRAMMAR_TARGET_SCOPE,
            current_fingerprint=operation_fingerprint,
        )
        rows = await conn.fetch(
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
                    AND job.status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
              )
            ORDER BY u.order_index ASC
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            GRAMMAR_JOB_TYPE,
            GRAMMAR_TARGET_SCOPE,
            operation_fingerprint,
        )
        results: list[GrammarBootstrapResult] = []
        for row in rows:
            run_id, job_id = await _insert_unit_job(
                conn,
                state=state,
                unit_id=str(row["unit_id"]),
                unit_order_index=int(row["order_index"]),
                unit_text_hash=str(row["text_hash"]),
                run_type=GRAMMAR_RUN_TYPE,
                job_type=GRAMMAR_JOB_TYPE,
                target_scope=GRAMMAR_TARGET_SCOPE,
                policy_version=GRAMMAR_POLICY_VERSION,
                trigger_kind=GRAMMAR_TRIGGER_KIND,
                operation_fingerprint=operation_fingerprint,
                max_attempts=DEFAULT_GRAMMAR_MAX_ATTEMPTS,
                envelope_json={
                    "record_id": str(state.record_id),
                    "base_id": str(state.base_id),
                    "target_scope": GRAMMAR_TARGET_SCOPE,
                    "target_unit_id": str(row["unit_id"]),
                    "layer_types": ["grammar_note", "sentence_analysis"],
                },
                input_signature_suffix=f"{state.base_language}:grammar_bundle:1",
                input_json={
                    "unit_id": str(row["unit_id"]),
                    "unit_order_index": int(row["order_index"]),
                    "unit_text_hash": str(row["text_hash"]),
                    "base_language": state.base_language,
                    "layer_types": ["grammar_note", "sentence_analysis"],
                },
                layer_name=_LAYER_NAME_BY_JOB_TYPE[GRAMMAR_JOB_TYPE],
            )
            results.append(
                GrammarBootstrapResult(
                    run_id=run_id,
                    job_id=job_id,
                    reading_record_id=state.record_id,
                    base_id=state.base_id,
                    unit_id=str(row["unit_id"]),
                    expected_generation=state.expected_generation,
                    operation_fingerprint=operation_fingerprint,
                )
            )
        return results


async def _load_locked_active_base_state(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
    user_id: UUID,
) -> _LockedActiveBaseState:
    record_row = await conn.fetchrow(
        """
        SELECT
            id,
            generation,
            active_base_id,
            lifecycle_status,
            product_state,
            reading_goal,
            reading_variant
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
        raise ValueError("enhancement bootstrap requires an active reading record")
    if record_row["product_state"] not in _BOOTSTRAP_READY_PRODUCT_STATES:
        raise ValueError("reading record is not ready for enhancement bootstrap")

    base_id = record_row["active_base_id"]
    if base_id is None:
        raise ValueError("enhancement bootstrap requires an active base")

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
            "active base generation does not match the reading record generation"
        )
    if base_row["status"] != "active":
        raise ValueError("enhancement bootstrap requires status='active' base")

    # Resolve the variant-first strategy from the reading record's first-class
    # reading_goal / reading_variant columns. These are persisted facts (T1 +
    # migration 0012), NOT inferred from source_metadata. The resolver fails
    # closed on missing/illegal pairs (including academic / academic_general);
    # there is no default fallback here. Historical records use the DB default
    # (daily_reading / intermediate_reading) which is a valid pair.
    strategy = resolve_reader_variant_strategy(
        str(record_row["reading_goal"]),
        str(record_row["reading_variant"]),
    )

    return _LockedActiveBaseState(
        record_id=record_id,
        user_id=user_id,
        base_id=base_id,
        expected_generation=expected_generation,
        base_language=str(base_row["language"] or "en"),
        last_event_sequence=await _load_last_event_sequence(conn, record_id=record_id),
        strategy=strategy,
    )


async def _bootstrap_display_title_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
) -> list[DisplayTitleBootstrapResult]:
    operation_fingerprint = _compose_operation_fingerprint(
        DISPLAY_TITLE_OPERATION_FINGERPRINT, state.strategy
    )
    await _supersede_stale_fingerprint_jobs(
        conn,
        record_id=state.record_id,
        base_id=state.base_id,
        expected_generation=state.expected_generation,
        job_type=DISPLAY_TITLE_JOB_TYPE,
        target_scope=DISPLAY_TITLE_TARGET_SCOPE,
        current_fingerprint=operation_fingerprint,
    )
    row = await conn.fetchrow(
        """
        SELECT title_generation_status
        FROM reading_records
        WHERE id = $1
          AND user_id = $2
          AND deleted_at IS NULL
        """,
        state.record_id,
        state.user_id,
    )
    if row is None:
        raise LookupError(f"reading record {state.record_id} not found")
    if row["title_generation_status"] == "succeeded":
        return []

    existing_job = await conn.fetchrow(
        """
        SELECT id
        FROM reader_jobs
        WHERE reading_record_id = $1
          AND base_id = $2
          AND job_type = $3
          AND target_type = $4
          AND target_key = $5
          AND expected_generation = $6
          AND operation_fingerprint = $7
          AND status IN ('queued', 'claimed', 'retry_later', 'paused', 'succeeded')
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        state.record_id,
        state.base_id,
        DISPLAY_TITLE_JOB_TYPE,
        DISPLAY_TITLE_TARGET_SCOPE,
        str(state.record_id),
        state.expected_generation,
        operation_fingerprint,
    )
    if existing_job is not None:
        return []

    await conn.execute(
        """
        UPDATE reading_records
        SET title_generation_status = 'pending',
            title_generation_error_code = NULL,
            title_generation_error_message = NULL,
            title_generation_updated_at = NOW(),
            updated_at = NOW()
        WHERE id = $1
          AND user_id = $2
          AND generation = $3
          AND active_base_id = $4
          AND deleted_at IS NULL
          AND title_generation_status <> 'succeeded'
        """,
        state.record_id,
        state.user_id,
        state.expected_generation,
        state.base_id,
    )

    run_id, job_id = await _insert_record_job(
        conn,
        state=state,
        run_type=DISPLAY_TITLE_RUN_TYPE,
        job_type=DISPLAY_TITLE_JOB_TYPE,
        target_scope=DISPLAY_TITLE_TARGET_SCOPE,
        policy_version=DISPLAY_TITLE_POLICY_VERSION,
        trigger_kind=DISPLAY_TITLE_TRIGGER_KIND,
        operation_fingerprint=operation_fingerprint,
        max_attempts=DEFAULT_DISPLAY_TITLE_MAX_ATTEMPTS,
        envelope_json={
            "record_id": str(state.record_id),
            "base_id": str(state.base_id),
            "target_scope": DISPLAY_TITLE_TARGET_SCOPE,
            "target_language": "zh-CN",
        },
        input_signature_suffix=f"{state.base_language}:display_title_zh:1",
        input_json={
            "target_language": "zh-CN",
            "base_language": state.base_language,
        },
        layer_name=None,
    )
    return [
        DisplayTitleBootstrapResult(
            run_id=run_id,
            job_id=job_id,
            reading_record_id=state.record_id,
            base_id=state.base_id,
            expected_generation=state.expected_generation,
            operation_fingerprint=operation_fingerprint,
        )
    ]


async def _load_last_event_sequence(
    conn: asyncpg.Connection,
    *,
    record_id: UUID,
) -> int:
    row = await conn.fetchrow(
        """
        SELECT next_sequence
        FROM reader_event_sequences
        WHERE reading_record_id = $1
        """,
        record_id,
    )
    if row is None or row["next_sequence"] is None:
        return 0
    return max(0, int(row["next_sequence"]) - 1)


async def _insert_record_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    run_type: str,
    job_type: str,
    target_scope: str,
    policy_version: str,
    trigger_kind: str,
    operation_fingerprint: str,
    max_attempts: int,
    envelope_json: dict[str, Any],
    input_signature_suffix: str,
    input_json: dict[str, Any],
    layer_name: str | None,
) -> tuple[UUID, UUID]:
    strategy_metadata = _build_strategy_metadata(state.strategy, layer_name)
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
        state.record_id,
        state.user_id,
        run_type,
        state.expected_generation,
        jsonb_param({**envelope_json, "strategy": strategy_metadata}),
        policy_version,
        trigger_kind,
    )
    if run_row is None:
        raise RuntimeError("reader_runs insert did not return a row")

    input_signature = (
        f"{state.base_id}:{state.record_id}:{state.expected_generation}:"
        f"{operation_fingerprint}:{input_signature_suffix}:"
        f"{state.strategy.strategy_hash}"
    )
    input_hash = hashlib.sha256(input_signature.encode("utf-8")).hexdigest()
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
            10,
            $8,
            $9,
            $10,
            $11,
            $12::jsonb,
            $13
        )
        RETURNING id
        """,
        state.record_id,
        state.base_id,
        run_row["id"],
        state.user_id,
        job_type,
        target_scope,
        str(state.record_id),
        state.expected_generation,
        operation_fingerprint,
        f"{operation_fingerprint}:{state.record_id}",
        input_hash,
        jsonb_param(
            {
                **input_json,
                **strategy_metadata,
                "record_id": str(state.record_id),
                "base_id": str(state.base_id),
                "expected_generation": state.expected_generation,
            }
        ),
        max_attempts,
    )
    if job_row is None:
        raise RuntimeError("reader_jobs insert did not return a row")
    return run_row["id"], job_row["id"]


async def _insert_unit_job(
    conn: asyncpg.Connection,
    *,
    state: _LockedActiveBaseState,
    unit_id: str,
    unit_order_index: int,
    unit_text_hash: str,
    run_type: str,
    job_type: str,
    target_scope: str,
    policy_version: str,
    trigger_kind: str,
    operation_fingerprint: str,
    max_attempts: int,
    envelope_json: dict[str, Any],
    input_signature_suffix: str,
    input_json: dict[str, Any],
    layer_name: str,
) -> tuple[UUID, UUID]:
    strategy_metadata = _build_strategy_metadata(state.strategy, layer_name)
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
        state.record_id,
        state.user_id,
        run_type,
        state.expected_generation,
        jsonb_param({**envelope_json, "strategy": strategy_metadata}),
        policy_version,
        trigger_kind,
    )
    if run_row is None:
        raise RuntimeError("reader_runs insert did not return a row")

    unit_text_signature = (
        f"{state.base_id}:{unit_id}:{unit_text_hash}:{input_signature_suffix}:"
        f"{state.strategy.strategy_hash}"
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
        state.record_id,
        state.base_id,
        run_row["id"],
        state.user_id,
        job_type,
        target_scope,
        unit_id,
        state.expected_generation,
        operation_fingerprint,
        f"{operation_fingerprint}:{unit_id}",
        input_hash,
        jsonb_param(
            {
                **input_json,
                **strategy_metadata,
                "unit_id": unit_id,
                "unit_order_index": unit_order_index,
                "unit_text_hash": unit_text_hash,
            }
        ),
        max_attempts,
    )
    if job_row is None:
        raise RuntimeError("reader_jobs insert did not return a row")
    return run_row["id"], job_row["id"]
