from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_orchestration import (
    GrammarNoteLayerOutput,
    ReaderTextRangeAnchor,
    SentenceAnalysisLayerOutput,
    TranslationLayerOutput,
    VocabularyLayerOutput,
)

from .event_runtime import ReaderEventEnvelope, ReaderEventRuntime
from .job_bootstrap import (
    GRAMMAR_JOB_TYPE,
    GRAMMAR_OPERATION_FINGERPRINT,
    GRAMMAR_TARGET_SCOPE,
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from .job_runtime import (
    FenceViolationError,
    ReaderJobRuntime,
    _assert_lease_valid,
)
from .repository import ReaderOrchestrationRepository
from .translation_parsed_decision import (
    TRANSLATION_PARSED_POLICY_CODE,
    TRANSLATION_PARSED_RATIONALE_CODE,
    build_translation_parsed_decision_documents,
    build_translation_parsed_decision_event_payload,
)

GRAMMAR_NOTE_LAYER_OPERATION_FINGERPRINT = "grammar_note_unit_v1"
SENTENCE_ANALYSIS_LAYER_OPERATION_FINGERPRINT = "sentence_analysis_unit_v1"


@dataclass(frozen=True, slots=True)
class PublishedTranslationLayer:
    layer_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    event: ReaderEventEnvelope


@dataclass(frozen=True, slots=True)
class PublishedVocabularyLayer:
    layer_id: UUID
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    event: ReaderEventEnvelope


@dataclass(frozen=True, slots=True)
class PublishedGrammarLayer:
    layer_id: UUID
    layer_type: str
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    event: ReaderEventEnvelope


@dataclass(frozen=True, slots=True)
class PublishedGrammarBundle:
    reading_record_id: UUID
    base_id: UUID
    unit_id: str
    generation: int
    grammar_note_layer: PublishedGrammarLayer | None
    sentence_analysis_layer: PublishedGrammarLayer | None
    events: tuple[ReaderEventEnvelope, ...]
    no_op: bool = False


@dataclass(frozen=True, slots=True)
class _UnitAnchorValidationContext:
    unit_text: str
    segments_by_id: dict[str, asyncpg.Record]


class TranslationLayerPublisher:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        repository: ReaderOrchestrationRepository | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_unit_translation(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: TranslationLayerOutput,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedTranslationLayer:
        payload = output.model_dump(mode="json")
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError("translation publish requires a claimed job")
                if job_row["job_type"] != "translate_unit" or job_row["target_type"] != "unit":
                    raise ValueError("translation publish requires a translate_unit/unit job")

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                unit_id = str(job_row["target_key"])

                unit_row = await conn.fetchrow(
                    """
                    SELECT unit.unit_id,
                           base.language AS source_language
                    FROM reading_units unit
                    JOIN reading_bases base
                      ON base.id = unit.base_id
                     AND base.reading_record_id = unit.reading_record_id
                    WHERE unit.reading_record_id = $1
                      AND unit.base_id = $2
                      AND unit.unit_id = $3
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                )
                if unit_row is None:
                    raise ValueError(f"translation publish target unit {unit_id} does not exist")

                existing_layer = await conn.fetchrow(
                    """
                    SELECT id
                    FROM enhancement_layers
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND layer_type = 'translation'
                      AND target_scope = 'unit'
                      AND target_key = $3
                      AND generation = $4
                      AND status = 'published'
                    LIMIT 1
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                )
                if existing_layer is not None:
                    raise ValueError(f"translation layer already published for unit {unit_id}")

                layer_row = await conn.fetchrow(
                    """
                    INSERT INTO enhancement_layers (
                        reading_record_id,
                        base_id,
                        layer_type,
                        layer_subtype,
                        target_scope,
                        target_key,
                        generation,
                        status,
                        operation_fingerprint,
                        schema_version,
                        output_json,
                        coverage_json,
                        quality_json,
                        source_run_id,
                        source_job_id,
                        published_at
                    )
                    VALUES (
                        $1,
                        $2,
                        'translation',
                        NULL,
                        'unit',
                        $3,
                        $4,
                        'published',
                        $5,
                        $6,
                        $7::jsonb,
                        '{}'::jsonb,
                        $8::jsonb,
                        $9,
                        $10,
                        $11
                    )
                    RETURNING id
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                    job_row["operation_fingerprint"],
                    int(output.schema_version),
                    jsonb_param(payload),
                    jsonb_param(dict(quality_json or {})),
                    job_row["run_id"],
                    job_id,
                    published_at,
                )
                if layer_row is None:
                    raise RuntimeError("enhancement_layers insert did not return a row")

                event = await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="layer_published",
                    payload_json={
                        "record_id": str(reading_record_id),
                        "base_id": str(base_id),
                        "layer_id": str(layer_row["id"]),
                        "layer_type": "translation",
                        "target_scope": "unit",
                        "target_key": unit_id,
                        "generation": generation,
                    },
                    source_run_id=job_row["run_id"],
                    source_job_id=job_id,
                    source_layer_id=layer_row["id"],
                    created_at=published_at,
                )
                source_language = str(unit_row["source_language"] or "en")
                coverage_json, decision_json = build_translation_parsed_decision_documents(
                    layer_id=layer_row["id"],
                    unit_id=unit_id,
                    generation=generation,
                    source_language=source_language,
                    target_language=output.target_language,
                )
                await self._repository.upsert_parsed_decision(
                    conn,
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    policy_code=TRANSLATION_PARSED_POLICY_CODE,
                    parsed_state="parsed",
                    rationale_code=TRANSLATION_PARSED_RATIONALE_CODE,
                    coverage_json=coverage_json,
                    source_layer_id=layer_row["id"],
                    source_job_id=job_id,
                    decision_json=decision_json,
                )
                await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="parsed_decision_updated",
                    payload_json=build_translation_parsed_decision_event_payload(
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        source_layer_id=layer_row["id"],
                        source_job_id=job_id,
                    ),
                    source_run_id=job_row["run_id"],
                    source_job_id=job_id,
                    source_layer_id=layer_row["id"],
                    created_at=published_at,
                )

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref={
                        "layer_id": str(layer_row["id"]),
                        "event_id": str(event.event_id),
                    },
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code="translation_published",
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": "translation_published",
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedTranslationLayer(
                    layer_id=layer_row["id"],
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    event=event,
                )


class VocabularyLayerPublisher:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_unit_vocabulary(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        output: VocabularyLayerOutput,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedVocabularyLayer:
        payload = output.model_dump(mode="json")
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError("vocabulary publish requires a claimed job")
                if (
                    job_row["job_type"] != VOCABULARY_JOB_TYPE
                    or job_row["target_type"] != VOCABULARY_TARGET_SCOPE
                    or not _fingerprint_matches_base(
                        job_row["operation_fingerprint"],
                        VOCABULARY_OPERATION_FINGERPRINT,
                    )
                ):
                    raise ValueError(
                        "vocabulary publish requires a build_vocabulary_layer/unit job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                unit_id = str(job_row["target_key"])

                unit_row = await conn.fetchrow(
                    """
                    SELECT unit_id
                    FROM reading_units
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                )
                if unit_row is None:
                    raise ValueError(f"vocabulary publish target unit {unit_id} does not exist")

                existing_layer = await conn.fetchrow(
                    """
                    SELECT id
                    FROM enhancement_layers
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND layer_type = 'vocabulary'
                      AND target_scope = 'unit'
                      AND target_key = $3
                      AND generation = $4
                      AND status = 'published'
                    LIMIT 1
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                )
                if existing_layer is not None:
                    raise ValueError(f"vocabulary layer already published for unit {unit_id}")

                await _validate_vocabulary_items(
                    conn,
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    output=output,
                )

                layer_row = await conn.fetchrow(
                    """
                    INSERT INTO enhancement_layers (
                        reading_record_id,
                        base_id,
                        layer_type,
                        layer_subtype,
                        target_scope,
                        target_key,
                        generation,
                        status,
                        operation_fingerprint,
                        schema_version,
                        output_json,
                        coverage_json,
                        quality_json,
                        source_run_id,
                        source_job_id,
                        published_at
                    )
                    VALUES (
                        $1,
                        $2,
                        'vocabulary',
                        NULL,
                        'unit',
                        $3,
                        $4,
                        'published',
                        $5,
                        $6,
                        $7::jsonb,
                        '{}'::jsonb,
                        $8::jsonb,
                        $9,
                        $10,
                        $11
                    )
                    RETURNING id
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                    generation,
                    job_row["operation_fingerprint"],
                    int(output.schema_version),
                    jsonb_param(payload),
                    jsonb_param(dict(quality_json or {})),
                    job_row["run_id"],
                    job_id,
                    published_at,
                )
                if layer_row is None:
                    raise RuntimeError("enhancement_layers insert did not return a row")

                event = await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="layer_published",
                    payload_json={
                        "record_id": str(reading_record_id),
                        "base_id": str(base_id),
                        "layer_id": str(layer_row["id"]),
                        "layer_type": "vocabulary",
                        "target_scope": "unit",
                        "target_key": unit_id,
                        "generation": generation,
                    },
                    source_run_id=job_row["run_id"],
                    source_job_id=job_id,
                    source_layer_id=layer_row["id"],
                    created_at=published_at,
                )

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref={
                        "layer_id": str(layer_row["id"]),
                        "event_id": str(event.event_id),
                    },
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code="vocabulary_published",
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": "vocabulary_published",
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedVocabularyLayer(
                    layer_id=layer_row["id"],
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    event=event,
                )


class GrammarBundleLayerPublisher:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        event_runtime: ReaderEventRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_unit_grammar_bundle(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        grammar_note_output: GrammarNoteLayerOutput | None,
        sentence_analysis_output: SentenceAnalysisLayerOutput | None,
        quality_json: dict[str, Any] | None = None,
    ) -> PublishedGrammarBundle:
        quality_payload = dict(quality_json or {})
        published_at = datetime.now(UTC)

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")
                if job_row["status"] != "claimed":
                    raise ValueError("grammar publish requires a claimed job")
                if (
                    job_row["job_type"] != GRAMMAR_JOB_TYPE
                    or job_row["target_type"] != GRAMMAR_TARGET_SCOPE
                    or not _fingerprint_matches_base(
                        job_row["operation_fingerprint"],
                        GRAMMAR_OPERATION_FINGERPRINT,
                    )
                ):
                    raise ValueError(
                        "grammar publish requires a build_grammar_bundle/unit job"
                    )

                _assert_lease_valid(job_row, job_id, lease_token)
                fence_error = await self._job_runtime._validate_fence(conn, job_row)  # type: ignore[attr-defined]
                if fence_error is not None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: {fence_error}"
                    )

                base_id = job_row["base_id"]
                if base_id is None:
                    raise FenceViolationError(
                        f"publish fence failed for job {job_id}: missing_base"
                    )
                reading_record_id = job_row["reading_record_id"]
                generation = int(job_row["expected_generation"])
                unit_id = str(job_row["target_key"])

                unit_row = await conn.fetchrow(
                    """
                    SELECT unit_id
                    FROM reading_units
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND unit_id = $3
                    """,
                    reading_record_id,
                    base_id,
                    unit_id,
                )
                if unit_row is None:
                    raise ValueError(f"grammar publish target unit {unit_id} does not exist")

                if grammar_note_output is not None:
                    await _assert_no_published_layer(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="grammar_note",
                    )
                    await _validate_grammar_note_items(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        output=grammar_note_output,
                    )
                if sentence_analysis_output is not None:
                    await _assert_no_published_layer(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="sentence_analysis",
                    )
                    await _validate_sentence_analysis_items(
                        conn,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        output=sentence_analysis_output,
                    )

                grammar_note_layer: PublishedGrammarLayer | None = None
                sentence_analysis_layer: PublishedGrammarLayer | None = None
                events: list[ReaderEventEnvelope] = []

                if grammar_note_output is not None:
                    grammar_note_layer = await _insert_published_grammar_layer(
                        conn,
                        event_runtime=self._event_runtime,
                        job_row=job_row,
                        job_id=job_id,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="grammar_note",
                        layer_operation_fingerprint=GRAMMAR_NOTE_LAYER_OPERATION_FINGERPRINT,
                        schema_version=int(grammar_note_output.schema_version),
                        payload=grammar_note_output.model_dump(mode="json"),
                        quality_json=quality_payload,
                        published_at=published_at,
                    )
                    events.append(grammar_note_layer.event)

                if sentence_analysis_output is not None:
                    sentence_analysis_layer = await _insert_published_grammar_layer(
                        conn,
                        event_runtime=self._event_runtime,
                        job_row=job_row,
                        job_id=job_id,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        unit_id=unit_id,
                        generation=generation,
                        layer_type="sentence_analysis",
                        layer_operation_fingerprint=(
                            SENTENCE_ANALYSIS_LAYER_OPERATION_FINGERPRINT
                        ),
                        schema_version=int(sentence_analysis_output.schema_version),
                        payload=sentence_analysis_output.model_dump(mode="json"),
                        quality_json=quality_payload,
                        published_at=published_at,
                    )
                    events.append(sentence_analysis_layer.event)

                output_ref: dict[str, Any]
                rationale_code: str
                if grammar_note_layer is None and sentence_analysis_layer is None:
                    output_ref = {
                        "no_op": True,
                        "grammar_note_count": 0,
                        "sentence_analysis_count": 0,
                        "diagnostics": quality_payload.get("diagnostics", {}),
                    }
                    rationale_code = "grammar_bundle_no_op"
                else:
                    output_ref = {
                        "grammar_note_layer_id": (
                            str(grammar_note_layer.layer_id)
                            if grammar_note_layer is not None
                            else None
                        ),
                        "sentence_analysis_layer_id": (
                            str(sentence_analysis_layer.layer_id)
                            if sentence_analysis_layer is not None
                            else None
                        ),
                        "event_ids": [str(event.event_id) for event in events],
                        "no_op": False,
                    }
                    rationale_code = "grammar_bundle_published"

                updated_job = await self._job_runtime._apply_transition(  # type: ignore[attr-defined]
                    conn,
                    job_row=job_row,
                    target_status="succeeded",
                    available_at=None,
                    pause_owner=None,
                    output_ref=output_ref,
                    failure_class=None,
                    failure_code=None,
                    failure_message=None,
                    rationale_code=rationale_code,
                )
                await self._job_runtime._insert_job_event(  # type: ignore[attr-defined]
                    conn,
                    reading_record_id=updated_job["reading_record_id"],
                    run_id=updated_job["run_id"],
                    job_id=updated_job["id"],
                    event_type="job_succeeded",
                    payload={
                        "previous_status": job_row["status"],
                        "target_status": "succeeded",
                        "rationale_code": rationale_code,
                    },
                )
                await conn.execute(
                    """
                    UPDATE reader_runs
                    SET status = 'completed',
                        failure_class = NULL,
                        failure_code = NULL,
                        finished_at = $2,
                        updated_at = $2
                    WHERE id = $1
                    """,
                    job_row["run_id"],
                    published_at,
                )

                return PublishedGrammarBundle(
                    reading_record_id=reading_record_id,
                    base_id=base_id,
                    unit_id=unit_id,
                    generation=generation,
                    grammar_note_layer=grammar_note_layer,
                    sentence_analysis_layer=sentence_analysis_layer,
                    events=tuple(events),
                    no_op=grammar_note_layer is None and sentence_analysis_layer is None,
                )


