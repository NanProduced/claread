from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_orchestration import TranslationLayerOutput, VocabularyLayerOutput

from .event_runtime import ReaderEventEnvelope, ReaderEventRuntime
from .job_bootstrap import (
    VOCABULARY_JOB_TYPE,
    VOCABULARY_OPERATION_FINGERPRINT,
    VOCABULARY_TARGET_SCOPE,
)
from .job_runtime import (
    FenceViolationError,
    ReaderJobRuntime,
    _assert_lease_valid,
)


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


class TranslationLayerPublisher:
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
                    or job_row["operation_fingerprint"] != VOCABULARY_OPERATION_FINGERPRINT
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
        raise ValueError(f"vocabulary publish target unit {unit_id} does not exist")

    unit_text = slice_by_utf16_offsets(
        str(unit_row["base_text"]),
        int(unit_row["base_start_utf16"]),
        int(unit_row["base_end_utf16"]),
    )
    if unit_text is None or not unit_text:
        raise ValueError(f"vocabulary publish target unit {unit_id} could not be sliced")

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
        raise ValueError(f"vocabulary publish target unit {unit_id} has no anchor segments")

    for item in output.items:
        anchor = item.anchor
        if anchor.base_id != str(base_id):
            raise ValueError(
                f"vocabulary item {item.item_type} base_id {anchor.base_id} "
                f"does not match publish base {base_id}"
            )
        if anchor.unit_id != unit_id:
            raise ValueError(
                f"vocabulary item {item.item_type} unit_id {anchor.unit_id} "
                f"does not match publish unit {unit_id}"
            )

        segment_row = segments_by_id.get(anchor.anchor_segment_id)
        if segment_row is None:
            raise ValueError(
                f"vocabulary item {item.item_type} anchor_segment_id "
                f"{anchor.anchor_segment_id} does not exist"
            )

        expected_sentence_id = str(
            segment_row["sentence_id"] or segment_row["anchor_segment_id"]
        )
        if anchor.sentence_id is not None and anchor.sentence_id != expected_sentence_id:
            raise ValueError(
                f"vocabulary item {item.item_type} sentence_id {anchor.sentence_id} "
                f"does not match anchor segment {expected_sentence_id}"
            )
        if anchor.segment_type != str(segment_row["segment_type"]):
            raise ValueError(
                f"vocabulary item {item.item_type} segment_type {anchor.segment_type} "
                f"does not match stored segment type {segment_row['segment_type']}"
            )

        segment_start = int(segment_row["unit_start_utf16"])
        segment_end = int(segment_row["unit_end_utf16"])
        if anchor.start_offset < segment_start or anchor.end_offset > segment_end:
            raise ValueError(
                f"vocabulary item {item.item_type} offsets fall outside anchor segment "
                f"{anchor.anchor_segment_id}"
            )

        segment_text = slice_by_utf16_offsets(unit_text, segment_start, segment_end)
        if segment_text is None or not segment_text:
            raise ValueError(
                f"vocabulary item {item.item_type} anchor segment "
                f"{anchor.anchor_segment_id} could not be sliced from unit text"
            )
        if compute_text_range_hash(segment_text) != str(segment_row["text_hash"]):
            raise ValueError(
                f"vocabulary item {item.item_type} anchor segment "
                f"{anchor.anchor_segment_id} hash mismatch"
            )

        selected_text = slice_by_utf16_offsets(
            unit_text,
            anchor.start_offset,
            anchor.end_offset,
        )
        if selected_text is None:
            raise ValueError(
                f"vocabulary item {item.item_type} offsets do not slice target unit {unit_id}"
            )
        if selected_text != anchor.selected_text:
            raise ValueError(
                f"vocabulary item {item.item_type} selected_text does not match "
                f"target unit {unit_id}"
            )
        if compute_text_range_hash(selected_text) != anchor.text_hash:
            raise ValueError(
                f"vocabulary item {item.item_type} text_hash does not match "
                f"target unit {unit_id}"
            )
