"""Hermetic **non-empty layer** Reader snapshot fixture (R1 evidence).

R1 task 6: this module constructs a duck-typed
``ReaderPlateSnapshot`` + ``ReaderPipelineRunSummary`` whose
``enhancement_layers`` carry **real, non-empty** translation +
vocabulary + sidecar_ref outputs. It exists so the official
:mod:`.reader_adapter` and the :mod:`.gate` can be exercised
end-to-end against a non-empty layer path — proving the artifact
carries reviewable evidence, not just count-only summaries.

Design boundaries:

1. The fixture is **duck-typed**: it does NOT import the
   ``app.schemas.reader_orchestration`` Pydantic models at runtime.
   It builds lightweight ``SimpleNamespace`` / dataclass objects
   whose attribute shape matches what
   :func:`.reader_adapter.build_artifact_from_snapshot` reads. This
   keeps the fixture hermetic (no ``app`` runtime dependency).

2. The fixture reuses the hermetic anchor-map builder from
   :mod:`.fixture_builder` so the navigation units / anchor segments
   have FNV-1a32 hashes that the gate can recompute against the
   canonical text evidence.

3. The translation layer output carries one
   :class:`.schema.TranslationGroupFact` projection with a real
   Simplified-Chinese ``translated_text`` and a valid 8-hex
   ``source_text_hash``.

4. The vocabulary layer output carries one
   :class:`.schema.VocabularyItemFact` projection with a real
   ``headword`` / ``brief_explanation`` / ``reason`` and an anchor
   referencing the first navigation unit / anchor segment.

5. A third ``grammar_note`` layer carries an opaque dict output —
   the adapter projects it as ``output_kind="sidecar_ref"`` with a
   content-addressed SHA-256, exercising the sidecar path.

6. The fixture does NOT call the LLM, does NOT touch the DB, and
   does NOT require spaCy. It is a pure constructor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from .fixture_builder import (
    HERMETIC_BUILDER_VERSION,
    HERMETIC_CANONICALIZER_VERSION,
    HERMETIC_SEGMENTER_VERSION,
    build_hermetic_anchor_map,
    canonicalize_hermetic,
    fnv1a32_utf16,
    sha256_hex,
    utf16_code_unit_length,
)

if TYPE_CHECKING:
    from app.schemas.reader_orchestration import ReaderPlateSnapshot
    from app.services.reader_orchestration.pipeline_runner import (
        ReaderPipelineRunSummary,
    )

    from .schema import ParseEvalArtifactV1

# ---------------------------------------------------------------------------
# Fixture canonical text — a small, deterministic, non-empty article
# ---------------------------------------------------------------------------
#
# Two short paragraphs so the hermetic anchor map builder produces
# two navigation units + two anchor segments. The gate recomputes
# the per-unit / per-segment FNV-1a32 hashes over the canonical-text
# UTF-16 slices, so the fixture must use the same canonicalization
# as the producer (``canonicalize_hermetic``).
# ---------------------------------------------------------------------------

FIXTURE_CANONICAL_TEXT: str = (
    "The quiet village of Auburn sits between two green hills.\n\n"
    "Every morning, fishermen return with baskets of fresh fish, "
    "and children walk to the small school by the river."
)


@dataclass(frozen=True, slots=True)
class FixtureLayerOutput:
    """Raw layer output blob carried by a snapshot layer fixture.

    The adapter accepts either a dict or a duck-typed object; we use
    a dataclass here so the fixture is self-documenting.
    """

    groups: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None
    opaque: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Project to the dict shape the adapter reads."""
        if self.groups is not None:
            return {"groups": self.groups}
        if self.items is not None:
            return {"items": self.items}
        if self.opaque is not None:
            return self.opaque
        return {}


@dataclass(frozen=True, slots=True)
class FixtureSnapshotLayer:
    """Duck-typed ``ReaderSnapshotLayer`` for the fixture path."""

    layer_id: str
    layer_type: str
    target_scope: str
    target_key: str
    status: str
    schema_version: int
    output: Any
    base_id: str
    published_at: str  # ISO timestamp string; the adapter does not read it


@dataclass(frozen=True, slots=True)
class FixtureNavigationUnit:
    """Duck-typed ``ReaderSnapshotNavigationUnit``."""

    unit_id: str
    order_index: int
    unit_type: str
    boundary_quality: str
    base_start_utf16: int
    base_end_utf16: int
    text_hash: str
    hash_algorithm: str


@dataclass(frozen=True, slots=True)
class FixtureAnchorSegment:
    """Duck-typed ``ReaderSnapshotAnchorSegment``."""

    anchor_segment_id: str
    sentence_id: str
    paragraph_id: str
    unit_id: str
    order_index: int
    unit_order_index: int
    segment_type: str
    boundary_quality: str
    base_start_utf16: int
    base_end_utf16: int
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    hash_algorithm: str


