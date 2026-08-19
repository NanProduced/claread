"""P0 forward-fix regression: pasted_text narrative paragraphs whose
source ends with the HTML space entity ``&#x20;`` used to lose the
second paragraph's automatic analysis layers.

Chain under test (production functions only, no DB):

    pasted_text -> InputDocumentNormalizer -> freeze plan
    -> Reading Base -> stable annotation analysis
    -> automatic-layer policy (grammar candidate filter)

Root cause being locked out: the Markdown parser decodes the trailing
``&#x20;`` into a real trailing space on ``ParsedBlock.text_content``;
the freeze canonical text kept that space while the base builder's
visible-unit ranges rstrip it, so the second paragraph's annotation
span exceeded its unit by one UTF-16 unit. The analyzer then recorded
``annotation_range_mismatch`` and the policy overrode that unit to
automatic-layer all-off (no grammar candidates).
"""

from __future__ import annotations

import pytest

from app.contracts.annotation import utf16_code_unit_length
from app.schemas.reader_documents import StableDocumentBlock
from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.services.reader_orchestration.automatic_layer_policy import (
    AutomaticLayerPolicy,
    build_reading_unit_metadata_json,
    build_semantic_integrity_override,
    filter_units_for_any_grammar,
)
from app.services.reader_orchestration.base_builder import (
    build_reading_base_from_canonical_text,
)
from app.services.reader_orchestration.document_freeze_plan import (
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.input_document_normalizer import (
    normalize_input_document,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)
from app.services.reader_orchestration.stable_annotation_analysis import (
    ANNOTATION_INLINE_MARK_INVALID,
    ANNOTATION_RANGE_MISMATCH,
    StableBlockAnnotation,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

_RECORD_ID = "rec-entity-tail-0001"
_BASE_ID = "00000000-0000-0000-0000-00000000e001"

# Two sufficiently long English narrative paragraphs separated by the
# ZWSP-only placeholder paragraph from the original report.
_PARAGRAPH_1 = (
    "The morning shift at the harbor begins before sunrise, when the "
    "first ferry leaves the dock and the gulls follow the warming "
    "engines across the bay toward the lighthouse."
)
_PARAGRAPH_2 = (
    "By noon the market stalls along the quay are full of fresh fish, "
    "and the fishermen argue cheerfully about the price of herring "
    "while tourists photograph the masts in the bright light."
)
_ZWSP = "\u200b"


def _run_chain(text: str):
    request = InputSuitabilityRequest(source_type="pasted_text", text=text)
    normalized = normalize_input_document(request)
    plan = build_stable_document_freeze_plan(
        reading_record_id=_RECORD_ID,
        record_generation=1,
        document_version=1,
        title=normalized.title,
        blocks=normalized.blocks,
    )
    # Same derivation as production persistence
    # (document_freeze_persistence._stable_block_annotations_from_plan):
    # every block with canonical offsets becomes an annotation and the
    # analyzer owns all validity judgement.
    annotations = [
        StableBlockAnnotation(
            start_utf16=block.canonical_text_start_utf16,
            end_utf16=block.canonical_text_end_utf16,
            block_type=block.block_type,
            block_id=block.block_id,
            parent_block_id=block.parent_block_id,
            payload_json=dict(block.payload_json) if block.payload_json else {},
        )
        for block in plan.blocks
        if block.canonical_text_start_utf16 is not None
        and block.canonical_text_end_utf16 is not None
    ]
    build = build_reading_base_from_canonical_text(
        reading_record_id=_RECORD_ID,
        base_id=_BASE_ID,
        canonical_text=plan.canonical_text,
        stable_block_annotations=annotations,
    )
    return normalized, plan, build


def _grammar_candidate_unit_rows(build) -> list[str]:
    """Replicate the persisted metadata + grammar job bootstrap filter.

    Mirrors repository._unit_metadata_json (semantic policy projection
    plus structural integrity override) followed by
    job_bootstrap's filter_units_for_any_grammar.
    """
    assert build.annotation_analysis is not None
    overrides = {
        override.unit_id: override
        for override in build.annotation_analysis.policy_overrides
    }
    rows: list[dict[str, object]] = []
    for unit in build.units:
        meta = build_reading_unit_metadata_json(
            sentence_provider=unit.sentence_provider,
            contract_version=unit.semantic_contract_version,
            content_role=unit.content_role,
            automatic_layer_policy=(
                AutomaticLayerPolicy.from_mapping(unit.automatic_layer_policy)
                if unit.automatic_layer_policy is not None
                else None
            ),
            resolver_version=unit.automatic_layer_policy_resolver_version,
        )
        override = overrides.get(unit.unit_id)
        if override is not None:
            meta["semantic_integrity_override"] = build_semantic_integrity_override(
                reason_code=override.reason_code,
            )
        rows.append({"unit_id": unit.unit_id, "metadata_json": meta})
    kept = filter_units_for_any_grammar(rows, record_id=_RECORD_ID, generation=1)
    return [str(row["unit_id"]) for row in kept]


def _assert_two_visible_units_grammar_ready(build) -> None:
    """Shared green assertions for a non-regressing two-paragraph input."""
    analysis = build.annotation_analysis
    assert analysis is not None
    # No annotation range mismatch diagnostic / override.
    mismatch_diagnostics = [
        d for d in analysis.diagnostics if d.code == ANNOTATION_RANGE_MISMATCH
    ]
    assert mismatch_diagnostics == []
    # No inline mark may exceed its block after tail normalization.
    invalid_mark_diagnostics = [
        d for d in analysis.diagnostics if d.code == ANNOTATION_INLINE_MARK_INVALID
    ]
    assert invalid_mark_diagnostics == []
    mismatch_overrides = [
        o
        for o in analysis.policy_overrides
        if o.reason_code == ANNOTATION_RANGE_MISMATCH
    ]
    assert mismatch_overrides == []
    # Exactly the two visible narrative units exist.
    assert len(build.units) == 2
    # Both units carry an all-on automatic-layer policy (grammar on).
    for unit in build.units:
        assert unit.automatic_layer_policy is not None
        policy = AutomaticLayerPolicy.from_mapping(unit.automatic_layer_policy)
        assert policy is not None and policy.grammar_note is True
    # Both units survive the grammar candidate filter.
    assert _grammar_candidate_unit_rows(build) == [
        unit.unit_id for unit in build.units
    ]
    # Canonical text tail boundary matches the last unit's end offset:
    # no invisible trailing whitespace is left outside the unit range.
    last_unit = build.units[-1]
    assert last_unit.base_end_utf16 == build.base.content_utf16_length


def test_entity_tail_whitespace_second_paragraph_keeps_automatic_layers():
    """`&#x20;`-terminated second paragraph must keep its automatic layers."""
    text = f"{_PARAGRAPH_1}\n\n{_ZWSP}\n\n{_PARAGRAPH_2}&#x20;"
    _normalized, _plan, build = _run_chain(text)
    _assert_two_visible_units_grammar_ready(build)


def test_literal_trailing_space_second_paragraph_keeps_automatic_layers():
    """A literal trailing space must not regress either."""
    text = f"{_PARAGRAPH_1}\n\n{_ZWSP}\n\n{_PARAGRAPH_2} "
    _normalized, _plan, build = _run_chain(text)
    _assert_two_visible_units_grammar_ready(build)


def test_clean_tail_second_paragraph_keeps_automatic_layers():
    """Baseline: no trailing entity/whitespace must keep working."""
    text = f"{_PARAGRAPH_1}\n\n{_ZWSP}\n\n{_PARAGRAPH_2}"
    _normalized, _plan, build = _run_chain(text)
    _assert_two_visible_units_grammar_ready(build)


# ---------------------------------------------------------------------------
# P1 regression: marks whose UTF-16 range reached into the trimmed tail
# ---------------------------------------------------------------------------

_EMPH_TAIL_PARAGRAPH = (
    "The harbor pilot patiently explained the tide tables and the "
    "channel markers to the young sailors who had never navigated the "
    "narrow strait at night *emphasis&#x20;*"
)

_LINK_TAIL_PARAGRAPH = (
    "The fishermen sold their morning catch directly from the wooden "
    "boats while the curious tourists asked about the freshest haul "
    "[https://example.com&#x20;](https://example.com)"
)


def _final_paragraph(normalized) -> StableDocumentBlock:
    paragraphs = [
        block for block in normalized.blocks if block.block_type == "paragraph"
    ]
    assert paragraphs
    return paragraphs[-1]


def _assert_marks_within_text(block: StableDocumentBlock) -> None:
    """Constraint 1: every kept mark satisfies 0 <= start < end <= len."""
    text = block.text_content or ""
    for mark in block.payload_json.get("inline_marks", []):
        assert 0 <= mark["start"] < mark["end"] <= utf16_code_unit_length(text), mark


def test_emphasis_entity_tail_keeps_valid_mark_and_automatic_layers():
    """`*emph&#x20;*` at the final paragraph tail: the em mark must be
    clamped to the trimmed text instead of pointing one unit past its
    end (which would trip ``annotation_inline_mark_invalid`` once the
    annotation range matches its unit again)."""
    text = f"{_PARAGRAPH_1}\n\n{_ZWSP}\n\n{_EMPH_TAIL_PARAGRAPH}"
    normalized, _plan, build = _run_chain(text)
    _assert_two_visible_units_grammar_ready(build)

    block = _final_paragraph(normalized)
    assert block.text_content is not None
    # The entity-decoded trailing space inside the emphasis is gone.
    assert block.text_content.endswith("night emphasis")
    _assert_marks_within_text(block)
    em_marks = [
        mark
        for mark in block.payload_json.get("inline_marks", [])
        if mark["type"] == "em"
    ]
    assert len(em_marks) == 1
    # The clamped em mark still covers the emphasis text at the tail.
    assert em_marks[0]["end"] == utf16_code_unit_length(block.text_content)


def test_link_entity_tail_keeps_valid_mark_label_and_automatic_layers():
    """A link whose label ends with ``&#x20;`` at the final paragraph
    tail: the link mark must be clamped, and the payload link text must
    match the trimmed block text (no removed trailing whitespace)."""
    text = f"{_PARAGRAPH_1}\n\n{_ZWSP}\n\n{_LINK_TAIL_PARAGRAPH}"
    normalized, _plan, build = _run_chain(text)
    _assert_two_visible_units_grammar_ready(build)

    block = _final_paragraph(normalized)
    assert block.text_content is not None
    assert block.text_content.endswith("haul https://example.com")
    _assert_marks_within_text(block)
    link_marks = [
        mark
        for mark in block.payload_json.get("inline_marks", [])
        if mark["type"] == "link"
    ]
    assert len(link_marks) == 1
    assert link_marks[0]["href"] == "https://example.com"
    assert link_marks[0]["end"] == utf16_code_unit_length(block.text_content)
    # Constraint 3: safe-link text matches the normalized block text.
    assert block.payload_json.get("links") == [
        {"text": "https://example.com", "href": "https://example.com"}
    ]


# ---------------------------------------------------------------------------
# P2 regression: unsafe-link audit labels vs tail trim
# ---------------------------------------------------------------------------

_UNSAFE_TAIL_PARAGRAPH = (
    "The fishermen sold their fresh morning catch directly from the "
    "wooden boats while the curious tourists kept asking about the "
    "newest haul and the market prices [forbidden&#x20;](javascript:alert(1))"
)


def test_unsafe_link_entity_tail_aligns_stripped_label_with_trimmed_body():
    """Terminal unsafe link whose label ends with ``&#x20;`` (raw-pattern
    path): the body is tail-trimmed, so the ``stripped_links`` audit text
    must match the trimmed body. The unsafe href stays confined to the
    audit record (never in ``links`` / ``inline_marks``) and the
    paragraph keeps its automatic layers."""
    text = f"{_PARAGRAPH_1}\n\n{_ZWSP}\n\n{_UNSAFE_TAIL_PARAGRAPH}"
    normalized, _plan, build = _run_chain(text)
    _assert_two_visible_units_grammar_ready(build)

    block = _final_paragraph(normalized)
    assert block.text_content is not None
    assert block.text_content.endswith("forbidden")
    assert block.payload_json.get("links") == []
    assert block.payload_json.get("inline_marks") is None
    assert block.payload_json.get("stripped_links") == [
        {
            "text": "forbidden",
            "href": "javascript:alert(1)",
            "reason": "unsafe_protocol",
        }
    ]


def test_unsafe_parsed_link_entity_tail_aligns_stripped_label():
    """The parsed unsafe-link path (markdown-it emits link_open /
    link_close for hrefs outside the source-link whitelist, e.g.
    ``ftp://``) must realign its audit label the same way."""
    result = MarkdownSourceParser().parse(
        "Words before the link [ftpbad&#x20;](ftp://example.com)"
    )
    paragraphs = [b for b in result.blocks if b.block_type == "paragraph"]
    assert len(paragraphs) == 1
    block = paragraphs[0]
    assert block.text_content is not None
    assert block.text_content.endswith("ftpbad")
    assert block.payload_json.get("stripped_links") == [
        {
            "text": "ftpbad",
            "href": "ftp://example.com",
            "reason": "unsafe_protocol",
        }
    ]


def test_unsafe_link_mid_text_label_keeps_internal_trailing_space():
    """Guard: realignment applies ONLY to labels covered by the tail
    trim. The required counterexample keeps the mid-text label's
    internal trailing space — no blanket rstrip."""
    result = MarkdownSourceParser().parse(
        "Words before the link [bad ](javascript:alert(1)) trailing&#x20;"
    )
    paragraphs = [b for b in result.blocks if b.block_type == "paragraph"]
    assert len(paragraphs) == 1
    block = paragraphs[0]
    assert block.text_content is not None
    assert block.text_content.endswith("trailing")
    assert block.payload_json.get("stripped_links") == [
        {
            "text": "bad ",
            "href": "javascript:alert(1)",
            "reason": "unsafe_protocol",
        }
    ]
