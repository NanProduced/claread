from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.reader_orchestration import (
    ReaderEnhancementProgress,
    ReaderEnhancementProgressLayer,
    ReaderPlateSnapshot,
    ReaderSnapshotAnchorSegment,
    ReaderSnapshotAskSupplement,
    ReaderSnapshotBase,
    ReaderSnapshotLayer,
    ReaderSnapshotNavigation,
    ReaderSnapshotNavigationUnit,
    ReaderSnapshotParsedDecision,
    ReaderSnapshotRecord,
    ReaderSnapshotUserAsset,
    ReaderUnitAnchor,
)
from app.services.reader_orchestration.base_builder import (
    LowImpactReadingBaseBuildInput,
    ReadingBaseBuildResult,
    build_low_impact_reading_base,
)
from app.services.reader_orchestration.snapshot import build_reader_plate_snapshot

# Fixed deterministic timestamp used by the in-memory fixture builder so
# that byte counts and serialized output are reproducible across runs.
_FIXTURE_TS = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


class SnapshotStructureCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    navigation_units: int
    anchor_segments: int
    published_layers: int
    user_assets: int
    ask_supplements: int
    parsed_decisions: int
    base_text_length_utf16: int


class SnapshotByteBuckets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_json_utf8_bytes: int
    value_json_utf8_bytes: int
    enhancement_layers_total_utf8_bytes: int
    enhancement_layers_by_type_utf8_bytes: dict[str, int]


class SnapshotDurations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_duration_ns: int | None
    record_snapshot_load_duration_ns: int | None = None
    json_serialize_duration_ns: int
    duration_source: Literal["local_monotonic"] = "local_monotonic"


class SnapshotProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_kind: Literal["reader_plate_snapshot_profile"] = (
        "reader_plate_snapshot_profile"
    )
    schema_version: Literal[1] = 1
    collected_at: datetime
    measurement_scope: Literal["logical_serialized_bytes"] = "logical_serialized_bytes"
    record_id: str
    base_id: str
    generation: int
    snapshot_id: str
    last_event_sequence: int
    counts: SnapshotStructureCounts
    byte_buckets: SnapshotByteBuckets
    durations: SnapshotDurations
    notes: list[str]


_PROFILE_NOTES: list[str] = [
    "logical_serialized_bytes: validated measurement scope for this profile",
    "HTTP Content-Length / Content-Encoding not validated (deferred to deployment/BFF verification)",
    "browser transfer / parse / render not collected (deferred to Web profiling slice)",
    "snapshot_id is for profiling correlation only and MUST NOT be reused as an HTTP ETag (LP-R1 representation coverage audit pending)",
    "durations are local monotonic (time.perf_counter_ns); not comparable across machines and not wall clock",
    "value_json_utf8_bytes and enhancement_layers bytes use compact json.dumps standalone serialization and may differ slightly from embedded representation",
    "record_snapshot_load_duration_ns (when present) measures ArticleReadyPersistenceService.load_snapshot() wall time only (DB facts load + snapshot build); pool init/close, settings load, and HTTP route overhead are excluded; it is NOT pure DB duration, NOT pure build duration, and NOT HTTP route or end-to-end request time",
]


