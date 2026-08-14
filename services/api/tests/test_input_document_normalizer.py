# task-history: (renamed from test_d6_i3b_input_document_normalizer.py)
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.reader_input_adapter import (
    InputSuitabilityRequest,
    NormalizedInputDocument,
)
from app.services.reader_orchestration.document_freeze_plan import (
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.input_document_normalizer import (
    InputDocumentNormalizationError,
    InputDocumentNormalizer,
    normalize_input_document,
)
from app.services.reader_orchestration.markdown_source_parser import (
    PARSER_NAME,
    PARSER_VERSION,
    PROFILE,
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


def _normalize(
    *,
    source_type: str = "pasted_text",
    text: str,
    filename: str | None = None,
    source_metadata: dict | None = None,
):
    return normalize_input_document(
        InputSuitabilityRequest(
            source_type=source_type,
            text=text,
            filename=filename,
            source_metadata=source_metadata or {},
        )
    )


def test_pasted_text_paragraphs_merge_soft_line_breaks() -> None:
    normalized = _normalize(
        text=(
            "The first paragraph explains how a careful reader joins nearby\n"
            "lines into one continuous thought for a stable reading block.\n\n"
            f"{_english_paragraph()}"
        )
    )

    assert [block.block_type for block in normalized.blocks] == ["paragraph", "paragraph"]
    assert (
        normalized.blocks[0].text_content
        == "The first paragraph explains how a careful reader joins nearby lines into one continuous thought for a stable reading block."
    )
    assert normalized.blocks[0].source_refs_json["line_start"] == 1
    assert normalized.blocks[0].source_refs_json["line_end"] == 2


def test_txt_file_with_enough_english_normalizes_to_paragraph_blocks() -> None:
    normalized = _normalize(
        source_type="txt_file",
        filename="reading.txt",
        text=f"{_english_paragraph()}\n\n{_english_paragraph()}",
    )

    assert normalized.source_type == "txt_file"
    assert normalized.title is None
    assert [block.block_type for block in normalized.blocks] == ["paragraph", "paragraph"]
    assert all(
        block.source_refs_json["filename"] == "reading.txt"
        for block in normalized.blocks
    )


def test_blank_or_short_input_raises_with_gate_outcome_and_flags() -> None:
    with pytest.raises(InputDocumentNormalizationError) as excinfo:
        _normalize(text="This brief note is far too short for useful reading analysis.")

    assert excinfo.value.outcome == "input_rejected_or_action_required"
    assert "too_short_for_learning" in excinfo.value.flags
    assert "outcome=input_rejected_or_action_required" in str(excinfo.value)


def test_markdown_heading_becomes_heading_block_and_title() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"# Weekly Review\n\n{_english_paragraph()}",
    )

    heading = normalized.blocks[0]

    assert normalized.title == "Weekly Review"
    assert heading.block_type == "heading"
    assert heading.text_content == "Weekly Review"
    assert heading.payload_json["level"] == 1
    # semantic_contract_v1 activation marker (role null for heading).
    assert heading.payload_json["semantic"]["contract_version"] == "semantic_contract_v1"
    assert heading.payload_json["semantic"]["content_role"] is None


def test_unordered_list_items_share_list_id_and_ordinals() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
{_english_paragraph()}

- Readers compare background evidence before revising a public plan in writing.
- Editors highlight tradeoffs so the article still teaches grammar and logic clearly.
- Students can review the sequence of reasons without losing the thread of the article.
""".strip(),
    )

    list_blocks = [block for block in normalized.blocks if block.block_type == "list_item"]

    assert len(list_blocks) == 3
    # Parser uses parent_block_id to express list grouping (no list_id in
    # payload_json). All list_items share the same parent_block_id (the
    # list wrapper block).
    assert len({block.parent_block_id for block in list_blocks}) == 1
    assert all(block.parent_block_id is not None for block in list_blocks)
    assert [block.payload_json["ordered"] for block in list_blocks] == [False, False, False]
    # Unordered lists do not carry ordinals (ordinal=None).
    assert [block.payload_json["ordinal"] for block in list_blocks] == [None, None, None]


def test_ordered_list_items_share_list_id_and_ordinals() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
{_english_paragraph()}

1. Readers first identify the main claim in the article before inspecting details.
2. They then compare supporting reasons and note how the author orders the evidence.
3. Finally they summarize the conclusion in plain English to confirm understanding.
""".strip(),
    )

    list_blocks = [block for block in normalized.blocks if block.block_type == "list_item"]

    assert len(list_blocks) == 3
    # Parser uses parent_block_id to express list grouping (no list_id in
    # payload_json). All list_items share the same parent_block_id (the
    # list wrapper block).
    assert len({block.parent_block_id for block in list_blocks}) == 1
    assert all(block.parent_block_id is not None for block in list_blocks)
    assert [block.payload_json["ordered"] for block in list_blocks] == [True, True, True]
    assert [block.payload_json["ordinal"] for block in list_blocks] == [1, 2, 3]


