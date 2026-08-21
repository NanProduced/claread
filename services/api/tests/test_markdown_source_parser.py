"""TDD skeleton for the Markdown Source Parser adapter.

This module drives the G0 → M1 transition for the Structured Source
Contract. It loads each G0 fixture under
``tests/fixtures/markdown_structured_source/`` and asserts that the
adapter ``MarkdownSourceParser`` produces the expected blocks, policy,
and diagnostics.

M1 state (2026-07-23): all G0 fixtures PASS under the production
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

# All 21 fixtures PASS under the M1 production adapter (13 original G0/L1
# fixtures plus source_callout, rich_html_aside, task_list, and
# definition_list, citation_reference, heading_levels, and gfm_alert).
# real_list_wrapper added in M3 prerequisite: focused list wrapper +
# list_item regression for Article RAG eligibility.
_PASSING_FIXTURES = (
    "citation_reference",
    "code_mermaid",
    "definition_list",
    "footnote",
    "gfm_alert",
    "gfm_table",
    "heading_levels",
    "nested_list",
    "ordinary_multi_paragraph_blockquote",
    "r14_complex",
    "raw_html",
    "real_list_wrapper",
    "reject_empty",
    "safe_html_adaptation",
    "simple_paragraph",
    "source_callout",
    "task_list",
    "table_structure_uncertain",
    "unclosed_fence",
    "unsafe_link",
    "rich_html_aside",
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
    # L1: every fixture warning declares a three-level classification;
    # the parser's classification must match it exactly.
    actual_classifications = {w.code: w.classification for w in result.warnings}
    expected_classifications = {
        w.get("code"): w.get("classification")
        for w in expected_diagnostics.get("warnings", [])
    }
    assert actual_classifications == expected_classifications, (
        f"warning classifications: actual={actual_classifications!r}, "
        f"expected={expected_classifications!r}"
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
# All G0 fixtures run as plain assertions under the M1 production
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
    # G2a-A: v2 bumps for the typed image representation (standalone
    # image blocks + owning-block inline_images + provenance seam).
    assert PARSER_VERSION == "v2"
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


def test_task_list_visible_marker_routes_to_candidate_without_checked_semantics() -> None:
    """Unsupported checkbox state stays visible and never freezes silently."""
    result = _parse("- [x] done\n- [ ] todo")
    assert [b.text_content for b in result.blocks if b.block_type == "list_item"] == [
        "[x] done",
        "[ ] todo",
    ]
    assert result.outcome == "candidate_document_required"
    assert {w.code for w in result.warnings} == {"task_list_unsupported"}
    assert {u.code for u in result.unsupported} == {"task_list"}


def test_definition_list_is_visible_plain_text_with_adaptation_notice() -> None:
    """Definition-list syntax remains recoverable text with an explicit notice."""
    result = _parse("Term\n: definition")
    assert len(result.blocks) == 1
    assert result.blocks[0].text_content == "Term\n: definition"
    assert result.outcome == "stable_document_ready"
    assert {w.code for w in result.warnings} == {"definition_list_degraded"}
    assert {u.code for u in result.unsupported} == {"definition_list"}


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
        "task_list_unsupported",
        "definition_list_degraded",
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
        "task_list",
        "definition_list",
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

    assert NORMALIZER_VERSION == "d6_i3b_structured_source_v2"

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


def test_clause2_missing_source_range_emits_warning_and_routes_to_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clause 2 — when a block token has no ``map``, the parser MUST
    emit a ``missing_source_range`` warning and route the document to
    ``candidate_document_required``. Silent ``SourceRange(0, 0)``
    fallback without a warning is a contract violation.
    """
    from app.services.reader_orchestration import markdown_source_parser as mod

    original_map_fn = mod._map_to_1based
    call_count = {"n": 0}

    def fake_map_to_1based(map_val):
        call_count["n"] += 1
        # Force the first call to return None to simulate a token with no map.
        if call_count["n"] == 1:
            return None
        return original_map_fn(map_val)

    monkeypatch.setattr(mod, "_map_to_1based", fake_map_to_1based)

    result = _parse("# Title\n\nParagraph text here.")
    warning_codes = {w.code for w in result.warnings}
    assert "missing_source_range" in warning_codes, (
        "Parser must emit missing_source_range warning when a token has no map"
    )
    assert result.outcome == "candidate_document_required", (
        "Missing source range must route to candidate_document_required, "
        f"got {result.outcome}"
    )


