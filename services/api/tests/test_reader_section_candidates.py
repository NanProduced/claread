"""section candidate projection from trusted outline."""

from __future__ import annotations

from app.services.reader_orchestration.section_candidates import (
    OutlineNodeInput,
    TrustedOutlineInput,
    project_section_candidates_from_outline,
)
from app.services.reader_orchestration.section_identity import SectionUnit

_UNITS = (
    SectionUnit("u1", 1),
    SectionUnit("u2", 2),
    SectionUnit("u3", 3),
    SectionUnit("u4", 4),
    SectionUnit("u5", 5),
    SectionUnit("u6", 6),
)


def _outline(
    *nodes: OutlineNodeInput,
    status: str = "ready",
    base_id: str = "base_1",
    generation: int = 1,
    revision: str = "rev_1",
) -> TrustedOutlineInput:
    return TrustedOutlineInput(
        status=status,
        source_base_id=base_id,
        source_generation=generation,
        outline_revision=revision,
        nodes=nodes,
    )


def test_sc01_ready_two_ranges() -> None:
    outline = _outline(
        OutlineNodeInput("n1", "u1", "u3", title="A", order_index=1),
        OutlineNodeInput("n2", "u4", "u6", title="B", order_index=2),
    )
    candidates = project_section_candidates_from_outline(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        outline=outline,
        ordered_units=_UNITS,
    )
    assert len(candidates) == 2
    assert candidates[0].identity.start_unit_id == "u1"
    assert candidates[0].audit_node_id == "n1"
    assert candidates[0].audit_outline_revision == "rev_1"
    assert candidates[1].identity.end_unit_id == "u6"
    # Durable identity has no node fields.
    assert not hasattr(candidates[0].identity, "node_id")


def test_sc02_partial_accepted_only_when_all_nodes_valid() -> None:
    outline = _outline(
        OutlineNodeInput("n1", "u1", "u2", title="Only", order_index=1),
        status="partial",
    )
    candidates = project_section_candidates_from_outline(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        outline=outline,
        ordered_units=_UNITS,
    )
    assert len(candidates) == 1


def test_sc03_null_pending_source_mismatch_zero() -> None:
    assert (
        project_section_candidates_from_outline(
            record_id="rec_1",
            base_id="base_1",
            generation=1,
            outline=None,
            ordered_units=_UNITS,
        )
        == ()
    )
    pending = _outline(
        OutlineNodeInput("n1", "u1", "u1"),
        status="pending",
    )
    assert (
        project_section_candidates_from_outline(
            record_id="rec_1",
            base_id="base_1",
            generation=1,
            outline=pending,
            ordered_units=_UNITS,
        )
        == ()
    )
    mismatch = _outline(
        OutlineNodeInput("n1", "u1", "u1"),
        base_id="other_base",
    )
    assert (
        project_section_candidates_from_outline(
            record_id="rec_1",
            base_id="base_1",
            generation=1,
            outline=mismatch,
            ordered_units=_UNITS,
        )
        == ()
    )


def test_sc04_any_invalid_node_fail_closed_zero() -> None:
    outline = _outline(
        OutlineNodeInput("n1", "u1", "u2", title="good", order_index=1),
        OutlineNodeInput("n2", "u4", "u1", title="inverted", order_index=2),
    )
    candidates = project_section_candidates_from_outline(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        outline=outline,
        ordered_units=_UNITS,
    )
    assert candidates == ()


def test_sc05_same_range_dedup_keeps_first_node_audit() -> None:
    outline = _outline(
        OutlineNodeInput("n1", "u1", "u3", title="First", order_index=1),
        OutlineNodeInput("n2", "u1", "u3", title="Dup", order_index=2),
    )
    candidates = project_section_candidates_from_outline(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        outline=outline,
        ordered_units=_UNITS,
    )
    assert len(candidates) == 1
    assert candidates[0].audit_node_id == "n1"
    assert candidates[0].title == "First"


def test_nested_ranges_not_merged() -> None:
    outline = _outline(
        OutlineNodeInput("parent", "u1", "u6", title="Parent", order_index=1),
        OutlineNodeInput("child", "u2", "u3", title="Child", order_index=2),
    )
    candidates = project_section_candidates_from_outline(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        outline=outline,
        ordered_units=_UNITS,
    )
    assert len(candidates) == 2
    assert candidates[0].identity.start_unit_id == "u1"
    assert candidates[0].identity.end_unit_id == "u6"
    assert candidates[1].identity.start_unit_id == "u2"
    assert candidates[1].identity.end_unit_id == "u3"
