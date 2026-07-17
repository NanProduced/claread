"""T5.6a-P1 — server-authoritative explicit section request planner (pure).

plan_explicit_section_request(intent, facts) -> Admit | NoOp | Reject

Zero side effects: no DB, no jobs, no events, no budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

from .section_candidates import (
    TrustedOutlineInput,
    project_section_candidates_from_outline,
)
from .section_identity import (
    SectionIdentity,
    SectionIdentityError,
    SectionUnit,
    expand_closed_unit_range,
)


class PlanOutcomeKind(str, Enum):
    ADMIT = "admit"
    NO_OP = "no_op"
    REJECT = "reject"


class SectionRequestTrigger(str, Enum):
    """T5.6a only admits user_explicit. Other values are locked rejects."""

    USER_EXPLICIT = "user_explicit"
    VIEWPORT = "viewport"
    ASK = "ask"


# Locked reason codes (tests assert exact strings).
REASON_NO_REQUEST_SIGNAL = "no_request_signal"
REASON_UNSUPPORTED_TRIGGER = "unsupported_trigger"
REASON_UNAUTHORIZED = "unauthorized"
REASON_RECORD_MISMATCH = "record_mismatch"
REASON_SOURCE_MISMATCH = "source_mismatch"
REASON_INVALID_RANGE = "invalid_range"
REASON_NODE_ONLY = "node_only"
REASON_NO_TRUSTED_OUTLINE = "no_trusted_outline"
REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT = "section_already_covered_or_inflight"
REASON_SECTION_RANGE_OVERLAP = "section_range_overlap"
REASON_AMBIGUOUS_SECTION_RANGE = "ambiguous_section_range"
REASON_LAYER_FAMILY_REQUIRED = "layer_family_required"


@dataclass(frozen=True, slots=True)
class ExplicitSectionIntent:
    """Client/candidate intent — never authoritative alone.

    ``trigger`` is the sole request-signal gate (not a bare bool).
    ``node_id`` / ``outline_revision`` are audit-only; never sufficient for Admit.
    """

    trigger: SectionRequestTrigger | None = None
    layer_family: str | None = None
    record_id: str | None = None
    base_id: str | None = None
    generation: int | None = None
    start_unit_id: str | None = None
    end_unit_id: str | None = None
    start_anchor_segment_id: str | None = None
    end_anchor_segment_id: str | None = None
    node_id: str | None = None
    outline_revision: str | None = None


@dataclass(frozen=True, slots=True)
class SectionPlannerFacts:
    """Server-resolved facts (future adapter fills this from DB/auth).

    Overlap state is **per layer_family** — translation activity must not
    block vocabulary (and vice versa).
    """

    authorized: bool
    record_id: str
    base_id: str
    generation: int
    ordered_units: tuple[SectionUnit, ...]
    anchor_to_unit: Mapping[str, str] = field(default_factory=dict)
    trusted_outline: TrustedOutlineInput | None = None
    published_units_by_family: Mapping[str, frozenset[str]] = field(
        default_factory=dict
    )
    active_target_units_by_family: Mapping[str, frozenset[str]] = field(
        default_factory=dict
    )
    active_section_ranges_by_family: Mapping[str, tuple[tuple[str, str], ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class SectionPlanAudit:
    client_node_id: str | None = None
    client_outline_revision: str | None = None
    client_start_anchor_segment_id: str | None = None
    client_end_anchor_segment_id: str | None = None


@dataclass(frozen=True, slots=True)
class SectionPlanResult:
    kind: PlanOutcomeKind
    reason: str | None = None
    identity: SectionIdentity | None = None
    target_unit_ids: tuple[str, ...] = ()
    layer_family: str | None = None
    audit: SectionPlanAudit | None = None

    @property
    def side_effects(self) -> dict[str, int]:
        """T5.6a invariant: planner never schedules work or spends budget."""
        return {
            "jobs_created": 0,
            "events_emitted": 0,
            "budget_units_consumed": 0,
        }


def _reject(reason: str) -> SectionPlanResult:
    return SectionPlanResult(kind=PlanOutcomeKind.REJECT, reason=reason)


def _no_op(reason: str) -> SectionPlanResult:
    return SectionPlanResult(kind=PlanOutcomeKind.NO_OP, reason=reason)


def _ranges_overlap(
    *,
    a_start: str,
    a_end: str,
    b_start: str,
    b_end: str,
    ordered_units: Sequence[SectionUnit],
) -> bool:
    try:
        a_ids = set(
            expand_closed_unit_range(
                start_unit_id=a_start,
                end_unit_id=a_end,
                ordered_units=ordered_units,
            )
        )
        b_ids = set(
            expand_closed_unit_range(
                start_unit_id=b_start,
                end_unit_id=b_end,
                ordered_units=ordered_units,
            )
        )
    except SectionIdentityError:
        return False
    return bool(a_ids & b_ids)


def _match_canonical_candidate(
    *,
    start_unit_id: str,
    end_unit_id: str,
    candidates: Sequence,
) -> tuple[SectionIdentity | None, str | None]:
    """Match trusted candidates by unit pair; return server identity or reject reason."""
    matches = [
        c
        for c in candidates
        if c.identity.start_unit_id == start_unit_id
        and c.identity.end_unit_id == end_unit_id
    ]
    if not matches:
        return None, REASON_INVALID_RANGE
    # Distinct full geometric identities for the same unit pair → ambiguous.
    unique_geo = {c.identity.geometric_key() for c in matches}
    if len(unique_geo) > 1:
        return None, REASON_AMBIGUOUS_SECTION_RANGE
    return matches[0].identity, None


def plan_explicit_section_request(
    intent: ExplicitSectionIntent,
    facts: SectionPlannerFacts,
) -> SectionPlanResult:
    """Pure admission planner. Never creates jobs or consumes budget."""
    if intent.trigger is None:
        return _no_op(REASON_NO_REQUEST_SIGNAL)
    if intent.trigger is not SectionRequestTrigger.USER_EXPLICIT:
        return _reject(REASON_UNSUPPORTED_TRIGGER)

    if not facts.authorized:
        return _reject(REASON_UNAUTHORIZED)

    if not intent.layer_family:
        return _reject(REASON_LAYER_FAMILY_REQUIRED)
    layer_family = intent.layer_family

    if intent.record_id is not None and intent.record_id != facts.record_id:
        return _reject(REASON_RECORD_MISMATCH)

    if intent.base_id is not None and intent.base_id != facts.base_id:
        return _reject(REASON_SOURCE_MISMATCH)
    if intent.generation is not None and intent.generation != facts.generation:
        return _reject(REASON_SOURCE_MISMATCH)

    has_range = bool(intent.start_unit_id and intent.end_unit_id)
    if not has_range:
        # node_id alone — even if present in trusted outline — never Admits.
        return _reject(REASON_NODE_ONLY)

    if facts.trusted_outline is None:
        return _reject(REASON_NO_TRUSTED_OUTLINE)
    candidates = project_section_candidates_from_outline(
        record_id=facts.record_id,
        base_id=facts.base_id,
        generation=facts.generation,
        outline=facts.trusted_outline,
        ordered_units=facts.ordered_units,
        anchor_to_unit=facts.anchor_to_unit,
    )
    if not candidates:
        return _reject(REASON_NO_TRUSTED_OUTLINE)

    assert intent.start_unit_id is not None and intent.end_unit_id is not None
    canonical, match_reason = _match_canonical_candidate(
        start_unit_id=intent.start_unit_id,
        end_unit_id=intent.end_unit_id,
        candidates=candidates,
    )
    if match_reason is not None:
        return _reject(match_reason)
    assert canonical is not None

    audit = SectionPlanAudit(
        client_node_id=intent.node_id,
        client_outline_revision=intent.outline_revision,
        client_start_anchor_segment_id=intent.start_anchor_segment_id,
        client_end_anchor_segment_id=intent.end_anchor_segment_id,
    )

    try:
        target_unit_ids = expand_closed_unit_range(
            start_unit_id=canonical.start_unit_id,
            end_unit_id=canonical.end_unit_id,
            ordered_units=facts.ordered_units,
        )
    except SectionIdentityError:
        return _reject(REASON_INVALID_RANGE)

    requested = frozenset(target_unit_ids)
    published = facts.published_units_by_family.get(layer_family, frozenset())
    active_units = facts.active_target_units_by_family.get(layer_family, frozenset())
    if requested & published:
        return _no_op(REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT)
    if requested & active_units:
        return _no_op(REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT)

    active_ranges = facts.active_section_ranges_by_family.get(layer_family, ())
    for other_start, other_end in active_ranges:
        if _ranges_overlap(
            a_start=canonical.start_unit_id,
            a_end=canonical.end_unit_id,
            b_start=other_start,
            b_end=other_end,
            ordered_units=facts.ordered_units,
        ):
            return _no_op(REASON_SECTION_RANGE_OVERLAP)

    return SectionPlanResult(
        kind=PlanOutcomeKind.ADMIT,
        reason=None,
        identity=canonical,
        target_unit_ids=target_unit_ids,
        layer_family=layer_family,
        audit=audit,
    )


__all__ = [
    "ExplicitSectionIntent",
    "PlanOutcomeKind",
    "REASON_AMBIGUOUS_SECTION_RANGE",
    "REASON_INVALID_RANGE",
    "REASON_LAYER_FAMILY_REQUIRED",
    "REASON_NODE_ONLY",
    "REASON_NO_REQUEST_SIGNAL",
    "REASON_NO_TRUSTED_OUTLINE",
    "REASON_RECORD_MISMATCH",
    "REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT",
    "REASON_SECTION_RANGE_OVERLAP",
    "REASON_SOURCE_MISMATCH",
    "REASON_UNAUTHORIZED",
    "REASON_UNSUPPORTED_TRIGGER",
    "SectionPlanAudit",
    "SectionPlanResult",
    "SectionPlannerFacts",
    "SectionRequestTrigger",
    "plan_explicit_section_request",
]
