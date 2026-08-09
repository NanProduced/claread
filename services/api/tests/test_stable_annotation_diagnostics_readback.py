"""Repository-seam tests for the persisted annotation diagnostics readback.

No database: a fake connection returns canned rows for the exact queries
``load_snapshot_facts`` issues, so the tests prove the SELECT → parse →
typed-consumer chain on ``reading_bases.diagnostics_json`` plus the frozen
ownership rule — the analyzer's recomputation owns reload behavior, the
persisted payload is only an observed audit artifact.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.services.reader_orchestration.base_builder import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
)
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)
from app.services.reader_orchestration.stable_annotation_analysis import (
    ANNOTATION_RANGE_MISMATCH,
    DIAGNOSTICS_READBACK_MALFORMED,
    DIAGNOSTICS_READBACK_MATCH,
    DIAGNOSTICS_READBACK_MISMATCH,
    DIAGNOSTICS_VERSION,
    StableAnnotationAnalysis,
    StableBlockAnnotation,
    StableUnitRange,
    analyze_stable_annotations,
    empty_diagnostics_payload,
    parse_diagnostics_payload,
    readback_persisted_diagnostics,
)

BASE_TEXT = "Alpha text."
BASE_UTF16_LENGTH = utf16_code_unit_length(BASE_TEXT)
UNIT_RANGE = StableUnitRange(unit_id="u1", start_utf16=0, end_utf16=BASE_UTF16_LENGTH)


def _dirty_analysis() -> StableAnnotationAnalysis:
    return analyze_stable_annotations(
        raw_annotations=[
            StableBlockAnnotation(
                start_utf16=0,
                end_utf16=BASE_UTF16_LENGTH,
                block_type="paragraph",
                block_id="b1",
            ),
            StableBlockAnnotation(
                start_utf16=2,
                end_utf16=BASE_UTF16_LENGTH,
                block_type="paragraph",
                block_id="bogus_overlap",
            ),
        ],
        base_utf16_length=BASE_UTF16_LENGTH,
        unit_ranges=[UNIT_RANGE],
    )


class TestParseDiagnosticsPayload:
    def test_empty_versioned_object_round_trips(self):
        assert parse_diagnostics_payload(empty_diagnostics_payload()) == ()

    def test_payload_with_items_round_trips(self):
        analysis = _dirty_analysis()
        assert analysis.diagnostics, "fixture must carry diagnostics"
        parsed = parse_diagnostics_payload(analysis.diagnostics_payload())
        assert parsed == analysis.diagnostics

    def test_version_field_is_part_of_the_contract(self):
        payload = _dirty_analysis().diagnostics_payload()
        assert payload["version"] == DIAGNOSTICS_VERSION

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            [],
            "not-a-payload",
            {},
            {"version": "stable_annotation_diagnostics_v0", "items": []},
            {"version": DIAGNOSTICS_VERSION},
            {"version": DIAGNOSTICS_VERSION, "items": {}},
            {"version": DIAGNOSTICS_VERSION, "items": ["not-a-dict"]},
            {
                "version": DIAGNOSTICS_VERSION,
                "items": [{"code": "c", "severity": "s", "scope": "b", "ref_id": "r"}],
            },
            {
                "version": DIAGNOSTICS_VERSION,
                "items": [
                    {
                        "code": "c",
                        "severity": "s",
                        "scope": "b",
                        "ref_id": "r",
                        "detail": "d",
                        "extra": "e",
                    }
                ],
            },
            {
                "version": DIAGNOSTICS_VERSION,
                "items": [
                    {
                        "code": "c",
                        "severity": "s",
                        "scope": "b",
                        "ref_id": "r",
                        "detail": 42,
                    }
                ],
            },
        ],
    )
    def test_malformed_payloads_parse_to_none(self, raw: Any):
        assert parse_diagnostics_payload(raw) is None


class TestReadbackPersistedDiagnostics:
    def test_matching_payload_reads_back_as_match(self):
        analysis = _dirty_analysis()
        readback = readback_persisted_diagnostics(
            analysis.diagnostics_payload(),
            recomputed=analysis,
        )
        assert readback.status == DIAGNOSTICS_READBACK_MATCH
        assert readback.persisted == analysis.diagnostics
        assert readback.recomputed == analysis.diagnostics

    def test_diverging_payload_reads_back_as_mismatch(self):
        analysis = _dirty_analysis()
        readback = readback_persisted_diagnostics(
            empty_diagnostics_payload(),
            recomputed=analysis,
        )
        assert readback.status == DIAGNOSTICS_READBACK_MISMATCH
        assert readback.persisted == ()
        assert readback.recomputed == analysis.diagnostics

    def test_malformed_payload_reads_back_as_malformed(self):
        analysis = _dirty_analysis()
        readback = readback_persisted_diagnostics(
            {"version": "unknown", "items": []},
            recomputed=analysis,
        )
        assert readback.status == DIAGNOSTICS_READBACK_MALFORMED
        assert readback.persisted is None
        assert readback.recomputed == analysis.diagnostics


class _FakeSnapshotConn:
    """Serves canned rows for the exact load_snapshot_facts query set."""

    def __init__(
        self,
        *,
        record_row: dict[str, Any],
        input_row: dict[str, Any],
        event_row: dict[str, Any],
        unit_rows: list[dict[str, Any]],
        anchor_rows: list[dict[str, Any]],
        block_rows: list[dict[str, Any]],
    ) -> None:
        self._record_row = record_row
        self._input_row = input_row
        self._event_row = event_row
        self._unit_rows = unit_rows
        self._anchor_rows = anchor_rows
        self._block_rows = block_rows

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM reading_records" in query:
            return self._record_row
        if "FROM original_inputs" in query:
            return self._input_row
        if "FROM reader_events" in query:
            return self._event_row
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO reading_bases" not in query:
            raise AssertionError(f"unexpected execute query: {query}")
        self._record_row["diagnostics_json"] = args[12]
        return "INSERT 0 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM reading_units" in query:
            return self._unit_rows
        if "FROM anchor_segments" in query:
            return self._anchor_rows
        if "FROM stable_reading_documents" in query:
            return self._block_rows
        if "FROM enhancement_layers" in query:
            return []
        if "FROM reader_jobs" in query:
            return []
        if "FROM parsed_decisions" in query:
            return []
        raise AssertionError(f"unexpected fetch query: {query}")


def _block_row(
    block_id: str,
    start: int,
    end: int,
    *,
    parent_block_id: str | None = None,
) -> dict[str, Any]:
    return {
        "block_id": block_id,
        "parent_block_id": parent_block_id,
        "block_type": "paragraph",
        "order_index": 0 if block_id == "b1" else 1,
        "text_content": BASE_TEXT[start:end],
        "payload_json": {},
        "source_refs_json": {},
        "quality_json": {},
        "interpretation_policy_json": {},
        "block_start_utf16": start,
        "block_end_utf16": end,
    }


def _fixture_rows(
    *,
    diagnostics_json: Any,
    dirty: bool,
) -> tuple[_FakeSnapshotConn, dict[str, Any]]:
    record_id = uuid4()
    user_id = uuid4()
    base_id = uuid4()
    now = datetime.now(UTC)
    unit_hash = compute_text_range_hash(BASE_TEXT)
    record_row = {
        "id": record_id,
        "user_id": user_id,
        "source_type": "markdown",
        "title": "Sample",
        "generated_title_zh": None,
        "title_generation_status": None,
        "title_generation_error_code": None,
        "title_generation_error_message": None,
        "language": "en",
        "product_state": "readable_enhancing",
        "readiness_state": "article_ready",
        "generation": 1,
        "active_base_id": base_id,
        "reading_goal": "daily_reading",
        "reading_variant": "intermediate_reading",
        "record_created_at": now,
        "record_updated_at": now,
        "base_id": base_id,
        "record_generation": 1,
        "text": BASE_TEXT,
        "content_sha256": hashlib.sha256(BASE_TEXT.encode("utf-8")).hexdigest(),
        "content_utf16_length": BASE_UTF16_LENGTH,
        "canonicalizer_version": "exact_canonical_text_v1",
        "builder_version": "test_builder",
        "segmenter_version": "test_segmenter",
        "base_language": "en",
        "title_snapshot": "Sample",
        "navigation_json": {},
        "diagnostics_json": diagnostics_json,
        "base_status": "active",
        "base_created_at": now,
        "next_sequence": 2,
    }
    unit_rows = [
        {
            "unit_id": "u1",
            "order_index": 0,
            "unit_type": "paragraph",
            "boundary_quality": "clean",
            "base_start_utf16": 0,
            "base_end_utf16": BASE_UTF16_LENGTH,
            "text_hash": unit_hash,
            "metadata_json": {},
        }
    ]
    anchor_rows = [
        {
            "unit_id": "u1",
            "anchor_segment_id": "a1",
            "sentence_id": "a1",
            "paragraph_id": "p1",
            "order_index": 0,
            "unit_order_index": 0,
            "segment_type": "sentence",
            "boundary_quality": "clean",
            "base_start_utf16": 0,
            "base_end_utf16": BASE_UTF16_LENGTH,
            "unit_start_utf16": 0,
            "unit_end_utf16": BASE_UTF16_LENGTH,
            "text_hash": unit_hash,
        }
    ]
    block_rows = [_block_row("b1", 0, BASE_UTF16_LENGTH)]
    if dirty:
        block_rows.append(_block_row("bogus_overlap", 2, BASE_UTF16_LENGTH))
    conn = _FakeSnapshotConn(
        record_row=record_row,
        input_row={"metadata_json": {}},
        event_row={"sequence": 1, "created_at": now},
        unit_rows=unit_rows,
        anchor_rows=anchor_rows,
        block_rows=block_rows,
    )
    return conn, {"record_id": record_id, "user_id": user_id, "base_id": base_id}


@pytest.fixture
def _patch_side_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: Any, conn: Any, **kwargs: Any) -> tuple:
        return ()

    monkeypatch.setattr(
        ReaderOrchestrationRepository,
        "_load_user_assets_for_snapshot",
        _noop,
    )
    monkeypatch.setattr(
        ReaderOrchestrationRepository,
        "_load_ask_supplements_for_snapshot",
        _noop,
    )


@pytest.mark.usefixtures("_patch_side_loaders")
@pytest.mark.asyncio
class TestLoadSnapshotFactsDiagnosticsReadback:
    async def test_repository_insert_then_load_round_trip_consumes_diagnostics(self):
        conn, ids = _fixture_rows(diagnostics_json=None, dirty=True)
        build_result = replace(
            build_low_impact_reading_base(
                LowImpactReadingBaseBuildInput(
                    reading_record_id=str(ids["record_id"]),
                    base_id=str(ids["base_id"]),
                    source_text=BASE_TEXT,
                    title="Sample",
                    language="en",
                )
            ),
            annotation_analysis=_dirty_analysis(),
        )
        repo = ReaderOrchestrationRepository()

        await repo.insert_reading_base(
            conn,  # type: ignore[arg-type]
            base_id=ids["base_id"],
            build_result=build_result,
            created_at=datetime.now(UTC),
        )
        facts = await repo.load_snapshot_facts(
            conn,  # type: ignore[arg-type]
            record_id=ids["record_id"],
            user_id=ids["user_id"],
        )

        readback = facts.annotation_diagnostics_readback
        analysis = facts.build_result.annotation_analysis
        assert analysis is not None
        assert readback.status == DIAGNOSTICS_READBACK_MATCH
        assert readback.persisted == _dirty_analysis().diagnostics
        assert readback.recomputed == analysis.diagnostics

    async def test_persisted_diagnostics_are_read_and_parsed(self):
        # Round-trip: freeze persists diagnostics_payload(); reload SELECTs
        # it, parses it through the versioned contract, and the recomputed
        # analysis matches it semantically.
        conn, ids = _fixture_rows(
            diagnostics_json=_dirty_analysis().diagnostics_payload(),
            dirty=True,
        )
        repo = ReaderOrchestrationRepository()
        facts = await repo.load_snapshot_facts(
            conn,  # type: ignore[arg-type]
            record_id=ids["record_id"],
            user_id=ids["user_id"],
        )
        readback = facts.annotation_diagnostics_readback
        assert readback.status == DIAGNOSTICS_READBACK_MATCH
        assert readback.persisted is not None
        assert [item.code for item in readback.persisted] == [ANNOTATION_RANGE_MISMATCH]
        # The recomputed analysis still owns behavior.
        analysis = facts.build_result.annotation_analysis
        assert analysis is not None
        assert [(o.unit_id, o.reason_code) for o in analysis.policy_overrides] == [
            ("u1", ANNOTATION_RANGE_MISMATCH),
        ]
        assert readback.recomputed == analysis.diagnostics

    async def test_clean_fixture_reads_back_empty_match(self):
        conn, ids = _fixture_rows(
            diagnostics_json=empty_diagnostics_payload(),
            dirty=False,
        )
        repo = ReaderOrchestrationRepository()
        facts = await repo.load_snapshot_facts(
            conn,  # type: ignore[arg-type]
            record_id=ids["record_id"],
            user_id=ids["user_id"],
        )
        readback = facts.annotation_diagnostics_readback
        assert readback.status == DIAGNOSTICS_READBACK_MATCH
        assert readback.persisted == ()
        assert facts.build_result.annotation_analysis is not None
        assert facts.build_result.annotation_analysis.diagnostics == ()

    async def test_mismatch_is_observed_but_recompute_owns_behavior(self):
        # Persisted payload claims no diagnostics; the stored blocks say
        # otherwise. The override still comes from the recomputation — the
        # persisted artifact never decides policy.
        conn, ids = _fixture_rows(
            diagnostics_json=empty_diagnostics_payload(),
            dirty=True,
        )
        repo = ReaderOrchestrationRepository()
        facts = await repo.load_snapshot_facts(
            conn,  # type: ignore[arg-type]
            record_id=ids["record_id"],
            user_id=ids["user_id"],
        )
        readback = facts.annotation_diagnostics_readback
        assert readback.status == DIAGNOSTICS_READBACK_MISMATCH
        assert readback.persisted == ()
        analysis = facts.build_result.annotation_analysis
        assert analysis is not None
        assert [item.code for item in analysis.diagnostics] == [ANNOTATION_RANGE_MISMATCH]
        assert [(o.unit_id, o.reason_code) for o in analysis.policy_overrides] == [
            ("u1", ANNOTATION_RANGE_MISMATCH),
        ]

    async def test_malformed_persisted_payload_never_breaks_reload(self):
        conn, ids = _fixture_rows(
            diagnostics_json={"version": "stable_annotation_diagnostics_v0", "items": []},
            dirty=True,
        )
        repo = ReaderOrchestrationRepository()
        facts = await repo.load_snapshot_facts(
            conn,  # type: ignore[arg-type]
            record_id=ids["record_id"],
            user_id=ids["user_id"],
        )
        readback = facts.annotation_diagnostics_readback
        assert readback.status == DIAGNOSTICS_READBACK_MALFORMED
        assert readback.persisted is None
        analysis = facts.build_result.annotation_analysis
        assert analysis is not None
        assert [(o.unit_id, o.reason_code) for o in analysis.policy_overrides] == [
            ("u1", ANNOTATION_RANGE_MISMATCH),
        ]

    async def test_string_serialized_payload_is_tolerated(self):
        # Legacy rows may surface jsonb as a raw string; the readback goes
        # through the same tolerant object normalization as other columns.
        import json

        conn, ids = _fixture_rows(
            diagnostics_json=json.dumps(_dirty_analysis().diagnostics_payload()),
            dirty=True,
        )
        repo = ReaderOrchestrationRepository()
        facts = await repo.load_snapshot_facts(
            conn,  # type: ignore[arg-type]
            record_id=ids["record_id"],
            user_id=ids["user_id"],
        )
        assert facts.annotation_diagnostics_readback.status == DIAGNOSTICS_READBACK_MATCH
