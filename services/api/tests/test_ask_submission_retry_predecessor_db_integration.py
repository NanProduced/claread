# task-history: ASK-SUBMISSION-RETRY-R1
# (renamed from test_ask_submission_retry_r1_db_integration.py)
"""Ask submission retry predecessor real PostgreSQL integration — OPT-IN only.

Fixes the retry predecessor lookup for client_submission_id turns: the R5
submission gateway creates the user + assistant pair and binds them in ONE
transaction sharing one ``created_at``, so the original strict
``created_at < assistant.created_at`` predecessor query can never see the
turn's own user message (retry 404 for every composer turn).

R1 contract:

1. Submission-bound turns resolve the predecessor through the explicit
   ``reader_ask_client_submissions`` binding
   (``assistant_message_id`` → ``user_message_id``), never through
   same-transaction timestamp ordering.
2. Non-submission history turns keep the strict preceding-user fallback
   unchanged.
3. Thread / role / identity fences stay enforced; anomalous bindings fail
   closed instead of guessing a predecessor.

Prerequisites (same as R6 DB gate): infra/migrations/0001_initial.sql applied
to the local DB, then set ``CLAREAD_RUN_SUBMISSION_DB_TESTS=1``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from app.config.settings import get_settings
from app.database.connection import init_connection

pytestmark = [pytest.mark.skipif(
    os.environ.get("CLAREAD_RUN_SUBMISSION_DB_TESTS") != "1",
    reason=(
        "opt-in: set CLAREAD_RUN_SUBMISSION_DB_TESTS=1 after Owner applies "
        "infra/migrations/0001_initial.sql to local DB"
    ),
    ),
    pytest.mark.chain_reader_ask,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]


async def _seed_thread(conn) -> tuple[object, object, object]:
    """Create a disposable thread under an existing local reading record."""
    fixture = await conn.fetchrow(
        """
        SELECT id AS reading_record_id, user_id
        FROM reading_records
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    )
    assert fixture is not None, "local DB needs one reading_record fixture"
    user_id = fixture["user_id"]
    record_id = fixture["reading_record_id"]
    thread_id = uuid4()
    now = datetime.now(UTC)
    await conn.execute(
        """
        INSERT INTO reader_ask_threads (
            id, user_id, reading_record_id, title, is_default,
            created_at, updated_at
        )
        VALUES ($1, $2, $3, 'r1-submission-retry', false, $4, $4)
        """,
        thread_id,
        user_id,
        record_id,
        now,
    )
    return user_id, record_id, thread_id


async def _make_pool():
    return await asyncpg.create_pool(
        get_settings().database_url,
        min_size=1,
        max_size=4,
        init=init_connection,
    )