def test_blockquote_text_strips_quote_markers() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
{_english_paragraph()}

> The quoted passage keeps its sentence content while losing the markdown marker.
> The second quote line should merge into the same readable block.
""".strip(),
    )

    quote_blocks = [block for block in normalized.blocks if block.block_type == "blockquote"]

    assert len(quote_blocks) == 1
    # Parser joins softbreak/hardbreak with "\n" (not a space).
    assert quote_blocks[0].text_content == (
        "The quoted passage keeps its sentence content while losing the markdown marker.\n"
        "The second quote line should merge into the same readable block."
    )


def test_fenced_code_block_preserves_code_and_defaults_to_main_reading() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
# Notes

{_english_paragraph(multiplier=2)}

```python
def add(a, b):
    return a + b
```

The article continues in plain English after the short example.
""".strip(),
    )

    code_block = next(block for block in normalized.blocks if block.block_type == "code_block")

    assert code_block.text_content == "def add(a, b):\n    return a + b"
    # Parser uses language (stripped info string), fenced, closed.
    assert code_block.payload_json["language"] == "python"
    assert code_block.payload_json["fenced"] is True
    assert code_block.payload_json["closed"] is True
    # Markdown ecosystem refactor: code_block defaults to
    # main_reading (it contributes to canonical text).
    assert code_block.interpretation_policy.default_route == "main_reading"


def test_divider_becomes_unknown_metadata_only_block() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
{_english_paragraph()}

---

{_english_paragraph()}
""".strip(),
    )

    # Parser emits thematic_break (structural, metadata_only) for hr.
    divider_block = next(
        block for block in normalized.blocks if block.block_type == "thematic_break"
    )

    assert divider_block.text_content is None
    assert set(divider_block.payload_json.keys()) == {"semantic"}
    assert (
        divider_block.payload_json["semantic"]["contract_version"]
        == "semantic_contract_v1"
    )
    assert divider_block.interpretation_policy.default_route == "metadata_only"


def test_inline_markdown_markers_are_removed_and_link_url_is_preserved() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
{_english_paragraph()}

Readers cite **important evidence**, add *context*, reference `key terms`, and keep [the source note](https://example.com/note) readable for learners.
""".strip(),
    )

    inline_block = next(
        block
        for block in normalized.blocks
        if block.block_type == "paragraph" and "the source note" in (block.text_content or "")
    )

    assert inline_block.text_content == (
        "Readers cite important evidence, add context, reference key terms, and keep the source note readable for learners."
    )
    # Parser puts links in payload_json (not source_refs_json) per the
    # Structured Source Contract, with {text, href} format.
    assert inline_block.payload_json["links"] == [
        {"text": "the source note", "href": "https://example.com/note"}
    ]


