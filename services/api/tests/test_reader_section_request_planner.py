"""T5.6a-P1 — Planner authority, triggers, family isolation, canonical identity."""

from __future__ import annotations

from app.services.reader_orchestration.section_candidates import (
    OutlineNodeInput,
    TrustedOutlineInput,
)
from app.services.reader_orchestration.section_identity import (
    SectionUnit,
    encode_section_target_key,
)
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    PlanOutcomeKind,
    REASON_AMBIGUOUS_SECTION_RANGE,
    REASON_INVALID_RANGE,
    REASON_NODE_ONLY,
    REASON_NO_REQUEST_SIGNAL,
    REASON_RECORD_MISMATCH,
    REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT,
    REASON_SECTION_RANGE_OVERLAP,
    REASON_SOURCE_MISMATCH,
    REASON_UNAUTHORIZED,
    REASON_UNSUPPORTED_TRIGGER,
    SectionPlannerFacts,
    SectionRequestTrigger,
    plan_explicit_section_request,
)

_UNITS = (
    SectionUnit("u1", 1),
    SectionUnit("u2", 2),
    SectionUnit("u3", 3),
    SectionUnit("u4", 4),
    SectionUnit("u5", 5),
    SectionUnit("u6", 6),
)


def _outline(
    *extra: OutlineNodeInput,
    nodes: tuple[OutlineNodeInput, ...] | None = None,
) -> TrustedOutlineInput:
    default = (
        OutlineNodeInput("n_parent", "u1", "u6", title="Whole", order_index=1),
        OutlineNodeInput("n_child", "u2", "u3", title="Child", order_index=2),
        OutlineNodeInput("n_tail", "u4", "u5", title="Tail", order_index=3),
    )
    return TrustedOutlineInput(
        status="ready",
        source_base_id="base_1",
        source_generation=1,
        outline_revision="rev_1",
        nodes=nodes if nodes is not None else default + extra,
    )


def _facts(**overrides: object) -> SectionPlannerFacts:
    base = dict(
        authorized=True,
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        ordered_units=_UNITS,
        anchor_to_unit={},
        trusted_outline=_outline(),
        published_units_by_family={},
        active_target_units_by_family={},
        active_section_ranges_by_family={},
    )
    base.update(overrides)
    return SectionPlannerFacts(**base)  # type: ignore[arg-type]


def _intent(**overrides: object) -> ExplicitSectionIntent:
    base = dict(
        trigger=SectionRequestTrigger.USER_EXPLICIT,
        layer_family="translation",
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u4",
        end_unit_id="u5",
    )
    base.update(overrides)
    return ExplicitSectionIntent(**base)  # type: ignore[arg-type]


def test_sc08_sc09_admit_and_no_trigger() -> None:
    result = plan_explicit_section_request(_intent(), _facts())
    assert result.kind == PlanOutcomeKind.ADMIT
    assert result.target_unit_ids == ("u4", "u5")
    assert result.layer_family == "translation"
    assert result.identity is not None
    assert result.side_effects == {
        "jobs_created": 0,
        "events_emitted": 0,
        "budget_units_consumed": 0,
    }

    quiet = plan_explicit_section_request(_intent(trigger=None), _facts())
    assert quiet.kind == PlanOutcomeKind.NO_OP
    assert quiet.reason == REASON_NO_REQUEST_SIGNAL


def test_trigger_viewport_and_ask_rejected() -> None:
    for trig in (SectionRequestTrigger.VIEWPORT, SectionRequestTrigger.ASK):
        result = plan_explicit_section_request(_intent(trigger=trig), _facts())
        assert result.kind == PlanOutcomeKind.REJECT
        assert result.reason == REASON_UNSUPPORTED_TRIGGER


def test_sc13_published_same_family_no_op() -> None:
    result = plan_explicit_section_request(
        _intent(start_unit_id="u4", end_unit_id="u5"),
        _facts(
            published_units_by_family={"translation": frozenset({"u5"})},
        ),
    )
    assert result.kind == PlanOutcomeKind.NO_OP
    assert result.reason == REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT


def test_sc14_active_window_same_family_no_op() -> None:
    full = frozenset({"u1", "u2", "u3", "u4", "u5", "u6"})
    result = plan_explicit_section_request(
        _intent(start_unit_id="u4", end_unit_id="u5"),
        _facts(active_target_units_by_family={"translation": full}),
    )
    assert result.kind == PlanOutcomeKind.NO_OP
    assert result.reason == REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT

    result2 = plan_explicit_section_request(
        _intent(start_unit_id="u2", end_unit_id="u3"),
        _facts(
            active_target_units_by_family={
                "translation": frozenset({"u3", "u4"}),
            }
        ),
    )
    assert result2.kind == PlanOutcomeKind.NO_OP
    assert result2.reason == REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT


def test_sc15_nested_same_family_no_op() -> None:
    result = plan_explicit_section_request(
        _intent(start_unit_id="u2", end_unit_id="u3"),
        _facts(
            active_section_ranges_by_family={
                "translation": (("u1", "u6"),),
            }
        ),
    )
    assert result.kind == PlanOutcomeKind.NO_OP
    assert result.reason == REASON_SECTION_RANGE_OVERLAP


