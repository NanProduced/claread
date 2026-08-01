"""R1 red tests for source-callout display-icon ownership.

The leading emoji is wrapper display metadata. It is not a canonical body
block, so it must never reach Stable ranges, Reading Units, anchors, or
automatic layer targets.
"""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "markdown-aside",
            "<aside>\n🎯\n\n**Alignment**: body.\n</aside>\n",
        ),
        (
            "gfm-alert",
            "> [!NOTE]\n> 🎯\n>\n> **Alignment**: body.\n",
        ),
        (
            "rich-html-aside",
            "<aside><p>🎯</p><p><strong>Alignment</strong>: body.</p></aside>\n",
        ),
    ],
)
def test_leading_callout_emoji_is_wrapper_display_metadata(
    label: str,
    source: str,
) -> None:
    result = MarkdownSourceParser().parse(source)
    wrappers = [
        block
        for block in result.blocks
        if block.payload_json.get("source_semantic_hint") in {"html_aside", "gfm_alert"}
    ]
    assert wrappers, f"{label}: expected a structural source-callout wrapper"
    assert wrappers[0].payload_json.get("display_icon") == "🎯"
    assert all(block.text_content != "🎯" for block in result.blocks)
    assert any(
        block.text_content == "Alignment: body."
        and block.parent_block_id == wrappers[0].block_id
        for block in result.blocks
    )


def test_root_emoji_paragraph_is_not_a_callout_icon() -> None:
    result = MarkdownSourceParser().parse("🎯\n\nOrdinary paragraph.\n")

    assert not any("display_icon" in block.payload_json for block in result.blocks)
    assert any(block.text_content == "🎯" for block in result.blocks)


def test_icon_promotion_rewrites_parent_chain_after_removing_icon_leaf() -> None:
    result = MarkdownSourceParser().parse(
        "> [!NOTE]\n"
        "> 🎯\n"
        ">\n"
        "> **Alignment**: body.\n"
        ">\n"
        "> Follow-up paragraph remains nested.\n"
    )

    assert [block.block_id for block in result.blocks] == [
        f"b{index}"
        for index in range(1, len(result.blocks) + 1)
    ]
    assert all(block.parent_block_id != block.block_id for block in result.blocks)
    wrapper = next(
        block
        for block in result.blocks
        if block.payload_json.get("source_semantic_hint") == "gfm_alert"
    )
    assert wrapper.payload_json.get("display_icon") == "🎯"
    assert sum(
        block.parent_block_id == wrapper.block_id
        for block in result.blocks
        if block.text_content
    ) >= 2