@pytest.mark.parametrize(
    ("body", "flag"),
    [
        (
            f"{_english_paragraph(multiplier=2)}\n\n```python\ndef add(a, b):\n    return a + b\n\nThe closing fence is missing, so the remainder cannot be normalized safely.",
            "document_block_degraded",
        ),
        (
            # L1: deterministic tables are stable-ready; an extra raw
            # cell (column mismatch) keeps the content_check contract.
            f"{_english_paragraph()}\n\n| City | Cost |\n| --- | --- |\n| A | 10 | 99 |",
            "table_structure_uncertain",
        ),
        (
            f"{_english_paragraph()}\n\n![Map](https://example.com/map.png)",
            "image_ocr_uncertain",
        ),
        (
            f"{_english_paragraph()}\n\nA note remains attached to the source.[^1]\n\n[^1]: Footnote body.",
            "footnote_or_caption_merged",
        ),
    ],
)
def test_complex_markdown_input_raises_when_gate_requires_candidate_document(
    body: str,
    flag: str,
) -> None:
    with pytest.raises(InputDocumentNormalizationError) as excinfo:
        _normalize(
            source_type="markdown_file",
            filename="review.md",
            text=body,
        )

    assert excinfo.value.outcome == "candidate_document_required"
    assert flag in excinfo.value.flags


def test_canonical_text_contains_reading_blocks_and_code_but_excludes_divider() -> None:
    normalized = _normalize(
        source_type="markdown_file",
        filename="review.md",
        text=f"""
# Overview

{_english_paragraph()}

- Readers map each supporting reason to the main claim before discussion starts.
- They compare vocabulary choices and sentence rhythm across the passage carefully.

> The quoted summary still belongs in the main reading flow for interpretation.

---

```python
def add(a, b):
    return a + b
```
""".strip(),
    )

    plan = build_stable_document_freeze_plan(
        reading_record_id="record-1",
        record_generation=1,
        document_version=1,
        title=normalized.title,
        blocks=normalized.blocks,
    )

    assert "Overview" in plan.canonical_text
    assert "Readers map each supporting reason to the main claim before discussion starts." in plan.canonical_text
    assert "The quoted summary still belongs in the main reading flow for interpretation." in plan.canonical_text
    # Markdown ecosystem refactor: code_block defaults to
    # main_reading, so the fenced code body now contributes to canonical
    # text (without the fence markers themselves).
    assert "return a + b" in plan.canonical_text
    assert "# Overview" not in plan.canonical_text
    assert "```python" not in plan.canonical_text
    assert "\n---\n" not in plan.canonical_text


def test_block_ids_and_order_indexes_are_deterministic() -> None:
    text = f"""
# Title

{_english_paragraph()}

1. Readers inspect the order of claims before writing a summary for class.
2. They keep track of evidence and transitions across each paragraph.
""".strip()

    first = _normalize(source_type="markdown_file", filename="review.md", text=text)
    second = _normalize(source_type="markdown_file", filename="review.md", text=text)

    # Parser inserts a list wrapper block before list_items, so the
    # block count is 5: heading, paragraph, list, list_item, list_item.
    assert [block.block_id for block in first.blocks] == ["b1", "b2", "b3", "b4", "b5"]
    assert [block.order_index for block in first.blocks] == [0, 1, 2, 3, 4]
    assert [block.model_dump() for block in first.blocks] == [
        block.model_dump() for block in second.blocks
    ]


def test_normalized_output_schema_rejects_extra_fields() -> None:
    normalized = _normalize(text=_english_paragraph(multiplier=2))
    payload = normalized.model_dump()
    payload["unexpected_field"] = True

    with pytest.raises(ValidationError):
        NormalizedInputDocument.model_validate(payload)


def test_normalizer_class_and_helper_return_same_result() -> None:
    request = InputSuitabilityRequest(
        source_type="pasted_text",
        text=_english_paragraph(multiplier=2),
        source_metadata={},
    )

    direct = InputDocumentNormalizer().normalize(request)
    helper = normalize_input_document(request)

    assert [block.model_dump() for block in direct.blocks] == [
        block.model_dump() for block in helper.blocks
    ]


def test_pasted_text_with_markdown_heading_upgrades_to_markdown_path() -> None:
    # pasted_text that contains Markdown-specific structure (heading)
    # must upgrade to the markdown parser path: blocks carry heading
    # block types and quality_json records the parser identity.
    normalized = _normalize(
        source_type="pasted_text",
        text=(
            "### Weekly Review\n\n"
            f"{_english_paragraph(multiplier=2)}"
        ),
    )

    heading_block = next(
        block for block in normalized.blocks if block.block_type == "heading"
    )
    assert heading_block.text_content == "Weekly Review"
    assert heading_block.payload_json["level"] == 3
    assert (
        heading_block.payload_json["semantic"]["contract_version"]
        == "semantic_contract_v1"
    )
    for block in normalized.blocks:
        assert block.quality_json["parser_name"]
        assert block.quality_json["parser_version"]
        assert block.quality_json["profile"]
    assert normalized.title == "Weekly Review"


