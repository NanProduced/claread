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
from app.services.auth.phone import normalize_phone
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


def _fail(message: str) -> None:
    raise AssertionError(message)


def _json_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_ids(rows: list[asyncpg.Record]) -> list[str]:
    return [str(row["id"]) for row in rows]


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
    record_id: UUID,
    user_id: UUID,
) -> dict[str, Any]:
    record = await conn.fetchrow(
        "SELECT active_base_id FROM reading_records WHERE id = $1",
        record_id,
    )
    base_id = record["active_base_id"] if record is not None else None
    stable = await conn.fetchrow(
        "SELECT id FROM stable_reading_documents WHERE reading_record_id = $1",
        record_id,
    )
    original_inputs = await conn.fetch(
        "SELECT id FROM original_inputs WHERE reading_record_id = $1 ORDER BY id",
        record_id,
    )
    units = await conn.fetch(
        """
        SELECT id, unit_id
        FROM reading_units
        WHERE reading_record_id = $1
        ORDER BY order_index, unit_id
        """,
        record_id,
    )
    segments = await conn.fetch(
        """
        SELECT id, anchor_segment_id
        FROM anchor_segments
        WHERE reading_record_id = $1
        ORDER BY order_index, anchor_segment_id
        """,
        record_id,
    )
    layers = await conn.fetch(
        "SELECT id FROM enhancement_layers WHERE reading_record_id = $1 ORDER BY id",
        record_id,
    )
    jobs = await conn.fetch(
        "SELECT id FROM reader_jobs WHERE reading_record_id = $1 ORDER BY id",
        record_id,
    )
    runs = await conn.fetch(
        "SELECT id FROM reader_runs WHERE reading_record_id = $1 ORDER BY id",
        record_id,
    )
    events = await conn.fetch(
        "SELECT id FROM reader_events WHERE reading_record_id = $1 ORDER BY sequence",
        record_id,
    )
    job_events = await conn.fetch(
        "SELECT id FROM reader_job_events WHERE reading_record_id = $1 ORDER BY id",
        record_id,
    )
    usage = await conn.fetch(
        """
        SELECT id FROM ai_usage_events
        WHERE user_id = $1 OR reading_record_id = $2
        ORDER BY id
        """,
        user_id,
        record_id,
    )
    spans = await conn.fetch(
        "SELECT id, trace_id FROM reader_runtime_spans WHERE reading_record_id = $1 ORDER BY id",
        record_id,
    )
    job_ids = [UUID(value) for value in _string_ids(jobs)]
    run_ids = [UUID(value) for value in _string_ids(runs)]
    journal = await conn.fetch(
        """
        SELECT id FROM ai_model_execution_journal
        WHERE reader_job_id = ANY($1::uuid[]) OR reader_run_id = ANY($2::uuid[])
        ORDER BY id
        """,
        job_ids,
        run_ids,
    )
    identities = await conn.fetch(
        "SELECT id FROM user_identities WHERE user_id = $1 ORDER BY id",
        user_id,
    )
    return {
        "user_id": str(user_id),
        "record_id": str(record_id),
        "base_id": str(base_id) if base_id is not None else None,
        "stable_document_id": str(stable["id"]) if stable is not None else None,
        "identity_ids": _string_ids(identities),
        "original_input_ids": _string_ids(original_inputs),
        "unit_row_ids": _string_ids(units),
        "unit_ids": [str(row["unit_id"]) for row in units],
        "anchor_segment_row_ids": _string_ids(segments),
        "anchor_segment_ids": [str(row["anchor_segment_id"]) for row in segments],
        "layer_ids": _string_ids(layers),
        "job_ids": _string_ids(jobs),
        "run_ids": _string_ids(runs),
        "event_ids": _string_ids(events),
        "job_event_ids": _string_ids(job_events),
        "usage_event_ids": _string_ids(usage),
        "runtime_span_ids": _string_ids(spans),
        "trace_ids": sorted({str(row["trace_id"]) for row in spans}),
        "journal_ids": _string_ids(journal),
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


async def _residual_counts(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    record_id: UUID | None,
    manifest: dict[str, Any],
) -> dict[str, int]:
    job_ids = [UUID(value) for value in manifest.get("job_ids", [])]
    run_ids = [UUID(value) for value in manifest.get("run_ids", [])]
    layer_ids = [UUID(value) for value in manifest.get("layer_ids", [])]
    trace_ids = [UUID(value) for value in manifest.get("trace_ids", [])]
    record_queries = {
        "reading_records": "SELECT COUNT(*) FROM reading_records WHERE id = $1 OR user_id = $2",
        "reading_units": "SELECT COUNT(*) FROM reading_units WHERE reading_record_id = $1",
        "anchor_segments": "SELECT COUNT(*) FROM anchor_segments WHERE reading_record_id = $1",
        "enhancement_layers": (
            "SELECT COUNT(*) FROM enhancement_layers WHERE reading_record_id = $1"
        ),
        "reader_runs": "SELECT COUNT(*) FROM reader_runs WHERE reading_record_id = $1",
        "reader_jobs": "SELECT COUNT(*) FROM reader_jobs WHERE reading_record_id = $1",
        "reader_events": "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
        "reader_job_events": "SELECT COUNT(*) FROM reader_job_events WHERE reading_record_id = $1",
        "dict_ai_candidate_entries": (
            "SELECT COUNT(*) FROM dict_ai_candidate_entries WHERE reading_record_id = $1"
        ),
    }
    counts = {
        "users": int(await conn.fetchval("SELECT COUNT(*) FROM users WHERE id = $1", user_id)),
        "user_identities": int(
            await conn.fetchval("SELECT COUNT(*) FROM user_identities WHERE user_id = $1", user_id)
        ),
    }
    if record_id is not None:
        for name, query in record_queries.items():
            args = (record_id, user_id) if name == "reading_records" else (record_id,)
            counts[name] = int(await conn.fetchval(query, *args))
    else:
        counts.update({name: 0 for name in record_queries})
    counts["ai_usage_events"] = int(
        await conn.fetchval(
            """
            SELECT COUNT(*) FROM ai_usage_events
            WHERE user_id = $1 OR reading_record_id = $2
               OR reader_job_id = ANY($3::uuid[])
               OR reader_run_id = ANY($4::uuid[])
               OR enhancement_layer_id = ANY($5::uuid[])
            """,
            user_id,
            record_id,
            job_ids,
            run_ids,
            layer_ids,
        )
    )
    counts["reader_runtime_spans"] = int(
        await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_runtime_spans
            WHERE reading_record_id = $1
               OR reader_job_id = ANY($2::uuid[])
               OR reader_run_id = ANY($3::uuid[])
               OR trace_id = ANY($4::uuid[])
            """,
            record_id,
            job_ids,
            run_ids,
            trace_ids,
        )
    )
    counts["ai_model_execution_journal"] = int(
        await conn.fetchval(
            """
            SELECT COUNT(*) FROM ai_model_execution_journal
            WHERE reader_job_id = ANY($1::uuid[]) OR reader_run_id = ANY($2::uuid[])
            """,
            job_ids,
            run_ids,
        )
    )
    return counts


async def _cleanup(phone: str, record_id: UUID | None) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
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
                    return {
                        "phone": normalized_phone,
                        "record_id": str(record_id) if record_id is not None else None,
                        "deleted_user": False,
                        "manifest": {},
                        "residual_counts": {},
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
                manifest = (
                    await _load_manifest(conn, record_id=record_id, user_id=user_id)
                    if record_id is not None
                    else {
                        "user_id": str(user_id),
                        "record_id": None,
                        "job_ids": [],
                        "run_ids": [],
                        "layer_ids": [],
                        "trace_ids": [],
                    }
                )
                if record_id is not None:
                    protected_dict_rows = int(
                        await conn.fetchval(
                            """
                            SELECT COUNT(*) FROM dict_ai_candidate_entries
                            WHERE reading_record_id = $1
                            """,
                            record_id,
                        )
                    )
                    if protected_dict_rows != 0:
                        _fail(
                            "cleanup refused because the fixture touched "
                            f"dict_ai_candidate_entries: {protected_dict_rows}"
                        )
                job_ids = [UUID(value) for value in manifest.get("job_ids", [])]
                run_ids = [UUID(value) for value in manifest.get("run_ids", [])]
                layer_ids = [UUID(value) for value in manifest.get("layer_ids", [])]
                trace_ids = [UUID(value) for value in manifest.get("trace_ids", [])]
                await conn.execute(
                    """
                    DELETE FROM ai_model_execution_journal
                    WHERE reader_job_id = ANY($1::uuid[])
                       OR reader_run_id = ANY($2::uuid[])
                    """,
                    job_ids,
                    run_ids,
                )
                await conn.execute(
                    """
                    DELETE FROM ai_usage_events
                    WHERE user_id = $1 OR reading_record_id = $2
                       OR reader_job_id = ANY($3::uuid[])
                       OR reader_run_id = ANY($4::uuid[])
                       OR enhancement_layer_id = ANY($5::uuid[])
                    """,
                    user_id,
                    record_id,
                    job_ids,
                    run_ids,
                    layer_ids,
                )
                await conn.execute(
                    """
                    DELETE FROM reader_runtime_spans
                    WHERE reading_record_id = $1
                       OR reader_job_id = ANY($2::uuid[])
                       OR reader_run_id = ANY($3::uuid[])
                       OR trace_id = ANY($4::uuid[])
                    """,
                    record_id,
                    job_ids,
                    run_ids,
                    trace_ids,
                )
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
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("record_id", type=UUID)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--phone", required=True)
    cleanup_parser.add_argument("--record-id", type=UUID)
    args = parser.parse_args()
    if args.command == "build":
        result = asyncio.run(_build(args.record_id))
    else:
        result = asyncio.run(_cleanup(args.phone, args.record_id))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
