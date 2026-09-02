"""Deterministic fixture lifecycle for the stable-order real-product E2E.

The browser creates the user and Reading Record through the real product path.
This test-only helper opts that record into the existing fake job namespace,
runs the real Reader orchestration/persistence/snapshot seams, verifies the
stable-order contract, and can precisely remove the run-owned user graph.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, replace
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.config.settings import get_settings
from app.contracts.annotation import compute_text_range_hash
from app.database.connection import close_db, init_db
from app.database.json_compat import ensure_json_object, jsonb_param
from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.grammar_worker import (
    GrammarBatchJobContext,
    GrammarBundleWorkerService,
)
from app.services.reader_orchestration.job_runtime import (
    FAKE_JOB_NAMESPACE,
    JOB_RUNTIME_SCOPE_FAKE,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.smoke_harness import (
    DevFakeGrammarBatchExecutor,
    DevFakeGrammarBundleExecutor,
    ReaderEnhancementSmokeHarness,
)

_EXPECTED_UNIT_IDS = ["u1", "u2", "u3", "u4"]
_EXPECTED_U3_SEGMENTS = ["s5", "s6", "s7", "s8"]
_EXPECTED_TRANSLATION_GROUPS = [
    ("u3_g5_7", ["s5", "s6", "s7"]),
    ("u3_g8_8", ["s8"]),
]
_ENHANCEMENT_JOB_TYPES = (
    "generate_display_title_zh",
    "translate_unit",
    "translate_article",
    "build_vocabulary_layer",
    "build_vocabulary_layer_article",
    "build_grammar_bundle",
    "build_grammar_bundle_window",
)
_TASK_OWNED_TABLES = (
    "users",
    "user_identities",
    "user_sessions",
    "reading_records",
    "original_inputs",
    "candidate_reading_documents",
    "confirmed_source_documents",
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
    "favorite_records",
    "user_credit_accounts",
    "user_credit_ledger",
)


def _fail(message: str) -> None:
    raise AssertionError(message)


def _normalize_phone(phone: str) -> str:
    # Legacy lookup key for pre-existing phone identities; phone auth itself
    # has been removed from the API, so this only ever matches legacy rows.
    cleaned = re.sub(r"[\s-]", "", phone)
    if cleaned.startswith("+86"):
        national = cleaned[3:]
    elif cleaned.startswith("86") and len(cleaned) == 13:
        national = cleaned[2:]
    else:
        national = cleaned
    return f"+86{national}"


def _json_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_ids(rows: list[asyncpg.Record]) -> list[str]:
    return [str(row["id"]) for row in rows]


def _table_entry(ids: list[str]) -> dict[str, Any]:
    return {"count": len(ids), "ids": ids}


def _manifest_uuid_ids(
    manifest: dict[str, Any] | None,
    table_name: str,
) -> list[UUID]:
    if manifest is None:
        return []
    tables = _json_object(manifest.get("tables"))
    entry = _json_object(tables.get(table_name))
    return [UUID(str(value)) for value in _json_list(entry.get("ids"))]


async def _fetch_ids(
    conn: asyncpg.Connection,
    query: str,
    *args: Any,
) -> list[str]:
    return _string_ids(await conn.fetch(query, *args))


class _StableOrderGrammarBatchExecutor(DevFakeGrammarBatchExecutor):
    """Keep the existing fake batch contract but target s6/s7 for u3."""

    async def generate_batch(self, context: GrammarBatchJobContext):
        baseline = await super().generate_batch(context)
        outputs: list[tuple[str, GrammarBundleOutput]] = []
        for unit in context.units:
            if unit.unit_id != "u3":
                outputs.append((unit.unit_id, GrammarBundleOutput()))
                continue

            segment_ids = [segment.anchor_segment_id for segment in unit.anchor_segments]
            if segment_ids != _EXPECTED_U3_SEGMENTS:
                _fail(
                    "stable-order u3 anchor contract changed: "
                    f"expected={_EXPECTED_U3_SEGMENTS} actual={segment_ids}"
                )
            analyses: list[SentenceAnalysisItem] = []
            for segment in unit.anchor_segments[1:3]:
                analyses.append(
                    SentenceAnalysisItem(
                        anchor=ReaderTextRangeAnchor(
                            base_id=str(context.base_id),
                            unit_id=unit.unit_id,
                            anchor_segment_id=segment.anchor_segment_id,
                            sentence_id=segment.sentence_id,
                            segment_type=segment.segment_type,
                            start_offset=segment.unit_start_utf16,
                            end_offset=segment.unit_end_utf16,
                            selected_text=segment.text,
                            text_hash=compute_text_range_hash(segment.text),
                        ),
                        label=f"{segment.anchor_segment_id} clause",
                        analysis=(
                            f"确定性句析：{segment.anchor_segment_id} 保持在第一个翻译组之后。"
                        ),
                        chunks=[
                            SentenceAnalysisChunk(
                                order=1,
                                label="clause",
                                text=segment.text,
                            )
                        ],
                    )
                )
            outputs.append(
                (
                    unit.unit_id,
                    GrammarBundleOutput(sentence_analyses=analyses),
                )
            )
        return replace(baseline, outputs=outputs)


async def _mark_fake_only_before_bootstrap(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> None:
    """Reuse the G5 fake namespace gate before any enhancement claim."""

    async with pool.acquire() as conn:
        async with conn.transaction():
            job_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = ANY($2::text[])
                """,
                record_id,
                list(_ENHANCEMENT_JOB_TYPES),
            )
            if int(job_count or 0) != 0:
                _fail(f"enhancement jobs existed before fake namespace gate: {job_count}")
            row = await conn.fetchrow(
                """
                SELECT metadata_json
                FROM original_inputs
                WHERE reading_record_id = $1
                ORDER BY created_at, id
                LIMIT 1
                FOR UPDATE
                """,
                record_id,
            )
            if row is None:
                _fail("product-created record has no original input")
            metadata = ensure_json_object(row["metadata_json"])
            metadata["executor_mode"] = "fake"
            metadata["fake_job_namespace"] = FAKE_JOB_NAMESPACE
            await conn.execute(
                """
                UPDATE original_inputs
                SET metadata_json = $2::jsonb
                WHERE reading_record_id = $1
                """,
                record_id,
                jsonb_param(metadata),
            )