def test_pasted_text_plain_text_stays_on_plain_text_path() -> None:
    # pasted_text without Markdown-specific structure (just plain
    # paragraphs) stays on the plain text path: quality_json does NOT
    # carry the markdown parser identity.
    normalized = _normalize(
        source_type="pasted_text",
        text=f"{_english_paragraph(multiplier=2)}",
    )

    assert [block.block_type for block in normalized.blocks] == ["paragraph", "paragraph"]
    for block in normalized.blocks:
        assert "parser_name" not in block.quality_json
        assert "parser_version" not in block.quality_json
        assert "profile" not in block.quality_json


def test_txt_file_with_markdown_structure_upgrades_to_markdown_path() -> None:
    # txt_file with Markdown structure (heading + list) upgrades to
    # the markdown parser path even though source_type is txt_file.
    normalized = _normalize(
        source_type="txt_file",
        filename="notes.txt",
        text=(
            "# Reading Notes\n\n"
            f"{_english_paragraph()}\n\n"
            "- Readers compare evidence before revising a public plan in writing.\n"
            "- Editors highlight tradeoffs so the article still teaches grammar clearly.\n"
        ),
    )

    heading_block = next(
        block for block in normalized.blocks if block.block_type == "heading"
    )
    assert heading_block.text_content == "Reading Notes"
    list_items = [
        block for block in normalized.blocks if block.block_type == "list_item"
    ]
    assert len(list_items) == 2
    for block in normalized.blocks:
        assert block.quality_json["parser_name"]
        assert block.quality_json["parser_version"]
        assert block.quality_json["profile"]


def test_pasted_text_markdown_upgraded_preserves_source_type() -> None:
    # When pasted_text upgrades to the markdown path, source_refs_json
    # must keep the original source_type ("pasted_text"); only the
    # parser identity in quality_json reflects the upgraded path.
    normalized = _normalize(
        source_type="pasted_text",
        text=(
            "### Weekly Review\n\n"
            f"{_english_paragraph(multiplier=2)}"
        ),
    )

    for block in normalized.blocks:
        assert block.source_refs_json["source_type"] == "pasted_text"
        assert block.quality_json["parser_name"]


def test_normalized_document_exposes_parser_identity_for_markdown_file() -> None:
    """``NormalizedInputDocument`` MUST expose a document-level
    ``parser_identity`` triple (parser_name / parser_version / profile)
    when the markdown parser path is used, so downstream freeze
    persistence can write it into ``source_profile_json`` (plan §4 G0
    Clause 1: parser identity written into document metadata).

    Block-level ``quality_json`` already carries the triple, but
    document-level metadata must not rely on block-level inference.
    """
    normalized = _normalize(
        source_type="markdown_file",
        text=(
            "# Title\n\n"
            f"{_english_paragraph(multiplier=2)}"
        ),
    )

    assert normalized.parser_identity is not None, (
        "NormalizedInputDocument must expose parser_identity for markdown_file"
    )
    assert normalized.parser_identity["parser_name"] == PARSER_NAME
    assert normalized.parser_identity["parser_version"] == PARSER_VERSION
    assert normalized.parser_identity["profile"] == PROFILE


def test_normalized_document_parser_identity_none_for_plain_text() -> None:
    """``NormalizedInputDocument.parser_identity`` MUST be ``None``
    when the plain text path is used (no markdown structure detected),
    so downstream freeze persistence does not falsely attribute the
    document to the structured-source parser.
    """
    normalized = _normalize(
        source_type="pasted_text",
        text=f"{_english_paragraph(multiplier=2)}",
    )

    assert [block.block_type for block in normalized.blocks] == ["paragraph", "paragraph"]
    assert normalized.parser_identity is None, (
        "NormalizedInputDocument.parser_identity must be None for plain text path"
    )
