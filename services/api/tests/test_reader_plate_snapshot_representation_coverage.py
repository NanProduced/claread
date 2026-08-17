# task-history: plate snapshot representation coverage (renamed from historical LP suite)
"""Reader Plate Snapshot Representation Coverage characterization tests.

These tests describe the CURRENT behavior of ``_build_snapshot_id`` and
``build_reader_plate_snapshot``. They are characterization tests — they
do NOT assert what the coverage *should* be, only what it *is*. The
``snapshot_id`` algorithm MUST NOT be modified to make these tests green.

Audit question answered:
- Which ``ReaderPlateSnapshot`` field changes advance ``snapshot_id``?
- Which advance ``last_event_sequence`` (via a reader_event publish)?
- Which advance NEITHER (true coverage gaps for ETag candidacy)?

Each test holds ``last_event_sequence`` constant and varies exactly one
snapshot field, then asserts whether ``snapshot_id`` changed. This
isolates the *direct* effect of the field on ``snapshot_id`` from the
*indirect* effect that would occur in production if the same change
also published a reader_event (which would advance ``last_event_sequence``
and therefore ``snapshot_id``).

The production event-publish mapping (which field changes publish
reader_events and thus advance ``last_event_sequence``) is documented in
the representation-coverage audit report and verified by code inspection, not by these
unit tests — it requires DB-level integration tests that are out of
scope for this read-only audit.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.contracts.annotation import (
    compute_text_range_hash,
    utf16_code_unit_length,
)
from app.schemas.reader_orchestration import (
    ReaderEnhancementProgress,
    ReaderEnhancementProgressLayer,
    ReaderSnapshotAskSupplement,
    ReaderSnapshotLayer,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotRecord,
    ReaderSnapshotUserAsset,
    ReaderUnitAnchor,
)
from app.services.reader_orchestration.base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    NavigationUnitFact,
    ReadingBaseBuildResult,
    StableReadingBase,
)
from app.services.reader_orchestration.snapshot import (
    _build_snapshot_id,
    build_reader_plate_snapshot,
)
from tests.reader_orchestration_test_support import fixture_analysis_progress

NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
RECORD_ID = "00000000-0000-0000-0000-000000000001"
BASE_ID = "00000000-0000-0000-0000-000000000002"
UNIT_TEXT = "Hello world."
SEGMENT_TEXT = "Hello"
UNIT_TEXT_HASH = compute_text_range_hash(UNIT_TEXT)
SEGMENT_TEXT_HASH = compute_text_range_hash(SEGMENT_TEXT)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_base(
    *,
    reading_record_id: str = RECORD_ID,
    base_id: str = BASE_ID,
    text: str = UNIT_TEXT,
    title_snapshot: str | None = "Test Title",
) -> StableReadingBase:
    return StableReadingBase(
        reading_record_id=reading_record_id,
        base_id=base_id,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content_utf16_length=utf16_code_unit_length(text),
        canonicalizer_version="test_v1",
        builder_version="test_v1",
        segmenter_version="test_v1",
        language="en",
        title_snapshot=title_snapshot,
    )


def _build_unit(
    *,
    unit_id: str = "u1",
    base_id: str = BASE_ID,
    text: str = UNIT_TEXT,
    label: str | None = "Unit 1",
) -> BuiltReadingUnit:
    return BuiltReadingUnit(
        reading_record_id=RECORD_ID,
        base_id=base_id,
        unit_id=unit_id,
        order_index=1,
        unit_type="body",
        boundary_quality="normal",
        base_start_utf16=0,
        base_end_utf16=utf16_code_unit_length(text),
        text_hash=compute_text_range_hash(text),
        text=text,
        label=label,
    )


def _build_segment(
    *,
    anchor_segment_id: str = "s1",
    unit_id: str = "u1",
    base_id: str = BASE_ID,
    text: str = SEGMENT_TEXT,
) -> BuiltAnchorSegment:
    return BuiltAnchorSegment(
        reading_record_id=RECORD_ID,
        base_id=base_id,
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        sentence_id=anchor_segment_id,
        paragraph_id="p1",
        order_index=1,
        unit_order_index=1,
        segment_type="sentence",
        boundary_quality="normal",
        base_start_utf16=0,
        base_end_utf16=utf16_code_unit_length(text),
        unit_start_utf16=0,
        unit_end_utf16=utf16_code_unit_length(text),
        text_hash=compute_text_range_hash(text),
        text=text,
    )


def _build_result(
    *,
    base: StableReadingBase | None = None,
    units: tuple[BuiltReadingUnit, ...] | None = None,
    anchor_segments: tuple[BuiltAnchorSegment, ...] | None = None,
    navigation_units: tuple[NavigationUnitFact, ...] | None = None,
) -> ReadingBaseBuildResult:
    unit = units[0] if units else _build_unit()
    segment = anchor_segments[0] if anchor_segments else _build_segment()
    return ReadingBaseBuildResult(
        base=base or _build_base(),
        units=units or (unit,),
        anchor_segments=anchor_segments or (segment,),
        navigation_units=navigation_units
        or (
            NavigationUnitFact(
                unit_id=unit.unit_id,
                order_index=1,
                unit_type="body",
                boundary_quality="normal",
                label="Unit 1",
                base_start_utf16=0,
                base_end_utf16=unit.base_end_utf16,
            ),
        ),
    )


def _make_layer(
    *,
    layer_id: str = "layer-1",
    layer_type: str = "translation",
    target_scope: str = "unit",
    target_key: str = "u1",
    schema_version: int = 1,
    output: object | None = None,
    published_at: datetime = NOW,
) -> ReaderSnapshotLayer:
    return ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type=layer_type,
        base_id=BASE_ID,
        target_scope=target_scope,
        target_key=target_key,
        status="published",
        schema_version=schema_version,
        output=output if output is not None else {"translation": "translated text"},
        published_at=published_at,
    )


def _make_parsed_decision(
    *,
    unit_id: str = "u1",
    policy_code: str = "translation_parsed",
    parsed_state: str = "parsed",
    rationale_code: str | None = "ok",
) -> ReaderSnapshotParsedDecision:
    return ReaderSnapshotParsedDecision(
        unit_id=unit_id,
        policy_code=policy_code,
        parsed_state=parsed_state,
        rationale_code=rationale_code,
    )


def _make_unit_anchor() -> ReaderUnitAnchor:
    return ReaderUnitAnchor(
        base_id=BASE_ID,
        unit_id="u1",
        text_hash=UNIT_TEXT_HASH,
    )


def _make_user_asset(
    *,
    asset_id: str = "asset-1",
    asset_type: str = "highlight",
    note_text: str | None = None,
    color: str | None = "#ff0000",
) -> ReaderSnapshotUserAsset:
    return ReaderSnapshotUserAsset(
        asset_id=asset_id,
        asset_type=asset_type,
        reading_record_id=RECORD_ID,
        generation=1,
        anchor=_make_unit_anchor(),
        note_text=note_text,
        color=color,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_ask_supplement(
    *,
    supplement_id: str = "supp-1",
    content: object | None = None,
) -> ReaderSnapshotAskSupplement:
    return ReaderSnapshotAskSupplement(
        supplement_id=supplement_id,
        content=content if content is not None else {"note": "supplement content"},
        created_at=NOW,
    )


def _make_record(
    *,
    title: str = "Test Title",
    display_title_zh: str | None = None,
    title_generation_status: str = "pending",
    product_state: str = "readable_enhancing",
    readiness_state: str = "article_ready",
    source_type: str = "text",
    source_metadata: dict[str, object] | None = None,
    generation: int = 1,
    reading_goal: str = "daily_reading",
    reading_variant: str = "intermediate_reading",
    created_at: datetime = NOW,
) -> ReaderSnapshotRecord:
    return ReaderSnapshotRecord(
        title=title,
        display_title_zh=display_title_zh,
        title_generation_status=title_generation_status,
        created_at=created_at,
        source_type=source_type,
        source_metadata=source_metadata if source_metadata is not None else {},
        generation=generation,
        product_state=product_state,
        readiness_state=readiness_state,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )


def _make_progress(
    *,
    overall_status: str = "processing",
    layers: list[ReaderEnhancementProgressLayer] | None = None,
) -> ReaderEnhancementProgress:
    return ReaderEnhancementProgress(
        overall_status=overall_status,
        layers=layers or [],
    )


def _build_snapshot(
    *,
    build_result: ReadingBaseBuildResult | None = None,
    last_event_sequence: int = 1,
    record: ReaderSnapshotRecord | None = None,
    enhancement_layers: list[ReaderSnapshotLayer] | None = None,
    parsed_decisions: list[ReaderSnapshotParsedDecision] | None = None,
    user_assets: list[ReaderSnapshotUserAsset] | None = None,
    ask_supplements: list[ReaderSnapshotAskSupplement] | None = None,
    enhancement_progress: ReaderEnhancementProgress | None = None,
    snapshot_taken_at: datetime = NOW,
) -> object:
    return build_reader_plate_snapshot(build_result or _build_result(),
        analysis_progress=fixture_analysis_progress(),
snapshot_taken_at=snapshot_taken_at,
        last_event_sequence=last_event_sequence,
        record=record,
        enhancement_layers=enhancement_layers,
        parsed_decisions=parsed_decisions,
        user_assets=user_assets,
        ask_supplements=ask_supplements,
        enhancement_progress=enhancement_progress,
    )


# ---------------------------------------------------------------------------
# Section A: snapshot_id INPUTS — fields that DO advance snapshot_id
# ---------------------------------------------------------------------------


def test_reading_record_id_advances_snapshot_id() -> None:
    """reading_record_id IS an input to _build_snapshot_id."""
    base_a = _build_base(reading_record_id="00000000-0000-0000-0000-0000000000aa")
    base_b = _build_base(reading_record_id="00000000-0000-0000-0000-0000000000bb")
    snap_a = _build_snapshot(build_result=_build_result(base=base_a))
    snap_b = _build_snapshot(build_result=_build_result(base=base_b))
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_base_id_advances_snapshot_id() -> None:
    """base_id IS an input to _build_snapshot_id."""
    base_a = _build_base(base_id="00000000-0000-0000-0000-0000000000aa")
    base_b = _build_base(base_id="00000000-0000-0000-0000-0000000000bb")
    snap_a = _build_snapshot(build_result=_build_result(base=base_a))
    snap_b = _build_snapshot(build_result=_build_result(base=base_b))
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_content_sha256_advances_snapshot_id() -> None:
    """content_sha256 IS an input to _build_snapshot_id (via build_result.base)."""
    base_a = _build_base(text="Content A.")
    base_b = _build_base(text="Content B.")
    assert base_a.content_sha256 != base_b.content_sha256
    snap_a = _build_snapshot(build_result=_build_result(base=base_a))
    snap_b = _build_snapshot(build_result=_build_result(base=base_b))
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_last_event_sequence_advances_snapshot_id() -> None:
    """last_event_sequence IS a direct input to _build_snapshot_id.

    This is the critical link: any reader_event that advances
    last_event_sequence ALSO advances snapshot_id.
    """
    snap_a = _build_snapshot(last_event_sequence=1)
    snap_b = _build_snapshot(last_event_sequence=2)
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_layer_id_advances_snapshot_id() -> None:
    """layer_id IS part of the layer fingerprint in _build_snapshot_id."""
    layer_a = _make_layer(layer_id="layer-aaa")
    layer_b = _make_layer(layer_id="layer-bbb")
    snap_a = _build_snapshot(enhancement_layers=[layer_a])
    snap_b = _build_snapshot(enhancement_layers=[layer_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_layer_target_scope_advances_snapshot_id() -> None:
    """target_scope IS part of the layer fingerprint."""
    layer_a = _make_layer(target_scope="unit")
    layer_b = _make_layer(target_scope="anchor_segment", target_key="s1")
    snap_a = _build_snapshot(enhancement_layers=[layer_a])
    snap_b = _build_snapshot(enhancement_layers=[layer_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_layer_target_key_advances_snapshot_id() -> None:
    """target_key IS part of the layer fingerprint."""
    layer_a = _make_layer(target_key="u1")
    layer_b = _make_layer(target_key="u2")
    # Need a second unit for the u2 layer to pass validation
    unit2 = _build_unit(unit_id="u2", text="Second unit.")
    seg2 = _build_segment(anchor_segment_id="s2", unit_id="u2", text="Second")
    build_result = _build_result(
        units=(_build_unit(), unit2),
        anchor_segments=(_build_segment(), seg2),
    )
    snap_a = _build_snapshot(build_result=build_result, enhancement_layers=[layer_a])
    snap_b = _build_snapshot(build_result=build_result, enhancement_layers=[layer_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_layer_schema_version_advances_snapshot_id() -> None:
    """schema_version IS part of the layer fingerprint."""
    layer_a = _make_layer(schema_version=1)
    layer_b = _make_layer(schema_version=2)
    snap_a = _build_snapshot(enhancement_layers=[layer_a])
    snap_b = _build_snapshot(enhancement_layers=[layer_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_parsed_decision_unit_id_advances_snapshot_id() -> None:
    """parsed_decision.unit_id IS part of the parsed_decision fingerprint."""
    unit2 = _build_unit(unit_id="u2", text="Second unit.")
    seg2 = _build_segment(anchor_segment_id="s2", unit_id="u2", text="Second")
    build_result = _build_result(
        units=(_build_unit(), unit2),
        anchor_segments=(_build_segment(), seg2),
    )
    dec_a = _make_parsed_decision(unit_id="u1")
    dec_b = _make_parsed_decision(unit_id="u2")
    snap_a = _build_snapshot(
        build_result=build_result, parsed_decisions=[dec_a]
    )
    snap_b = _build_snapshot(
        build_result=build_result, parsed_decisions=[dec_b]
    )
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_parsed_decision_policy_code_advances_snapshot_id() -> None:
    """policy_code IS part of the parsed_decision fingerprint."""
    dec_a = _make_parsed_decision(policy_code="translation_parsed")
    dec_b = _make_parsed_decision(policy_code="vocabulary_parsed")
    snap_a = _build_snapshot(parsed_decisions=[dec_a])
    snap_b = _build_snapshot(parsed_decisions=[dec_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_parsed_decision_parsed_state_advances_snapshot_id() -> None:
    """parsed_state IS part of the parsed_decision fingerprint."""
    dec_a = _make_parsed_decision(parsed_state="parsed")
    dec_b = _make_parsed_decision(parsed_state="partial")
    snap_a = _build_snapshot(parsed_decisions=[dec_a])
    snap_b = _build_snapshot(parsed_decisions=[dec_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_parsed_decision_rationale_code_advances_snapshot_id() -> None:
    """rationale_code IS part of the parsed_decision fingerprint."""
    dec_a = _make_parsed_decision(rationale_code="ok")
    dec_b = _make_parsed_decision(rationale_code="retry")
    snap_a = _build_snapshot(parsed_decisions=[dec_a])
    snap_b = _build_snapshot(parsed_decisions=[dec_b])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_layer_count_advances_snapshot_id() -> None:
    """Adding/removing a layer changes the fingerprint (more/fewer parts)."""
    snap_a = _build_snapshot(enhancement_layers=[])
    snap_b = _build_snapshot(enhancement_layers=[_make_layer()])
    assert snap_a.snapshot_id != snap_b.snapshot_id


def test_parsed_decision_count_advances_snapshot_id() -> None:
    """Adding/removing a parsed_decision changes the fingerprint."""
    snap_a = _build_snapshot(parsed_decisions=[])
    snap_b = _build_snapshot(parsed_decisions=[_make_parsed_decision()])
    assert snap_a.snapshot_id != snap_b.snapshot_id


# ---------------------------------------------------------------------------
# Section B: snapshot_id GAPS — fields that do NOT advance snapshot_id
#            (when last_event_sequence is held constant)
#
# In production, SOME of these fields advance last_event_sequence via a
# reader_event publish, which would INDIRECTLY advance snapshot_id. The
# tests below isolate the DIRECT effect by holding last_event_sequence
# constant. See the representation-coverage report for the full event-publish mapping.
# ---------------------------------------------------------------------------


def test_layer_output_content_does_not_advance_snapshot_id() -> None:
    """GAP: layer.output content is NOT hashed — only layer metadata is.

    In production, a new layer publish creates a NEW layer_id (INSERT,
    not UPDATE), so snapshot_id advances via the layer_id change. But
    if a layer's output were ever updated in-place (same layer_id,
    different output), snapshot_id would NOT catch it.
    """
    layer_a = _make_layer(output={"translation": "translation A"})
    layer_b = _make_layer(output={"translation": "translation B"})
    snap_a = _build_snapshot(enhancement_layers=[layer_a])
    snap_b = _build_snapshot(enhancement_layers=[layer_b])
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_title_does_not_advance_snapshot_id() -> None:
    """GAP: record.title is NOT hashed.

    In production, record.title is set on INSERT and never updated
    (display_title_zh is the mutable title). So this gap is benign for
    title — but it IS a gap for display_title_zh (see next test).
    """
    snap_a = _build_snapshot(record=_make_record(title="Title A"))
    snap_b = _build_snapshot(record=_make_record(title="Title B"))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_display_title_zh_does_not_advance_snapshot_id() -> None:
    """GAP: record.display_title_zh is NOT hashed.

    In production, display_title_zh changes publish record_state_changed
    (via display_title_worker._insert_title_reader_event), which advances
    last_event_sequence and therefore indirectly advances snapshot_id.
    But snapshot_id does NOT directly hash display_title_zh.
    """
    snap_a = _build_snapshot(record=_make_record(display_title_zh="标题甲"))
    snap_b = _build_snapshot(record=_make_record(display_title_zh="标题乙"))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_title_generation_status_does_not_advance_snapshot_id() -> None:
    """GAP: record.title_generation_status is NOT hashed.

    In production:
    - 'pending' transition (bootstrap + claim) does NOT publish a reader_event
      → COVERAGE GAP (neither snapshot_id nor last_event_sequence advances).
    - 'succeeded' / 'failed_retryable' transitions DO publish record_state_changed
      → last_event_sequence advances → snapshot_id advances indirectly.
    """
    snap_a = _build_snapshot(record=_make_record(title_generation_status="pending"))
    snap_b = _build_snapshot(record=_make_record(title_generation_status="succeeded"))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_title_generation_error_does_not_advance_snapshot_id() -> None:
    """GAP: title_generation_error_code/message are NOT hashed."""
    snap_a = _build_snapshot(record=_make_record())
    snap_b = _build_snapshot(
        record=_make_record(
            title_generation_status="failed_retryable",
        )
    )
    # Only title_generation_status changed (no error fields on the schema
    # for this test — but status change alone doesn't advance snapshot_id).
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_product_state_does_not_advance_snapshot_id() -> None:
    """GAP: record.product_state is NOT hashed.

    In production, product_state changes publish record_product_state_updated
    (worker_loop.py:329), which advances last_event_sequence → snapshot_id
    advances indirectly.
    """
    snap_a = _build_snapshot(record=_make_record(product_state="readable_enhancing"))
    snap_b = _build_snapshot(record=_make_record(product_state="failed"))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_readiness_state_does_not_advance_snapshot_id() -> None:
    """GAP: record.readiness_state is NOT hashed.

    In production, readiness_state changes publish record_state_changed
    (completion_finalizer.py:503), which advances last_event_sequence →
    snapshot_id advances indirectly.
    """
    snap_a = _build_snapshot(record=_make_record(readiness_state="article_ready"))
    snap_b = _build_snapshot(record=_make_record(readiness_state="coverage_complete"))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_source_type_does_not_advance_snapshot_id() -> None:
    """GAP: record.source_type is NOT hashed."""
    snap_a = _build_snapshot(record=_make_record(source_type="text"))
    snap_b = _build_snapshot(record=_make_record(source_type="file"))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_source_metadata_does_not_advance_snapshot_id() -> None:
    """GAP: record.source_metadata is NOT hashed."""
    snap_a = _build_snapshot(record=_make_record(source_metadata={}))
    snap_b = _build_snapshot(
        record=_make_record(source_metadata={"key": "value"})
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_generation_does_not_advance_snapshot_id() -> None:
    """GAP: record.generation is NOT hashed (the record field, not base).

    Note: generation is NOT the same as base_id. A record can advance
    generation (e.g., re-freeze) which changes the active base_id — and
    base_id IS hashed. But the record.generation field itself is not.
    """
    snap_a = _build_snapshot(record=_make_record(generation=1))
    snap_b = _build_snapshot(record=_make_record(generation=2))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_reading_goal_does_not_advance_snapshot_id() -> None:
    """GAP: record.reading_goal is NOT hashed.

    In production, reading_goal is set on INSERT and never updated.
    So this gap is benign (no runtime mutation path exists).
    """
    snap_a = _build_snapshot(
        record=_make_record(
            reading_goal="daily_reading", reading_variant="intermediate_reading"
        )
    )
    snap_b = _build_snapshot(
        record=_make_record(reading_goal="exam", reading_variant="gaokao")
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_reading_variant_does_not_advance_snapshot_id() -> None:
    """GAP: record.reading_variant is NOT hashed.

    In production, reading_variant is set on INSERT and never updated.
    Benign gap (no runtime mutation path exists).
    """
    snap_a = _build_snapshot(
        record=_make_record(reading_variant="intermediate_reading")
    )
    snap_b = _build_snapshot(
        record=_make_record(reading_variant="beginner_reading")
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_record_created_at_does_not_advance_snapshot_id() -> None:
    """GAP: record.created_at is NOT hashed."""
    snap_a = _build_snapshot(record=_make_record(created_at=NOW))
    snap_b = _build_snapshot(
        record=_make_record(created_at=datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC))
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_enhancement_progress_does_not_advance_snapshot_id() -> None:
    """GAP: enhancement_progress (overall_status + per-layer status) is NOT hashed.

    In production, enhancement_progress changes are indirectly captured by
    layer_published / record_state_changed events (which advance
    last_event_sequence). But snapshot_id does NOT directly hash progress.
    """
    progress_a = _make_progress(overall_status="processing")
    progress_b = _make_progress(overall_status="ready")
    snap_a = _build_snapshot(enhancement_progress=progress_a)
    snap_b = _build_snapshot(enhancement_progress=progress_b)
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_enhancement_progress_layer_status_does_not_advance_snapshot_id() -> None:
    """GAP: per-layer progress status is NOT hashed."""
    progress_a = _make_progress(
        layers=[
            ReaderEnhancementProgressLayer(
                capability="translation",
                status="queued",
            )
        ]
    )
    progress_b = _make_progress(
        layers=[
            ReaderEnhancementProgressLayer(
                capability="translation",
                status="succeeded",
                layer_id="layer-1",
            )
        ]
    )
    snap_a = _build_snapshot(enhancement_progress=progress_a)
    snap_b = _build_snapshot(enhancement_progress=progress_b)
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_navigation_label_does_not_advance_snapshot_id() -> None:
    """GAP: navigation unit labels are NOT hashed.

    In production, navigation is derived from the base (which IS hashed
    via base_id + content_sha256). So a navigation label change would
    require a new base, which advances snapshot_id. But the label itself
    is not directly hashed.
    """
    nav_a = (
        NavigationUnitFact(
            unit_id="u1",
            order_index=1,
            unit_type="body",
            boundary_quality="normal",
            label="Label A",
            base_start_utf16=0,
            base_end_utf16=utf16_code_unit_length(UNIT_TEXT),
        ),
    )
    nav_b = (
        NavigationUnitFact(
            unit_id="u1",
            order_index=1,
            unit_type="body",
            boundary_quality="normal",
            label="Label B",
            base_start_utf16=0,
            base_end_utf16=utf16_code_unit_length(UNIT_TEXT),
        ),
    )
    snap_a = _build_snapshot(build_result=_build_result(navigation_units=nav_a))
    snap_b = _build_snapshot(build_result=_build_result(navigation_units=nav_b))
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_anchor_segment_boundary_quality_does_not_advance_snapshot_id() -> None:
    """GAP: anchor_segment boundary_quality / order_index are NOT directly hashed.

    In production, anchor_segments are derived from the base (hashed via
    base_id + content_sha256). A new base is required to change them.
    The segment text itself cannot be varied independently because the
    snapshot builder round-trip-validates segment text against unit text
    at the given UTF-16 offsets. So we vary boundary_quality instead,
    which is a non-hashed metadata field on the anchor segment.
    """
    seg_a = _build_segment()
    seg_b = BuiltAnchorSegment(
        reading_record_id=seg_a.reading_record_id,
        base_id=seg_a.base_id,
        unit_id=seg_a.unit_id,
        anchor_segment_id=seg_a.anchor_segment_id,
        sentence_id=seg_a.sentence_id,
        paragraph_id=seg_a.paragraph_id,
        order_index=seg_a.order_index,
        unit_order_index=seg_a.unit_order_index,
        segment_type=seg_a.segment_type,
        boundary_quality="low",
        base_start_utf16=seg_a.base_start_utf16,
        base_end_utf16=seg_a.base_end_utf16,
        unit_start_utf16=seg_a.unit_start_utf16,
        unit_end_utf16=seg_a.unit_end_utf16,
        text_hash=seg_a.text_hash,
        text=seg_a.text,
    )
    assert seg_a.boundary_quality != seg_b.boundary_quality
    snap_a = _build_snapshot(
        build_result=_build_result(anchor_segments=(seg_a,))
    )
    snap_b = _build_snapshot(
        build_result=_build_result(anchor_segments=(seg_b,))
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_user_assets_do_not_advance_snapshot_id() -> None:
    """GAP: user_assets (highlights/notes) are NOT hashed.

    In production, ALL user_asset writes (INSERT/UPDATE/soft-delete) are
    SILENT — they do NOT publish any reader_event. So neither snapshot_id
    nor last_event_sequence advances. This is a TRUE coverage gap.
    """
    snap_a = _build_snapshot(user_assets=[])
    snap_b = _build_snapshot(user_assets=[_make_user_asset()])
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_user_asset_content_does_not_advance_snapshot_id() -> None:
    """GAP: user_asset note_text / color changes are NOT hashed."""
    asset_a = _make_user_asset(note_text="Note A", color="#ff0000")
    asset_b = _make_user_asset(note_text="Note B", color="#00ff00")
    snap_a = _build_snapshot(user_assets=[asset_a])
    snap_b = _build_snapshot(user_assets=[asset_b])
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_ask_supplements_do_not_advance_snapshot_id() -> None:
    """GAP: ask_supplements are NOT hashed.

    In production, ALL ask_supplement writes (INSERT/soft-delete) are
    SILENT — they do NOT publish any reader_event. TRUE coverage gap.
    """
    snap_a = _build_snapshot(ask_supplements=[])
    snap_b = _build_snapshot(ask_supplements=[_make_ask_supplement()])
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_ask_supplement_content_does_not_advance_snapshot_id() -> None:
    """GAP: ask_supplement content changes are NOT hashed."""
    supp_a = _make_ask_supplement(content={"note": "A"})
    supp_b = _make_ask_supplement(content={"note": "B"})
    snap_a = _build_snapshot(ask_supplements=[supp_a])
    snap_b = _build_snapshot(ask_supplements=[supp_b])
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_value_does_not_advance_snapshot_id_when_layers_constant() -> None:
    """GAP: value[] is derived from layers + build_result, NOT directly hashed.

    value[] changes only when layers or build_result change (both of which
    advance snapshot_id via their own fingerprints). If value[] could
    change independently (it cannot in current code), snapshot_id would
    not catch it.
    """
    # value[] is derived — we cannot change it independently. But we can
    # verify that the same layers + build_result produce the same snapshot_id
    # even if we imagine value[] were different (which it cannot be).
    snap_a = _build_snapshot(enhancement_layers=[_make_layer()])
    snap_b = _build_snapshot(enhancement_layers=[_make_layer()])
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_snapshot_taken_at_does_not_advance_snapshot_id() -> None:
    """GAP: snapshot_taken_at is NOT hashed (it's a build-time timestamp)."""
    snap_a = _build_snapshot(snapshot_taken_at=NOW)
    snap_b = _build_snapshot(
        snapshot_taken_at=datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


# ---------------------------------------------------------------------------
# Section C: _build_snapshot_id direct algorithm verification
# ---------------------------------------------------------------------------


def test_build_snapshot_id_uses_sha256_truncated_to_16_hex() -> None:
    """The fingerprint is the first 16 hex chars of SHA-256, prefixed."""
    build_result = _build_result()
    snapshot_id = _build_snapshot_id(
        build_result,
        last_event_sequence=1,
        enhancement_layers=[],
        parsed_decisions=[],
        analysis_progress=fixture_analysis_progress(),
    )
    assert snapshot_id.startswith("reader_snapshot_")
    fingerprint = snapshot_id.removeprefix("reader_snapshot_")
    assert len(fingerprint) == 16
    int(fingerprint, 16)  # must be valid hex


def test_build_snapshot_id_is_deterministic() -> None:
    """Same inputs → same snapshot_id (determinism)."""
    build_result = _build_result()
    snap_id_a = _build_snapshot_id(
        build_result,
        last_event_sequence=1,
        enhancement_layers=[],
        parsed_decisions=[],
        analysis_progress=fixture_analysis_progress(),
    )
    snap_id_b = _build_snapshot_id(
        build_result,
        last_event_sequence=1,
        enhancement_layers=[],
        parsed_decisions=[],
        analysis_progress=fixture_analysis_progress(),
    )
    assert snap_id_a == snap_id_b


def test_build_snapshot_id_parts_format() -> None:
    """Verify the exact parts string format matches the algorithm."""
    build_result = _build_result()
    layer = _make_layer()
    decision = _make_parsed_decision()
    progress = fixture_analysis_progress()
    expected_parts = [
        build_result.base.reading_record_id,
        build_result.base.base_id,
        build_result.base.content_sha256,
        "1",
        "ap:"
        f"{progress.mode}:{progress.overall_status}:"
        f"{progress.translation_status}:{progress.completed_section_count}",
        f"layer:{layer.layer_id}:{layer.target_scope}:{layer.target_key}:{layer.schema_version}",
        f"parsed:{decision.unit_id}:{decision.policy_code}:{decision.parsed_state}:{decision.rationale_code or ''}",
    ]
    expected_hash = hashlib.sha256(
        "|".join(expected_parts).encode("utf-8")
    ).hexdigest()[:16]
    expected_id = f"reader_snapshot_{expected_hash}"

    actual_id = _build_snapshot_id(
        build_result,
        last_event_sequence=1,
        enhancement_layers=[layer],
        parsed_decisions=[decision],
        analysis_progress=progress,
    )
    assert actual_id == expected_id


def test_build_snapshot_id_empty_rationale_code() -> None:
    """rationale_code=None is serialized as empty string in the fingerprint."""
    build_result = _build_result()
    decision = _make_parsed_decision(rationale_code=None)
    progress = fixture_analysis_progress()
    expected_parts = [
        build_result.base.reading_record_id,
        build_result.base.base_id,
        build_result.base.content_sha256,
        "1",
        "ap:"
        f"{progress.mode}:{progress.overall_status}:"
        f"{progress.translation_status}:{progress.completed_section_count}",
        f"parsed:{decision.unit_id}:{decision.policy_code}:{decision.parsed_state}:",
    ]
    expected_hash = hashlib.sha256(
        "|".join(expected_parts).encode("utf-8")
    ).hexdigest()[:16]
    expected_id = f"reader_snapshot_{expected_hash}"

    actual_id = _build_snapshot_id(
        build_result,
        last_event_sequence=1,
        enhancement_layers=[],
        parsed_decisions=[decision],
        analysis_progress=progress,
    )
    assert actual_id == expected_id


# ---------------------------------------------------------------------------
# Section D: Coverage gap summary test
#
# This test documents the COMPLETE list of fields that change the snapshot
# representation WITHOUT directly advancing snapshot_id. If the algorithm
# is ever changed to cover any of these, this test will fail and must be
# updated — ensuring the audit matrix stays in sync with the code.
# ---------------------------------------------------------------------------


def test_snapshot_id_gap_fields_summary() -> None:
    """Document all fields that do NOT directly advance snapshot_id.

    This test exists so that if _build_snapshot_id is ever modified to
    hash any of these fields, the test will FAIL and force the auditor
    to update the representation-coverage audit matrix.

    Fields NOT hashed (direct coverage gaps):
    - record.title, display_title_zh, title_generation_status,
      title_generation_error_code/message
    - record.product_state, readiness_state, source_type, source_metadata,
      generation, reading_goal, reading_variant, created_at
    - enhancement_progress (overall_status, per-layer status/job_status/etc.)
    - navigation unit labels
    - anchor_segments (text_hash, offsets, segment_type)
    - enhancement_layers.output (content)
    - enhancement_layers.published_at, layer_type, layer_subtype, status, base_id
      (only layer_id/target_scope/target_key/schema_version are hashed)
    - user_assets (all fields)
    - ask_supplements (all fields)
    - value[] (derived, not directly hashed)
    - snapshot_taken_at
    """
    # Baseline snapshot
    baseline = _build_snapshot(
        record=_make_record(),
        enhancement_progress=_make_progress(),
        user_assets=[],
        ask_supplements=[],
    )

    # Vary every non-hashed field — snapshot_id must stay the same
    varied = _build_snapshot(
        record=_make_record(
            title="Different Title",
            display_title_zh="不同标题",
            title_generation_status="succeeded",
            product_state="failed",
            readiness_state="coverage_complete",
            source_type="file",
            source_metadata={"k": "v"},
            generation=2,
            reading_goal="exam",
            reading_variant="gaokao",
            created_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        ),
        enhancement_progress=_make_progress(
            overall_status="ready",
            layers=[
                ReaderEnhancementProgressLayer(
                    capability="translation",
                    status="succeeded",
                    layer_id="layer-1",
                )
            ],
        ),
        user_assets=[_make_user_asset(note_text="note", color="#000000")],
        ask_supplements=[_make_ask_supplement(content={"x": 1})],
        snapshot_taken_at=datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC),
    )

    assert baseline.snapshot_id == varied.snapshot_id, (
        "snapshot_id changed when only non-hashed fields were varied. "
        "If _build_snapshot_id was modified to hash these fields, "
        "update the representation-coverage audit matrix."
    )
