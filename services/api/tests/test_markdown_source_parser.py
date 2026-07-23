"""TDD skeleton for the Markdown Source Parser adapter.

This module drives the G0 → M1 transition for the Structured Source
Contract. It loads each G0 fixture under
``tests/fixtures/markdown_structured_source/`` and asserts that the
adapter ``MarkdownSourceParser`` produces the expected blocks, policy,
and diagnostics.

M1 state (2026-07-23): all 10 G0 fixtures PASS under the production
adapter. The legacy regex normalizer and the candidate-service regex
draft path have been replaced by this adapter (single parse result).

Contract reference:
``services/api/tests/fixtures/markdown_structured_source/CONTRACT.md``

Spike reference (historical):
``scripts/spike_markdown_parser.py`` +
``scripts/spike_markdown_parser_report.md``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    PROFILE,
    MarkdownParseResult,
    MarkdownSourceParser,
    ParsedBlock,
)

_FIXTURES_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "markdown_structured_source"
)

# All 10 G0 fixtures PASS under the M1 production adapter.
_PASSING_FIXTURES = (
    "code_mermaid",
    "footnote",
    "gfm_table",
    "nested_list",
    "r14_complex",
    "raw_html",
    "reject_empty",
    "simple_paragraph",
    "unclosed_fence",
    "unsafe_link",
)

# No fixtures are in RED state after M1; kept as an empty mapping so the
# parametrization helper stays a no-op for forward compatibility.
_XFAIL_FIXTURES: dict[str, str] = {}


def _fixture_params(names: tuple[str, ...]):
    """Build pytest.params for the given fixture names (no marks)."""
    return [pytest.param(n, id=n) for n in names]


def _xfail_fixture_params(mapping: dict[str, str]):
    """Build pytest.params with xfail marks for the given RED fixtures."""
    return [
        pytest.param(name, marks=pytest.mark.xfail(reason=reason, strict=False), id=name)
        for name, reason in mapping.items()
    ]


def _load_fixture(name: str) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load the four fixture files for a fixture by name."""
    fixture_dir = _FIXTURES_ROOT / name
    input_md = (fixture_dir / "input.md").read_text(encoding="utf-8")
    expected_blocks = json.loads(
        (fixture_dir / "expected_blocks.json").read_text(encoding="utf-8")
    )
    expected_policy = json.loads(
        (fixture_dir / "expected_policy.json").read_text(encoding="utf-8")
    )
    expected_diagnostics = json.loads(
        (fixture_dir / "expected_diagnostics.json").read_text(encoding="utf-8")
    )
    return input_md, expected_blocks, expected_policy, expected_diagnostics


def _parse(input_md: str) -> MarkdownParseResult:
    """Run the adapter and return the typed result."""
    return MarkdownSourceParser().parse(input_md)


def _block_to_dict(block: ParsedBlock) -> dict[str, Any]:
    """Project a ParsedBlock to the fixture-comparable dict shape."""
    return {
        "block_id": block.block_id,
        "block_type": block.block_type,
        "text_content": block.text_content,
        "payload_json": block.payload_json,
        "parent_block_id": block.parent_block_id,
        "order_index": block.order_index,
        "source_range": {
            "line_start": block.source_range.line_start,
            "line_end": block.source_range.line_end,
        },
    }


def _assert_blocks_match(
    actual_blocks: list[ParsedBlock],
    expected_blocks: list[dict[str, Any]],
) -> None:
    """Assert actual adapter blocks match the expected fixture blocks.

    Comparison is field-by-field; the first mismatch raises an
    AssertionError with a readable diff.
    """
    actual_dicts = [_block_to_dict(b) for b in actual_blocks]
    assert len(actual_dicts) == len(expected_blocks), (
        f"block count: actual={len(actual_dicts)}, expected={len(expected_blocks)}"
    )
    for idx, (actual, expected) in enumerate(zip(actual_dicts, expected_blocks, strict=False)):
        for key in (
            "block_id",
            "block_type",
            "text_content",
            "parent_block_id",
            "order_index",
        ):
            assert actual.get(key) == expected.get(key), (
                f"block[{idx}] {key}: actual={actual.get(key)!r}, "
                f"expected={expected.get(key)!r}"
            )
        actual_payload = actual.get("payload_json") or {}
        expected_payload = expected.get("payload_json") or {}
        assert actual_payload == expected_payload, (
            f"block[{idx}] payload_json: "
            f"actual={actual_payload!r}, expected={expected_payload!r}"
        )
        actual_range = actual.get("source_range") or {}
        expected_range = expected.get("source_range") or {}
        assert actual_range.get("line_start") == expected_range.get("line_start"), (
            f"block[{idx}] source_range.line_start: "
            f"actual={actual_range.get('line_start')!r}, "
            f"expected={expected_range.get('line_start')!r}"
        )
        assert actual_range.get("line_end") == expected_range.get("line_end"), (
            f"block[{idx}] source_range.line_end: "
            f"actual={actual_range.get('line_end')!r}, "
            f"expected={expected_range.get('line_end')!r}"
        )