async def _assert_no_published_layer(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    generation: int,
    layer_type: str,
) -> None:
    existing_layer = await conn.fetchrow(
        """
        SELECT id
        FROM enhancement_layers
        WHERE reading_record_id = $1
          AND base_id = $2
          AND layer_type = $3
          AND target_scope = 'unit'
          AND target_key = $4
          AND generation = $5
          AND status = 'published'
        LIMIT 1
        """,
        reading_record_id,
        base_id,
        layer_type,
        unit_id,
        generation,
    )
    if existing_layer is not None:
        raise ValueError(f"{layer_type} layer already published for unit {unit_id}")


async def _insert_published_grammar_layer(
    conn: asyncpg.Connection,
    *,
    event_runtime: ReaderEventRuntime,
    job_row: asyncpg.Record,
    job_id: UUID,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    generation: int,
    layer_type: str,
    layer_operation_fingerprint: str,
    schema_version: int,
    payload: dict[str, Any],
    quality_json: dict[str, Any],
    published_at: datetime,
) -> PublishedGrammarLayer:
    layer_row = await conn.fetchrow(
        """
        INSERT INTO enhancement_layers (
            reading_record_id,
            base_id,
            layer_type,
            layer_subtype,
            target_scope,
            target_key,
            generation,
            status,
            operation_fingerprint,
            schema_version,
            output_json,
            coverage_json,
            quality_json,
            source_run_id,
            source_job_id,
            published_at
        )
        VALUES (
            $1,
            $2,
            $3,
            NULL,
            'unit',
            $4,
            $5,
            'published',
            $6,
            $7,
            $8::jsonb,
            '{}'::jsonb,
            $9::jsonb,
            $10,
            $11,
            $12
        )
        RETURNING id
        """,
        reading_record_id,
        base_id,
        layer_type,
        unit_id,
        generation,
        layer_operation_fingerprint,
        schema_version,
        jsonb_param(payload),
        jsonb_param(quality_json),
        job_row["run_id"],
        job_id,
        published_at,
    )
    if layer_row is None:
        raise RuntimeError("enhancement_layers insert did not return a row")

    event = await event_runtime.publish_event_in_transaction(
        conn,
        record_id=reading_record_id,
        event_type="layer_published",
        payload_json={
            "record_id": str(reading_record_id),
            "base_id": str(base_id),
            "layer_id": str(layer_row["id"]),
            "layer_type": layer_type,
            "target_scope": "unit",
            "target_key": unit_id,
            "generation": generation,
        },
        source_run_id=job_row["run_id"],
        source_job_id=job_id,
        source_layer_id=layer_row["id"],
        created_at=published_at,
    )
    return PublishedGrammarLayer(
        layer_id=layer_row["id"],
        layer_type=layer_type,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
        generation=generation,
        event=event,
    )


