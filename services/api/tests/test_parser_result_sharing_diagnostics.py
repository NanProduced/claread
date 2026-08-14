# task-history: (renamed from test_a4_parser_result_sharing_diagnostics.py)
"""解析结果共享 + 诊断透传（TDD）。

Tests that gate / normalizer / candidate creation accept an optional
``preparsed: MarkdownParseResult`` parameter so callers parse once and
share the result across the pipeline (4 parses → 1 parse).

Also verifies:
- ``NormalizedInputDocument.warnings`` propagates parser warning codes
  (the hardcoded ``[]`` is removed).
- ``plaintext_upgraded_to_markdown`` warning is recorded when a
  plain-text source silently upgrades to the markdown path.
- The legacy ``_strip_inline_markdown`` regex path is removed from the
  plain-text normalizer (inline flattening reuses the parser).

Public API route/schema contracts are unchanged — only the in-process
function signatures gain an optional keyword argument.
"""

from __future__ import annotations

import inspect

import pytest

from app.schemas.reader_input_adapter import (
    InputSuitabilityRequest,
    NormalizedInputDocument,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    _build_candidate_blocks,
)
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizer,
    normalize_input_document,
)
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityGate,
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


def _english_paragraph(multiplier: int = 1) -> str:
    sentence = (
        "This article explains how communities compare evidence, revise plans, "
        "and discuss tradeoffs before making a decision about public projects. "
        "Each paragraph stays focused on natural language reading, includes "
        "complete sentences, and keeps enough context for vocabulary, grammar, "
        "and sentence analysis to be genuinely useful for an English learner."
    )
    return "\n\n".join(sentence for _ in range(multiplier))


def _parse(text: str):
    return MarkdownSourceParser().parse(text)


# ---------------------------------------------------------------------------
# Gate accepts optional preparsed parameter
# ---------------------------------------------------------------------------


def test_evaluate_input_suitability_accepts_preparsed_parameter() -> None:
    """Signature MUST accept ``preparsed: MarkdownParseResult | None``."""
    sig = inspect.signature(evaluate_input_suitability)
    assert "preparsed" in sig.parameters
    param = sig.parameters["preparsed"]
    assert param.default is None


def test_gate_evaluate_accepts_preparsed_parameter() -> None:
    """Method signature MUST accept ``preparsed`` keyword argument."""
    sig = inspect.signature(InputSuitabilityGate.evaluate)
    assert "preparsed" in sig.parameters
    assert sig.parameters["preparsed"].default is None


