"""Test-side deterministic runner for the real Reader Markdown G5 path.

This helper is intentionally not production wiring.  It reuses the existing
smoke harness' explicitly injected fake executors against the development
PostgreSQL database, then verifies the persisted source/document/snapshot
contracts before the browser asserts the product projection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.config.settings import get_settings
from app.contracts.annotation import slice_by_utf16_offsets, utf16_code_unit_length
from app.database.connection import close_db, init_db
from app.database.json_compat import ensure_json_object, jsonb_param
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_runtime import FAKE_JOB_NAMESPACE
from app.services.reader_orchestration.smoke_harness import (
    ReaderEnhancementSmokeHarness,
)

_ENHANCEMENT_JOB_TYPES = (
    "generate_display_title_zh",
    "translate_unit",
    "translate_article",
    "build_vocabulary_layer",
    "build_vocabulary_layer_article",
    "build_grammar_bundle",
    "build_grammar_bundle_window",
)
_EXPECTED_BLOCK_TYPES = {
    "heading",
    "paragraph",
    "blockquote",
    "list",
    "list_item",
    "table",
    "table_row",
    "table_cell",
    "code_block",
}
_EXPECTED_T_ONLY_POLICY = {
    "translation": True,
    "vocabulary": False,
    "grammar_note": False,
    "sentence_analysis": False,
}
_EXPECTED_CALLOUT_ICONS = ["🎯", "⚠️"]


def _json_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _target_unit_ids(job: asyncpg.Record) -> set[str]:
    input_json = _json_object(job["input_json"])
    target_ids = input_json.get("target_unit_ids")
    if isinstance(target_ids, list):
        return {str(unit_id) for unit_id in target_ids if unit_id}
    target_id = input_json.get("target_unit_id")
    return {str(target_id)} if target_id else set()


def _tree_nodes(nodes: object) -> list[dict[str, Any]]:
    if not isinstance(nodes, list):
        return []
    result: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        result.append(node)
        result.extend(_tree_nodes(node.get("children")))
    return result


def _slice_or_fail(text: str, start: int, end: int, *, label: str) -> str:
    if start == end:
        return ""
    value = slice_by_utf16_offsets(text, start, end)
    if value is None:
        _fail(f"{label} is not a valid UTF-16 range: {start}:{end}")
    return value


async def _mark_fake_only_before_bootstrap(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> None:
    """Atomically opt the product-created record into the fake G5 namespace.

    The browser submission itself must remain a real product request. This
    gate runs before the first enhancement bootstrap/claim and refuses to
    reuse a record that already has enhancement jobs, which prevents a real
    worker from racing the fake runner.
    """
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
                _fail(
                    "fake-only G5 preflight found enhancement jobs before the "
                    f"namespace gate: {job_count}"
                )
            row = await conn.fetchrow(
                """
                SELECT metadata_json
                FROM original_inputs
                WHERE reading_record_id = $1
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                FOR UPDATE
                """,
                record_id,
            )
            if row is None:
                _fail("fake-only G5 preflight found no original input")
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


def _fail(message: str) -> None:
    raise AssertionError(message)


async def _run(record_id: UUID, expected_source_sha256: str | None) -> dict[str, Any]:
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
            _fail(f"active reading record not found: {record_id}")

        harness = ReaderEnhancementSmokeHarness(pool=pool)
        await _mark_fake_only_before_bootstrap(pool, record_id=record_id)
        runner = harness._build_pipeline_runner(
            executor_mode="fake",
            grammar_topology="production",
        )
        summary = await runner.run(
            record_id=record_id,
            user_id=record["user_id"],
            lease_owner="reader-markdown-g5-real-product",
            lease_duration=timedelta(seconds=120),
            max_ticks=96,
            max_jobs=96,
        )

        snapshot_service = ArticleReadyPersistenceService(pool=pool)
        snapshot = await snapshot_service.load_snapshot(
            record_id=record_id,
            user_id=record["user_id"],
            expected_base_id=record["active_base_id"],
            expected_generation=int(record["generation"]),
        )
        reloaded_snapshot = await snapshot_service.load_snapshot(
            record_id=record_id,
            user_id=record["user_id"],
            expected_base_id=record["active_base_id"],
            expected_generation=int(record["generation"]),
        )

        async with pool.acquire() as conn:
            source = await conn.fetchrow(
                """
                SELECT status, revision, markdown_text, content_sha256
                FROM confirmed_source_documents
                WHERE reading_record_id = $1 AND record_generation = $2
                """,
                record_id,
                int(record["generation"]),
            )
            base = await conn.fetchrow(
                """
                SELECT id, text, content_sha256, content_utf16_length
                FROM reading_bases
                WHERE id = $1
                  AND reading_record_id = $2
                  AND record_generation = $3
                  AND status = 'active'
                """,
                record["active_base_id"],
                record_id,
                int(record["generation"]),
            )
            stable = await conn.fetchrow(
                """
                SELECT id, document_version, status, content_sha256
                FROM stable_reading_documents
                WHERE reading_record_id = $1 AND record_generation = $2
                """,
                record_id,
                int(record["generation"]),
            )
            blocks = await conn.fetch(
                """
                SELECT block_id, block_type, parent_block_id, order_index,
                       canonical_text_start_utf16, canonical_text_end_utf16,
                       text_content, payload_json, interpretation_policy_json
                FROM stable_document_blocks
                WHERE stable_document_id = $1
                ORDER BY order_index, block_id
                """,
                stable["id"] if stable is not None else None,
            )
            units = await conn.fetch(
                """
                SELECT unit_id, unit_type, base_start_utf16, base_end_utf16,
                       metadata_json
                FROM reading_units
                WHERE base_id = $1
                ORDER BY order_index, unit_id
                """,
                record["active_base_id"],
            )
            anchor_segments = await conn.fetch(
                """
                SELECT unit_id, anchor_segment_id, order_index,
                       base_start_utf16, base_end_utf16,
                       unit_start_utf16, unit_end_utf16
                FROM anchor_segments
                WHERE base_id = $1
                ORDER BY order_index, anchor_segment_id
                """,
                record["active_base_id"],
            )
            layers = await conn.fetch(
                """
                SELECT layer_type, status, quality_json
                FROM enhancement_layers
                WHERE reading_record_id = $1
                ORDER BY created_at, layer_type
                """,
                record_id,
            )
            jobs = await conn.fetch(
                """
                SELECT job_type, status, input_json, output_ref_json, failure_code
                FROM reader_jobs
                WHERE reading_record_id = $1
                  AND job_type = ANY($2::text[])
                ORDER BY created_at, job_type
                """,
                record_id,
                list(_ENHANCEMENT_JOB_TYPES),
            )
            all_jobs = await conn.fetch(
                """
                SELECT job_type, status, input_json, output_ref_json, failure_code
                FROM reader_jobs
                WHERE reading_record_id = $1
                ORDER BY created_at, job_type
                """,
                record_id,
            )

        if source is None or source["status"] != "frozen":
            _fail(f"confirmed source is not frozen: {source}")
        if expected_source_sha256 is not None:
            actual_source_sha256 = hashlib.sha256(
                (source["markdown_text"] or "").encode("utf-8")
            ).hexdigest()
            if actual_source_sha256 != expected_source_sha256:
                _fail(
                    "confirmed source hash changed: "
                    f"expected={expected_source_sha256} actual={actual_source_sha256}"
                )
        if stable is None or stable["status"] != "active":
            _fail(f"stable reading document is not active: {stable}")
        if not blocks:
            _fail("stable document has no persisted blocks")
        if base is None:
            _fail("active canonical reading base is missing")

        canonical_text = str(base["text"] or "")
        canonical_length = utf16_code_unit_length(canonical_text)
        if canonical_length != int(base["content_utf16_length"]):
            _fail(
                "canonical UTF-16 length mismatch: "
                f"computed={canonical_length} persisted={base['content_utf16_length']}"
            )
        canonical_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
        if canonical_sha256 != str(base["content_sha256"]):
            _fail(
                "canonical text hash mismatch: "
                f"computed={canonical_sha256} persisted={base['content_sha256']}"
            )

        block_types = [str(block["block_type"]) for block in blocks]
        missing_types = sorted(_EXPECTED_BLOCK_TYPES.difference(block_types))
        if missing_types:
            _fail(f"stable block coverage missing: {missing_types}; got={block_types}")

        block_ids = {str(block["block_id"]) for block in blocks}
        if any(
            block["parent_block_id"] is not None
            and str(block["parent_block_id"]) not in block_ids
            for block in blocks
        ):
            _fail("stable block parent points outside the persisted document")
        if any(
            block["canonical_text_start_utf16"] is not None
            and (
                block["canonical_text_end_utf16"] is None
                or block["canonical_text_end_utf16"]
                <= block["canonical_text_start_utf16"]
            )
            for block in blocks
        ):
            _fail("stable block has an invalid canonical UTF-16 range")

        ranged_blocks = [
            block
            for block in blocks
            if block["canonical_text_start_utf16"] is not None
            and block["canonical_text_end_utf16"] is not None
        ]
        if [block["order_index"] for block in ranged_blocks] != sorted(
            block["order_index"] for block in ranged_blocks
        ):
            _fail("stable ranged blocks are not persisted in order")
        ranges = [
            (
                int(block["canonical_text_start_utf16"]),
                int(block["canonical_text_end_utf16"]),
            )
            for block in ranged_blocks
        ]
        if len(ranges) != len(set(ranges)):
            _fail("stable document contains duplicate canonical ranges")
        cursor = 0
        for block, (start, end) in zip(ranged_blocks, ranges, strict=True):
            if start < cursor:
                _fail(f"stable canonical ranges overlap at {block['block_id']}")
            gap = _slice_or_fail(
                canonical_text,
                cursor,
                start,
                label=f"gap before {block['block_id']}",
            )
            if gap.strip():
                _fail(
                    f"unowned canonical prose between blocks before {block['block_id']}: "
                    f"{gap!r}"
                )
            block_text = block["text_content"]
            if not isinstance(block_text, str):
                _fail(f"ranged block has no text content: {block['block_id']}")
            sliced = _slice_or_fail(
                canonical_text,
                start,
                end,
                label=f"block {block['block_id']}",
            )
            if sliced != block_text:
                _fail(
                    f"stable block text differs from canonical slice: {block['block_id']}"
                )
            cursor = end
        trailing_gap = _slice_or_fail(
            canonical_text,
            cursor,
            canonical_length,
            label="canonical trailing gap",
        )
        if trailing_gap.strip():
            _fail(f"canonical text has unowned trailing prose: {trailing_gap!r}")

        children_by_parent: dict[str, list[asyncpg.Record]] = {}
        for block in blocks:
            parent_id = block["parent_block_id"]
            if parent_id is not None:
                children_by_parent.setdefault(str(parent_id), []).append(block)
        for parent_id, children in children_by_parent.items():
            if [child["order_index"] for child in children] != sorted(
                child["order_index"] for child in children
            ):
                _fail(f"stable child order is not preserved under {parent_id}")

        def block_content_role(block: asyncpg.Record) -> str | None:
            payload = _json_object(block["payload_json"])
            semantic = _json_object(payload.get("semantic"))
            role = semantic.get("content_role")
            return str(role) if isinstance(role, str) else None

        def descendants(parent_id: str) -> list[asyncpg.Record]:
            result: list[asyncpg.Record] = []
            for child in children_by_parent.get(parent_id, []):
                result.append(child)
                result.extend(descendants(str(child["block_id"])))
            return result

        callout_blocks = [
            block for block in blocks if block_content_role(block) == "source_callout"
        ]
        callout_wrappers = [
            block
            for block in callout_blocks
            if block["canonical_text_start_utf16"] is None
            and block["parent_block_id"] is None
        ]
        if not callout_wrappers:
            _fail("stable document has no structural source_callout wrapper")
        if len(callout_wrappers) != len(_EXPECTED_CALLOUT_ICONS):
            _fail(
                "stable document source_callout wrapper count mismatch: "
                f"expected={len(_EXPECTED_CALLOUT_ICONS)} got={len(callout_wrappers)}"
            )
        for wrapper, expected_icon in zip(
            callout_wrappers, _EXPECTED_CALLOUT_ICONS, strict=True
        ):
            if str(wrapper["text_content"] or "").strip():
                _fail(f"source_callout wrapper repeats visible text: {wrapper['block_id']}")
            wrapper_payload = _json_object(wrapper["payload_json"])
            if wrapper_payload.get("display_icon") != expected_icon:
                _fail(
                    "source_callout wrapper did not persist the expected display_icon: "
                    f"{wrapper['block_id']} payload={wrapper_payload}"
                )
            if wrapper["canonical_text_start_utf16"] is not None:
                _fail(f"source_callout wrapper owns a canonical range: {wrapper['block_id']}")
            children = children_by_parent.get(str(wrapper["block_id"]), [])
            if not children:
                _fail(f"source_callout wrapper has no children: {wrapper['block_id']}")
            role_bearing_descendants = [
                block
                for block in descendants(str(wrapper["block_id"]))
                if str(block["block_type"]) in {"paragraph", "blockquote", "list_item"}
            ]
            if any(
                block_content_role(block) != "source_callout"
                for block in role_bearing_descendants
            ):
                _fail(f"source_callout child lost inherited role: {wrapper['block_id']}")
        if any(
            block["text_content"] in set(_EXPECTED_CALLOUT_ICONS)
            for block in callout_blocks
        ):
            _fail("callout display icon leaked into a canonical Stable block")

        callout_mark_types: set[str] = set()
        safe_link_mark_count = 0
        for block in callout_blocks:
            payload = _json_object(block["payload_json"])
            marks = payload.get("inline_marks")
            if not isinstance(marks, list):
                continue
            for mark in marks:
                if not isinstance(mark, dict):
                    continue
                mark_type = mark.get("type")
                if isinstance(mark_type, str):
                    callout_mark_types.add(mark_type)
                if mark_type == "link" and str(mark.get("href", "")).startswith(
                    "https://"
                ):
                    safe_link_mark_count += 1
        if not {"strong", "em", "link"}.issubset(callout_mark_types):
            _fail(f"source_callout inline marks were not preserved: {callout_mark_types}")
        if safe_link_mark_count == 0:
            _fail("source_callout has no safe link mark")

        units_by_id = {str(unit["unit_id"]): unit for unit in units}
        units_by_range = {
            (int(unit["base_start_utf16"]), int(unit["base_end_utf16"])): unit
            for unit in units
        }
        callout_unit_ids: set[str] = set()
        for block in callout_blocks:
            start = block["canonical_text_start_utf16"]
            end = block["canonical_text_end_utf16"]
            if start is None or end is None:
                continue
            unit = units_by_range.get((int(start), int(end)))
            if unit is None:
                _fail(f"source_callout block has no matching reading unit: {block['block_id']}")
            callout_unit_ids.add(str(unit["unit_id"]))
        if not callout_unit_ids:
            _fail("source_callout has no canonical reading units")
        for unit_id in callout_unit_ids:
            unit = units_by_id[unit_id]
            metadata = _json_object(unit["metadata_json"])
            semantic = _json_object(metadata.get("semantic"))
            if semantic.get("content_role") != "source_callout":
                _fail(f"source_callout unit lost semantic role: {unit_id}")
            if semantic.get("automatic_layer_policy") != _EXPECTED_T_ONLY_POLICY:
                _fail(
                    f"source_callout automatic policy is not T-only: "
                    f"{unit_id} {semantic.get('automatic_layer_policy')}"
                )

        segments_by_unit: dict[str, list[asyncpg.Record]] = {}
        for segment in anchor_segments:
            segments_by_unit.setdefault(str(segment["unit_id"]), []).append(segment)
        if not any(
            len(segments_by_unit.get(unit_id, [])) >= 2 for unit_id in callout_unit_ids
        ):
            _fail("G5 source_callout did not exercise a multi-segment unit")
        for unit_id in callout_unit_ids:
            unit = units_by_id[unit_id]
            unit_start = int(unit["base_start_utf16"])
            unit_end = int(unit["base_end_utf16"])
            segment_cursor = unit_start
            for segment in segments_by_unit.get(unit_id, []):
                segment_start = int(segment["base_start_utf16"])
                segment_end = int(segment["base_end_utf16"])
                if segment_start < segment_cursor or segment_end > unit_end:
                    _fail(f"anchor segment range is not ordered inside unit {unit_id}")
                segment_gap = _slice_or_fail(
                    canonical_text,
                    segment_cursor,
                    segment_start,
                    label=f"anchor gap in {unit_id}",
                )
                if segment_gap.strip():
                    _fail(f"anchor segment gap contains prose in {unit_id}")
                _slice_or_fail(
                    canonical_text,
                    segment_start,
                    segment_end,
                    label=f"anchor segment {segment['anchor_segment_id']}",
                )
                segment_cursor = segment_end
            if segment_cursor != unit_end:
                _fail(f"anchor segments do not cover unit {unit_id}")

        trailing_blocks = [
            block
            for block in blocks
            if block["text_content"]
            == "Trailing prose must remain visible after every structured block."
        ]
        if len(trailing_blocks) != 1 or trailing_blocks[0]["parent_block_id"] is not None:
            _fail("trailing prose was not preserved as a separate root block")

        if not layers:
            _fail("fake pipeline produced no enhancement layers")
        for layer in layers:
            quality = dict(layer["quality_json"] or {})
            if quality.get("model_provider") != "fake":
                _fail(
                    "enhancement layer was not produced by the deterministic fake "
                    f"provider: {quality}"
                )
        for job in jobs:
            if job["status"] != "succeeded":
                _fail(
                    f"enhancement job did not succeed: "
                    f"{job['job_type']} status={job['status']} failure={job['failure_code']}"
                )
            if job["job_type"] == "generate_display_title_zh":
                profile = str((job["output_ref_json"] or {}).get("model_profile", ""))
                if "fake" not in profile:
                    _fail(f"display title job was not fake: {profile}")

            job_type = str(job["job_type"])
            job_payload = json.dumps(dict(job), ensure_ascii=False, default=str)
            if any(icon in job_payload for icon in _EXPECTED_CALLOUT_ICONS):
                _fail(f"callout display icon leaked into automatic job payload: {job_type}")
            if any(
                layer_name in job_type
                for layer_name in ("vocabulary", "grammar", "sentence")
            ):
                if _target_unit_ids(job).intersection(callout_unit_ids):
                    _fail(
                        f"T-only source_callout was targeted by {job_type}: "
                        f"{sorted(_target_unit_ids(job).intersection(callout_unit_ids))}"
                    )

        snapshot_payload = snapshot.model_dump(mode="json")
        reloaded_snapshot_payload = reloaded_snapshot.model_dump(mode="json")
        snapshot_json = json.dumps(snapshot_payload, ensure_ascii=False)
        if "```" in snapshot_json:
            _fail("snapshot unexpectedly carries raw fenced Markdown")
        stable_tree = snapshot_payload.get("stable_document_tree") or []
        if not stable_tree:
            _fail("snapshot reload has no stable document tree")
        if stable_tree != reloaded_snapshot_payload.get("stable_document_tree"):
            _fail("fresh and reloaded Stable trees are not equal")
        tree_nodes = _tree_nodes(stable_tree)
        tree_callouts = [
            node for node in tree_nodes if node.get("content_role") == "source_callout"
        ]
        if not tree_callouts:
            _fail("snapshot tree lost source_callout role")
        tree_callout_wrappers = [
            node
            for node in tree_callouts
            if node.get("canonical_text_start_utf16") is None
            and node.get("parent_block_id") is None
        ]
        if not tree_callout_wrappers:
            _fail("snapshot tree lost structural source_callout wrapper")
        tree_icon_nodes = [
            node
            for node in tree_nodes
            if node.get("text_content") in set(_EXPECTED_CALLOUT_ICONS)
        ]
        if tree_icon_nodes:
            _fail("snapshot tree contains an emoji-only canonical leaf")
        tree_wrapper_icons = [
            _json_object(node.get("payload")).get("display_icon")
            for node in tree_callout_wrappers
        ]
        if tree_wrapper_icons != _EXPECTED_CALLOUT_ICONS:
            _fail(
                "snapshot tree did not preserve display_icon on the wrapper: "
                f"{tree_wrapper_icons}"
            )
        if any(node.get("text_content") for node in tree_callout_wrappers):
            _fail("snapshot source_callout wrapper repeats child text")

        outside_g5_jobs = [
            job
            for job in all_jobs
            if str(job["job_type"]) not in _ENHANCEMENT_JOB_TYPES
        ]
        if any(job["status"] not in {"queued", "paused"} for job in outside_g5_jobs):
            _fail(
                "non-G5 jobs were consumed by the fake runner: "
                f"{[(job['job_type'], job['status']) for job in outside_g5_jobs]}"
            )

        return {
            "record_id": str(record_id),
            "user_id": str(record["user_id"]),
            "executor_mode": "fake",
            "runner_total_jobs": summary.total_jobs,
            "runner_total_ticks": summary.total_ticks,
            "runner_stopped_reason": summary.stopped_reason,
            "runner_outcome_counts": asdict(summary.outcome_counts),
            "confirmed_source": {
                "status": source["status"],
                "revision": source["revision"],
                "content_sha256": source["content_sha256"],
                "markdown_length": len(source["markdown_text"] or ""),
            },
            "stable_document": {
                "id": str(stable["id"]),
                "document_version": stable["document_version"],
                "status": stable["status"],
                "block_count": len(blocks),
                "block_types": block_types,
                "parented_block_count": sum(
                    block["parent_block_id"] is not None for block in blocks
                ),
            },
            "snapshot": {
                "last_event_sequence": snapshot.last_event_sequence,
                "stable_tree_node_count": len(stable_tree),
                "enhancement_layer_count": len(snapshot.enhancement_layers),
                "fresh_reload_equal": True,
            },
            "fake_layer_count": len(layers),
            "fake_job_count": len(jobs),
        }
    finally:
        await close_db()


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit(
            "usage: reader_markdown_g5_fake_runner.py RECORD_ID [SOURCE_SHA256]"
        )
    result = asyncio.run(
        _run(
            UUID(sys.argv[1]),
            sys.argv[2] if len(sys.argv) == 3 else None,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
