"""Test-only shared real-product session fixture for the web E2E suite.

The web real-product specs (Reader / Ask acceptance) need a real FastAPI
session without driving the login UI. This helper provisions an isolated
email identity and an email/web session through the production
identity/session primitives, and
precisely removes the run-owned user graph afterwards (identity, session,
Reader and Ask data must be 0 residual).

Design rules:
- unique email per run: `claread-e2e-<random>@example.invalid`;
- verified email identity via `get_or_create_user_by_verified_email`;
- session via `create_session(provider="email", client_platform="web")`
  (DB keeps only the token hash);
- the plaintext session token is returned only on stdout to the current
  test process — never logged;
- cleanup deletes the user graph and proves 0 residual across the tables
  the run can own (identity, session, Reader, Ask, usage, credits).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID

import asyncpg

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.database.connection import close_db, init_db
from app.services.auth.email_credentials import get_or_create_user_by_verified_email
from app.services.auth.session import create_session

_TASK_OWNED_TABLES = (
    "users",
    "user_identities",
    "user_sessions",
    "reading_records",
    "original_inputs",
    "candidate_reading_documents",
    "confirmed_source_documents",
    "confirmed_source_revisions",
    "reading_bases",
    "reading_units",
    "anchor_segments",
    "stable_reading_documents",
    "stable_document_blocks",
    "parsed_decisions",
    "enhancement_layers",
    "reader_runs",
    "reader_jobs",
    "reader_events",
    "reader_event_sequences",
    "reader_job_events",
    "reader_runtime_spans",
    "ai_usage_events",
    "ai_model_execution_journal",
    "layer_analysis_plans",
    "analysis_windows",
    "dict_ai_candidate_entries",
    "source_artifacts",
    "reader_article_rag_index_runs",
    "reader_ask_threads",
    "reader_ask_messages",
    "reader_ask_turn_runs",
    "reader_ask_supplements",
    "reader_ask_client_submissions",
    "reader_ask_thread_memory",
    "favorite_records",
    "feedback",
    "vocabulary_book",
    "user_credit_accounts",
    "user_credit_ledger",
)


def _fail(message: str) -> None:
    raise AssertionError(message)


def _json_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_ids(rows: list[asyncpg.Record]) -> list[str]:
    return [str(row["id"]) for row in rows]


def _table_entry(ids: list[str]) -> dict[str, Any]:
    return {"count": len(ids), "ids": ids}


def _manifest_uuid_ids(manifest: dict[str, Any] | None, table_name: str) -> list[UUID]:
    if manifest is None:
        return []
    tables = _json_object(manifest.get("tables"))
    entry = _json_object(tables.get(table_name))
    return [UUID(str(value)) for value in _json_list(entry.get("ids"))]


async def _fetch_ids(conn: asyncpg.Connection, query: str, *args: Any) -> list[str]:
    return _string_ids(await conn.fetch(query, *args))


async def _load_manifest(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    record_id: UUID | None,
    scope_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoped_user_ids = _manifest_uuid_ids(scope_manifest, "users")
    scoped_identity_ids = _manifest_uuid_ids(scope_manifest, "user_identities")
    scoped_session_ids = _manifest_uuid_ids(scope_manifest, "user_sessions")
    scoped_record_ids = _manifest_uuid_ids(scope_manifest, "reading_records")
    scoped_input_ids = _manifest_uuid_ids(scope_manifest, "original_inputs")
    scoped_candidate_ids = _manifest_uuid_ids(scope_manifest, "candidate_reading_documents")
    scoped_confirmed_ids = _manifest_uuid_ids(scope_manifest, "confirmed_source_documents")
    scoped_base_ids = _manifest_uuid_ids(scope_manifest, "reading_bases")
    scoped_unit_ids = _manifest_uuid_ids(scope_manifest, "reading_units")
    scoped_segment_ids = _manifest_uuid_ids(scope_manifest, "anchor_segments")
    scoped_stable_ids = _manifest_uuid_ids(scope_manifest, "stable_reading_documents")
    scoped_block_ids = _manifest_uuid_ids(scope_manifest, "stable_document_blocks")
    scoped_decision_ids = _manifest_uuid_ids(scope_manifest, "parsed_decisions")
    scoped_layer_ids = _manifest_uuid_ids(scope_manifest, "enhancement_layers")
    scoped_run_ids = _manifest_uuid_ids(scope_manifest, "reader_runs")
    scoped_job_ids = _manifest_uuid_ids(scope_manifest, "reader_jobs")
    scoped_event_ids = _manifest_uuid_ids(scope_manifest, "reader_events")
    scoped_sequence_ids = _manifest_uuid_ids(scope_manifest, "reader_event_sequences")
    scoped_job_event_ids = _manifest_uuid_ids(scope_manifest, "reader_job_events")
    scoped_span_ids = _manifest_uuid_ids(scope_manifest, "reader_runtime_spans")
    scoped_usage_ids = _manifest_uuid_ids(scope_manifest, "ai_usage_events")
    scoped_journal_ids = _manifest_uuid_ids(scope_manifest, "ai_model_execution_journal")
    scoped_plan_ids = _manifest_uuid_ids(scope_manifest, "layer_analysis_plans")
    scoped_window_ids = _manifest_uuid_ids(scope_manifest, "analysis_windows")
    scoped_dict_ids = _manifest_uuid_ids(scope_manifest, "dict_ai_candidate_entries")
    scoped_artifact_ids = _manifest_uuid_ids(scope_manifest, "source_artifacts")
    scoped_rag_ids = _manifest_uuid_ids(scope_manifest, "reader_article_rag_index_runs")
    scoped_ask_thread_ids = _manifest_uuid_ids(scope_manifest, "reader_ask_threads")
    scoped_ask_message_ids = _manifest_uuid_ids(scope_manifest, "reader_ask_messages")
    scoped_ask_run_ids = _manifest_uuid_ids(scope_manifest, "reader_ask_turn_runs")
    scoped_ask_supplement_ids = _manifest_uuid_ids(scope_manifest, "reader_ask_supplements")
    scoped_ask_submission_ids = _manifest_uuid_ids(
        scope_manifest, "reader_ask_client_submissions"
    )
    scoped_ask_memory_ids = _manifest_uuid_ids(scope_manifest, "reader_ask_thread_memory")
    scoped_favorite_ids = _manifest_uuid_ids(scope_manifest, "favorite_records")
    scoped_feedback_ids = _manifest_uuid_ids(scope_manifest, "feedback")
    scoped_vocab_ids = _manifest_uuid_ids(scope_manifest, "vocabulary_book")
    scoped_credit_account_ids = _manifest_uuid_ids(scope_manifest, "user_credit_accounts")
    scoped_credit_ledger_ids = _manifest_uuid_ids(scope_manifest, "user_credit_ledger")
    scoped_trace_ids = (
        [UUID(str(value)) for value in _json_list(scope_manifest.get("trace_ids"))]
        if scope_manifest is not None
        else []
    )

    users = await _fetch_ids(
        conn,
        "SELECT id FROM users WHERE id = $1 OR id = ANY($2::uuid[]) ORDER BY id",
        user_id,
        scoped_user_ids,
    )
    identities = await _fetch_ids(
        conn,
        """
        SELECT id FROM user_identities
        WHERE user_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        user_id,
        scoped_identity_ids,
    )
    sessions = await _fetch_ids(
        conn,
        """
        SELECT id FROM user_sessions
        WHERE user_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        user_id,
        scoped_session_ids,
    )
    records = await _fetch_ids(
        conn,
        """
        SELECT id FROM reading_records
        WHERE user_id = $1 OR id = $2 OR id = ANY($3::uuid[]) ORDER BY id
        """,
        user_id,
        record_id,
        scoped_record_ids,
    )
    original_inputs = await _fetch_ids(
        conn,
        """
        SELECT id FROM original_inputs
        WHERE user_id = $1 OR reading_record_id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        scoped_input_ids,
    )
    original_input_ids = [UUID(value) for value in original_inputs]
    candidates = await _fetch_ids(
        conn,
        """
        SELECT id FROM candidate_reading_documents
        WHERE user_id = $1 OR reading_record_id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        scoped_candidate_ids,
    )
    confirmed = await _fetch_ids(
        conn,
        """
        SELECT id FROM confirmed_source_documents
        WHERE user_id = $1 OR reading_record_id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        scoped_confirmed_ids,
    )
    confirmed_revisions = await _fetch_ids(
        conn,
        """
        SELECT id FROM confirmed_source_revisions
        WHERE user_id = $1 OR confirmed_source_document_id = ANY($2::uuid[])
        ORDER BY id
        """,
        user_id,
        confirmed,
    )
    bases = await _fetch_ids(
        conn,
        """
        SELECT id FROM reading_bases
        WHERE reading_record_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        record_id,
        scoped_base_ids,
    )
    base_ids = [UUID(value) for value in bases]
    units = await _fetch_ids(
        conn,
        """
        SELECT id FROM reading_units
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        scoped_unit_ids,
    )
    segments = await _fetch_ids(
        conn,
        """
        SELECT id FROM anchor_segments
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        scoped_segment_ids,
    )
    stable_documents = await _fetch_ids(
        conn,
        """
        SELECT id FROM stable_reading_documents
        WHERE reading_record_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        record_id,
        scoped_stable_ids,
    )
    stable_ids = [UUID(value) for value in stable_documents]
    stable_blocks = await _fetch_ids(
        conn,
        """
        SELECT id FROM stable_document_blocks
        WHERE stable_document_id = ANY($1::uuid[]) OR id = ANY($2::uuid[])
        ORDER BY id
        """,
        stable_ids,
        scoped_block_ids,
    )
    decisions = await _fetch_ids(
        conn,
        """
        SELECT id FROM parsed_decisions
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        scoped_decision_ids,
    )
    layers = await _fetch_ids(
        conn,
        """
        SELECT id FROM enhancement_layers
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        scoped_layer_ids,
    )
    layer_ids = [UUID(value) for value in layers]
    runs = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_runs
        WHERE user_id = $1 OR reading_record_id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        scoped_run_ids,
    )
    run_ids = [UUID(value) for value in runs]
    jobs = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_jobs
        WHERE user_id = $1 OR reading_record_id = $2 OR base_id = ANY($3::uuid[])
           OR run_id = ANY($4::uuid[]) OR id = ANY($5::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        base_ids,
        run_ids,
        scoped_job_ids,
    )
    job_ids = [UUID(value) for value in jobs]
    events = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_events
        WHERE reading_record_id = $1 OR source_job_id = ANY($2::uuid[])
           OR source_run_id = ANY($3::uuid[]) OR source_layer_id = ANY($4::uuid[])
           OR id = ANY($5::uuid[])
        ORDER BY id
        """,
        record_id,
        job_ids,
        run_ids,
        layer_ids,
        scoped_event_ids,
    )
    event_sequences = await _fetch_ids(
        conn,
        """
        SELECT reading_record_id AS id FROM reader_event_sequences
        WHERE reading_record_id = $1 OR reading_record_id = ANY($2::uuid[])
        ORDER BY reading_record_id
        """,
        record_id,
        scoped_sequence_ids,
    )
    job_events = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_job_events
        WHERE reading_record_id = $1 OR job_id = ANY($2::uuid[])
           OR run_id = ANY($3::uuid[]) OR id = ANY($4::uuid[])
        ORDER BY id
        """,
        record_id,
        job_ids,
        run_ids,
        scoped_job_event_ids,
    )
    plans = await _fetch_ids(
        conn,
        """
        SELECT id FROM layer_analysis_plans
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        scoped_plan_ids,
    )
    plan_ids = [UUID(value) for value in plans]
    windows = await _fetch_ids(
        conn,
        """
        SELECT id FROM analysis_windows
        WHERE plan_id = ANY($1::uuid[]) OR job_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        plan_ids,
        job_ids,
        scoped_window_ids,
    )
    usage = await _fetch_ids(
        conn,
        """
        SELECT id FROM ai_usage_events
        WHERE user_id = $1 OR reading_record_id = $2
           OR reader_job_id = ANY($3::uuid[]) OR reader_run_id = ANY($4::uuid[])
           OR enhancement_layer_id = ANY($5::uuid[]) OR id = ANY($6::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        job_ids,
        run_ids,
        layer_ids,
        scoped_usage_ids,
    )
    usage_ids = [UUID(value) for value in usage]
    spans = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_runtime_spans
        WHERE reading_record_id = $1 OR reader_job_id = ANY($2::uuid[])
           OR reader_run_id = ANY($3::uuid[]) OR ai_usage_event_id = ANY($4::uuid[])
           OR trace_id = ANY($5::uuid[]) OR id = ANY($6::uuid[])
        ORDER BY id
        """,
        record_id,
        job_ids,
        run_ids,
        usage_ids,
        scoped_trace_ids,
        scoped_span_ids,
    )
    journal = await _fetch_ids(
        conn,
        """
        SELECT id FROM ai_model_execution_journal
        WHERE reader_job_id = ANY($1::uuid[]) OR reader_run_id = ANY($2::uuid[])
           OR ai_usage_event_id = ANY($3::uuid[]) OR id = ANY($4::uuid[])
        ORDER BY id
        """,
        job_ids,
        run_ids,
        usage_ids,
        scoped_journal_ids,
    )
    dict_candidates = await _fetch_ids(
        conn,
        """
        SELECT id FROM dict_ai_candidate_entries
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR usage_event_id = ANY($3::uuid[]) OR id = ANY($4::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        usage_ids,
        scoped_dict_ids,
    )
    artifacts = await _fetch_ids(
        conn,
        """
        SELECT id FROM source_artifacts
        WHERE user_id = $1 OR reading_record_id = $2
           OR original_input_id = ANY($3::uuid[]) OR id = ANY($4::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        original_input_ids,
        scoped_artifact_ids,
    )
    rag_runs = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_article_rag_index_runs
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR stable_document_id = ANY($3::uuid[]) OR job_id = ANY($4::uuid[])
           OR reader_run_id = ANY($5::uuid[]) OR id = ANY($6::uuid[])
        ORDER BY id
        """,
        record_id,
        base_ids,
        stable_ids,
        job_ids,
        run_ids,
        scoped_rag_ids,
    )
    ask_threads = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_ask_threads
        WHERE user_id = $1 OR reading_record_id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        scoped_ask_thread_ids,
    )
    ask_thread_ids = [UUID(value) for value in ask_threads]
    ask_messages = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_ask_messages
        WHERE thread_id = ANY($1::uuid[]) OR id = ANY($2::uuid[])
        ORDER BY id
        """,
        ask_thread_ids,
        scoped_ask_message_ids,
    )
    ask_message_ids = [UUID(value) for value in ask_messages]
    ask_runs = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_ask_turn_runs
        WHERE user_id = $1 OR reading_record_id = $2
           OR message_id = ANY($3::uuid[]) OR id = ANY($4::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        ask_message_ids,
        scoped_ask_run_ids,
    )
    ask_supplements = await _fetch_ids(
        conn,
        """
        SELECT id FROM reader_ask_supplements
        WHERE user_id = $1 OR reading_record_id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        scoped_ask_supplement_ids,
    )
    ask_submissions = await _fetch_ids(
        conn,
        """
        SELECT DISTINCT thread_id AS id FROM reader_ask_client_submissions
        WHERE thread_id = ANY($1::uuid[]) OR thread_id = ANY($2::uuid[])
        ORDER BY thread_id
        """,
        ask_thread_ids,
        scoped_ask_submission_ids,
    )
    ask_memory = await _fetch_ids(
        conn,
        """
        SELECT thread_id AS id FROM reader_ask_thread_memory
        WHERE thread_id = ANY($1::uuid[]) OR thread_id = ANY($2::uuid[])
        ORDER BY thread_id
        """,
        ask_thread_ids,
        scoped_ask_memory_ids,
    )
    favorites = await _fetch_ids(
        conn,
        """
        SELECT id FROM favorite_records
        WHERE user_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        user_id,
        scoped_favorite_ids,
    )
    feedback = await _fetch_ids(
        conn,
        """
        SELECT id FROM feedback
        WHERE user_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        user_id,
        scoped_feedback_ids,
    )
    vocab = await _fetch_ids(
        conn,
        """
        SELECT id FROM vocabulary_book
        WHERE user_id = $1 OR id = ANY($2::uuid[]) ORDER BY id
        """,
        user_id,
        scoped_vocab_ids,
    )
    credit_accounts = await _fetch_ids(
        conn,
        """
        SELECT user_id AS id FROM user_credit_accounts
        WHERE user_id = $1 OR user_id = ANY($2::uuid[])
        ORDER BY user_id
        """,
        user_id,
        scoped_credit_account_ids,
    )
    credit_ledger = await _fetch_ids(
        conn,
        """
        SELECT id FROM user_credit_ledger
        WHERE user_id = $1 OR reading_record_id = $2
           OR reader_job_id = ANY($3::uuid[]) OR reader_run_id = ANY($4::uuid[])
           OR id = ANY($5::uuid[])
        ORDER BY id
        """,
        user_id,
        record_id,
        job_ids,
        run_ids,
        scoped_credit_ledger_ids,
    )
    table_ids: dict[str, list[str]] = {
        "users": users,
        "user_identities": identities,
        "user_sessions": sessions,
        "reading_records": records,
        "original_inputs": original_inputs,
        "candidate_reading_documents": candidates,
        "confirmed_source_documents": confirmed,
        "confirmed_source_revisions": confirmed_revisions,
        "reading_bases": bases,
        "reading_units": units,
        "anchor_segments": segments,
        "stable_reading_documents": stable_documents,
        "stable_document_blocks": stable_blocks,
        "parsed_decisions": decisions,
        "enhancement_layers": layers,
        "reader_runs": runs,
        "reader_jobs": jobs,
        "reader_events": events,
        "reader_event_sequences": event_sequences,
        "reader_job_events": job_events,
        "reader_runtime_spans": _string_ids(spans),
        "ai_usage_events": usage,
        "ai_model_execution_journal": journal,
        "layer_analysis_plans": plans,
        "analysis_windows": windows,
        "dict_ai_candidate_entries": dict_candidates,
        "source_artifacts": artifacts,
        "reader_article_rag_index_runs": rag_runs,
        "reader_ask_threads": ask_threads,
        "reader_ask_messages": ask_messages,
        "reader_ask_turn_runs": ask_runs,
        "reader_ask_supplements": ask_supplements,
        "reader_ask_client_submissions": ask_submissions,
        "reader_ask_thread_memory": ask_memory,
        "favorite_records": favorites,
        "feedback": feedback,
        "vocabulary_book": vocab,
        "user_credit_accounts": credit_accounts,
        "user_credit_ledger": credit_ledger,
    }
    return {
        "user_id": str(user_id),
        "record_id": str(record_id) if record_id is not None else None,
        "identity_ids": identities,
        "session_ids": sessions,
        "job_ids": jobs,
        "run_ids": runs,
        "usage_event_ids": usage,
        "journal_ids": journal,
        "runtime_span_ids": _string_ids(spans),
        "trace_ids": sorted({str(row["trace_id"]) for row in spans if row["trace_id"] is not None}),
        "tables": {table_name: _table_entry(ids) for table_name, ids in table_ids.items()},
        "populated_tables": {
            table_name: _table_entry(ids) for table_name, ids in table_ids.items() if ids
        },
    }


async def _open_db():
    settings = get_settings()
    return await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )


async def _provision(email: str) -> dict[str, Any]:
    await _open_db()
    try:
        result = await get_or_create_user_by_verified_email(email)
        if not result.created:
            _fail("provision refused an existing email identity")
        session_token, expires_at = await create_session(
            result.user_id,
            provider="email",
            client_platform="web",
        )
        return {
            "status": "PASS",
            "user_id": str(result.user_id),
            "session_token": session_token,
            "expires_at": expires_at.isoformat(),
        }
    finally:
        await close_db()


async def provision_real_product_session(email: str) -> dict[str, Any]:
    """Provision one isolated email identity/session for a test process."""
    return await _provision(email)


async def _cleanup(email: str, record_id: UUID | None) -> dict[str, Any]:
    await _open_db()
    try:
        if db_connection.DB_POOL is None:
            raise RuntimeError("Database pool not initialized")
        async with db_connection.DB_POOL.acquire() as conn:
            async with conn.transaction():
                identity_user_id = await conn.fetchval(
                    """
                    SELECT user_id FROM user_identities
                    WHERE provider = 'email' AND provider_user_id = $1
                    """,
                    email,
                )
                record = (
                    await conn.fetchrow(
                        "SELECT user_id FROM reading_records WHERE id = $1 FOR UPDATE",
                        record_id,
                    )
                    if record_id is not None
                    else None
                )
                record_user_id = record["user_id"] if record is not None else None
                if (
                    identity_user_id is not None
                    and record_user_id is not None
                    and identity_user_id != record_user_id
                ):
                    _fail("cleanup email identity does not own the requested record")
                user_id = identity_user_id or record_user_id
                if user_id is None:
                    residual_counts = {table_name: 0 for table_name in _TASK_OWNED_TABLES}
                    return {
                        "record_id": str(record_id) if record_id is not None else None,
                        "deleted_user": False,
                        "manifest": {"tables": {}, "populated_tables": {}},
                        "residual_counts": residual_counts,
                        "residual_total": 0,
                    }
                owned_records = await conn.fetch(
                    "SELECT id FROM reading_records WHERE user_id = $1 ORDER BY id FOR UPDATE",
                    user_id,
                )
                owned_record_ids = [UUID(str(row["id"])) for row in owned_records]
                if record_id is None and len(owned_record_ids) == 1:
                    record_id = owned_record_ids[0]
                if any(value != record_id for value in owned_record_ids):
                    _fail(f"cleanup user owns unexpected records: {owned_record_ids}")
                identity_count = int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM user_identities WHERE user_id = $1",
                        user_id,
                    )
                )
                if identity_count != 1:
                    _fail(f"cleanup user owns unexpected identities: {identity_count}")
                manifest = await _load_manifest(conn, record_id=record_id, user_id=user_id)
                job_ids = [UUID(value) for value in manifest.get("job_ids", [])]
                run_ids = [UUID(value) for value in manifest.get("run_ids", [])]
                layer_ids = [UUID(value) for value in manifest.get("layer_ids", [])]
                usage_ids = [UUID(value) for value in manifest.get("usage_event_ids", [])]
                journal_ids = [UUID(value) for value in manifest.get("journal_ids", [])]
                span_ids = [UUID(value) for value in manifest.get("runtime_span_ids", [])]
                trace_ids = [UUID(value) for value in manifest.get("trace_ids", [])]
                await conn.execute(
                    """
                    DELETE FROM ai_model_execution_journal
                    WHERE reader_job_id = ANY($1::uuid[])
                       OR reader_run_id = ANY($2::uuid[])
                       OR ai_usage_event_id = ANY($3::uuid[])
                       OR id = ANY($4::uuid[])
                    """,
                    job_ids,
                    run_ids,
                    usage_ids,
                    journal_ids,
                )
                await conn.execute(
                    """
                    DELETE FROM ai_usage_events
                    WHERE user_id = $1 OR reading_record_id = $2
                       OR reader_job_id = ANY($3::uuid[])
                       OR reader_run_id = ANY($4::uuid[])
                       OR enhancement_layer_id = ANY($5::uuid[])
                       OR id = ANY($6::uuid[])
                    """,
                    user_id,
                    record_id,
                    job_ids,
                    run_ids,
                    layer_ids,
                    usage_ids,
                )
                await conn.execute(
                    """
                    DELETE FROM reader_runtime_spans
                    WHERE reading_record_id = $1
                       OR reader_job_id = ANY($2::uuid[])
                       OR reader_run_id = ANY($3::uuid[])
                       OR ai_usage_event_id = ANY($4::uuid[])
                       OR trace_id = ANY($5::uuid[])
                       OR id = ANY($6::uuid[])
                    """,
                    record_id,
                    job_ids,
                    run_ids,
                    usage_ids,
                    trace_ids,
                    span_ids,
                )
                await conn.execute(
                    """
                    DELETE FROM user_credit_ledger
                    WHERE user_id = $1 OR reading_record_id = $2
                       OR reader_job_id = ANY($3::uuid[])
                       OR reader_run_id = ANY($4::uuid[])
                    """,
                    user_id,
                    record_id,
                    job_ids,
                    run_ids,
                )
                if record_id is not None:
                    # Remove base-scoped children before jobs/runs so their SET NULL
                    # paths cannot race the composite reading_bases CASCADE.
                    await conn.execute(
                        "DELETE FROM parsed_decisions WHERE reading_record_id = $1",
                        record_id,
                    )
                    await conn.execute(
                        "DELETE FROM enhancement_layers WHERE reading_record_id = $1",
                        record_id,
                    )
                    await conn.execute(
                        "DELETE FROM reader_jobs WHERE reading_record_id = $1",
                        record_id,
                    )
                    await conn.execute(
                        "DELETE FROM reader_runs WHERE reading_record_id = $1",
                        record_id,
                    )
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)

            residual_counts = await _residual_counts(
                conn,
                user_id=user_id,
                record_id=record_id,
                manifest=manifest,
            )
            residual_total = sum(residual_counts.values())
            if residual_total != 0:
                _fail(f"fixture cleanup left residual rows: {residual_counts}")
            return {
                "record_id": str(record_id) if record_id is not None else None,
                "deleted_user": True,
                "manifest": manifest,
                "residual_counts": residual_counts,
                "residual_total": residual_total,
            }
    finally:
        await close_db()


async def cleanup_real_product_session(
    email: str,
    record_id: UUID | None = None,
) -> dict[str, Any]:
    """Remove one isolated email identity/session and its owned data."""
    return await _cleanup(email, record_id)


async def _residual_counts(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    record_id: UUID | None,
    manifest: dict[str, Any],
) -> dict[str, int]:
    residual = await _load_manifest(
        conn,
        record_id=record_id,
        user_id=user_id,
        scope_manifest=manifest,
    )
    return {
        table_name: int(_json_object(entry).get("count", 0))
        for table_name, entry in _json_object(residual.get("tables")).items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--email", required=True)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--email", required=True)
    cleanup_parser.add_argument("--record-id", type=UUID)
    args = parser.parse_args()
    if args.command == "provision":
        result = asyncio.run(_provision(args.email))
    else:
        result = asyncio.run(_cleanup(args.email, args.record_id))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