def _assert_diagnostics_match(
    result: MarkdownParseResult,
    expected_diagnostics: dict[str, Any],
) -> None:
    """Assert warnings/unsupported/outcome match the expected fixture."""
    actual_warning_codes = {w.code for w in result.warnings}
    expected_warning_codes = {w.get("code") for w in expected_diagnostics.get("warnings", [])}
    assert actual_warning_codes == expected_warning_codes, (
        f"warning codes: actual={sorted(actual_warning_codes)!r}, "
        f"expected={sorted(expected_warning_codes)!r}"
    )
    actual_unsupported_codes = {u.code for u in result.unsupported}
    expected_unsupported_codes = {
        u.get("code") for u in expected_diagnostics.get("unsupported", [])
    }
    assert actual_unsupported_codes == expected_unsupported_codes, (
        f"unsupported codes: actual={sorted(actual_unsupported_codes)!r}, "
        f"expected={sorted(expected_unsupported_codes)!r}"
    )
    assert result.outcome == expected_diagnostics.get("outcome"), (
        f"outcome: actual={result.outcome!r}, "
        f"expected={expected_diagnostics.get('outcome')!r}"
    )


def _assert_identity(result: MarkdownParseResult) -> None:
    """Assert Clause 1 identity constants on the parse result."""
    assert result.parser_name == PARSER_NAME
    assert result.parser_version == PARSER_VERSION
    assert result.profile == PROFILE


# ---------------------------------------------------------------------------
# Fixture-driven parametrized tests
#
# All 10 G0 fixtures run as plain assertions under the M1 production
# adapter. The xfail parametrization helper is retained but empty; if a
# future regression introduces a RED fixture, re-add it to
# ``_XFAIL_FIXTURES`` with a reason.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    _fixture_params(_PASSING_FIXTURES) + _xfail_fixture_params(_XFAIL_FIXTURES),
)
def test_fixture_blocks_match_expected(fixture_name: str) -> None:
    """Adapter blocks must match the G0 fixture expected_blocks.json."""
    input_md, expected_blocks, _, _ = _load_fixture(fixture_name)
    result = _parse(input_md)
    _assert_identity(result)
    _assert_blocks_match(list(result.blocks), expected_blocks.get("blocks", []))


@pytest.mark.parametrize(
    "fixture_name",
    _fixture_params(_PASSING_FIXTURES) + _xfail_fixture_params(_XFAIL_FIXTURES),
)
def test_fixture_diagnostics_match_expected(fixture_name: str) -> None:
    """Adapter diagnostics (warnings/unsupported/outcome) must match
    the G0 fixture expected_diagnostics.json.
    """
    input_md, _, _, expected_diagnostics = _load_fixture(fixture_name)
    result = _parse(input_md)
    _assert_diagnostics_match(result, expected_diagnostics)


# ---------------------------------------------------------------------------
# Clause-level smoke tests (independent of fixture JSON)
# ---------------------------------------------------------------------------


def test_clause1_identity_constants_are_frozen() -> None:
    """Clause 1 — parser identity must be frozen constants."""
    assert PARSER_NAME == "markdown_it_py"
    assert PARSER_VERSION == "v1"
    assert PROFILE == "commonmark_gfm_v1"


def test_clause1_result_carries_identity() -> None:
    """Clause 1 — parse result must carry the frozen identity."""
    result = _parse("# Heading\n\nParagraph.")
    assert result.parser_name == PARSER_NAME
    assert result.parser_version == PARSER_VERSION
    assert result.profile == PROFILE


def test_clause2_newline_normalization_crlf() -> None:
    """Clause 2 — \\r\\n and \\r must be normalized to \\n before parsing."""
    result = _parse("first line\r\nsecond line")
    assert result.outcome == "stable_document_ready"
    # Source range must be 1-based, not 0-based.
    assert all(b.source_range.line_start >= 1 for b in result.blocks)


def test_clause2_source_range_is_1based() -> None:
    """Clause 2 — line ranges are 1-based [start, end]."""
    result = _parse("# Heading\n\nParagraph.")
    for block in result.blocks:
        assert block.source_range.line_start >= 1
        assert block.source_range.line_end >= block.source_range.line_start


def test_clause3_block_ids_are_sequential_b_prefixed() -> None:
    """Clause 1 — block_id format is `b{order_index+1}`."""
    result = _parse("first paragraph.\n\nsecond paragraph.")
    assert [b.block_id for b in result.blocks] == ["b1", "b2"]
    assert [b.order_index for b in result.blocks] == [0, 1]


def test_clause3_inline_marks_flatten_to_text_content() -> None:
    """Clause 3 — emphasis / strong / strikethrough / inline_code are
    flattened into the parent block text_content.
    """
    result = _parse("This has *emphasis* and **strong** and `code`.")
    assert len(result.blocks) == 1
    text = result.blocks[0].text_content or ""
    assert "emphasis" in text
    assert "strong" in text
    assert "code" in text
    assert "*" not in text
    assert "`" not in text


