"""Durable model-execution receipts and DB-only usage materialization."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.ai_usage import (
    AIUsageEventCreate,
    insert_ai_usage_event_by_invocation_key_in_transaction,
)
from app.services.model_execution_journal.models import (
    BeginDisposition,
    CapturedReceipt,
    CaptureEnvelopeConflictError,
    ExecutionIdentity,
    JournalConflictError,
    MaterializationSummary,
    PayloadContractError,
    PreparedCaptureEnvelope,
    RecoveryDisposition,
)
from app.services.model_execution_journal.payload_codec import (
    decode_resume_payload,
    decode_usage_event_draft,
    prepare_capture_envelope,
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise PayloadContractError("malformed_stored_payload")
    return value


class ModelExecutionJournalService:
    def __init__(self, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("database pool is not initialized")
        return pool

    async def begin_execution(
        self,
        *,
        identity: ExecutionIdentity,
        invocation_kind: str,
    ) -> BeginDisposition:
        if identity.attempt_ordinal < 1 or identity.execution_slot < 0:
            raise ValueError("invalid_execution_identity")
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchrow(
                    """
                    INSERT INTO ai_model_execution_journal (
                        invocation_key, invocation_kind, reader_job_id,
                        reader_run_id, attempt_ordinal, execution_slot
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (invocation_key) DO NOTHING
                    RETURNING *
                    """,
                    identity.invocation_key,
                    invocation_kind,
                    identity.reader_job_id,
                    identity.reader_run_id,
                    identity.attempt_ordinal,
                    identity.execution_slot,
                )
                row = inserted or await conn.fetchrow(
                    """
                    SELECT * FROM ai_model_execution_journal
                    WHERE invocation_key = $1
                    FOR UPDATE
                    """,
                    identity.invocation_key,
                )
                if row is None:
                    raise RuntimeError("journal_begin_not_confirmed")
                self._assert_identity(row, identity, invocation_kind)
                return BeginDisposition(
                    journal_id=row["id"],
                    invocation_key=identity.invocation_key,
                    capture_state=row["capture_state"],
                    provider_call_allowed=inserted is not None,
                )

    async def capture_execution(
        self,
        *,
        identity: ExecutionIdentity,
        prepared: PreparedCaptureEnvelope,
    ) -> CapturedReceipt:
        conflict = False
        receipt: CapturedReceipt | None = None
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM ai_model_execution_journal
                    WHERE invocation_key = $1
                    FOR UPDATE
                    """,
                    identity.invocation_key,
                )
                if row is None:
                    raise LookupError("model_execution_not_started")
                self._assert_identity(row, identity, prepared.invocation_kind)
                if row["capture_state"] == "captured":
                    conflict = (
                        row["capture_envelope_sha256"]
                        != prepared.capture_envelope_sha256
                    )
                    if conflict:
                        await self._pause_owning_job_for_conflict(conn, row)
                    else:
                        receipt = self._receipt_from_row(
                            row,
                            idempotent_replay=True,
                        )
                elif row["capture_state"] != "started":
                    raise JournalConflictError(
                        "ambiguous_execution_requires_verified_receipt"
                    )
                else:
                    captured = await conn.fetchrow(
                        """
                        UPDATE ai_model_execution_journal
                        SET capture_state = 'captured',
                            usage_delivery_state = 'pending',
                            resume_payload_kind = $2,
                            resume_payload_schema_version = $3,
                            usage_event_draft_schema_version = $4,
                            normalized_payload_json = $5::jsonb,
                            usage_event_draft_json = $6::jsonb,
                            capture_envelope_sha256 = $7,
                            resume_payload_bytes = $8,
                            usage_event_draft_bytes = $9,
                            captured_at = NOW(),
                            updated_at = NOW()
                        WHERE id = $1
                        RETURNING *
                        """,
                        row["id"],
                        prepared.resume_payload_kind,
                        prepared.resume_payload_schema_version,
                        prepared.usage_event_draft_schema_version,
                        jsonb_param(prepared.normalized_payload),
                        jsonb_param(prepared.usage_event_draft),
                        prepared.capture_envelope_sha256,
                        prepared.resume_payload_bytes,
                        prepared.usage_event_draft_bytes,
                    )
                    receipt = self._receipt_from_row(captured)
        if conflict:
            raise CaptureEnvelopeConflictError("capture_envelope_conflict")
        if receipt is None:
            raise RuntimeError("capture_not_confirmed")
        return receipt

    async def load_captured_receipt(
        self,
        *,
        invocation_key: str,
    ) -> CapturedReceipt:
        async with self.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM ai_model_execution_journal
                WHERE invocation_key = $1 AND capture_state = 'captured'
                """,
                invocation_key,
            )
        if row is None:
            raise LookupError("captured_receipt_not_found")
        return self._receipt_from_row(row)

    async def inspect_attempt_for_recovery(
        self,
        conn: asyncpg.Connection,
        *,
        reader_job_id: UUID,
        attempt_ordinal: int,
    ) -> RecoveryDisposition:
        rows = await conn.fetch(
            """
            SELECT * FROM ai_model_execution_journal
            WHERE reader_job_id = $1 AND attempt_ordinal = $2
            ORDER BY execution_slot ASC
            FOR UPDATE
            """,
            reader_job_id,
            attempt_ordinal,
        )
        if not rows:
            return RecoveryDisposition(kind="none")
        if any(row["capture_state"] == "started" for row in rows):
            await conn.execute(
                """
                UPDATE ai_model_execution_journal
                SET capture_state = 'ambiguous', ambiguous_at = NOW(),
                    updated_at = NOW()
                WHERE reader_job_id = $1 AND attempt_ordinal = $2
                  AND capture_state = 'started'
                """,
                reader_job_id,
                attempt_ordinal,
            )
            return RecoveryDisposition(kind="ambiguous")
        if any(row["capture_state"] == "ambiguous" for row in rows):
            return RecoveryDisposition(kind="ambiguous")
        return RecoveryDisposition(
            kind="captured_resume",
            receipts=tuple(self._receipt_from_row(row) for row in rows),
        )

    async def materialize_pending(
        self,
        *,
        limit: int = 100,
        max_attempts: int = 3,
    ) -> MaterializationSummary:
        scanned = 0
        reconciled = 0
        dead_lettered = 0
        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT * FROM ai_model_execution_journal
                    WHERE capture_state = 'captured'
                      AND usage_delivery_state = 'pending'
                      AND (delivery_next_attempt_at IS NULL
                           OR delivery_next_attempt_at <= NOW())
                    ORDER BY created_at ASC, id ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    limit,
                )
                for row in rows:
                    scanned += 1
                    try:
                        async with conn.transaction():
                            draft = decode_usage_event_draft(
                                schema_version=row[
                                    "usage_event_draft_schema_version"
                                ],
                                payload=_json_object(
                                    row["usage_event_draft_json"]
                                ),
                            )
                            event = AIUsageEventCreate(
                                **draft.model_dump(mode="python")
                            )
                            event_id = (
                                await insert_ai_usage_event_by_invocation_key_in_transaction(
                                    conn,
                                    invocation_key=row["invocation_key"],
                                    event=event,
                                )
                            )
                            await conn.execute(
                                """
                                UPDATE ai_model_execution_journal
                                SET usage_delivery_state = 'reconciled',
                                    ai_usage_event_id = $2,
                                    reconciled_at = NOW(),
                                    delivery_last_error_code = NULL,
                                    delivery_last_error_message = NULL,
                                    updated_at = NOW()
                                WHERE id = $1
                                """,
                                row["id"],
                                event_id,
                            )
                    except Exception as exc:
                        attempts = int(row["delivery_attempt_count"]) + 1
                        terminal = attempts >= max_attempts
                        await conn.execute(
                            """
                            UPDATE ai_model_execution_journal
                            SET usage_delivery_state = $2,
                                delivery_attempt_count = $3,
                                delivery_next_attempt_at = CASE
                                    WHEN $2 = 'pending'
                                    THEN NOW() + $4::interval
                                    ELSE NULL
                                END,
                                delivery_last_error_code = $5,
                                delivery_last_error_message = $6,
                                dead_lettered_at = CASE
                                    WHEN $2 = 'dead_letter' THEN NOW()
                                    ELSE NULL
                                END,
                                updated_at = NOW()
                            WHERE id = $1
                            """,
                            row["id"],
                            "dead_letter" if terminal else "pending",
                            attempts,
                            timedelta(seconds=min(300, 2**attempts)),
                            type(exc).__name__[:100],
                            "usage materialization failed",
                        )
                        dead_lettered += int(terminal)
                    else:
                        reconciled += 1
        return MaterializationSummary(
            scanned=scanned,
            reconciled=reconciled,
            dead_lettered=dead_lettered,
        )

    @staticmethod
    def _assert_identity(
        row: asyncpg.Record,
        identity: ExecutionIdentity,
        invocation_kind: str,
    ) -> None:
        if (
            row["invocation_kind"] != invocation_kind
            or row["reader_job_id"] != identity.reader_job_id
            or row["reader_run_id"] != identity.reader_run_id
            or int(row["attempt_ordinal"]) != identity.attempt_ordinal
            or int(row["execution_slot"]) != identity.execution_slot
        ):
            raise JournalConflictError("execution_identity_conflict")

    @staticmethod
    async def _pause_owning_job_for_conflict(
        conn: asyncpg.Connection,
        row: asyncpg.Record,
    ) -> None:
        job_id = row["reader_job_id"]
        if job_id is None:
            return
        updated = await conn.fetchrow(
            """
            UPDATE reader_jobs
            SET status = 'paused', pause_owner = 'system',
                lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, claimed_at = NULL,
                rationale_code = 'model_execution_capture_conflict',
                failure_class = 'model_execution',
                failure_code = 'capture_envelope_conflict',
                failure_message = NULL,
                updated_at = NOW()
            WHERE id = $1
              AND status NOT IN (
                  'skipped', 'succeeded', 'failed_terminal',
                  'cancelled', 'superseded'
              )
            RETURNING reading_record_id, run_id
            """,
            job_id,
        )
        if updated is not None:
            await conn.execute(
                """
                INSERT INTO reader_job_events (
                    reading_record_id, run_id, job_id,
                    event_type, payload_json
                ) VALUES ($1, $2, $3, 'job_action_required', $4::jsonb)
                """,
                updated["reading_record_id"],
                updated["run_id"],
                job_id,
                jsonb_param(
                    {
                        "rationale_code": "model_execution_capture_conflict",
                        "failure_code": "capture_envelope_conflict",
                    }
                ),
            )

    @staticmethod
    def _receipt_from_row(
        row: asyncpg.Record,
        *,
        idempotent_replay: bool = False,
    ) -> CapturedReceipt:
        normalized_payload = _json_object(row["normalized_payload_json"])
        usage_event_draft = _json_object(row["usage_event_draft_json"])
        prepared = prepare_capture_envelope(
            invocation_kind=row["invocation_kind"],
            resume_payload_kind=row["resume_payload_kind"],
            resume_payload_schema_version=int(
                row["resume_payload_schema_version"]
            ),
            usage_event_draft_schema_version=int(
                row["usage_event_draft_schema_version"]
            ),
            normalized_payload=normalized_payload,
            usage_event_draft=usage_event_draft,
        )
        if (
            prepared.capture_envelope_sha256
            != row["capture_envelope_sha256"]
            or prepared.resume_payload_bytes != row["resume_payload_bytes"]
            or prepared.usage_event_draft_bytes
            != row["usage_event_draft_bytes"]
        ):
            raise PayloadContractError("stored_capture_envelope_mismatch")
        decode_resume_payload(
            kind=prepared.resume_payload_kind,
            schema_version=prepared.resume_payload_schema_version,
            payload=prepared.normalized_payload,
        )
        return CapturedReceipt(
            journal_id=row["id"],
            identity=ExecutionIdentity(
                invocation_key=row["invocation_key"],
                reader_job_id=row["reader_job_id"],
                reader_run_id=row["reader_run_id"],
                attempt_ordinal=int(row["attempt_ordinal"]),
                execution_slot=int(row["execution_slot"]),
            ),
            invocation_kind=row["invocation_kind"],
            resume_payload_kind=prepared.resume_payload_kind,
            resume_payload_schema_version=(
                prepared.resume_payload_schema_version
            ),
            usage_event_draft_schema_version=(
                prepared.usage_event_draft_schema_version
            ),
            normalized_payload=prepared.normalized_payload,
            usage_event_draft=prepared.usage_event_draft,
            capture_envelope_sha256=prepared.capture_envelope_sha256,
            captured_at=row["captured_at"],
            usage_delivery_state=row["usage_delivery_state"],
            ai_usage_event_id=row["ai_usage_event_id"],
            idempotent_replay=idempotent_replay,
        )
