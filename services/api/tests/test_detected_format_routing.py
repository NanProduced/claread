"""L2 — detected_format 解耦与结构真相链路（pasted_text Markdown）。

 根因：candidate 块构造用 ``source_type == "markdown_file"`` 决定是否
走 MarkdownSourceParser，导致 pasted_text 粘贴的 Markdown 在 candidate
路径全部退化为 paragraph，confirm 后 Stable/Reader 出现 raw ``##`` /
``>`` / ``-``。本文件锁两件事：

1. **格式检测**：``detect_input_format`` 只依据 parser 块结构判断
   ``plain_text`` / ``markdown``，``source_type`` 只描述来源。
2. **candidate 结构保真**：检测到 Markdown 结构的 pasted_text 必须走
   parser 产生 typed blocks（heading/blockquote/list/list_item），
   无 Markdown 标记的纯文本保持现有纯文本行为。

math 误判：``\\[Video]``、普通转义方括号不得识别为数学公式；数学判定
要求成对边界且内容像公式。
"""

from __future__ import annotations

from uuid import uuid4

from app.schemas.reader_input_adapter import (
    InputSuitabilityRequest,
)
from app.services.reader_orchestration.candidate_document_creation_service import (
    _build_candidate_blocks,
)
from app.services.reader_orchestration.input_format import (
    detect_input_format,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownSourceParser,
)

_PARSER = MarkdownSourceParser()

# 任务指定真实文本：h2 + blockquote + paragraph + h3 + list，
# 含 ``\[Video]`` 与普通 HTTPS 链接。
PASTED_MARKDOWN = """## Morning Reading Notes

> The editor pulled this quote about reading habits and long-form attention
> because the committee wanted a memorable opening for the public summary.

The committee reviewed the proposal and agreed that the appendix should
remain available to every participant before the vote takes place next
month in the main hall.

### Action Items

- Finalize the budget by next Tuesday and notify all department leads
- Send out the stakeholder survey to collect feedback on the proposal
- Schedule a follow-up review session with the executive team

Watch the \\[Video] summary at https://example.com/reading-notes for the
background context before the next committee session begins.
"""

PLAIN_PROSE = (
    "The committee reviewed the proposal and agreed that the appendix "
    "should remain available to every participant before the vote takes "
    "place next month in the main hall near the river district office.\n\n"
    "A second paragraph keeps the plain text input comfortably above the "
    "minimum word count so the gate sees ordinary English prose without "
    "any markdown markers at all in the body of the note."
)


def _detect(source_type: str, text: str) -> str:
    parse_result = _PARSER.parse(text)
    return detect_input_format(
        source_type=source_type,  # type: ignore[arg-type]
        parse_result=parse_result,
    )


# ---------------------------------------------------------------------------
# detected_format：来源与格式解耦
# ---------------------------------------------------------------------------


def test_pasted_text_with_markdown_structure_detected_as_markdown() -> None:
    assert _detect("pasted_text", PASTED_MARKDOWN) == "markdown"


def test_pasted_plain_prose_detected_as_plain_text() -> None:
    assert _detect("pasted_text", PLAIN_PROSE) == "plain_text"


def test_markdown_file_source_always_detected_as_markdown() -> None:
    # markdown_file 显式声明格式，即使只有段落也按 markdown 处理（现状保持）。
    assert _detect("markdown_file", PLAIN_PROSE) == "markdown"


# ---------------------------------------------------------------------------
# candidate 块构造：format 驱动，而非 source_type 驱动
# ---------------------------------------------------------------------------


