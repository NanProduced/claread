"""T5.4a: optional semantic_outline snapshot projection (None / JSON null)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.schemas.reader_orchestration import (
    ReaderPlateSnapshot,
    ReaderSnapshotLayer,
    ReaderSnapshotRecord,
)
from app.services.reader_orchestration.base_builder import (
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
)
from app.services.reader_orchestration.semantic_outline_snapshot import (
    project_semantic_outline_for_snapshot,
)
from app.services.reader_orchestration.snapshot import build_reader_plate_snapshot

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
BASE_ID = "base_outline_snap_01"
RECORD_ID = "rec_outline_snap_01"
GENERATION = 1
REVISION = "olrev_test_revision_001"
LAYER_ID = "layer_semantic_outline_01"


def _build_result():
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id=RECORD_ID,
            base_id=BASE_ID,
            source_text=(
                "First paragraph of the article for outline projection tests. "
                "It needs enough text to form a stable reading unit."
            ),
            title="Outline Snapshot Fixture",
            language="en",
        )
    )


def _record(*, generation: int = GENERATION) -> ReaderSnapshotRecord:
    return ReaderSnapshotRecord(
        title="Outline Snapshot Fixture",
        created_at=NOW,
        source_type="text",
        source_metadata={},
        generation=generation,
        product_state="readable_enhancing",
        readiness_state="article_ready",
    )


def _units_and_anchors(build_result):
    unit = build_result.units[0]
    anchors = list(build_result.anchor_segments)
    return unit, anchors


def _ready_envelope(
    build_result,
    *,
    base_id: str = BASE_ID,
    generation: int = GENERATION,
    layer_id: str | None = LAYER_ID,
    revision: str = REVISION,
    title: str = "Section One",
) -> dict[str, Any]:
    unit, anchors = _units_and_anchors(build_result)
    start_anchor = anchors[0].anchor_segment_id if anchors else None
    end_anchor = anchors[-1].anchor_segment_id if anchors else None
    return {
        "schema_kind": "reader_semantic_outline",
        "schema_version": 1,
        "status": "ready",
        "source_identity": {"base_id": base_id, "generation": generation},
        "publication": {
            "outline_revision": revision,
            "layer_id": layer_id,
            "published_at": NOW.isoformat().replace("+00:00", "Z"),
        },
        "provenance": {
            "kind": "llm",
            "builder": "reader-semantic-outline-publisher-v1",
            "model": "test-model",
        },
        "nodes": [
            {
                "node_id": "n1",
                "parent_node_id": None,
                "depth": 1,
                "title": title,
                "start_unit_id": unit.unit_id,
                "end_unit_id": unit.unit_id,
                "start_anchor_segment_id": start_anchor,
                "end_anchor_segment_id": end_anchor,
                "order_index": 1,
            }
        ],
        "diagnostics": {"drops": [], "skipped_node_count": 0},
    }


def _partial_envelope(build_result) -> dict[str, Any]:
    env = _ready_envelope(build_result)
    unit = build_result.units[0]
    env["status"] = "partial"
    env["nodes"].append(
        {
            "node_id": "n_bad",
            "parent_node_id": "missing_parent",
            "depth": 2,
            "title": "Dropped Child",
            "start_unit_id": unit.unit_id,
            "end_unit_id": unit.unit_id,
            "start_anchor_segment_id": None,
            "end_anchor_segment_id": None,
            "order_index": 2,
        }
    )
    env["diagnostics"] = {
        "drops": [{"node_id": "n_bad", "reason_code": "invalid_parent"}],
        "skipped_node_count": 1,
    }
    return env


def _outline_layer(
    build_result,
    *,
    layer_id: str = LAYER_ID,
    output: dict[str, Any] | str | None = None,
    published_at: datetime = NOW,
    target_scope: str = "record",
    target_key: str = "document",
    base_id: str = BASE_ID,
) -> ReaderSnapshotLayer:
    if output is None:
        output = _ready_envelope(build_result)
    return ReaderSnapshotLayer(
        layer_id=layer_id,
        layer_type="semantic_outline",
        owner="system_ai",
        base_id=base_id,
        target_scope=target_scope,  # type: ignore[arg-type]
        target_key=target_key,
        status="published",
        schema_version=1,
        output=output,
        published_at=published_at,
    )


def _snapshot(
    build_result,
    *,
    layers: list[ReaderSnapshotLayer] | None = None,
    generation: int = GENERATION,
) -> ReaderPlateSnapshot:
    return build_reader_plate_snapshot(
        build_result,
        snapshot_taken_at=NOW,
        last_event_sequence=1,
        record=_record(generation=generation),
        enhancement_layers=layers or [],
    )


def _json(snapshot: ReaderPlateSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json")


# ---------------------------------------------------------------------------
# A1 / A2 / A12 — no trusted published outline → None / null
# ---------------------------------------------------------------------------


def test_a1_no_layer_python_none_and_json_null() -> None:
    build_result = _build_result()
    snapshot = _snapshot(build_result, layers=[])
    assert snapshot.semantic_outline is None
    dumped = _json(snapshot)
    assert "semantic_outline" in dumped
    assert dumped["semantic_outline"] is None


def test_a2_pending_job_no_published_is_none() -> None:
    """T5.4a does not read jobs; pending with no published layer ≡ no projection."""
    build_result = _build_result()
    snapshot = _snapshot(build_result, layers=[])
    assert snapshot.semantic_outline is None
    assert _json(snapshot)["semantic_outline"] is None


def test_a12_failed_job_no_published_is_none() -> None:
    build_result = _build_result()
    snapshot = _snapshot(build_result, layers=[])
    assert snapshot.semantic_outline is None
    assert _json(snapshot)["semantic_outline"] is None


# ---------------------------------------------------------------------------
# A3 / A4 — trusted ready / partial
# ---------------------------------------------------------------------------


def test_published_ready_projects_with_real_ids() -> None:
    build_result = _build_result()
    layer = _outline_layer(build_result)
    snapshot = _snapshot(build_result, layers=[layer])
    outline = snapshot.semantic_outline
    assert outline is not None
    assert outline.status == "ready"
    assert outline.publication.outline_revision == REVISION
    assert outline.publication.layer_id == LAYER_ID
    assert outline.provenance.kind == "llm"
    assert outline.provenance.model == "test-model"
    assert outline.source_identity.base_id == BASE_ID
    assert outline.source_identity.generation == GENERATION
    assert len(outline.nodes) == 1
    assert outline.nodes[0].node_id == "n1"
    dumped = _json(snapshot)
    assert dumped["semantic_outline"]["status"] == "ready"
    assert dumped["semantic_outline"]["publication"]["layer_id"] == LAYER_ID
    # audit inventory still lists the layer
    assert any(layer.layer_type == "semantic_outline" for layer in snapshot.enhancement_layers)


def test_published_partial_keeps_valid_nodes_only() -> None:
    build_result = _build_result()
    layer = _outline_layer(build_result, output=_partial_envelope(build_result))
    snapshot = _snapshot(build_result, layers=[layer])
    outline = snapshot.semantic_outline
    assert outline is not None
    assert outline.status == "partial"
    assert len(outline.nodes) >= 1
    assert all(n.node_id != "n_bad" or n.parent_node_id is None for n in outline.nodes)
    # invalid parent node must not survive revalidation as a valid child
    assert all(n.node_id != "n_bad" for n in outline.nodes)


# ---------------------------------------------------------------------------
# A5 — old published survives failed new job (job not consulted)
# ---------------------------------------------------------------------------


def test_old_published_still_projected_when_only_published_present() -> None:
    """New job failure is not consulted; sole published layer remains projected."""
    build_result = _build_result()
    env = _ready_envelope(build_result, revision="olrev_old_kept", layer_id="layer_old")
    layer = _outline_layer(build_result, output=env, layer_id="layer_old")
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is not None
    assert snapshot.semantic_outline.status == "ready"
    assert snapshot.semantic_outline.publication.outline_revision == "olrev_old_kept"
    assert snapshot.semantic_outline.publication.layer_id == "layer_old"


# ---------------------------------------------------------------------------
# A6 / A7 — invalid / source mismatch → None
# ---------------------------------------------------------------------------


def test_a6_invalid_json_output_is_none_snapshot_still_builds() -> None:
    build_result = _build_result()
    layer = ReaderSnapshotLayer(
        layer_id=LAYER_ID,
        layer_type="semantic_outline",
        owner="system_ai",
        base_id=BASE_ID,
        target_scope="record",
        target_key="document",
        status="published",
        schema_version=1,
        output={"not": "a valid outline envelope"},
        published_at=NOW,
    )
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is None
    assert snapshot.snapshot_id
    assert len(snapshot.navigation.units) >= 1
    assert _json(snapshot)["semantic_outline"] is None
    assert len(snapshot.enhancement_layers) == 1


def test_a6b_non_mapping_output_is_none() -> None:
    build_result = _build_result()
    layer = ReaderSnapshotLayer(
        layer_id=LAYER_ID,
        layer_type="semantic_outline",
        owner="system_ai",
        base_id=BASE_ID,
        target_scope="record",
        target_key="document",
        status="published",
        schema_version=1,
        output="not-json-object",
        published_at=NOW,
    )
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is None
    assert _json(snapshot)["semantic_outline"] is None


def test_a7_source_identity_mismatch_is_none() -> None:
    build_result = _build_result()
    env = _ready_envelope(build_result, generation=99)
    layer = _outline_layer(build_result, output=env)
    snapshot = _snapshot(build_result, layers=[layer], generation=GENERATION)
    assert snapshot.semantic_outline is None
    assert _json(snapshot)["semantic_outline"] is None


# ---------------------------------------------------------------------------
# A8 / A9 — multi candidate pick latest; stable revision
# ---------------------------------------------------------------------------


def test_a8_prefers_newest_published_among_candidates() -> None:
    build_result = _build_result()
    older = _outline_layer(
        build_result,
        layer_id="layer_old",
        output=_ready_envelope(build_result, layer_id="layer_old", revision="olrev_old"),
        published_at=NOW - timedelta(hours=1),
    )
    newer = _outline_layer(
        build_result,
        layer_id="layer_new",
        output=_ready_envelope(build_result, layer_id="layer_new", revision="olrev_new"),
        published_at=NOW,
    )
    snapshot = _snapshot(build_result, layers=[older, newer])
    assert snapshot.semantic_outline is not None
    assert snapshot.semantic_outline.publication.layer_id == "layer_new"
    assert snapshot.semantic_outline.publication.outline_revision == "olrev_new"


def test_a9_idempotent_reuse_stable_revision() -> None:
    build_result = _build_result()
    layer = _outline_layer(build_result)
    s1 = _snapshot(build_result, layers=[layer])
    s2 = _snapshot(build_result, layers=[layer])
    assert s1.semantic_outline is not None and s2.semantic_outline is not None
    assert (
        s1.semantic_outline.publication.outline_revision
        == s2.semantic_outline.publication.outline_revision
        == REVISION
    )
    assert s1.semantic_outline.publication.layer_id == s2.semantic_outline.publication.layer_id


# ---------------------------------------------------------------------------
# A10 — value / navigation unaffected
# ---------------------------------------------------------------------------


def test_a10_value_and_navigation_unchanged_by_outline() -> None:
    build_result = _build_result()
    baseline = _snapshot(build_result, layers=[])
    with_outline = _snapshot(build_result, layers=[_outline_layer(build_result)])
    assert baseline.navigation.model_dump() == with_outline.navigation.model_dump()
    assert baseline.value == with_outline.value
    assert with_outline.semantic_outline is not None
    assert baseline.semantic_outline is None


# ---------------------------------------------------------------------------
# A11 — enhancement_layers inventory retained
# ---------------------------------------------------------------------------


def test_a11_enhancement_layers_still_lists_outline_for_audit() -> None:
    build_result = _build_result()
    layer = _outline_layer(build_result)
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is not None
    outline_layers = [
        item for item in snapshot.enhancement_layers if item.layer_type == "semantic_outline"
    ]
    assert len(outline_layers) == 1
    assert outline_layers[0].layer_id == LAYER_ID
    assert outline_layers[0].target_scope == "record"
    assert outline_layers[0].target_key == "document"


# ---------------------------------------------------------------------------
# Pure helper edge cases
# ---------------------------------------------------------------------------


def test_revalidate_empty_nodes_yields_none() -> None:
    build_result = _build_result()
    env = _ready_envelope(build_result)
    env["nodes"] = [
        {
            "node_id": "n_gone",
            "parent_node_id": None,
            "depth": 1,
            "title": "Missing Unit",
            "start_unit_id": "unit_does_not_exist",
            "end_unit_id": "unit_does_not_exist",
            "start_anchor_segment_id": None,
            "end_anchor_segment_id": None,
            "order_index": 1,
        }
    ]
    layer = _outline_layer(build_result, output=env)
    projected = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=GENERATION,
        enhancement_layers=[layer],
    )
    assert projected is None


def test_wrong_target_scope_ignored() -> None:
    build_result = _build_result()
    unit = build_result.units[0]
    layer = _outline_layer(
        build_result,
        target_scope="unit",
        target_key=unit.unit_id,
    )
    projected = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=GENERATION,
        enhancement_layers=[layer],
    )
    assert projected is None


def test_layer_id_always_from_row_not_envelope() -> None:
    build_result = _build_result()
    env = _ready_envelope(build_result, layer_id="envelope_stale_id")
    layer = _outline_layer(build_result, layer_id="row_authoritative_id", output=env)
    projected = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=GENERATION,
        enhancement_layers=[layer],
    )
    assert projected is not None
    assert projected.publication.layer_id == "row_authoritative_id"


def test_default_field_on_model_is_none() -> None:
    """Schema contract: optional default None without building via snapshot."""
    # Minimal check that field exists with default — full snapshot path covers rest.
    fields = ReaderPlateSnapshot.model_fields
    assert "semantic_outline" in fields
    assert fields["semantic_outline"].default is None


# ---------------------------------------------------------------------------
# T5.4a-P1 — envelope status fence + durable published_at
# ---------------------------------------------------------------------------


def test_envelope_status_failed_with_valid_nodes_is_none() -> None:
    """Published row cannot upgrade a failed envelope via node revalidation."""
    build_result = _build_result()
    env = _ready_envelope(build_result)
    env["status"] = "failed"
    layer = _outline_layer(build_result, output=env)
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is None
    assert _json(snapshot)["semantic_outline"] is None
    projected = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=GENERATION,
        enhancement_layers=[layer],
    )
    assert projected is None


@pytest.mark.parametrize("bad_status", ["pending", "stale", "unavailable"])
def test_envelope_status_non_trusted_not_upgraded(bad_status: str) -> None:
    build_result = _build_result()
    env = _ready_envelope(build_result)
    env["status"] = bad_status
    layer = _outline_layer(build_result, output=env)
    projected = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=GENERATION,
        enhancement_layers=[layer],
    )
    assert projected is None
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is None
    assert _json(snapshot)["semantic_outline"] is None


def test_publication_published_at_from_layer_row_not_envelope() -> None:
    build_result = _build_result()
    layer_ts = datetime(2026, 7, 17, 15, 30, 0, tzinfo=UTC)
    envelope_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    env = _ready_envelope(build_result)
    env["publication"]["published_at"] = envelope_ts.isoformat().replace("+00:00", "Z")
    layer = _outline_layer(build_result, output=env, published_at=layer_ts)
    projected = project_semantic_outline_for_snapshot(
        build_result=build_result,
        record_generation=GENERATION,
        enhancement_layers=[layer],
    )
    assert projected is not None
    assert projected.status == "ready"
    assert projected.publication.published_at == layer_ts
    assert projected.publication.published_at != envelope_ts
    snapshot = _snapshot(build_result, layers=[layer])
    assert snapshot.semantic_outline is not None
    assert snapshot.semantic_outline.publication.published_at == layer_ts