def test_clause3_safe_link_preserved_in_text() -> None:
    """Clause 3 — safe-protocol links keep their text in text_content."""
    result = _parse("[Claread](https://example.com) is a reader.")
    assert len(result.blocks) == 1
    text = result.blocks[0].text_content or ""
    assert "Claread" in text


def test_clause5_outcome_stable_for_simple_paragraph() -> None:
    """Clause 5 — simple narrative input → stable_document_ready."""
    result = _parse("Just a paragraph.")
    assert result.outcome == "stable_document_ready"
    assert result.warnings == ()


def test_clause5_outcome_candidate_for_unclosed_fence() -> None:
    """Clause 5 — unclosed fence → candidate_document_required."""
    result = _parse("# Title\n\n```\ndef foo():\n    return 1")
    assert result.outcome == "candidate_document_required"
    warning_codes = {w.code for w in result.warnings}
    assert "has_unclosed_fence" in warning_codes


def test_clause5_outcome_rejected_for_code_dominant() -> None:
    """Clause 5 — code-only input → input_rejected_or_action_required."""
    result = _parse("```\ndef foo():\n    pass\n```")
    assert result.outcome == "input_rejected_or_action_required"
    warning_codes = {w.code for w in result.warnings}
    assert "code_dominant" in warning_codes


def test_clause5_warning_codes_belong_to_closed_set() -> None:
    """Clause 5 — warning codes must be from the closed set."""
    closed_set = {
        "raw_html_block",
        "inline_html",
        "has_unclosed_fence",
        "unsafe_link_protocol",
        "footnote_reference",
        "strikethrough_extension",
        "mermaid_static_only",
        "code_dominant",
        "missing_source_range",
    }
    result = _parse("# Title\n\nParagraph.")
    for w in result.warnings:
        assert w.code in closed_set, f"unknown warning code: {w.code}"


def test_clause5_unsupported_codes_belong_to_closed_set() -> None:
    """Clause 5 — unsupported codes must be from the closed set."""
    closed_set = {
        "raw_html",
        "unsafe_link_sanitization",
        "footnote_full_semantics",
    }
    result = _parse("# Title\n\nParagraph.")
    for u in result.unsupported:
        assert u.code in closed_set, f"unknown unsupported code: {u.code}"


def test_clause6_normalizer_uses_structured_source_identity() -> None:
    """Clause 6 — the normalizer now consumes the structured-source
    adapter. ``NORMALIZER_VERSION`` must reflect the structured source
    integration, and a markdown_file normalization must write the
    parser identity triple into the frozen block ``quality_json``.
    """
    from app.schemas.reader_input_adapter import InputSuitabilityRequest
    from app.services.reader_orchestration.input_document_normalizer import (
        NORMALIZER_VERSION,
        normalize_input_document,
    )

    assert NORMALIZER_VERSION == "d6_i3b_structured_source_v1"

    normalized = normalize_input_document(
        InputSuitabilityRequest(
            source_type="markdown_file",
            filename="review.md",
            text="# Heading\n\n"
            "This paragraph has enough English words for a stable reading block "
            "to be frozen without triggering the candidate review path. "
            "The content stays focused on natural language reading with complete "
            "sentences and enough context for vocabulary, grammar, and sentence "
            "analysis to be genuinely useful for an English learner studying "
            "this kind of article in detail.",
        )
    )
    for block in normalized.blocks:
        quality = block.quality_json
        assert quality["parser_name"] == PARSER_NAME
        assert quality["parser_version"] == PARSER_VERSION
        assert quality["profile"] == PROFILE


# ---------------------------------------------------------------------------
# Explicit assertion: all 10 fixtures must exist on disk
# ---------------------------------------------------------------------------


def test_all_g0_fixtures_exist_on_disk() -> None:
    """All 10 G0 fixtures must be present with all four files."""
    expected_names = {
        "code_mermaid",
        "footnote",
        "gfm_table",
        "nested_list",
        "r14_complex",
        "raw_html",
        "reject_empty",
        "simple_paragraph",
        "unclosed_fence",
        "unsafe_link",
    }
    actual_names = {
        d.name
        for d in _FIXTURES_ROOT.iterdir()
        if d.is_dir() and (d / "input.md").exists()
    }
    assert actual_names == expected_names, (
        f"missing fixtures: {expected_names - actual_names}; "
        f"extra: {actual_names - expected_names}"
    )
    for name in expected_names:
        fixture_dir = _FIXTURES_ROOT / name
        for filename in (
            "input.md",
            "expected_blocks.json",
            "expected_policy.json",
            "expected_diagnostics.json",
        ):
            assert (fixture_dir / filename).exists(), (
                f"{name}/{filename} missing"
            )