def test_pasted_markdown_candidate_blocks_are_typed_not_all_paragraph() -> None:
    """pasted_text 粘贴的 Markdown 走 candidate 路径时必须保留块类型。

    修复前现状：``_build_candidate_blocks`` 只在
    ``source_type == "markdown_file"`` 时走 parser，pasted_text 全部
    退化为 paragraph，confirm 后 Reader 出现 raw ``##`` / ``>`` / ``-``。
    """
    blocks, title = _build_candidate_blocks(
        source_type="pasted_text",
        text=PASTED_MARKDOWN,
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    block_types = {block.block_type for block in blocks}

    assert "heading" in block_types, f"blocks: {block_types}"
    assert "blockquote" in block_types, f"blocks: {block_types}"
    assert "list_item" in block_types, f"blocks: {block_types}"
    assert "paragraph" in block_types, f"blocks: {block_types}"
    # 标题来自第一个 heading。
    assert title == "Morning Reading Notes"
    # canonical 文本不含 raw markdown 标记。
    for block in blocks:
        text = block.text_content or ""
        assert not text.startswith("##"), text
        assert not text.startswith(">"), text
        assert not text.startswith("- "), text


def test_pasted_markdown_candidate_blocks_carry_parser_identity() -> None:
    """检测到 markdown 的 pasted_text 块必须带 parser identity（provenance）。"""
    blocks, _ = _build_candidate_blocks(
        source_type="pasted_text",
        text=PASTED_MARKDOWN,
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    for block in blocks:
        assert block.quality_json.get("parser_name"), (
            f"block {block.block_id} ({block.block_type}) must carry parser identity"
        )


def test_pasted_plain_text_candidate_blocks_stay_plain_paragraphs() -> None:
    """无 Markdown 标记的纯文本保持现有纯文本行为（空行分段、无 parser identity）。"""
    blocks, _ = _build_candidate_blocks(
        source_type="pasted_text",
        text=PLAIN_PROSE,
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks
    assert all(block.block_type == "paragraph" for block in blocks)
    for block in blocks:
        assert "parser_name" not in block.quality_json


def test_pasted_markdown_candidate_preserves_list_hierarchy() -> None:
    """list_item 必须通过 parent_block_id 挂在 list 容器下。"""
    blocks, _ = _build_candidate_blocks(
        source_type="pasted_text",
        text=PASTED_MARKDOWN,
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    by_id = {block.block_id: block for block in blocks}
    list_items = [b for b in blocks if b.block_type == "list_item"]
    assert list_items, "expected list_item blocks"
    for item in list_items:
        assert item.parent_block_id is not None
        parent = by_id[item.parent_block_id]
        assert parent.block_type == "list"


# ---------------------------------------------------------------------------
# math 误判：转义方括号 / 普通链接不得识别为数学公式
# ---------------------------------------------------------------------------


def _evaluate(source_type: str, text: str):
    return evaluate_input_suitability(
        InputSuitabilityRequest(
            source_type=source_type,  # type: ignore[arg-type]
            text=text,
        )
    )


def test_escaped_video_bracket_does_not_trigger_math_candidate() -> None:
    """``\\[Video]`` 是转义方括号，不是数学公式；不得进 content_check。"""
    result = _evaluate("pasted_text", PASTED_MARKDOWN)

    assert result.outcome == "stable_document_ready", (
        f"outcome={result.outcome}, flags={result.flags}, reasons={result.reasons}"
    )
    assert "document_block_degraded" not in result.flags
    assert "markdown_complex_structure" not in result.flags
    content_check_codes = {
        record.code for record in result.adaptations
        if record.classification == "content_check"
    }
    assert "document_block_degraded" not in content_check_codes


def test_escaped_bracket_pair_without_math_content_is_not_math() -> None:
    """成对转义括号但内容不像公式（如 ``\\[see note\\]``）不触发 math。"""
    text = (
        PLAIN_PROSE
        + "\n\nThe appendix \\[see note] reference and the \\[Video] link "
        "both use escaped brackets in ordinary prose sentences here."
    )
    result = _evaluate("pasted_text", text)
    assert "document_block_degraded" not in result.flags


def test_real_paired_math_still_routes_to_candidate() -> None:
    """真实成对数学边界（内容像公式）仍必须触发 candidate review。"""
    text = (
        PLAIN_PROSE
        + "\n\nThe derivation closes with \\[x^2 + y^2 = z^2\\] and the "
        "inline form \\(E = mc^2\\) plus a display block $$a_i = b_i + c_i$$."
    )
    result = _evaluate("pasted_text", text)
    assert result.outcome == "candidate_document_required"
    assert "document_block_degraded" in result.flags


def test_plain_https_link_does_not_trigger_candidate() -> None:
    """普通 HTTPS 链接不得进 content_check/candidate。"""
    text = (
        PLAIN_PROSE
        + "\n\nThe full report is published at https://example.com/report "
        "and every committee member confirmed they could open the link."
    )
    result = _evaluate("pasted_text", text)
    assert result.outcome == "stable_document_ready", (
        f"outcome={result.outcome}, flags={result.flags}"
    )


def test_safe_aside_pasted_text_is_adaptation_notice_not_candidate() -> None:
    """Notion 风格安全 <aside> 清洗后继续（adaptation_notice），不进 candidate。"""
    text = (
        PLAIN_PROSE
        + '\n\n<aside class="note">Rendered callout from a Notion page.</aside>'
        + "\n\n"
        + PLAIN_PROSE
    )
    result = _evaluate("pasted_text", text)
    assert result.outcome == "stable_document_ready", (
        f"outcome={result.outcome}, flags={result.flags}"
    )
    adaptations = {record.code: record.classification for record in result.adaptations}
    assert adaptations.get("raw_html_block") == "adaptation_notice"