@pytest.mark.asyncio
async def test_submission_bound_pair_resolves_predecessor_via_binding() -> None:
    """The R5 path is the defect repro: pair + bind share one created_at.

    ``ensure_submission_for_send`` creates both messages in one
    transaction (identical timestamps); retry must still resolve the
    turn's own user message through the submission binding.
    """
    from app.services.reader_record_ask.repository import (
        ReaderRecordAskRepository,
        SubmissionIdempotencyUnavailable,
    )
    from app.services.reader_record_ask.submission_gateway import (
        build_retry_snapshot,
        ensure_submission_for_send,
    )

    pool = await _make_pool()
    repo = ReaderRecordAskRepository(pool=pool)
    snap = build_retry_snapshot(
        model_option_key="deterministic-e2e-r0",
        web_search_mode="disabled",
    )
    try:
        async with pool.acquire() as conn:
            user_id, _record_id, thread_id = await _seed_thread(conn)

        try:
            ensured = await ensure_submission_for_send(
                repo=repo,
                thread_id=thread_id,
                user_id=user_id,
                client_submission_id=uuid4(),
                content_md="r1 submission-bound question",
                retry_snapshot=snap,
            )
        except SubmissionIdempotencyUnavailable as exc:
            pytest.fail(f"baseline schema (0001_initial.sql) not applied: {exc}")

        assert ensured is not None and ensured.may_create_model
        assert ensured.user_message is not None
        assert ensured.assistant_message is not None
        assistant_id = ensured.assistant_message["id"]
        user_message_id = ensured.user_message["id"]

        # The pair shares one transaction timestamp — ordering cannot
        # distinguish them; only the explicit binding can.
        async with pool.acquire() as conn:
            stamps = await conn.fetch(
                """
                SELECT role, created_at
                FROM reader_ask_messages
                WHERE thread_id = $1
                ORDER BY role
                """,
                thread_id,
            )
        assert len(stamps) == 2
        assert stamps[0]["created_at"] == stamps[1]["created_at"]

        assistant_msg, user_msg = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_id,
            message_id=uuid_safe(assistant_id),
        )
        assert assistant_msg is not None
        assert user_msg is not None, (
            "submission-bound turn must resolve its own user message via "
            "the reader_ask_client_submissions binding"
        )
        assert user_msg["id"] == user_message_id
        assert user_msg["role"] == "user"
        assert user_msg["thread_id"] == str(thread_id)
        # Retry snapshot metadata must survive for the v2 trust check.
        assert user_msg["metadata_json"]["retry_snapshot"]["execution_version"] == (
            "reader_record_ask_agentic_v2"
        )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_non_submission_turns_keep_strict_predecessor_fallback() -> None:
    """Sequential (non-submission) pairs keep the strict ordering path."""
    from app.services.reader_record_ask.repository import ReaderRecordAskRepository

    pool = await _make_pool()
    repo = ReaderRecordAskRepository(pool=pool)
    try:
        async with pool.acquire() as conn:
            _user_id, _record_id, thread_id = await _seed_thread(conn)

        user_one = await repo.create_message(
            thread_id=thread_id,
            role="user",
            status="completed",
            content_md="first question",
            metadata={"execution_version": "reader_record_ask_agentic_v2"},
        )
        assistant_one = await repo.create_message(
            thread_id=thread_id,
            role="assistant",
            status="completed",
            content_md="first answer",
            metadata={"execution_version": "reader_record_ask_agentic_v2"},
        )
        user_two = await repo.create_message(
            thread_id=thread_id,
            role="user",
            status="completed",
            content_md="second question",
            metadata={"execution_version": "reader_record_ask_agentic_v2"},
        )
        assistant_two = await repo.create_message(
            thread_id=thread_id,
            role="assistant",
            status="completed",
            content_md="second answer",
            metadata={"execution_version": "reader_record_ask_agentic_v2"},
        )

        found_assistant, found_user = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_id,
            message_id=uuid_safe(assistant_two["id"]),
        )
        assert found_assistant is not None
        assert found_user is not None
        assert found_user["id"] == user_two["id"], (
            "non-submission turns must resolve the closest strictly-earlier user message"
        )

        first_assistant, first_user = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_id,
            message_id=uuid_safe(assistant_one["id"]),
        )
        assert first_assistant is not None
        assert first_user is not None
        assert first_user["id"] == user_one["id"]
        del user_one, user_two
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_same_timestamp_unbound_pair_keeps_strict_semantics() -> None:
    """Without a binding, equal timestamps still resolve nothing (strict).

    Guards against a lax fix: the fallback must remain strict ``<`` for
    turns that never went through the submission gateway.
    """
    from app.services.reader_record_ask.repository import ReaderRecordAskRepository

    pool = await _make_pool()
    repo = ReaderRecordAskRepository(pool=pool)
    try:
        async with pool.acquire() as conn:
            _user_id, _record_id, thread_id = await _seed_thread(conn)
            shared_now = datetime.now(UTC)
            user_row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_messages (
                    thread_id, role, status, content_md,
                    context_anchors_json, citations_json,
                    action_proposals_json, tool_trace_json,
                    metadata_json, created_at, updated_at
                )
                VALUES ($1, 'user', 'completed', 'unbound same-ts question',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                        '{}'::jsonb, $2, $3)
                RETURNING id
                """,
                thread_id,
                shared_now,
                shared_now,
            )
            assistant_row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_messages (
                    thread_id, role, status, content_md,
                    context_anchors_json, citations_json,
                    action_proposals_json, tool_trace_json,
                    metadata_json, created_at, updated_at
                )
                VALUES ($1, 'assistant', 'completed', 'unbound same-ts answer',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                        '{}'::jsonb, $2, $3)
                RETURNING id
                """,
                thread_id,
                shared_now,
                shared_now,
            )

        found_assistant, found_user = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_id,
            message_id=assistant_row["id"],
        )
        assert found_assistant is not None
        assert found_user is None, (
            "unbound same-timestamp pairs must NOT resolve a predecessor; "
            f"got {found_user!r} (pair user id {user_row['id']})"
        )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_anomalous_binding_fails_closed_without_guessing() -> None:
    """A binding pointing outside the thread fails closed (no fallback)."""
    from app.services.reader_record_ask.repository import ReaderRecordAskRepository

    pool = await _make_pool()
    repo = ReaderRecordAskRepository(pool=pool)
    try:
        async with pool.acquire() as conn:
            _user_id, _record_id, thread_one = await _seed_thread(conn)
            _user_id_two, _record_id_two, thread_two = await _seed_thread(conn)
            now = datetime.now(UTC)

            # Thread one: an unbound pair with distinct timestamps — the
            # strict fallback WOULD find this user message, which is
            # exactly what an anomalous binding must not silently do.
            user_row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_messages (
                    thread_id, role, status, content_md,
                    context_anchors_json, citations_json,
                    action_proposals_json, tool_trace_json,
                    metadata_json, created_at, updated_at
                )
                VALUES ($1, 'user', 'completed', 'thread-one question',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                        '{}'::jsonb, $2, $3)
                RETURNING id
                """,
                thread_one,
                now,
                now,
            )
            assistant_row = await conn.fetchrow(
                """
                INSERT INTO reader_ask_messages (
                    thread_id, role, status, content_md,
                    context_anchors_json, citations_json,
                    action_proposals_json, tool_trace_json,
                    metadata_json, created_at, updated_at
                )
                VALUES ($1, 'assistant', 'completed', 'thread-one answer',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                        '{}'::jsonb, $2, $3)
                RETURNING id
                """,
                thread_one,
                now,
                now,
            )

            # Thread two: a decoy user message the anomalous binding
            # points at (wrong thread for the retried assistant).
            decoy_user = await conn.fetchrow(
                """
                INSERT INTO reader_ask_messages (
                    thread_id, role, status, content_md,
                    context_anchors_json, citations_json,
                    action_proposals_json, tool_trace_json,
                    metadata_json, created_at, updated_at
                )
                VALUES ($1, 'user', 'completed', 'thread-two decoy',
                        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                        '{}'::jsonb, $2, $3)
                RETURNING id
                """,
                thread_two,
                now,
                now,
            )

            await conn.execute(
                """
                INSERT INTO reader_ask_client_submissions (
                    thread_id, client_submission_id, user_id,
                    user_message_id, assistant_message_id, status
                )
                VALUES ($1, $2, $3, $4, $5, 'completed')
                """,
                thread_one,
                uuid4(),
                _user_id,
                decoy_user["id"],
                assistant_row["id"],
            )

        found_assistant, found_user = await repo.get_assistant_message_with_preceding_user_message(
            thread_id=thread_one,
            message_id=assistant_row["id"],
        )
        assert found_assistant is not None
        assert found_user is None, (
            "anomalous cross-thread binding must fail closed, never fall "
            f"back to a guessed predecessor (got {found_user!r}; local user "
            f"{user_row['id']}, decoy {decoy_user['id']})"
        )
    finally:
        await pool.close()


def uuid_safe(value: object):
    from uuid import UUID

    return value if isinstance(value, UUID) else UUID(str(value))
