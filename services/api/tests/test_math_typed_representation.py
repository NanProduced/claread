"""Math-A typed representation + analysis exclusion（MD Math-A RED→GREEN）。

合同来源：``math-markdown-representation-diagnosis.md`` §5/§6（Owner M-1/M-2
已于 2026-08-24 拍板，OWNER pending=0）。本文件锁定：

1. inline ``$..$`` / 行内 ``$$..$$`` → owning block payload ``inline_math``
   entry = ``{"latex": <内层源码逐字>, "display": <markup=="$$">,
   "before_utf16": <相对最终块文本>}``；公式不贡献块文本（镜像
   ``inline_images`` 的 U+0020 分隔判例）；LaTeX 原文中的 CommonMark 活性
   字符（``*``、``\\|`` 等）逐字保真——dollarmath token 化后不再参与
   emphasis / escape 解析。
2. standalone ``$$`` 块与纯公式段落 → 保留 ``paragraph`` 容器 + 显式
   ``metadata_only`` policy + payload ``math_blocks``；LaTeX 不进
   canonical/units/jobs/RAG（freeze plan 只聚合 main_reading）。
3. gate 检测切换 parser-aware：fenced code 与 inline code span 内的
   ``$..$``/``$$..$$`` 不再强制 Candidate；真实 math 与货币 ``$5...$10``
   维持现行 candidate 结果（M-1/M-2 裁决）。
4. tree 投影守恒：payload 数学字段原样透传，fresh/reload 两次投影逐字节
   相等，text 字段不吸收公式源码。
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from app.schemas.reader_documents import StableDocumentBlock
from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.services.reader_orchestration.document_freeze_plan import (
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.input_suitability_gate import (
    evaluate_input_suitability,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownParseResult,
    MarkdownSourceParser,
)

_PARSER = MarkdownSourceParser()


def _parse(text: str) -> MarkdownParseResult:
    return _PARSER.parse(text)


def _prose() -> str:
    return (
        "Reading comprehension depends on steady attention to sentence "
        "structure and vocabulary in context every single day. Learners "
        "who annotate carefully retain far more detail than passive "
        "readers, and the difference compounds across every chapter of a "
        "long technical book assigned during the semester."
    )


def _block_dicts(result: MarkdownParseResult) -> list[dict[str, object]]:
    """Map ParsedBlock rows to StableDocumentBlock-compatible dicts."""
    return [
        {
            "block_id": block.block_id,
            "parent_block_id": block.parent_block_id,
            "order_index": block.order_index,
            "block_type": block.block_type,
            "text_content": block.text_content,
            "payload_json": dict(block.payload_json),
            "source_refs_json": {},
            "quality_json": {},
            **(
                {"interpretation_policy": dict(block.interpretation_policy)}
                if block.interpretation_policy is not None
                else {}
            ),
        }
        for block in result.blocks
    ]


# ---------------------------------------------------------------------------
# 1. inline math typed representation
# ---------------------------------------------------------------------------


def test_inline_math_latex_verbatim_asterisks_preserved() -> None:
    result = _parse("risk $a*b*c$ done")
    paragraph = result.blocks[0]
    assert paragraph.block_type == "paragraph"
    entries = paragraph.payload_json.get("inline_math")
    assert isinstance(entries, list) and len(entries) == 1
    # RED（现状）：无 dollarmath 时星号被 emphasis 吞掉、payload 无 inline_math。
    assert entries[0]["latex"] == "a*b*c"
    assert entries[0]["display"] is False


def test_inline_math_never_enters_block_text() -> None:
    result = _parse("risk $a*b*c$ done")
    paragraph = result.blocks[0]
    # 镜像 inline_images 判例：公式不贡献文本，前后紧邻时插入单个 U+0020；
    # 本例两侧已有空格，故为双空格拼接。
    assert "$" not in (paragraph.text_content or "")
    assert paragraph.text_content == "risk  done"


def test_inline_math_order_display_flag_and_before_utf16() -> None:
    result = _parse("A $$\\min_B f$$ B $y$ C")
    entries = result.blocks[0].payload_json.get("inline_math")
    assert isinstance(entries, list) and len(entries) == 2
    assert [entry["display"] for entry in entries] == [True, False]
    assert [entry["latex"] for entry in entries] == ["\\min_B f", "y"]
    assert [entry["before_utf16"] for entry in entries] == [2, 5]
    assert result.blocks[0].text_content == "A  B  C"


def test_math_pipe_escape_survives_inside_display_math() -> None:
    result = _parse("norm $$\\|A - B\\|_F^2$$ end")
    entries = result.blocks[0].payload_json.get("inline_math")
    assert isinstance(entries, list) and len(entries) == 1
    # RED（现状）：`\|` 被 escape 吃掉变成 `|`。
    assert "\\|A - B\\|_F^2" in entries[0]["latex"]


# ---------------------------------------------------------------------------
# 2. standalone math containers（metadata-only）
# ---------------------------------------------------------------------------


def test_standalone_math_block_metadata_only_paragraph() -> None:
    result = _parse("$$\n\\|A - B\\|_F^2\n$$\n\n" + _prose())
    math_block = result.blocks[0]
    assert math_block.block_type == "paragraph"
    policy = math_block.interpretation_policy
    assert policy is not None
    assert policy.get("default_route") == "metadata_only"
    assert policy.get("rag_eligible") is False
    entries = math_block.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "\n\\|A - B\\|_F^2\n"
    assert entries[0]["display"] is True
    # text_content 是展示回退（LaTeX 源），但绝不进入 canonical（见 §3 冻结测试）。
    assert math_block.text_content is not None
    assert "\\|A - B\\|_F^2" in math_block.text_content


def test_math_only_inline_paragraph_becomes_metadata_only_container() -> None:
    result = _parse("$x+y$\n\n" + _prose())
    first = result.blocks[0]
    assert first.block_type == "paragraph"
    policy = first.interpretation_policy
    assert policy is not None
    assert policy.get("default_route") == "metadata_only"
    entries = first.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "x+y"
    assert entries[0]["display"] is False
    # 后续纯散文段不受影响（默认策略，无显式 carrier）。
    prose = result.blocks[1]
    assert prose.interpretation_policy is None


def test_mixed_prose_paragraph_keeps_main_reading_default() -> None:
    result = _parse("A $x+y$ B continues as readable narrative text here.")
    paragraph = result.blocks[0]
    # 混排段落保持 main_reading 默认（散文仍进 canonical/units）。
    assert paragraph.interpretation_policy is None


# ---------------------------------------------------------------------------
# 3. 五类 owning container 的 inline_math
# ---------------------------------------------------------------------------


def test_heading_carries_inline_math() -> None:
    result = _parse("# Title $h^2$ tail\n\n" + _prose())
    heading = next(b for b in result.blocks if b.block_type == "heading")
    entries = heading.payload_json.get("inline_math")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "h^2"


def test_list_item_carries_inline_math() -> None:
    result = _parse("- item $i+1$ rest\n\n" + _prose())
    item = next(b for b in result.blocks if b.block_type == "list_item")
    entries = item.payload_json.get("inline_math")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "i+1"


def test_blockquote_carries_inline_math() -> None:
    result = _parse("> quoted $q_1$ note\n\n" + _prose())
    quote = next(b for b in result.blocks if b.block_type == "blockquote")
    entries = quote.payload_json.get("inline_math")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "q_1"


def test_table_cell_carries_inline_math() -> None:
    result = _parse("| A | B |\n|---|---|\n| $c^2$ | plain |\n\n" + _prose())
    cells = [b for b in result.blocks if b.block_type == "table_cell"]
    assert any(
        isinstance(cell.payload_json.get("inline_math"), list)
        and cell.payload_json["inline_math"][0]["latex"] == "c^2"
        for cell in cells
    )


# ---------------------------------------------------------------------------
# 4. analysis exclusion：canonical / freeze plan
# ---------------------------------------------------------------------------


def test_freeze_plan_canonical_excludes_math_and_keeps_prose() -> None:
    result = _parse(
        _prose() + "\n\n$$\n\\sum_{i=1}^{n} x_i^2\n$$\n\nTail paragraph keeps "
        "flowing with ordinary sentences for the reading experience."
    )
    plan = build_stable_document_freeze_plan(
        reading_record_id="rec-math-1",
        record_generation=1,
        document_version=1,
        title=None,
        blocks=_block_dicts(result),
    )
    assert "attention to sentence" in plan.canonical_text
    assert "sum_{i=1}" not in plan.canonical_text
    assert "\\sum" not in plan.canonical_text
    # 非 canonical 块的 canonical offsets 为 NULL。
    for block in plan.blocks:
        payload = block.payload_json
        if "math_blocks" in payload or "inline_math" in payload:
            has_canonical_range = (
                block.canonical_text_start_utf16 is not None
                and block.canonical_text_end_utf16 is not None
            ) or (
                block.canonical_text_start_utf16 is None
                and block.canonical_text_end_utf16 is None
                and _block_is_metadata_only(block)
            )
            assert has_canonical_range or _block_is_metadata_only(block)


def _block_is_metadata_only(block: StableDocumentBlock) -> bool:
    return block.interpretation_policy.default_route == "metadata_only"


# ---------------------------------------------------------------------------
# 5. tree projection conservation（fresh == reload 代理）
# ---------------------------------------------------------------------------


def _fake_raw_row(index: int, payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        block_id=f"b{index}",
        parent_block_id=None,
        order_index=index,
        block_type="paragraph",
        text_content=None if "math_blocks" in payload else "prose",
        payload_json=payload,
        source_refs_json={},
        quality_json={},
        canonical_text_start_utf16=None,
        canonical_text_end_utf16=None,
        interpretation_policy_json={"default_route": "metadata_only"},
        unit_id=None,
        anchor_segment_ids=[],
        semantic_contract_version=None,
        content_role=None,
        automatic_layer_policy=None,
        automatic_layer_policy_resolver_version=None,
    )


def test_tree_projection_conserves_math_payload_byte_identically() -> None:
    from app.services.reader_orchestration.snapshot import (
        _build_stable_document_tree,
    )

    payloads = [
        {
            "math_blocks": [{"latex": "\n\\sum_i x_i\n", "display": True}],
        },
        {"inline_math": [{"latex": "a*b*c", "display": False, "before_utf16": 5}]},
    ]
    rows = [_fake_raw_row(i, p) for i, p in enumerate(payloads)]
    build_result = SimpleNamespace(stable_document_blocks=rows, units=[], anchor_segments=[])

    first = _build_stable_document_tree(build_result)
    second = _build_stable_document_tree(build_result)

    def _math_view(nodes: Iterable[object]) -> list[object]:
        return [node.payload for node in nodes]

    assert _math_view(first) == _math_view(second)
    assert first[0].payload["math_blocks"][0]["latex"] == "\n\\sum_i x_i\n"
    assert first[1].payload["inline_math"][0]["latex"] == "a*b*c", (
        "inline latex 必须逐字保真（RED：现状无该字段）"
    )
    # 文本字段不得吸收公式源码。
    assert first[0].text_content is None


# ---------------------------------------------------------------------------
# 6. gate parser-aware detection（M-1/M-2 裁决落地）
# ---------------------------------------------------------------------------


def _evaluate(source_type: str, text: str):
    return evaluate_input_suitability(InputSuitabilityRequest(source_type=source_type, text=text))


def test_gate_fenced_code_dollars_no_longer_require_candidate() -> None:
    text = (
        _prose()
        + "\n\n```\n$$x^2$$ inside a fence\n```\n\n"
        + "Closing prose keeps flowing with ordinary sentences for readers."
    )
    result = _evaluate("markdown_file", text)
    assert result.outcome == "stable_document_ready", result.reasons
    assert "document_block_degraded" not in result.flags


def test_gate_inline_code_span_dollars_no_longer_require_candidate() -> None:
    text = (
        _prose() + "\n\nUse the placeholder `$x = y$` when quoting formulas in "
        "documentation comments and ordinary explanatory sentences."
    )
    result = _evaluate("markdown_file", text)
    assert result.outcome == "stable_document_ready", result.reasons
    assert "document_block_degraded" not in result.flags


def test_gate_currency_pair_keeps_current_candidate_behavior() -> None:
    text = (
        _prose() + "\n\nIt costs $5 now and $10 later according to the published "
        "price schedule distributed yesterday morning."
    )
    result = _evaluate("markdown_file", text)
    # M-2(c)：货币启发式保持现状 —— 继续 candidate review。
    assert result.outcome == "candidate_document_required"
    assert "document_block_degraded" in result.flags


def test_gate_real_math_still_requires_candidate() -> None:
    text = (
        _prose() + "\n\nThe appendix records $E = mc^2$ exactly as written in the "
        "original manuscript page shared with the class."
    )
    result = _evaluate("markdown_file", text)
    assert result.outcome == "candidate_document_required"


@pytest.mark.parametrize(
    "snippet",
    [
        r"\(x^2 + y^2 = z^2\)",
        r"\[E = mc^2\]",
    ],
)
def test_gate_escaped_pair_math_still_requires_candidate(snippet: str) -> None:
    text = (
        _prose()
        + "\n\nThe derivation closes with "
        + snippet
        + " and the surrounding explanation stays intact."
    )
    result = _evaluate("markdown_file", text)
    assert result.outcome == "candidate_document_required"


# ---------------------------------------------------------------------------
# 7. F1/F2 窄返修（review 2026-08-24）：blockquote 内 standalone $$ 块 +
#    _extract_inline_text 的 math_inline_double skip
# ---------------------------------------------------------------------------


def test_blockquote_standalone_math_block_pure_is_metadata_only() -> None:
    result = _parse("> $$E = mc^2$$\n\n" + _prose())
    quote = result.blocks[0]
    assert quote.block_type == "blockquote"
    entries = quote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    # latex 逐字（dollarmath 定界符内层源码）。
    assert entries[0]["latex"] == "E = mc^2"
    assert entries[0]["display"] is True
    # 纯公式 blockquote 退化为 metadata_only 容器。
    policy = quote.interpretation_policy
    assert policy is not None
    assert policy.get("default_route") == "metadata_only"
    assert policy.get("rag_eligible") is False


def test_blockquote_mixed_text_and_math_block_keeps_main_reading() -> None:
    result = _parse("> quoted note\n>\n> $$E = mc^2$$\n\n" + _prose())
    quote = next(b for b in result.blocks if b.block_type == "blockquote")
    # 混排（文本 + $$ 块）保持 main_reading 默认，仅记录 math_blocks。
    assert quote.interpretation_policy is None
    assert "quoted note" in (quote.text_content or "")
    entries = quote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "E = mc^2"
    assert entries[0]["display"] is True


def test_gate_blockquote_math_requires_candidate() -> None:
    text = (
        _prose()
        + "\n\n> The appendix quote follows here.\n>\n> $$E = mc^2$$\n\n"
        + "Closing prose keeps flowing with ordinary sentences for readers."
    )
    result = _evaluate("markdown_file", text)
    # M-1：真实 math 强制 Candidate——blockquote 内公式不得静默丢失。
    assert result.outcome == "candidate_document_required"


def test_footnote_inline_double_dollar_math_not_leaked_into_text() -> None:
    result = _parse(
        _prose() + " with a note.[^1]\n\n[^1]: note with $$x^2$$ inline math.\n"
    )
    footnote = next(b for b in result.blocks if b.block_type == "footnote")
    assert footnote.text_content is not None
    # F2：math_inline_double 源码不得经 else 分支 mangled 进 footnote 文本。
    assert "x^2" not in footnote.text_content


# ---------------------------------------------------------------------------
# 8. F3/F4 窄返修（review 2026-08-24 第二轮）：footnote 定义内 standalone
#    $$ 块接线 + blockquote 多行 $$ 的 "> " 前缀 de-quote + list_item 内
#    standalone $$ 续行缩进去除
# ---------------------------------------------------------------------------


def test_footnote_standalone_math_block_mixed_keeps_verbatim_payload() -> None:
    result = _parse("text[^1].\n\n[^1]: note\n\n    $$\n    x^2\n    $$\n")
    footnote = next(b for b in result.blocks if b.block_type == "footnote")
    # F3：footnote 定义内 standalone $$ 不再静默丢失——latex 逐字入 payload。
    entries = footnote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "\n    x^2\n    "
    assert entries[0]["display"] is True
    # 混排保持默认 policy；footnote 文本不含公式源。
    assert footnote.interpretation_policy is None
    assert footnote.text_content is not None
    assert "x^2" not in footnote.text_content


def test_footnote_standalone_math_block_pure_is_metadata_only() -> None:
    result = _parse("text[^1].\n\n[^1]:\n\n    $$\n    x^2\n    $$\n")
    footnote = next(b for b in result.blocks if b.block_type == "footnote")
    entries = footnote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "\n    x^2\n    "
    # 纯公式 footnote 走既有 _math_only_container_override 退化。
    policy = footnote.interpretation_policy
    assert policy is not None
    assert policy.get("default_route") == "metadata_only"
    assert policy.get("rag_eligible") is False


def test_blockquote_multiline_math_block_latex_dequoted() -> None:
    result = _parse("> q\n>\n> $$\n> E = mc^2\n> $$\n\n" + _prose())
    quote = next(b for b in result.blocks if b.block_type == "blockquote")
    entries = quote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    # F4：dollarmath state.src 切片带入的中间行 "> " 前缀必须确定性去除。
    assert entries[0]["latex"] == "\nE = mc^2\n"
    assert entries[0]["display"] is True
    # 混排（有可见文本 q）保持 main_reading 默认。
    assert quote.interpretation_policy is None


def test_blockquote_multiline_math_block_pure_is_metadata_only() -> None:
    result = _parse("> $$\n> E = mc^2\n> $$\n\n" + _prose())
    quote = next(b for b in result.blocks if b.block_type == "blockquote")
    entries = quote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["latex"] == "\nE = mc^2\n"
    policy = quote.interpretation_policy
    assert policy is not None
    assert policy.get("default_route") == "metadata_only"


def test_blockquote_singleline_math_block_dequote_idempotent() -> None:
    result = _parse("> $$E = mc^2$$\n\n" + _prose())
    quote = result.blocks[0]
    entries = quote.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    # 单行形态 content 本就干净，de-quote 幂等。
    assert entries[0]["latex"] == "E = mc^2"


def test_list_item_standalone_math_block_latex_dedented() -> None:
    result = _parse("- item\n\n  $$\n  x^2\n  $$\n\n" + _prose())
    math_block = next(
        b for b in result.blocks if b.payload_json.get("math_blocks")
    )
    entries = math_block.payload_json["math_blocks"]
    # list_item 内 standalone $$ 走顶层 handler；续行缩进必须确定性去除。
    assert entries[0]["latex"] == "\nx^2\n"
    assert entries[0]["display"] is True
    assert math_block.interpretation_policy is not None
    assert math_block.interpretation_policy.get("default_route") == "metadata_only"


def test_top_level_math_block_latex_stays_verbatim() -> None:
    result = _parse("$$\n  a + b\n  c + d\n$$\n\n" + _prose())
    math_block = result.blocks[0]
    entries = math_block.payload_json.get("math_blocks")
    assert isinstance(entries, list) and len(entries) == 1
    # 顶层 content 无污染，逐字保真（含用户的书写缩进），不被 dedent 触碰。
    assert entries[0]["latex"] == "\n  a + b\n  c + d\n"