@dataclass(frozen=True, slots=True)
class FixtureSnapshotRecord:
    """Duck-typed ``ReaderSnapshotRecord`` (minimal field subset)."""

    record_id: str
    generation: int
    reading_goal: str
    reading_variant: str


@dataclass(frozen=True, slots=True)
class FixtureSnapshotBase:
    """Duck-typed ``ReaderSnapshotBase`` (minimal field subset)."""

    base_id: str
    content_sha256: str
    canonicalizer_version: str
    builder_version: str
    segmenter_version: str
    text_length_utf16: int
    hash_algorithm: str


@dataclass(frozen=True, slots=True)
class FixtureSnapshotNavigation:
    """Duck-typed ``ReaderSnapshotNavigation``."""

    units: list[FixtureNavigationUnit]


@dataclass(frozen=True, slots=True)
class FixturePlateSnapshot:
    """Duck-typed ``ReaderPlateSnapshot`` (minimal field subset).

    Only the attributes read by
    :func:`.reader_adapter.build_artifact_from_snapshot` are populated.
    """

    record: FixtureSnapshotRecord
    base: FixtureSnapshotBase
    navigation: FixtureSnapshotNavigation
    anchor_segments: list[FixtureAnchorSegment]
    enhancement_layers: list[FixtureSnapshotLayer]
    last_event_sequence: int


@dataclass(frozen=True, slots=True)
class FixturePipelineRunSummary:
    """Duck-typed ``ReaderPipelineRunSummary`` (minimal field subset)."""

    record_id: str
    base_id: str
    total_ticks: int
    total_jobs: int
    stopped_reason: str


# ---------------------------------------------------------------------------
# Fixed source-id + base-id + record-id (deterministic, fixture-grade)
# ---------------------------------------------------------------------------

FIXTURE_SOURCE_ID: str = "reader-record-fixture-non-empty-layers-0001"
FIXTURE_BASE_ID: str = "fixture-base-0001"
FIXTURE_RECORD_ID: str = "fixture-record-0001"
FIXTURE_SNAPSHOT_ID: str = "fixture-snapshot-0001"


def _build_translation_groups(
    anchor_map_units: list[Any],
) -> list[dict[str, Any]]:
    """Build one translation group referencing the first anchor segment.

    The ``source_text_hash`` is the FNV-1a32 over a fixed source
    snippet — it does NOT need to match any slice of the canonical
    text (the gate only verifies the SHA-256 of the canonical JSON
    of the normalized output, not the inner source_text_hash).
    """
    first_unit = anchor_map_units[0]
    first_anchor_segment_id = f"anchor-{first_unit.order_index:04d}"
    source_snippet = "The quiet village of Auburn sits between two green hills."
    return [
        {
            "group_id": "translation-group-0001",
            "anchor_segment_ids": [first_anchor_segment_id],
            "source_text_hash": fnv1a32_utf16(source_snippet),
            "translated_text": (
                "宁静的奥本村坐落在两座青山之间。"
            ),
        }
    ]


def _build_vocabulary_items(
    anchor_map_units: list[Any],
    anchor_segments: list[Any],
    canonical_text: str,
) -> list[dict[str, Any]]:
    """Build one vocab_highlight item anchored to a real text range.

    The ``anchor.text_hash`` is the FNV-1a32 over the
    ``selected_text`` (mirroring the real Reader contract
    ``ReaderTextRangeAnchor.validate_offsets``). We pick a real
    substring of the canonical text so the anchor is grounded in
    the document.
    """
    first_unit = anchor_map_units[0]
    first_segment = anchor_segments[0]
    # Pick "Auburn" as the highlighted headword. Find its UTF-16
    # offset in the canonical text.
    selected_text = "Auburn"
    start_offset_char = canonical_text.find(selected_text)
    assert start_offset_char >= 0, "fixture canonical text must contain 'Auburn'"
    prefix = canonical_text[:start_offset_char]
    start_offset_utf16 = utf16_code_unit_length(prefix)
    end_offset_utf16 = start_offset_utf16 + utf16_code_unit_length(selected_text)
    selected_text_hash = fnv1a32_utf16(selected_text)
    return [
        {
            "item_type": "vocab_highlight",
            "anchor": {
                "anchor_type": "text_range",
                "base_id": FIXTURE_BASE_ID,
                "unit_id": first_unit.unit_id,
                "anchor_segment_id": first_segment.anchor_segment_id,
                "sentence_id": first_segment.sentence_id,
                "segment_type": "sentence",
                "offset_unit": "utf16",
                "start_offset": start_offset_utf16,
                "end_offset": end_offset_utf16,
                "selected_text": selected_text,
                "text_hash": selected_text_hash,
                "hash_algorithm": "fnv1a32-utf16",
            },
            "headword": "Auburn",
            "brief_explanation": "奥本（地名）",
            "reason": "专有名词，美国、澳大利亚等地常见的地名。",
        }
    ]