def test_gate_uses_preparsed_and_does_not_reparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``preparsed`` is supplied, the gate MUST NOT call the parser."""
    text = _english_paragraph(multiplier=2)
    request = InputSuitabilityRequest(
        source_type="pasted_text",
        text=text,
        source_metadata={},
    )
    preparsed = _parse(text)

    call_count = {"calls": 0}
    original_parse = MarkdownSourceParser.parse

    def counting_parse(self, raw_text):  # type: ignore[no-untyped-def]
        call_count["calls"] += 1
        return original_parse(self, raw_text)

    monkeypatch.setattr(MarkdownSourceParser, "parse", counting_parse)

    result = evaluate_input_suitability(request, preparsed=preparsed)

    assert result.outcome == "stable_document_ready"
    # Gate must NOT re-parse when preparsed is provided.
    assert call_count["calls"] == 0


def test_gate_without_preparsed_still_parses_internally() -> None:
    """Without preparsed, the gate MUST continue to parse (legacy path)."""
    text = _english_paragraph(multiplier=2)
    request = InputSuitabilityRequest(
        source_type="pasted_text",
        text=text,
        source_metadata={},
    )
    result = evaluate_input_suitability(request)
    assert result.outcome == "stable_document_ready"


# ---------------------------------------------------------------------------
# Normalizer accepts preparsed + propagates warnings
# ---------------------------------------------------------------------------


def test_normalize_input_document_accepts_preparsed_parameter() -> None:
    sig = inspect.signature(normalize_input_document)
    assert "preparsed" in sig.parameters
    assert sig.parameters["preparsed"].default is None


def test_normalizer_class_normalize_accepts_preparsed_parameter() -> None:
    sig = inspect.signature(InputDocumentNormalizer.normalize)
    assert "preparsed" in sig.parameters
    assert sig.parameters["preparsed"].default is None


def test_normalizer_uses_preparsed_and_does_not_reparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _english_paragraph(multiplier=2)
    request = InputSuitabilityRequest(
        source_type="pasted_text",
        text=text,
        source_metadata={},
    )
    preparsed = _parse(text)

    call_count = {"calls": 0}
    original_parse = MarkdownSourceParser.parse

    def counting_parse(self, raw_text):  # type: ignore[no-untyped-def]
        call_count["calls"] += 1
        return original_parse(self, raw_text)

    monkeypatch.setattr(MarkdownSourceParser, "parse", counting_parse)

    normalized = normalize_input_document(request, preparsed=preparsed)

    assert normalized.source_type == "pasted_text"
    # Normalizer must NOT re-parse when preparsed is provided.
    assert call_count["calls"] == 0


def test_normalizer_propagates_parser_warning_codes_into_normalized_warnings() -> None:
    """When parser emits warnings (e.g. unsafe link), normalizer MUST
    propagate their codes into ``NormalizedInputDocument.warnings``
    instead of hardcoding ``[]``.
    """
    text = (
        f"{_english_paragraph(multiplier=2)}\n\n"
        "Readers cite [the source note](javascript:alert(1)) carefully."
    )
    request = InputSuitabilityRequest(
        source_type="markdown_file",
        filename="review.md",
        text=text,
        source_metadata={},
    )
    preparsed = _parse(text)

    normalized = normalize_input_document(request, preparsed=preparsed)

    # Parser must have emitted the unsafe_link_protocol warning.
    parser_warning_codes = {w.code for w in preparsed.warnings}
    assert "unsafe_link_protocol" in parser_warning_codes
    # Normalized warnings MUST include that code (no longer hardcoded []).
    assert "unsafe_link_protocol" in normalized.warnings


def test_normalizer_adds_plaintext_upgraded_to_markdown_warning_when_upgrades() -> None:
    """When a plain-text probe detects Markdown structure and upgrades
    to the Markdown path, the normalizer MUST record a
    ``plaintext_upgraded_to_markdown`` warning so the frontend can hint.
    """
    # Plain-text source that secretly contains Markdown structure.
    text = (
        f"{_english_paragraph()}\n\n"
        "# Hidden Heading\n\n"
        "- Readers notice the hidden heading and list structure.\n"
        "- The plain text path upgrades to Markdown parsing."
    )
    request = InputSuitabilityRequest(
        source_type="pasted_text",
        text=text,
        source_metadata={},
    )
    preparsed = _parse(text)
    # Sanity: parser sees non-paragraph blocks.
    block_types = {b.block_type for b in preparsed.blocks}
    assert "heading" in block_types
    assert "list_item" in block_types

    normalized = normalize_input_document(request, preparsed=preparsed)
    assert "plaintext_upgraded_to_markdown" in normalized.warnings


def test_normalizer_does_not_add_upgrade_warning_for_pure_markdown_source() -> None:
    """``markdown_file`` source MUST NOT emit the upgrade warning
    (it's already the Markdown path).
    """
    text = f"# Title\n\n{_english_paragraph(multiplier=2)}"
    request = InputSuitabilityRequest(
        source_type="markdown_file",
        filename="review.md",
        text=text,
        source_metadata={},
    )
    preparsed = _parse(text)
    normalized = normalize_input_document(request, preparsed=preparsed)
    assert "plaintext_upgraded_to_markdown" not in normalized.warnings


# ---------------------------------------------------------------------------
# Legacy _strip_inline_markdown regex path is removed
# ---------------------------------------------------------------------------


def test_strip_inline_markdown_is_removed_from_normalizer_module() -> None:
    """The legacy regex-based ``_strip_inline_markdown`` helper MUST be
    deleted from the normalizer module; plain-text inline flattening
    MUST reuse the parser instead of regex heuristics.
    """
    from app.services.reader_orchestration import input_document_normalizer as mod

    assert not hasattr(mod, "_strip_inline_markdown"), (
        "_strip_inline_markdown must be removed; plain-text path must "
        "reuse the parser inline flatten."
    )
    # Associated regex constants are no longer needed.
    for legacy_attr in (
        "_INLINE_LINK_PATTERN",
        "_INLINE_CODE_PATTERN",
        "_STRONG_PATTERN",
        "_EMPHASIS_PATTERN",
    ):
        assert not hasattr(mod, legacy_attr), (
            f"{legacy_attr} must be removed along with _strip_inline_markdown"
        )


def test_plain_text_path_preserves_inline_link_text_via_parser() -> None:
    """Plain-text input with inline Markdown markers MUST still flatten
    correctly (label preserved, markers removed) using the parser path.
    """
    text = (
        f"{_english_paragraph(multiplier=2)}\n\n"
        "Readers cite **important evidence**, add *context*, reference "
        "`key terms`, and keep [the source note](https://example.com/note) "
        "readable for learners."
    )
    request = InputSuitabilityRequest(
        source_type="pasted_text",
        text=text,
        source_metadata={},
    )
    preparsed = _parse(text)

    normalized = normalize_input_document(request, preparsed=preparsed)

    inline_block = next(
        block
        for block in normalized.blocks
        if block.block_type == "paragraph" and "the source note" in (block.text_content or "")
    )
    assert inline_block.text_content == (
        "Readers cite important evidence, add context, reference key terms, "
        "and keep the source note readable for learners."
    )
    # Safe link captured in payload_json per Structured Source Contract.
    assert inline_block.payload_json["links"] == [
        {"text": "the source note", "href": "https://example.com/note"}
    ]


# ---------------------------------------------------------------------------
# Candidate creation service accepts preparsed
# ---------------------------------------------------------------------------


def test_build_candidate_blocks_accepts_preparsed_parameter() -> None:
    sig = inspect.signature(_build_candidate_blocks)
    assert "preparsed" in sig.parameters
    assert sig.parameters["preparsed"].default is None


def test_build_candidate_blocks_uses_preparsed_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``preparsed`` is provided, ``_build_candidate_blocks`` MUST
    NOT call the parser again.
    """
    text = (
        f"{_english_paragraph(multiplier=2)}\n\n"
        "| City | Cost |\n| --- | --- |\n| A | 10 |"
    )
    preparsed = _parse(text)

    call_count = {"calls": 0}
    original_parse = MarkdownSourceParser.parse

    def counting_parse(self, raw_text):  # type: ignore[no-untyped-def]
        call_count["calls"] += 1
        return original_parse(self, raw_text)

    monkeypatch.setattr(MarkdownSourceParser, "parse", counting_parse)

    from uuid import uuid4

    blocks, _title = _build_candidate_blocks(
        source_type="markdown_file",
        text=text,
        filename="review.md",
        source_metadata={},
        original_input_id=uuid4(),
        preparsed=preparsed,
    )

    assert blocks  # candidate blocks produced
    assert call_count["calls"] == 0


# ---------------------------------------------------------------------------
# Full pipeline share — gate + normalizer parse exactly once
# ---------------------------------------------------------------------------


def test_full_pipeline_shares_single_parse_for_markdown_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: gate + normalizer for a stable-document-ready
    Markdown input MUST invoke the parser exactly once when the
    caller pre-parses and threads ``preparsed`` through both calls.
    """
    text = f"# Title\n\n{_english_paragraph(multiplier=2)}"
    request = InputSuitabilityRequest(
        source_type="markdown_file",
        filename="review.md",
        text=text,
        source_metadata={},
    )

    call_count = {"calls": 0}
    original_parse = MarkdownSourceParser.parse

    def counting_parse(self, raw_text):  # type: ignore[no-untyped-def]
        call_count["calls"] += 1
        return original_parse(self, raw_text)

    monkeypatch.setattr(MarkdownSourceParser, "parse", counting_parse)

    preparsed = original_parse(MarkdownSourceParser(), text)
    # The single parse above is the caller's pre-parse. Reset counter
    # so we only count downstream parser invocations.
    call_count["calls"] = 0

    suitability = evaluate_input_suitability(request, preparsed=preparsed)
    assert suitability.outcome == "stable_document_ready"

    normalized = normalize_input_document(request, preparsed=preparsed)
    assert normalized.blocks
    assert normalized.parser_identity is not None

    # The downstream pipeline (gate + normalizer) MUST NOT re-parse.
    assert call_count["calls"] == 0


def test_normalized_document_warnings_field_remains_list_of_strings() -> None:
    """Schema contract: ``warnings`` stays ``list[str]`` (warning codes).
    No structural change to the public Pydantic model.
    """
    import typing

    fields = NormalizedInputDocument.model_fields
    assert "warnings" in fields
    # Pydantic stores the annotation; verify it remains list[str].
    warnings_field = fields["warnings"]
    annotation = warnings_field.annotation
    # Accept either ``list[str]`` annotation forms (Pydantic may store
    # the parameterized generic as ``list[str]`` or ``List[str]``).
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    assert origin is list
    assert args == (str,)
