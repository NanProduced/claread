"""Focused unit tests for Reading Record Ask Context Envelope + tool contracts.

Coverage (this slice):
- envelope fingerprint stability for identical verified inputs
- record / base / generation / anchor each change the fingerprint
- visible_range stays None when omitted; empty/half ranges rejected
- agent/tool input schemas reject server-owned auth fields
- read_range locator modes (utf16 / whole unit|segment / order span)
- typed tool status + EvidenceHandleRef-only evidence_handles
- evidence kind/source legal combinations
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.reader_record_ask.context_envelope import (
    ENVELOPE_VERSION,
    SERVER_OWNED_SCOPE_FIELDS,
    EnvelopeInitialAnchor,
    EnvelopeVisibleRange,
    ReadingRecordAskAgentContextProjection,
    VerifiedEnvelopeInput,
    build_context_envelope,
    compute_envelope_fingerprint,
)
from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceHandle,
    ServerEvidenceObservation,
    assert_legal_evidence_kind_source,
    build_server_evidence_observation,
    is_valid_evidence_handle_id,
    mint_evidence_handle_id,
    mint_server_evidence_handle,
    parse_evidence_handle_ref,
)
from app.services.reader_record_ask.tool_contracts import (
    READ_RANGE_OFFSET_UNIT,
    ReaderRecordAskToolResult,
    ReadRangeLocator,
    ReadRangeToolInput,
    SearchCurrentArticleToolInput,
    assert_no_server_owned_fields,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_FINGERPRINT_HEX = "a" * 64
_BASE_CONTENT_SHA256 = "b" * 64
_HANDLE_AB = "evh_" + ("ab" * 16)
_HANDLE_CD = "evh_" + ("cd" * 16)


def _anchor(**overrides: object) -> EnvelopeInitialAnchor:
    payload = dict(
        unit_id="u1",
        anchor_segment_id="s1",
        start_offset=0,
        end_offset=5,
        selected_text="hello",
        text_hash="a1b2c3d4",
        base_start_utf16=10,
        base_end_utf16=15,
    )
    payload.update(overrides)
    return EnvelopeInitialAnchor(**payload)  # type: ignore[arg-type]


def _verified(**overrides: object) -> VerifiedEnvelopeInput:
    payload = dict(
        user_id=_USER,
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        stable_document_id=_DOC,
        base_content_sha256=_BASE_CONTENT_SHA256,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        initial_anchor=_anchor(),
        visible_range=None,
    )
    payload.update(overrides)
    return VerifiedEnvelopeInput(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fingerprint stability / sensitivity
# ---------------------------------------------------------------------------


def test_envelope_fingerprint_stable_for_identical_verified_input() -> None:
    left = build_context_envelope(_verified())
    right = build_context_envelope(_verified())
    assert left.envelope_fingerprint == right.envelope_fingerprint
    assert left.envelope_version == ENVELOPE_VERSION
    assert len(left.envelope_fingerprint) == 64


def test_fingerprint_helper_matches_factory() -> None:
    verified = _verified()
    envelope = build_context_envelope(verified)
    direct = compute_envelope_fingerprint(
        envelope_version=ENVELOPE_VERSION,
        user_id=verified.user_id,
        reading_record_id=verified.reading_record_id,
        base_id=verified.base_id,
        record_generation=verified.record_generation,
        stable_document_id=verified.stable_document_id,
        base_content_sha256=verified.base_content_sha256,
        initial_anchor=verified.initial_anchor,
        visible_range=verified.visible_range,
    )
    assert envelope.envelope_fingerprint == direct


@pytest.mark.parametrize(
    "field,value",
    [
        ("reading_record_id", UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        ("base_id", UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
        ("record_generation", 2),
        ("initial_anchor", _anchor(unit_id="u-other")),
    ],
)
def test_fingerprint_changes_when_record_base_generation_or_anchor_changes(
    field: str,
    value: object,
) -> None:
    base = build_context_envelope(_verified())
    changed = build_context_envelope(_verified(**{field: value}))
    assert base.envelope_fingerprint != changed.envelope_fingerprint


def test_fingerprint_changes_when_anchor_offsets_change() -> None:
    base = build_context_envelope(_verified())
    changed = build_context_envelope(
        _verified(initial_anchor=_anchor(start_offset=1, end_offset=6))
    )
    assert base.envelope_fingerprint != changed.envelope_fingerprint


def test_fingerprint_changes_when_stable_document_changes() -> None:
    base = build_context_envelope(_verified())
    changed = build_context_envelope(
        _verified(stable_document_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"))
    )
    assert base.envelope_fingerprint != changed.envelope_fingerprint


def test_base_content_sha256_requires_64_hex() -> None:
    with pytest.raises(ValidationError):
        _verified(base_content_sha256="contenthash001")
    with pytest.raises(ValidationError):
        _verified(base_content_sha256="G" * 64)
    ok = build_context_envelope(_verified(base_content_sha256="c" * 64))
    assert ok.base_content_sha256 == "c" * 64


# ---------------------------------------------------------------------------
# Visible range contract
# ---------------------------------------------------------------------------


def test_visible_range_missing_is_none_not_fabricated() -> None:
    envelope = build_context_envelope(_verified(visible_range=None))
    assert envelope.visible_range is None
    assert envelope.capabilities.has_visible_range is False


def test_visible_range_empty_object_rejected() -> None:
    with pytest.raises(ValidationError, match="complete"):
        EnvelopeVisibleRange()
    with pytest.raises(ValidationError, match="complete"):
        EnvelopeVisibleRange.model_validate({})


@pytest.mark.parametrize(
    "payload",
    [
        {"start_unit_id": "u1"},
        {"end_unit_id": "u3"},
        {"start_unit_order_index": 0},
        {"end_unit_order_index": 2},
        {"start_unit_id": "u1", "start_unit_order_index": 0},
    ],
)
def test_visible_range_half_specified_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EnvelopeVisibleRange.model_validate(payload)


def test_visible_range_order_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError, match="end_unit_order_index"):
        EnvelopeVisibleRange(
            start_unit_order_index=3,
            end_unit_order_index=1,
        )


def test_visible_range_passthrough_when_complete() -> None:
    visible = EnvelopeVisibleRange(
        start_unit_id="u1",
        end_unit_id="u3",
        start_unit_order_index=0,
        end_unit_order_index=2,
    )
    envelope = build_context_envelope(_verified(visible_range=visible))
    assert envelope.visible_range is not None
    assert envelope.visible_range.start_unit_id == "u1"
    assert envelope.capabilities.has_visible_range is True
    without = build_context_envelope(_verified(visible_range=None))
    assert envelope.envelope_fingerprint != without.envelope_fingerprint


def test_visible_range_order_only_complete_ok() -> None:
    visible = EnvelopeVisibleRange(
        start_unit_order_index=0,
        end_unit_order_index=4,
    )
    assert visible.start_unit_id is None
    envelope = build_context_envelope(_verified(visible_range=visible))
    assert envelope.capabilities.has_visible_range is True


# ---------------------------------------------------------------------------
# Agent projection boundary
# ---------------------------------------------------------------------------


def test_agent_projection_omits_server_owned_auth_fields() -> None:
    envelope = build_context_envelope(_verified())
    projection = envelope.to_agent_projection()
    assert isinstance(projection, ReadingRecordAskAgentContextProjection)
    dumped = projection.model_dump(mode="json")
    for field in SERVER_OWNED_SCOPE_FIELDS:
        assert field not in dumped
    assert "user_id" not in dumped
    assert "reading_record_id" not in dumped
    assert "base_id" not in dumped
    assert "record_generation" not in dumped
    assert "stable_document_id" not in dumped
    assert "rag_substrate_id" not in dumped
    assert projection.has_initial_selection is True
    assert projection.selection_preview == "hello"
    assert projection.has_visible_range is False


def test_agent_projection_rejects_extra_auth_fields() -> None:
    with pytest.raises(ValidationError):
        ReadingRecordAskAgentContextProjection(
            envelope_version=ENVELOPE_VERSION,
            has_initial_selection=False,
            has_visible_range=False,
            can_read_range=True,
            can_search_current_article=True,
            article_rag_ready=False,
            readiness_state="article_ready",
            user_id=str(_USER),  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Tool input schemas — no server-owned fields
# ---------------------------------------------------------------------------


def test_read_range_input_accepts_whole_unit() -> None:
    tool_input = ReadRangeToolInput(
        locator=ReadRangeLocator(unit_id="u1"),
        max_chars=500,
    )
    assert tool_input.locator.resolve_mode() == "whole_unit"
    assert tool_input.locator.offset_unit == READ_RANGE_OFFSET_UNIT


def test_read_range_input_accepts_unit_utf16_range() -> None:
    locator = ReadRangeLocator(unit_id="u1", start_offset=0, end_offset=10)
    assert locator.resolve_mode() == "unit_utf16_range"
    assert locator.offset_unit == "utf16"


def test_read_range_input_rejects_server_owned_fields() -> None:
    with pytest.raises(ValidationError):
        ReadRangeToolInput.model_validate(
            {
                "locator": {"unit_id": "u1"},
                "reading_record_id": str(_RECORD),
                "base_id": str(_BASE),
                "user_id": str(_USER),
            }
        )


def test_read_range_locator_rejects_server_owned_fields() -> None:
    with pytest.raises(ValidationError):
        ReadRangeLocator.model_validate(
            {
                "unit_id": "u1",
                "record_generation": 1,
                "stable_document_id": str(_DOC),
            }
        )


def test_search_current_article_input_accepts_query_only() -> None:
    tool_input = SearchCurrentArticleToolInput(query="main idea", limit=5)
    assert tool_input.query == "main idea"
    dumped = tool_input.model_dump()
    for field in SERVER_OWNED_SCOPE_FIELDS:
        assert field not in dumped


def test_search_current_article_rejects_server_owned_fields() -> None:
    with pytest.raises(ValidationError):
        SearchCurrentArticleToolInput.model_validate(
            {
                "query": "main idea",
                "reading_record_id": str(_RECORD),
                "rag_substrate_id": "rag_x",
                "source_scope": "main_reading_text",
            }
        )


def test_assert_no_server_owned_fields_helper() -> None:
    assert_no_server_owned_fields({"query": "ok"})
    with pytest.raises(ValueError, match="server-owned"):
        assert_no_server_owned_fields({"query": "x", "user_id": "u", "base_id": "b"})


def test_read_range_locator_requires_legal_mode() -> None:
    with pytest.raises(ValidationError):
        ReadRangeLocator()


@pytest.mark.parametrize(
    "payload",
    [
        {"unit_id": "u1", "start_offset": 0},  # half offset
        {"unit_id": "u1", "end_offset": 10},  # half offset
        {"start_offset": 0, "end_offset": 10},  # offsets without target
        {"start_unit_order_index": 0},  # half order
        {"end_unit_order_index": 2},
        {
            "unit_id": "u1",
            "start_unit_order_index": 0,
            "end_unit_order_index": 2,
        },  # mix order + unit
        {
            "unit_id": "u1",
            "start_offset": 0,
            "end_offset": 5,
            "start_unit_order_index": 0,
            "end_unit_order_index": 1,
        },
        {"unit_id": "u1", "start_offset": 5, "end_offset": 5},  # end <= start
    ],
)
def test_read_range_locator_illegal_combinations_rejected(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ReadRangeLocator.model_validate(payload)


@pytest.mark.parametrize(
    "payload,mode",
    [
        ({"unit_id": "u1"}, "whole_unit"),
        ({"anchor_segment_id": "s1"}, "whole_segment"),
        ({"unit_id": "u1", "anchor_segment_id": "s1"}, "whole_segment"),
        (
            {"start_unit_order_index": 0, "end_unit_order_index": 3},
            "unit_order_span",
        ),
        (
            {"unit_id": "u1", "start_offset": 0, "end_offset": 8},
            "unit_utf16_range",
        ),
        (
            {
                "anchor_segment_id": "s1",
                "start_offset": 0,
                "end_offset": 4,
            },
            "segment_utf16_range",
        ),
        (
            {
                "unit_id": "u1",
                "anchor_segment_id": "s1",
                "start_offset": 1,
                "end_offset": 3,
            },
            "segment_utf16_range",
        ),
    ],
)
def test_read_range_locator_legal_modes(
    payload: dict[str, object],
    mode: str,
) -> None:
    locator = ReadRangeLocator.model_validate(payload)
    assert locator.resolve_mode() == mode
    assert locator.offset_unit == "utf16"


# ---------------------------------------------------------------------------
# Typed tool result status + evidence_handles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        "ok",
        "empty",
        "not_ready",
        "not_indexed",
        "indexing",
        "unavailable",
        "budget_exhausted",
        "invalid_locator",
        "context_stale",
        "error",
    ],
)
def test_tool_result_accepts_typed_statuses(status: str) -> None:
    result = ReaderRecordAskToolResult(
        status=status,  # type: ignore[arg-type]
        summary=f"status={status}",
        next_actions=["answer_with_existing_evidence"],
        payloads={"detail": status},
        evidence_handles=[],
    )
    assert result.status == status
    assert result.payloads == {"detail": status}


def test_tool_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ReaderRecordAskToolResult(
            status="weird_string",  # type: ignore[arg-type]
            summary="nope",
        )


def test_tool_result_strips_empty_next_actions() -> None:
    result = ReaderRecordAskToolResult(
        status="error",
        summary="failed",
        next_actions=["  retry  ", "", "   "],
    )
    assert result.next_actions == ["retry"]


def test_tool_result_accepts_evidence_handle_refs() -> None:
    result = ReaderRecordAskToolResult(
        status="ok",
        summary="read",
        evidence_handles=[
            EvidenceHandleRef(handle_id=_HANDLE_AB),
            {"handle_id": _HANDLE_CD},
            _HANDLE_AB,  # mint-shaped bare string coerced then validated
        ],
    )
    assert len(result.evidence_handles) == 3
    assert all(isinstance(item, EvidenceHandleRef) for item in result.evidence_handles)
    assert result.evidence_handles[0].handle_id == _HANDLE_AB


@pytest.mark.parametrize(
    "bad_handle",
    [
        "not-a-handle",
        "citation_1",
        "evh_short",
        "evh_" + ("zz" * 16),  # non-hex
        {"handle_id": "freeform"},
    ],
)
def test_tool_result_rejects_illegal_evidence_handles(bad_handle: object) -> None:
    with pytest.raises(ValidationError):
        ReaderRecordAskToolResult(
            status="ok",
            summary="read",
            evidence_handles=[bad_handle],  # type: ignore[list-item]
        )


# ---------------------------------------------------------------------------
# Evidence handle contract
# ---------------------------------------------------------------------------


def test_mint_evidence_handle_shape() -> None:
    handle_id = mint_evidence_handle_id()
    assert is_valid_evidence_handle_id(handle_id)
    ref = parse_evidence_handle_ref(handle_id)
    assert ref.handle_id == handle_id


def test_evidence_handle_ref_rejects_illegal_ids() -> None:
    with pytest.raises(ValidationError):
        EvidenceHandleRef(handle_id="not-a-handle")
    with pytest.raises(ValidationError):
        EvidenceHandleRef(handle_id="evh_short")
    with pytest.raises(ValidationError):
        EvidenceHandleRef(handle_id="citation_1")


def test_server_evidence_observation_legal_input() -> None:
    handle = mint_server_evidence_handle(
        kind="read_range",
        envelope_fingerprint=_FINGERPRINT_HEX,
        source_tool="read_range",
        handle_id=_HANDLE_AB,
    )
    obs = ServerEvidenceObservation(
        handle=handle,
        snippet="excerpt",
        locator_summary={"unit_id": "u1"},
        unit_id="u1",
    )
    assert obs.handle.handle_id.startswith("evh_")
    assert obs.locator_summary == {"unit_id": "u1"}


def test_server_evidence_observation_rejects_substrate_authority_in_locator() -> None:
    handle = mint_server_evidence_handle(
        kind="search_hit",
        envelope_fingerprint=_FINGERPRINT_HEX,
        source_tool="search_current_article",
    )
    with pytest.raises(ValidationError, match="authority"):
        ServerEvidenceObservation(
            handle=handle,
            locator_summary={"rag_substrate_id": "rag_x", "unit_id": "u1"},
        )


def test_build_server_evidence_observation_helper() -> None:
    obs = build_server_evidence_observation(
        kind="initial_anchor",
        envelope_fingerprint=_FINGERPRINT_HEX,
        source_tool="initial_anchor",
        snippet="hello",
        unit_id="u1",
        anchor_segment_id="s1",
        handle_id=_HANDLE_CD,
    )
    assert obs.handle.kind == "initial_anchor"
    assert obs.snippet == "hello"
    assert is_valid_evidence_handle_id(obs.handle.handle_id)


def test_server_handle_rejects_bad_fingerprint_length() -> None:
    with pytest.raises((ValidationError, ValueError)):
        mint_server_evidence_handle(
            kind="observation",
            envelope_fingerprint="too-short",
            source_tool="read_range",
        )


def test_evidence_kind_source_legal_pairs() -> None:
    assert_legal_evidence_kind_source("initial_anchor", "initial_anchor")
    assert_legal_evidence_kind_source("read_range", "read_range")
    assert_legal_evidence_kind_source("search_hit", "search_current_article")
    assert_legal_evidence_kind_source("observation", "search_current_article")


def test_evidence_kind_source_illegal_pairs_rejected() -> None:
    with pytest.raises(ValueError, match="illegal evidence kind/source"):
        assert_legal_evidence_kind_source("initial_anchor", "search_current_article")
    with pytest.raises((ValidationError, ValueError)):
        mint_server_evidence_handle(
            kind="initial_anchor",
            envelope_fingerprint=_FINGERPRINT_HEX,
            source_tool="search_current_article",
        )
    with pytest.raises(ValidationError):
        ServerEvidenceHandle(
            handle_id=_HANDLE_AB,
            kind="search_hit",
            envelope_fingerprint=_FINGERPRINT_HEX,
            source_tool="read_range",
        )