async def _load_unit_anchor_validation_context(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
) -> _UnitAnchorValidationContext:
    unit_row = await conn.fetchrow(
        """
        SELECT base.text AS base_text,
               unit.base_start_utf16,
               unit.base_end_utf16
        FROM reading_units unit
        JOIN reading_bases base
          ON base.id = unit.base_id
         AND base.reading_record_id = unit.reading_record_id
        WHERE unit.reading_record_id = $1
          AND unit.base_id = $2
          AND unit.unit_id = $3
        """,
        reading_record_id,
        base_id,
        unit_id,
    )
    if unit_row is None:
        raise ValueError(f"publish target unit {unit_id} does not exist")

    unit_text = slice_by_utf16_offsets(
        str(unit_row["base_text"]),
        int(unit_row["base_start_utf16"]),
        int(unit_row["base_end_utf16"]),
    )
    if unit_text is None or not unit_text:
        raise ValueError(f"publish target unit {unit_id} could not be sliced")

    segment_rows = await conn.fetch(
        """
        SELECT anchor_segment_id,
               sentence_id,
               segment_type,
               unit_start_utf16,
               unit_end_utf16,
               text_hash
        FROM anchor_segments
        WHERE reading_record_id = $1
          AND base_id = $2
          AND unit_id = $3
        ORDER BY order_index ASC
        """,
        reading_record_id,
        base_id,
        unit_id,
    )
    segments_by_id = {
        str(row["anchor_segment_id"]): row
        for row in segment_rows
    }
    if not segments_by_id:
        raise ValueError(f"publish target unit {unit_id} has no anchor segments")

    return _UnitAnchorValidationContext(
        unit_text=unit_text,
        segments_by_id=segments_by_id,
    )