def _build_grammar_note_opaque() -> dict[str, Any]:
    """Build an opaque grammar_note output blob (sidecar path)."""
    return {
        "items": [
            {
                "item_type": "grammar_note",
                "spans": [
                    {
                        "base_id": FIXTURE_BASE_ID,
                        "unit_id": "unit-0001",
                        "anchor_segment_id": "anchor-0001",
                        "start_offset": 0,
                        "end_offset": 10,
                    }
                ],
                "grammar_point": "simple_present",
                "pattern": "Subject + V(s) + Object",
                "note": "``sits`` 是一般现在时，表示习惯性或长期状态。",
            }
        ]
    }


# ---------------------------------------------------------------------------
# Top-level fixture builder
# ---------------------------------------------------------------------------


def build_non_empty_layer_snapshot_fixture(
    *,
    canonical_text: str = FIXTURE_CANONICAL_TEXT,
) -> tuple[FixturePlateSnapshot, FixturePipelineRunSummary, str]:
    """Build a duck-typed snapshot + pipeline summary + canonical text.

    Returns a triple ``(snapshot, pipeline_summary, canonical_text)``
    ready to be passed to
    :func:`.reader_adapter.build_artifact_from_snapshot`.

    The fixture carries three published layers:

    1. ``translation`` — one TranslationGroupFact (normalized_output path).
    2. ``vocabulary`` — one VocabularyItemFact (normalized_output path).
    3. ``grammar_note`` — opaque dict output (sidecar_ref path).

    All three layers are non-empty, so the artifact will carry
    reviewable evidence — not a count-only summary.
    """
    canonical = canonicalize_hermetic(canonical_text)
    if not canonical:
        raise ValueError("fixture canonical text is empty after canonicalization")

    anchor_map = build_hermetic_anchor_map(canonical)
    navigation_units_data = anchor_map.navigation_units
    anchor_segments_data = anchor_map.anchor_segments

    navigation_units: list[FixtureNavigationUnit] = [
        FixtureNavigationUnit(
            unit_id=u.unit_id,
            order_index=u.order_index,
            unit_type=u.unit_type,
            boundary_quality=u.boundary_quality,
            base_start_utf16=u.base_start_utf16,
            base_end_utf16=u.base_end_utf16,
            text_hash=u.text_hash,
            hash_algorithm=u.hash_algorithm,
        )
        for u in navigation_units_data
    ]
    anchor_segments: list[FixtureAnchorSegment] = [
        FixtureAnchorSegment(
            anchor_segment_id=s.anchor_segment_id,
            sentence_id=s.sentence_id,
            paragraph_id=s.paragraph_id,
            unit_id=s.unit_id,
            order_index=s.order_index,
            unit_order_index=s.unit_order_index,
            segment_type=s.segment_type,
            boundary_quality=s.boundary_quality,
            base_start_utf16=s.base_start_utf16,
            base_end_utf16=s.base_end_utf16,
            unit_start_utf16=s.unit_start_utf16,
            unit_end_utf16=s.unit_end_utf16,
            text_hash=s.text_hash,
            hash_algorithm=s.hash_algorithm,
        )
        for s in anchor_segments_data
    ]

    translation_groups = _build_translation_groups(navigation_units_data)
    vocabulary_items = _build_vocabulary_items(
        navigation_units_data, anchor_segments_data, canonical
    )
    grammar_note_opaque = _build_grammar_note_opaque()

    layers: list[FixtureSnapshotLayer] = [
        FixtureSnapshotLayer(
            layer_id="layer-translation-0001",
            layer_type="translation",
            target_scope="record",
            target_key="record",
            status="published",
            schema_version=1,
            output={"groups": translation_groups},
            base_id=FIXTURE_BASE_ID,
            published_at="2026-07-23T00:00:00Z",
        ),
        FixtureSnapshotLayer(
            layer_id="layer-vocabulary-0001",
            layer_type="vocabulary",
            target_scope="record",
            target_key="record",
            status="published",
            schema_version=1,
            output={"items": vocabulary_items},
            base_id=FIXTURE_BASE_ID,
            published_at="2026-07-23T00:00:00Z",
        ),
        FixtureSnapshotLayer(
            layer_id="layer-grammar-note-0001",
            layer_type="grammar_note",
            target_scope="record",
            target_key="record",
            status="published",
            schema_version=1,
            output=grammar_note_opaque,
            base_id=FIXTURE_BASE_ID,
            published_at="2026-07-23T00:00:00Z",
        ),
    ]

    record = FixtureSnapshotRecord(
        record_id=FIXTURE_RECORD_ID,
        generation=1,
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
    )
    base = FixtureSnapshotBase(
        base_id=FIXTURE_BASE_ID,
        content_sha256=sha256_hex(canonical),
        canonicalizer_version=HERMETIC_CANONICALIZER_VERSION,
        builder_version=HERMETIC_BUILDER_VERSION,
        segmenter_version=HERMETIC_SEGMENTER_VERSION,
        text_length_utf16=utf16_code_unit_length(canonical),
        hash_algorithm="fnv1a32-utf16",
    )
    navigation = FixtureSnapshotNavigation(units=navigation_units)
    snapshot = FixturePlateSnapshot(
        record=record,
        base=base,
        navigation=navigation,
        anchor_segments=anchor_segments,
        enhancement_layers=layers,
        last_event_sequence=0,
    )

    pipeline_summary = FixturePipelineRunSummary(
        record_id=FIXTURE_RECORD_ID,
        base_id=FIXTURE_BASE_ID,
        total_ticks=3,
        total_jobs=3,
        stopped_reason="all_workers_no_job",
    )

    return snapshot, pipeline_summary, canonical