def _build_runner(pool: asyncpg.Pool):
    harness = ReaderEnhancementSmokeHarness(pool=pool)
    runner = harness._build_pipeline_runner(
        executor_mode="fake",
        grammar_topology="production",
    )
    runner._grammar_worker_service = GrammarBundleWorkerService(
        pool=pool,
        job_runtime=ReaderJobRuntime(pool=pool, job_scope=JOB_RUNTIME_SCOPE_FAKE),
        executor=DevFakeGrammarBundleExecutor(),
        batch_executor=_StableOrderGrammarBatchExecutor(),
    )
    return runner


async def _load_manifest(
    conn: asyncpg.Connection,
    *,
    record_id: UUID | None,
    user_id: UUID,
    scope_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = await conn.fetchrow(
        "SELECT active_base_id FROM reading_records WHERE id = $1",
        record_id,
    )
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
    scoped_favorite_ids = _manifest_uuid_ids(scope_manifest, "favorite_records")
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
        WHERE user_id = $1 OR id = ANY($2::uuid[])
        ORDER BY id
        """,
        user_id,
        scoped_identity_ids,
    )
    sessions = await _fetch_ids(
        conn,
        """
        SELECT id FROM user_sessions
        WHERE user_id = $1 OR id = ANY($2::uuid[])
        ORDER BY id
        """,
        user_id,
        scoped_session_ids,
    )
    records = await _fetch_ids(
        conn,
        """
        SELECT id FROM reading_records
        WHERE user_id = $1 OR id = $2 OR id = ANY($3::uuid[])
        ORDER BY id
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
    bases = await _fetch_ids(
        conn,
        """
        SELECT id FROM reading_bases
        WHERE reading_record_id = $1 OR id = ANY($2::uuid[])
        ORDER BY id
        """,
        record_id,
        scoped_base_ids,
    )
    base_ids = [UUID(value) for value in bases]
    active_base_id = (
        record["active_base_id"]
        if record is not None
        else (base_ids[0] if len(base_ids) == 1 else None)
    )
    units = await conn.fetch(
        """
        SELECT id, unit_id
        FROM reading_units
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY order_index, unit_id
        """,
        record_id,
        base_ids,
        scoped_unit_ids,
    )
    segments = await conn.fetch(
        """
        SELECT id, anchor_segment_id
        FROM anchor_segments
        WHERE reading_record_id = $1 OR base_id = ANY($2::uuid[])
           OR id = ANY($3::uuid[])
        ORDER BY order_index, anchor_segment_id
        """,
        record_id,
        base_ids,
        scoped_segment_ids,
    )
    stable_documents = await _fetch_ids(
        conn,
        """
        SELECT id FROM stable_reading_documents
        WHERE reading_record_id = $1 OR id = ANY($2::uuid[])
        ORDER BY id
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
    spans = await conn.fetch(
        """
        SELECT id, trace_id FROM reader_runtime_spans
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
    favorites = await _fetch_ids(
        conn,
        """
        SELECT id FROM favorite_records
        WHERE user_id = $1 OR id = ANY($2::uuid[])
        ORDER BY id
        """,
        user_id,
        scoped_favorite_ids,
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
        "reading_bases": bases,
        "reading_units": _string_ids(units),
        "anchor_segments": _string_ids(segments),
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
        "favorite_records": favorites,
        "user_credit_accounts": credit_accounts,
        "user_credit_ledger": credit_ledger,
    }
    return {
        "user_id": str(user_id),
        "record_id": str(record_id) if record_id is not None else None,
        "base_id": str(active_base_id) if active_base_id is not None else None,
        "base_ids": bases,
        "stable_document_id": stable_documents[0] if len(stable_documents) == 1 else None,
        "stable_document_ids": stable_documents,
        "identity_ids": identities,
        "session_ids": sessions,
        "original_input_ids": original_inputs,
        "unit_row_ids": _string_ids(units),
        "unit_ids": [str(row["unit_id"]) for row in units],
        "anchor_segment_row_ids": _string_ids(segments),
        "anchor_segment_ids": [str(row["anchor_segment_id"]) for row in segments],
        "layer_ids": layers,
        "job_ids": jobs,
        "run_ids": runs,
        "event_ids": events,
        "job_event_ids": job_events,
        "usage_event_ids": usage,
        "runtime_span_ids": _string_ids(spans),
        "trace_ids": sorted({str(row["trace_id"]) for row in spans if row["trace_id"] is not None}),
        "journal_ids": journal,
        "tables": {table_name: _table_entry(ids) for table_name, ids in table_ids.items()},
        "populated_tables": {
            table_name: _table_entry(ids) for table_name, ids in table_ids.items() if ids
        },
    }


async def _validate_contract(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    base_id: UUID,
    generation: int,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        units = await conn.fetch(
            """
            SELECT unit_id FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index
            """,
            record_id,
            base_id,
        )
        unit_ids = [str(row["unit_id"]) for row in units]
        if unit_ids != _EXPECTED_UNIT_IDS:
            _fail(f"stable-order unit contract changed: {unit_ids}")
        segments = await conn.fetch(
            """
            SELECT unit_id, anchor_segment_id
            FROM anchor_segments
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index
            """,
            record_id,
            base_id,
        )
        u3_segments = [
            str(row["anchor_segment_id"]) for row in segments if str(row["unit_id"]) == "u3"
        ]
        if u3_segments != _EXPECTED_U3_SEGMENTS:
            _fail(f"stable-order u3 segment contract changed: {u3_segments}")
        layer_rows = await conn.fetch(
            """
            SELECT layer_type, target_key, output_json, quality_json
            FROM enhancement_layers
            WHERE reading_record_id = $1 AND status = 'published'
            ORDER BY published_at, id
            """,
            record_id,
        )

    translation_rows = [
        row
        for row in layer_rows
        if row["layer_type"] == "translation" and row["target_key"] == "u3"
    ]
    if len(translation_rows) != 1:
        _fail(f"expected one u3 translation layer, got {len(translation_rows)}")
    groups = _json_list(_json_object(translation_rows[0]["output_json"]).get("groups"))
    group_contract = [
        (
            str(_json_object(group).get("group_id")),
            [str(value) for value in _json_list(_json_object(group).get("anchor_segment_ids"))],
        )
        for group in groups
    ]
    if group_contract != _EXPECTED_TRANSLATION_GROUPS:
        _fail(f"stable-order translation groups changed: {group_contract}")

    sentence_rows = [
        row
        for row in layer_rows
        if row["layer_type"] == "sentence_analysis" and row["target_key"] == "u3"
    ]
    if len(sentence_rows) != 1:
        _fail(f"expected one u3 sentence-analysis layer, got {len(sentence_rows)}")
    items = _json_list(_json_object(sentence_rows[0]["output_json"]).get("items"))
    analysis_segments = [
        str(_json_object(_json_object(item).get("anchor")).get("anchor_segment_id"))
        for item in items
    ]
    if analysis_segments != ["s6", "s7"]:
        _fail(f"stable-order sentence analyses changed: {analysis_segments}")
    for row in (*translation_rows, *sentence_rows):
        if _json_object(row["quality_json"]).get("model_provider") != "fake":
            _fail(f"non-fake enhancement quality metadata: {dict(row)}")

    service = ArticleReadyPersistenceService(pool=pool)
    snapshot = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
        expected_base_id=base_id,
        expected_generation=generation,
    )
    reloaded = await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
        expected_base_id=base_id,
        expected_generation=generation,
    )
    snapshot_layers = [layer.model_dump(mode="json") for layer in snapshot.enhancement_layers]
    reloaded_layers = [layer.model_dump(mode="json") for layer in reloaded.enhancement_layers]
    if snapshot_layers != reloaded_layers:
        _fail("enhancement layer contract changed after snapshot reload")
    snapshot_segments = [segment.model_dump(mode="json") for segment in snapshot.anchor_segments]
    reloaded_segments = [segment.model_dump(mode="json") for segment in reloaded.anchor_segments]
    if snapshot_segments != reloaded_segments:
        _fail("anchor segment contract changed after snapshot reload")
    return {
        "unit_ids": unit_ids,
        "u3_anchor_segment_ids": u3_segments,
        "translation_groups": [
            {"group_id": group_id, "anchor_segment_ids": anchor_ids}
            for group_id, anchor_ids in group_contract
        ],
        "sentence_analysis_anchor_segment_ids": analysis_segments,
        "u4_follows_u3": unit_ids.index("u4") == unit_ids.index("u3") + 1,
        "snapshot_reload_equal": True,
        "snapshot_layer_count": len(snapshot_layers),
    }


async def _build(record_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    pool = await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )
    try:
        async with pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                SELECT user_id, active_base_id, generation
                FROM reading_records
                WHERE id = $1 AND lifecycle_status = 'active'
                """,
                record_id,
            )
        if record is None:
            _fail(f"active product-created record not found: {record_id}")
        await _mark_fake_only_before_bootstrap(pool, record_id=record_id)
        summary = await _build_runner(pool).run(
            record_id=record_id,
            user_id=record["user_id"],
            lease_owner="reader-stable-order-real-product",
            lease_duration=timedelta(seconds=120),
            max_ticks=96,
            max_jobs=96,
        )
        contract = await _validate_contract(
            pool,
            record_id=record_id,
            user_id=record["user_id"],
            base_id=record["active_base_id"],
            generation=int(record["generation"]),
        )
        async with pool.acquire() as conn:
            manifest = await _load_manifest(
                conn,
                record_id=record_id,
                user_id=record["user_id"],
            )
        dict_candidates = _json_object(
            _json_object(manifest.get("tables")).get("dict_ai_candidate_entries")
        )
        if int(dict_candidates.get("count", 0)) != 0:
            _fail(f"fixture build touched protected dict_ai_candidate_entries: {dict_candidates}")
        return {
            "executor_mode": "fake",
            "pipeline_summary": {
                "total_jobs": summary.total_jobs,
                "total_ticks": summary.total_ticks,
                "stopped_reason": summary.stopped_reason,
                "outcome_counts": asdict(summary.outcome_counts),
            },
            "contract": contract,
            "manifest": manifest,
        }
    finally:
        await close_db()


async def _preflight(phone: str) -> dict[str, Any]:
    normalized_phone = _normalize_phone(phone)
    settings = get_settings()
    pool = await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetch(
                """
                SELECT id, user_id FROM user_identities
                WHERE provider = 'phone' AND provider_user_id = $1
                ORDER BY id
                """,
                normalized_phone,
            )
        if existing:
            _fail(
                "phone preflight refused an existing identity: "
                f"phone={normalized_phone} identity_ids={_string_ids(existing)} "
                f"user_ids={[str(row['user_id']) for row in existing]}"
            )
        return {
            "status": "PASS",
            "provider": "phone",
            "phone": normalized_phone,
            "identity_count": 0,
        }
    finally:
        await close_db()


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


async def _cleanup(phone: str, record_id: UUID | None) -> dict[str, Any]:
    normalized_phone = _normalize_phone(phone)
    settings = get_settings()
    pool = await init_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        max_inactive_connection_lifetime=settings.database_max_inactive_connection_lifetime,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                identity_user_id = await conn.fetchval(
                    """
                    SELECT user_id FROM user_identities
                    WHERE provider = 'phone' AND provider_user_id = $1
                    """,
                    normalized_phone,
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
                    _fail("cleanup phone identity does not own the requested record")
                user_id = identity_user_id or record_user_id
                if user_id is None:
                    residual_counts = {table_name: 0 for table_name in _TASK_OWNED_TABLES}
                    return {
                        "phone": normalized_phone,
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
                manifest = await _load_manifest(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                )
                protected_dict = _json_object(
                    _json_object(manifest.get("tables")).get("dict_ai_candidate_entries")
                )
                if int(protected_dict.get("count", 0)) != 0:
                    _fail(
                        "cleanup refused because the fixture touched "
                        f"dict_ai_candidate_entries: {protected_dict}"
                    )
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
                "phone": normalized_phone,
                "record_id": str(record_id) if record_id is not None else None,
                "deleted_user": True,
                "manifest": manifest,
                "residual_counts": residual_counts,
                "residual_total": residual_total,
            }
    finally:
        await close_db()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--phone", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("record_id", type=UUID)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--phone", required=True)
    cleanup_parser.add_argument("--record-id", type=UUID)
    args = parser.parse_args()
    if args.command == "preflight":
        result = asyncio.run(_preflight(args.phone))
    elif args.command == "build":
        result = asyncio.run(_build(args.record_id))
    else:
        result = asyncio.run(_cleanup(args.phone, args.record_id))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