def _validate_text_range_anchor(
    context: _UnitAnchorValidationContext,
    *,
    item_label: str,
    anchor: ReaderTextRangeAnchor,
    base_id: UUID,
    unit_id: str,
) -> None:
    if anchor.base_id != str(base_id):
        raise ValueError(
            f"{item_label} base_id {anchor.base_id} does not match publish base {base_id}"
        )
    if anchor.unit_id != unit_id:
        raise ValueError(
            f"{item_label} unit_id {anchor.unit_id} does not match publish unit {unit_id}"
        )

    segment_row = context.segments_by_id.get(anchor.anchor_segment_id)
    if segment_row is None:
        raise ValueError(
            f"{item_label} anchor_segment_id {anchor.anchor_segment_id} does not exist"
        )

    expected_sentence_id = str(segment_row["sentence_id"] or segment_row["anchor_segment_id"])
    if anchor.sentence_id is not None and anchor.sentence_id != expected_sentence_id:
        raise ValueError(
            f"{item_label} sentence_id {anchor.sentence_id} "
            f"does not match anchor segment {expected_sentence_id}"
        )
    if anchor.segment_type != str(segment_row["segment_type"]):
        raise ValueError(
            f"{item_label} segment_type {anchor.segment_type} "
            f"does not match stored segment type {segment_row['segment_type']}"
        )

    segment_start = int(segment_row["unit_start_utf16"])
    segment_end = int(segment_row["unit_end_utf16"])
    if anchor.start_offset < segment_start or anchor.end_offset > segment_end:
        raise ValueError(
            f"{item_label} offsets fall outside anchor segment {anchor.anchor_segment_id}"
        )

    segment_text = slice_by_utf16_offsets(context.unit_text, segment_start, segment_end)
    if segment_text is None or not segment_text:
        raise ValueError(
            f"{item_label} anchor segment {anchor.anchor_segment_id} "
            "could not be sliced from unit text"
        )
    if compute_text_range_hash(segment_text) != str(segment_row["text_hash"]):
        raise ValueError(
            f"{item_label} anchor segment {anchor.anchor_segment_id} hash mismatch"
        )

    selected_text = slice_by_utf16_offsets(
        context.unit_text,
        anchor.start_offset,
        anchor.end_offset,
    )
    if selected_text is None:
        raise ValueError(f"{item_label} offsets do not slice target unit {unit_id}")
    if selected_text != anchor.selected_text:
        raise ValueError(f"{item_label} selected_text does not match target unit {unit_id}")
    if compute_text_range_hash(selected_text) != anchor.text_hash:
        raise ValueError(f"{item_label} text_hash does not match target unit {unit_id}")