def profile_reader_plate_snapshot(
    snapshot: ReaderPlateSnapshot,
    *,
    collected_at: datetime | None = None,
    build_duration_ns: int | None = None,
    record_snapshot_load_duration_ns: int | None = None,
) -> SnapshotProfile:
    """Profile an already-built ``ReaderPlateSnapshot``.

    Pure and read-only: does not mutate ``snapshot`` and performs no
    DB / LLM / worker / orchestration work. Measures logical serialized
    JSON byte sizes and structural counts only.
    """
    if collected_at is None:
        collected_at = datetime.now(timezone.utc)
    elif collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)

    serialize_start = time.perf_counter_ns()
    full_json = snapshot.model_dump_json()
    serialize_end = time.perf_counter_ns()
    json_serialize_duration_ns = serialize_end - serialize_start

    full_json_utf8_bytes = len(full_json.encode("utf-8"))
    value_json_utf8_bytes = len(
        json.dumps(
            snapshot.value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    enhancement_layers_by_type_utf8_bytes: dict[str, int] = {}
    enhancement_layers_total_utf8_bytes = 0
    for layer in snapshot.enhancement_layers:
        layer_json = json.dumps(
            layer.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        layer_bytes = len(layer_json.encode("utf-8"))
        enhancement_layers_total_utf8_bytes += layer_bytes
        enhancement_layers_by_type_utf8_bytes[layer.layer_type] = (
            enhancement_layers_by_type_utf8_bytes.get(layer.layer_type, 0)
            + layer_bytes
        )

    counts = SnapshotStructureCounts(
        navigation_units=len(snapshot.navigation.units),
        anchor_segments=len(snapshot.anchor_segments),
        published_layers=len(snapshot.enhancement_layers),
        user_assets=len(snapshot.user_assets),
        ask_supplements=len(snapshot.ask_supplements),
        parsed_decisions=len(snapshot.parsed_decisions),
        base_text_length_utf16=snapshot.base.text_length_utf16,
    )

    byte_buckets = SnapshotByteBuckets(
        full_json_utf8_bytes=full_json_utf8_bytes,
        value_json_utf8_bytes=value_json_utf8_bytes,
        enhancement_layers_total_utf8_bytes=enhancement_layers_total_utf8_bytes,
        enhancement_layers_by_type_utf8_bytes=enhancement_layers_by_type_utf8_bytes,
    )

    durations = SnapshotDurations(
        build_duration_ns=build_duration_ns,
        record_snapshot_load_duration_ns=record_snapshot_load_duration_ns,
        json_serialize_duration_ns=json_serialize_duration_ns,
    )

    return SnapshotProfile(
        collected_at=collected_at,
        record_id=snapshot.record_id,
        base_id=snapshot.base.base_id,
        generation=snapshot.record.generation,
        snapshot_id=snapshot.snapshot_id,
        last_event_sequence=snapshot.last_event_sequence,
        counts=counts,
        byte_buckets=byte_buckets,
        durations=durations,
        notes=list(_PROFILE_NOTES),
    )


def build_and_profile_reader_plate_snapshot(
    build_result: ReadingBaseBuildResult,
    *,
    collected_at: datetime | None = None,
    **build_kwargs: object,
) -> tuple[ReaderPlateSnapshot, SnapshotProfile]:
    """Build a snapshot via ``build_reader_plate_snapshot`` and profile it.

    Measures the build duration with ``time.perf_counter_ns`` and forwards
    it to :func:`profile_reader_plate_snapshot` as ``build_duration_ns``.
    """
    build_start = time.perf_counter_ns()
    snapshot = build_reader_plate_snapshot(build_result, **build_kwargs)
    build_end = time.perf_counter_ns()
    build_duration_ns = build_end - build_start
    profile = profile_reader_plate_snapshot(
        snapshot,
        collected_at=collected_at,
        build_duration_ns=build_duration_ns,
    )
    return snapshot, profile


def build_deterministic_profiling_fixture() -> ReaderPlateSnapshot:
    """Construct a deterministic in-memory ``ReaderPlateSnapshot``.

    Built directly via Pydantic (not via ``build_reader_plate_snapshot``) to
    avoid complex layer/anchor cross-validation. No DB, no LLM, no worker.
    All timestamps are fixed so byte counts are reproducible.
    """
    base = ReaderSnapshotBase(
        base_id="base_fix_01",
        content_sha256="a" * 64,
        canonicalizer_version="v1",
        builder_version="v1",
        segmenter_version="v1",
        text_length_utf16=120,
    )

    navigation = ReaderSnapshotNavigation(
        units=[
            ReaderSnapshotNavigationUnit(
                unit_id="unit_01",
                order_index=1,
                unit_type="body",
                boundary_quality="normal",
                base_start_utf16=0,
                base_end_utf16=60,
                text_hash="abcdef01",
            ),
            ReaderSnapshotNavigationUnit(
                unit_id="unit_02",
                order_index=2,
                unit_type="body",
                boundary_quality="normal",
                base_start_utf16=60,
                base_end_utf16=120,
                text_hash="abcdef02",
            ),
        ]
    )

    anchor_segments = [
        ReaderSnapshotAnchorSegment(
            anchor_segment_id="seg_01",
            sentence_id="seg_01",
            paragraph_id="p1",
            unit_id="unit_01",
            order_index=1,
            unit_order_index=1,
            segment_type="sentence",
            boundary_quality="normal",
            base_start_utf16=0,
            base_end_utf16=60,
            unit_start_utf16=0,
            unit_end_utf16=60,
            text_hash="abcdef01",
        ),
        ReaderSnapshotAnchorSegment(
            anchor_segment_id="seg_02",
            sentence_id="seg_02",
            paragraph_id="p2",
            unit_id="unit_02",
            order_index=2,
            unit_order_index=1,
            segment_type="sentence",
            boundary_quality="normal",
            base_start_utf16=60,
            base_end_utf16=120,
            unit_start_utf16=0,
            unit_end_utf16=60,
            text_hash="abcdef02",
        ),
    ]

    enhancement_layers = [
        ReaderSnapshotLayer(
            layer_id="layer_translation_01",
            layer_type="translation",
            owner="system_ai",
            base_id="base_fix_01",
            target_scope="unit",
            target_key="unit_01",
            status="published",
            schema_version=1,
            output={
                "groups": [
                    {
                        "group_id": "g1",
                        "anchor_segment_ids": ["seg_01"],
                        "source_text_hash": "abcdef01",
                        "translated_text": "示例译文",
                    }
                ]
            },
            published_at=_FIXTURE_TS,
        ),
        ReaderSnapshotLayer(
            layer_id="layer_vocabulary_01",
            layer_type="vocabulary",
            owner="system_ai",
            base_id="base_fix_01",
            target_scope="unit",
            target_key="unit_01",
            status="published",
            schema_version=1,
            output={"schema_version": 1, "items": []},
            published_at=_FIXTURE_TS,
        ),
        ReaderSnapshotLayer(
            layer_id="layer_grammar_note_01",
            layer_type="grammar_note",
            owner="system_ai",
            base_id="base_fix_01",
            target_scope="unit",
            target_key="unit_01",
            status="published",
            schema_version=1,
            output={
                "schema_version": 1,
                "items": [
                    {
                        "item_type": "grammar_note",
                        "spans": [],
                        "grammar_point": "example",
                        "note": "示例",
                    }
                ],
            },
            published_at=_FIXTURE_TS,
        ),
        ReaderSnapshotLayer(
            layer_id="layer_sentence_analysis_01",
            layer_type="sentence_analysis",
            owner="system_ai",
            base_id="base_fix_01",
            target_scope="unit",
            target_key="unit_01",
            status="published",
            schema_version=1,
            output={"schema_version": 1, "items": []},
            published_at=_FIXTURE_TS,
        ),
    ]

    enhancement_progress = ReaderEnhancementProgress(
        overall_status="readable_enhancing",
        layers=[
            ReaderEnhancementProgressLayer(
                capability="translation",
                status="succeeded",
            ),
            ReaderEnhancementProgressLayer(
                capability="vocabulary",
                status="not_started",
            ),
        ],
    )

    ask_supplements = [
        ReaderSnapshotAskSupplement(
            supplement_id="ask_01",
            owner="ask_supplement",
            anchor=None,
            content={"text": "示例 ask"},
            created_at=_FIXTURE_TS,
        )
    ]

    user_assets = [
        ReaderSnapshotUserAsset(
            asset_id="asset_01",
            asset_type="highlight",
            owner="user",
            reading_record_id="rec_fix_01",
            generation=1,
            anchor=ReaderUnitAnchor(
                anchor_type="unit",
                base_id="base_fix_01",
                unit_id="unit_01",
                text_hash="abcdef01",
            ),
            created_at=_FIXTURE_TS,
            updated_at=_FIXTURE_TS,
        )
    ]

    parsed_decisions = [
        ReaderSnapshotParsedDecision(
            unit_id="unit_01",
            policy_code="default",
            parsed_state="parsed",
            rationale_code=None,
        )
    ]

    value: list[dict[str, object]] = [
        {
            "type": "reader_unit",
            "owner": "stable",
            "base_id": "base_fix_01",
            "unit_id": "unit_01",
            "order_index": 1,
            "unit_type": "body",
            "boundary_quality": "normal",
            "base_start_utf16": 0,
            "base_end_utf16": 60,
            "text_hash": "abcdef01",
            "hash_algorithm": "fnv1a32-utf16",
            "children": [
                {
                    "type": "reader_source_block",
                    "owner": "stable",
                    "base_id": "base_fix_01",
                    "unit_id": "unit_01",
                    "base_start_utf16": 0,
                    "base_end_utf16": 60,
                    "children": [
                        {
                            "text": "First unit sample text for profiling fixture.",
                            "owner": "stable",
                            "lock_source": True,
                            "source_role": "segment_text",
                            "base_start_utf16": 0,
                            "base_end_utf16": 60,
                        }
                    ],
                }
            ],
        },
        {
            "type": "reader_unit",
            "owner": "stable",
            "base_id": "base_fix_01",
            "unit_id": "unit_02",
            "order_index": 2,
            "unit_type": "body",
            "boundary_quality": "normal",
            "base_start_utf16": 60,
            "base_end_utf16": 120,
            "text_hash": "abcdef02",
            "hash_algorithm": "fnv1a32-utf16",
            "children": [
                {
                    "type": "reader_source_block",
                    "owner": "stable",
                    "base_id": "base_fix_01",
                    "unit_id": "unit_02",
                    "base_start_utf16": 60,
                    "base_end_utf16": 120,
                    "children": [
                        {
                            "text": "Second unit sample text for profiling fixture.",
                            "owner": "stable",
                            "lock_source": True,
                            "source_role": "segment_text",
                            "base_start_utf16": 60,
                            "base_end_utf16": 120,
                        }
                    ],
                }
            ],
        },
    ]

    record = ReaderSnapshotRecord(
        title="Profiling Fixture",
        created_at=_FIXTURE_TS,
        source_type="text",
        source_metadata={},
        generation=1,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )

    return ReaderPlateSnapshot(
        snapshot_id="reader_snapshot_fix01",
        snapshot_taken_at=_FIXTURE_TS,
        last_event_sequence=5,
        record_id="rec_fix_01",
        record=record,
        base=base,
        navigation=navigation,
        anchor_segments=anchor_segments,
        enhancement_layers=enhancement_layers,
        enhancement_progress=enhancement_progress,
        ask_supplements=ask_supplements,
        user_assets=user_assets,
        parsed_decisions=parsed_decisions,
        value=value,
    )


def build_minimal_build_result_for_build_profile() -> ReadingBaseBuildResult:
    """Build a tiny ``ReadingBaseBuildResult`` for exercising ``build_and_profile``.

    Uses ``build_low_impact_reading_base`` with a short ASCII text so the
    result has 1 unit + 1 anchor segment and trivially valid UTF-16 offsets.
    No enhancement layers are attached here; callers pass none to
    ``build_and_profile_reader_plate_snapshot`` to avoid layer validation.
    """
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="rec_min_01",
            base_id="base_min_01",
            source_text="Hello world.",
            title="Minimal Profile",
            language="en",
        )
    )