def build_fake_artifact_with_non_empty_layers(
    *,
    deterministic_clock_token: str = "non-empty-fixture-v1",
) -> ParseEvalArtifactV1:
    """Build a **fake-executor** artifact via the official adapter.

    R2 (P1-3) correction: this fixture builder always produces an
    artifact whose runner provenance carries ``is_fake=True`` and
    whose model / prompt provenance fields are ``None``. The fixture
    is hand-constructed content — there is no real LLM run behind it,
    so the provenance contract forbids labelling it ``executor_mode="real"``.

    The artifact still carries three non-empty published layers
    (translation + vocabulary via normalized_output, grammar_note via
    sidecar_ref), so the adapter + gate can be exercised end-to-end
    against the non-empty layer path.

    Returns:
        A validated :class:`.schema.ParseEvalArtifactV1` with
        ``executor_mode="fake"``.
    """
    from .reader_adapter import build_artifact_from_snapshot

    snapshot, pipeline_summary, canonical_text = (
        build_non_empty_layer_snapshot_fixture()
    )

    return build_artifact_from_snapshot(
        cast("ReaderPlateSnapshot", snapshot),
        canonical_text=canonical_text,
        source_id=FIXTURE_SOURCE_ID,
        source_shape="short_news",
        source_attribution="fixture-only non-empty-layer evidence (fake executor)",
        pipeline_summary=cast("ReaderPipelineRunSummary", pipeline_summary),
        executor_mode="fake",
        executor_note="non-empty-layer fixture via official adapter (fake executor)",
        runner_version="fixture_pipeline_runner_v1",
        model_provider=None,
        model_name=None,
        model_profile=None,
        prompt_revision=None,
        deterministic_clock_token=deterministic_clock_token,
    )


# ---------------------------------------------------------------------------
# R3 (P1): build_schema_only_real_provenance_fixture has been REMOVED from
# the public package. It was a schema-only helper that constructed a full
# ``ParseEvalArtifactV1`` with ``executor_mode="real"`` and
# ``is_fake=False`` directly via Pydantic — bypassing the official adapter.
# This made it a public, eval-consumable "real artifact producer" that the
# gate could not distinguish from a genuine run output.
#
# The schema-only real-provenance branch is now covered by a TEST-LOCAL
# helper in ``tests/test_reader_parse_eval_r1.py`` that builds a fixture
# artifact claiming real, and the gate's new
# ``artifact_provenance.fixture_claims_real_execution`` check rejects it.
# ---------------------------------------------------------------------------


__all__ = [
    "FIXTURE_CANONICAL_TEXT",
    "FIXTURE_SOURCE_ID",
    "FIXTURE_BASE_ID",
    "FIXTURE_RECORD_ID",
    "FIXTURE_SNAPSHOT_ID",
    "FixtureLayerOutput",
    "FixtureSnapshotLayer",
    "FixtureNavigationUnit",
    "FixtureAnchorSegment",
    "FixtureSnapshotRecord",
    "FixtureSnapshotBase",
    "FixtureSnapshotNavigation",
    "FixturePlateSnapshot",
    "FixturePipelineRunSummary",
    "build_non_empty_layer_snapshot_fixture",
    "build_fake_artifact_with_non_empty_layers",
]
