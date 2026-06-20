from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param

ReaderEventType = Literal[
    "article_ready",
    "layer_published",
    "layer_failed",
    "parsed_decision_updated",
    "record_state_changed",
    "action_required",
    "run_completed",
    "record_superseded",
    "projection_ops",
    "projection_reset_required",
]


@dataclass(frozen=True, slots=True)
class ReaderEventEnvelope:
    event_id: UUID
    reading_record_id: UUID
    sequence: int
    event_type: str
    payload_json: dict[str, Any]
    source_run_id: UUID | None
    source_job_id: UUID | None
    source_layer_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReaderEventPollResult:
    reading_record_id: UUID
    after_sequence: int
    next_after_sequence: int
    last_event_sequence: int
    has_more: bool
    truncated: bool
    reload_required: bool
    reload_reason: str | None
    events: tuple[ReaderEventEnvelope, ...]


class ReaderEventRuntime:
    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_event(
        self,
        *,
        record_id: UUID,
        event_type: ReaderEventType | str,
        payload_json: Mapping[str, Any],
        source_run_id: UUID | None = None,
        source_job_id: UUID | None = None,
        source_layer_id: UUID | None = None,
        event_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReaderEventEnvelope:
        pool = self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await self.publish_event_in_transaction(
                    conn,
                    record_id=record_id,
                    event_type=event_type,
                    payload_json=payload_json,
                    source_run_id=source_run_id,
                    source_job_id=source_job_id,
                    source_layer_id=source_layer_id,
                    event_id=event_id,
                    created_at=created_at,
                )

    async def publish_event_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        event_type: ReaderEventType | str,
        payload_json: Mapping[str, Any],
        source_run_id: UUID | None = None,
        source_job_id: UUID | None = None,
        source_layer_id: UUID | None = None,
        event_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReaderEventEnvelope:
        if not conn.is_in_transaction():
            raise RuntimeError("publish_event_in_transaction requires an active transaction")

        event_id_value = event_id or uuid4()
        created_at_value = created_at or datetime.now(UTC)
        payload = _require_json_object(payload_json)

        await conn.execute(
            """
            INSERT INTO reader_event_sequences (reading_record_id, next_sequence, updated_at)
            VALUES ($1, 1, $2)
            ON CONFLICT (reading_record_id) DO NOTHING
            """,
            record_id,
            created_at_value,
        )
        sequence = await conn.fetchval(
            """
            UPDATE reader_event_sequences
            SET next_sequence = next_sequence + 1,
                updated_at = $2
            WHERE reading_record_id = $1
            RETURNING next_sequence - 1
            """,
            record_id,
            created_at_value,
        )
        if not isinstance(sequence, int):
            raise ValueError(f"reader_event_sequences missing for record {record_id}")

        row = await conn.fetchrow(
            """
            INSERT INTO reader_events (
                id,
                reading_record_id,
                sequence,
                event_type,
                payload_json,
                source_run_id,
                source_job_id,
                source_layer_id,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            RETURNING id, reading_record_id, sequence, event_type, payload_json,
                      source_run_id, source_job_id, source_layer_id, created_at
            """,
            event_id_value,
            record_id,
            sequence,
            event_type,
            jsonb_param(payload),
            source_run_id,
            source_job_id,
            source_layer_id,
            created_at_value,
        )
        if row is None:
            raise RuntimeError("reader_events insert did not return a row")
        return _event_from_row(row)

    async def poll_events(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> ReaderEventPollResult:
        after_sequence_value = _require_non_negative_int(
            "after_sequence",
            after_sequence,
        )
        limit_value = _require_positive_int("limit", limit)

        pool = self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await self._assert_record_owner(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                sequence_summary = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE((
                            SELECT next_sequence - 1
                            FROM reader_event_sequences
                            WHERE reading_record_id = $1
                        ), 0) AS counter_last_event_sequence,
                        COALESCE((
                            SELECT MAX(sequence)
                            FROM reader_events
                            WHERE reading_record_id = $1
                        ), 0) AS latest_event_sequence
                    """,
                    record_id,
                )
                if sequence_summary is None:
                    raise RuntimeError("reader event sequence summary query failed")

                counter_last_event_sequence = int(
                    sequence_summary["counter_last_event_sequence"]
                )
                last_event_sequence = int(sequence_summary["latest_event_sequence"])

                if counter_last_event_sequence != last_event_sequence:
                    return _reload_required_result(
                        record_id=record_id,
                        after_sequence=after_sequence_value,
                        last_event_sequence=last_event_sequence,
                        reason="reader_event_sequences counter does not match the latest event",
                    )

                if after_sequence_value > last_event_sequence:
                    return ReaderEventPollResult(
                        reading_record_id=record_id,
                        after_sequence=after_sequence_value,
                        next_after_sequence=after_sequence_value,
                        last_event_sequence=last_event_sequence,
                        has_more=False,
                        truncated=False,
                        reload_required=False,
                        reload_reason=None,
                        events=(),
                    )

                rows = await conn.fetch(
                    """
                    SELECT id, reading_record_id, sequence, event_type, payload_json,
                           source_run_id, source_job_id, source_layer_id, created_at
                    FROM reader_events
                    WHERE reading_record_id = $1
                      AND sequence > $2
                    ORDER BY sequence ASC
                    LIMIT $3
                    """,
                    record_id,
                    after_sequence_value,
                    limit_value + 1,
                )

                if not rows:
                    if after_sequence_value < last_event_sequence:
                        return _reload_required_result(
                            record_id=record_id,
                            after_sequence=after_sequence_value,
                            last_event_sequence=last_event_sequence,
                            reason="reader event stream is missing expected events",
                        )
                    return ReaderEventPollResult(
                        reading_record_id=record_id,
                        after_sequence=after_sequence_value,
                        next_after_sequence=after_sequence_value,
                        last_event_sequence=last_event_sequence,
                        has_more=False,
                        truncated=False,
                        reload_required=False,
                        reload_reason=None,
                        events=(),
                    )

                expected_sequence = after_sequence_value + 1
                for row in rows:
                    row_sequence = int(row["sequence"])
                    if row_sequence != expected_sequence:
                        return _reload_required_result(
                            record_id=record_id,
                            after_sequence=after_sequence_value,
                            last_event_sequence=last_event_sequence,
                            reason=(
                                "reader event sequence gap detected at "
                                f"{expected_sequence}, got {row_sequence}"
                            ),
                        )

                    if _coerce_event_payload(row["payload_json"]) is None:
                        return _reload_required_result(
                            record_id=record_id,
                            after_sequence=after_sequence_value,
                            last_event_sequence=last_event_sequence,
                            reason="reader event payload_json must be a JSON object",
                        )
                    expected_sequence += 1

                has_more = len(rows) > limit_value
                selected_rows = rows[:limit_value]
                events = tuple(_event_from_row(row) for row in selected_rows)
                next_after_sequence = (
                    events[-1].sequence if events else after_sequence_value
                )
                return ReaderEventPollResult(
                    reading_record_id=record_id,
                    after_sequence=after_sequence_value,
                    next_after_sequence=next_after_sequence,
                    last_event_sequence=last_event_sequence,
                    has_more=has_more,
                    truncated=has_more,
                    reload_required=False,
                    reload_reason=None,
                    events=events,
                )

    async def _assert_record_owner(
        self,
        conn: asyncpg.Connection,
        *,
        record_id: UUID,
        user_id: UUID,
    ) -> None:
        row = await conn.fetchrow(
            """
            SELECT id
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
            """,
            record_id,
            user_id,
        )
        if row is None:
            raise LookupError(f"reading record {record_id} not found for user {user_id}")


def parse_last_event_id(last_event_id: str | None) -> int | None:
    if last_event_id is None:
        return None
    raw_value = last_event_id.strip()
    if not raw_value or not raw_value.isdigit():
        return None
    parsed_value = int(raw_value)
    return parsed_value if parsed_value >= 0 else None


def _event_from_row(row: asyncpg.Record) -> ReaderEventEnvelope:
    payload = _coerce_event_payload(row["payload_json"])
    if payload is None:
        raise ValueError("reader event payload_json must be a JSON object")

    return ReaderEventEnvelope(
        event_id=UUID(str(row["id"])),
        reading_record_id=UUID(str(row["reading_record_id"])),
        sequence=int(row["sequence"]),
        event_type=str(row["event_type"]),
        payload_json=payload,
        source_run_id=(
            UUID(str(row["source_run_id"])) if row["source_run_id"] is not None else None
        ),
        source_job_id=(
            UUID(str(row["source_job_id"])) if row["source_job_id"] is not None else None
        ),
        source_layer_id=(
            UUID(str(row["source_layer_id"])) if row["source_layer_id"] is not None else None
        ),
        created_at=row["created_at"],
    )


def _reload_required_result(
    *,
    record_id: UUID,
    after_sequence: int,
    last_event_sequence: int,
    reason: str,
) -> ReaderEventPollResult:
    return ReaderEventPollResult(
        reading_record_id=record_id,
        after_sequence=after_sequence,
        next_after_sequence=after_sequence,
        last_event_sequence=last_event_sequence,
        has_more=False,
        truncated=False,
        reload_required=True,
        reload_reason=reason,
        events=(),
    )


def _coerce_event_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _require_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("payload_json must be a JSON object mapping")
    return dict(value)


def _require_non_negative_int(field_name: str, value: int) -> int:
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _require_positive_int(field_name: str, value: int) -> int:
    if value <= 0:
        raise ValueError(f"{field_name} must be >= 1")
    return value
