from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.schemas.reader_orchestration import TranslationLayerOutput

from .event_runtime import ReaderEventEnvelope, ReaderEventRuntime
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