async def _validate_vocabulary_items(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    output: VocabularyLayerOutput,
) -> None:
    if not output.items:
        return

    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )
    for item in output.items:
        _validate_text_range_anchor(
            context,
            item_label=f"vocabulary item {item.item_type}",
            anchor=item.anchor,
            base_id=base_id,
            unit_id=unit_id,
        )


async def _validate_grammar_note_items(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    output: GrammarNoteLayerOutput,
) -> None:
    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )
    for item in output.items:
        for span_index, span in enumerate(item.spans):
            _validate_text_range_anchor(
                context,
                item_label=(
                    f"grammar_note item {item.grammar_point} span[{span_index}]"
                ),
                anchor=span,
                base_id=base_id,
                unit_id=unit_id,
            )


async def _validate_sentence_analysis_items(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit_id: str,
    output: SentenceAnalysisLayerOutput,
) -> None:
    context = await _load_unit_anchor_validation_context(
        conn,
        reading_record_id=reading_record_id,
        base_id=base_id,
        unit_id=unit_id,
    )
    for item in output.items:
        _validate_text_range_anchor(
            context,
            item_label=f"sentence_analysis item {item.label}",
            anchor=item.anchor,
            base_id=base_id,
            unit_id=unit_id,
        )
        for chunk in item.chunks:
            if item.anchor.selected_text.find(chunk.text) < 0:
                raise ValueError(
                    f"sentence_analysis item {item.label} chunk text "
                    f"{chunk.text!r} is not grounded in anchor selected_text"
                )