# ---------------------------------------------------------------------------
# Explicit assertion: all G0 fixtures must exist on disk
# ---------------------------------------------------------------------------


def test_all_baseline_parser_fixtures_exist_on_disk() -> None:
    """All G0 fixtures must be present with all four files."""
    expected_names = {
        "code_mermaid",
        "citation_reference",
        "definition_list",
        "footnote",
        "gfm_alert",
        "gfm_table",
        "heading_levels",
        "nested_list",
        "ordinary_multi_paragraph_blockquote",
        "r14_complex",
        "raw_html",
        "real_list_wrapper",
        "reject_empty",
        "safe_html_adaptation",
        "simple_paragraph",
        "source_callout",
        "task_list",
        "table_structure_uncertain",
        "unclosed_fence",
        "unsafe_link",
        "rich_html_aside",
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


# ---------------------------------------------------------------------------
# M3 prerequisite: real list wrapper regression
#
# The G1 blind spot was that existing G0 fixtures + RAG tests only verified
# isolated list_item blocks. The real_list_wrapper fixture covers parser-
# emitted list wrapper (text_content=None) + list_item child combination
# in a realistic article context, which is what the Article RAG index
# plan builder actually receives.
# ---------------------------------------------------------------------------


def test_real_list_wrapper_parser_emits_wrapper_with_null_text() -> None:
    """M3 前置 — parser 对 real_list_wrapper fixture 必须产出 list wrapper
    block（text_content=None）+ list_item 子节点（parent_block_id 指向
    wrapper）。这是 G1 盲区的核心覆盖：之前 G0 fixture 测试只验证
    isolated list_item，未显式断言 wrapper 的 text_content=None 和
    parent_block_id 指向关系。

    断言要点：
      * 两个 list wrapper block（无序 + 有序），text_content 均为 None
      * list_item 子节点的 parent_block_id 指向对应 wrapper
      * heading 在最前、paragraph 在最后
    """
    input_md, expected_blocks, _, _ = _load_fixture("real_list_wrapper")
    result = _parse(input_md)
    blocks = list(result.blocks)

    # 11 blocks: heading + paragraph + list(ul) + 3 list_item +
    # list(ol) + 3 list_item + paragraph
    assert len(blocks) == 11

    # 第一个 block 是 heading
    assert blocks[0].block_type == "heading"
    assert blocks[0].text_content == "Real List Wrapper Article"

    # 找到两个 list wrapper block
    list_wrappers = [b for b in blocks if b.block_type == "list"]
    assert len(list_wrappers) == 2, (
        f"expected 2 list wrappers (unordered + ordered), "
        f"got {len(list_wrappers)}"
    )

    # list wrapper 的 text_content 必须是 None（结构性容器，叙事在子节点）
    for wrapper in list_wrappers:
        assert wrapper.text_content is None, (
            f"list wrapper {wrapper.block_id} must have text_content=None, "
            f"got {wrapper.text_content!r}"
        )
        assert wrapper.parent_block_id is None, (
            f"top-level list wrapper {wrapper.block_id} must have "
            f"parent_block_id=None"
        )

    # 无序 list wrapper
    ul_wrapper = list_wrappers[0]
    assert ul_wrapper.payload_json.get("ordered") is False
    # 有序 list wrapper
    ol_wrapper = list_wrappers[1]
    assert ol_wrapper.payload_json.get("ordered") is True

    # list_item 子节点的 parent_block_id 必须指向对应 wrapper
    ul_items = [b for b in blocks if b.parent_block_id == ul_wrapper.block_id]
    ol_items = [b for b in blocks if b.parent_block_id == ol_wrapper.block_id]
    assert len(ul_items) == 3, (
        f"expected 3 unordered list_items, got {len(ul_items)}"
    )
    assert len(ol_items) == 3, (
        f"expected 3 ordered list_items, got {len(ol_items)}"
    )
    for item in ul_items + ol_items:
        assert item.block_type == "list_item"
        assert item.text_content is not None and len(item.text_content) > 0

    # 最后一个 block 是 paragraph
    assert blocks[-1].block_type == "paragraph"
    assert blocks[-1].text_content == "Closing paragraph that follows the lists."