def test_cross_family_isolation_admits_despite_other_family_activity() -> None:
    # translation fully covered/active must NOT block vocabulary.
    result = plan_explicit_section_request(
        _intent(layer_family="vocabulary", start_unit_id="u4", end_unit_id="u5"),
        _facts(
            published_units_by_family={
                "translation": frozenset({"u1", "u2", "u3", "u4", "u5", "u6"}),
            },
            active_target_units_by_family={
                "translation": frozenset({"u1", "u2", "u3", "u4", "u5", "u6"}),
            },
            active_section_ranges_by_family={
                "translation": (("u1", "u6"),),
            },
        ),
    )
    assert result.kind == PlanOutcomeKind.ADMIT
    assert result.layer_family == "vocabulary"
    assert result.target_unit_ids == ("u4", "u5")


def test_node_only_always_reject_even_if_known() -> None:
    known = plan_explicit_section_request(
        _intent(start_unit_id=None, end_unit_id=None, node_id="n_tail"),
        _facts(),
    )
    assert known.kind == PlanOutcomeKind.REJECT
    assert known.reason == REASON_NODE_ONLY

    unknown = plan_explicit_section_request(
        _intent(start_unit_id=None, end_unit_id=None, node_id="n_missing"),
        _facts(),
    )
    assert unknown.kind == PlanOutcomeKind.REJECT
    assert unknown.reason == REASON_NODE_ONLY

    no_outline = plan_explicit_section_request(
        _intent(start_unit_id=None, end_unit_id=None, node_id="n_tail"),
        _facts(trusted_outline=None),
    )
    assert no_outline.kind == PlanOutcomeKind.REJECT
    assert no_outline.reason == REASON_NODE_ONLY


def test_sc16_forged_unauthorized_wrong_source() -> None:
    assert (
        plan_explicit_section_request(_intent(), _facts(authorized=False)).reason
        == REASON_UNAUTHORIZED
    )
    assert (
        plan_explicit_section_request(
            _intent(record_id="other_rec"),
            _facts(),
        ).reason
        == REASON_RECORD_MISMATCH
    )
    assert (
        plan_explicit_section_request(
            _intent(base_id="base_other"),
            _facts(),
        ).reason
        == REASON_SOURCE_MISMATCH
    )
    illegal = plan_explicit_section_request(
        _intent(start_unit_id="u1", end_unit_id="u2"),
        _facts(),
    )
    assert illegal.kind == PlanOutcomeKind.REJECT
    assert illegal.reason == REASON_INVALID_RANGE


def test_server_canonical_identity_rewrites_client_anchors() -> None:
    anchors = {"seg_u4": "u4", "seg_u5": "u5"}
    outline = TrustedOutlineInput(
        status="ready",
        source_base_id="base_1",
        source_generation=1,
        outline_revision="rev_1",
        nodes=(
            OutlineNodeInput(
                "n_tail",
                "u4",
                "u5",
                title="Tail",
                order_index=1,
                start_anchor_segment_id="seg_u4",
                end_anchor_segment_id="seg_u5",
            ),
        ),
    )
    result = plan_explicit_section_request(
        _intent(
            start_unit_id="u4",
            end_unit_id="u5",
            start_anchor_segment_id="client_forged_anchor",
            end_anchor_segment_id="also_forged",
            node_id="n_tail",
            outline_revision="client_stale",
        ),
        _facts(trusted_outline=outline, anchor_to_unit=anchors),
    )
    assert result.kind == PlanOutcomeKind.ADMIT
    assert result.identity is not None
    assert result.identity.start_anchor_segment_id == "seg_u4"
    assert result.identity.end_anchor_segment_id == "seg_u5"
    # Durable target key follows server candidate, not client anchors.
    assert "client_forged" not in encode_section_target_key(result.identity)
    assert result.audit is not None
    assert result.audit.client_start_anchor_segment_id == "client_forged_anchor"
    assert result.audit.client_outline_revision == "client_stale"


def test_ambiguous_unit_pair_multiple_candidates_reject() -> None:
    # Same unit pair, different valid anchors → two distinct trusted candidates.
    anchors = {
        "a1": "u4",
        "a2": "u5",
        "b1": "u4",
        "b2": "u5",
    }
    outline = TrustedOutlineInput(
        status="ready",
        source_base_id="base_1",
        source_generation=1,
        outline_revision="rev_1",
        nodes=(
            OutlineNodeInput(
                "n_a",
                "u4",
                "u5",
                title="A",
                order_index=1,
                start_anchor_segment_id="a1",
                end_anchor_segment_id="a2",
            ),
            OutlineNodeInput(
                "n_b",
                "u4",
                "u5",
                title="B",
                order_index=2,
                start_anchor_segment_id="b1",
                end_anchor_segment_id="b2",
            ),
        ),
    )
    result = plan_explicit_section_request(
        _intent(start_unit_id="u4", end_unit_id="u5"),
        _facts(trusted_outline=outline, anchor_to_unit=anchors),
    )
    assert result.kind == PlanOutcomeKind.REJECT
    assert result.reason == REASON_AMBIGUOUS_SECTION_RANGE


def test_admit_has_no_job_event_budget_side_effects() -> None:
    result = plan_explicit_section_request(_intent(), _facts())
    assert result.kind == PlanOutcomeKind.ADMIT
    assert result.side_effects == {
        "jobs_created": 0,
        "events_emitted": 0,
        "budget_units_consumed": 0,
    }
