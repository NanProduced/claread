"""L1 — Authoritative Normalization 安全样本合同测试（测试先行）。

封住的产品合同（TMP-reader-markdown-adaptation-analysis-2026-07-28 §5.4）：

1. script / iframe / event handler 属性 / javascript:/data:/vbscript: 协议
   被移除或安全降级，绝不进入正文可执行形态；分类为 ``silent`` 或
   ``adaptation_notice``，不得整篇 ``input_rejected_or_action_required``。
2. 安全 ``<aside>``、普通 http/https/mailto 链接、``vector<T>`` / ``<name>``
   非 HTML 占位文本：清洗后继续，不得触发 candidate / rejected；aside
   文本必须可见保留且带 ``adaptation_notice``。
3. 未闭合 fence 属于 ``content_check`` 分类（candidate 路径），不得静默。
4. parser 的每条 warning 与 gate 结果都携带显式三级分类
   （``silent`` / ``adaptation_notice`` / ``content_check``）。
5. 行列不一致的 GFM table（parser 会丢/补单元格）属于
   ``content_check``；行列一致的确定性 table 走 stable。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityGate,
)
from app.services.reader_orchestration.markdown_source_parser import (
    MarkdownParseResult,
    MarkdownSourceParser,
)

_FIXTURES_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "markdown_structured_source"
)

_CLASSIFICATIONS = {"silent", "adaptation_notice", "content_check"}

# Codes that MUST NOT be executed / rendered as active content anywhere
# in the normalized document payload.
_FORBIDDEN_TEXT_FRAGMENTS = ("<script", "<iframe", "onerror", "javascript:", "data:", "vbscript:")


def _load_input(name: str) -> str:
    return (_FIXTURES_ROOT / name / "input.md").read_text(encoding="utf-8")


def _parse(name: str) -> MarkdownParseResult:
    return MarkdownSourceParser().parse(_load_input(name))


def _gate(name: str):
    request = InputSuitabilityRequest(
        source_type="markdown_file",
        filename=f"{name}.md",
        text=_load_input(name),
    )
    return InputSuitabilityGate().evaluate(request)


# 满足 gate 最低英文词数阈值的填充段落（gate 路由断言需要 ≥50 英文词，
# 否则会先触发 too_short_for_learning 而掩盖结构路由）。
_ENGLISH_FILLER = (
    "This article explains how communities compare evidence, revise plans, "
    "and discuss tradeoffs before making a decision about public projects. "
    "Each paragraph stays focused on natural language reading, includes "
    "complete sentences, and keeps enough context for vocabulary, grammar, "
    "and sentence analysis to be genuinely useful for an English learner."
)


def _gate_with_filler(name: str):
    """Gate evaluation with enough English prose to isolate structure routing."""
    request = InputSuitabilityRequest(
        source_type="markdown_file",
        filename=f"{name}.md",
        text=f"{_load_input(name)}\n\n{_ENGLISH_FILLER}\n\n{_ENGLISH_FILLER}",
    )
    return InputSuitabilityGate().evaluate(request)


def _all_text_fragments(result: MarkdownParseResult) -> list[str]:
    """Collect every user-visible text fragment (text_content + link hrefs)."""
    fragments: list[str] = []
    for block in result.blocks:
        if block.text_content:
            fragments.append(block.text_content)
        for link in block.payload_json.get("links", []):
            fragments.append(str(link.get("href", "")))
        for mark in block.payload_json.get("inline_marks", []):
            if "href" in mark:
                fragments.append(str(mark["href"]))
    return fragments


def _classifications_by_code(result: MarkdownParseResult) -> dict[str, str]:
    return {w.code: w.classification for w in result.warnings}


# ---------------------------------------------------------------------------
# 1. 危险内容安全降级
# ---------------------------------------------------------------------------


def test_safe_fixture_routes_stable_not_rejected() -> None:
    """script/iframe/event-handler/危险协议样本：清洗后继续，不得 rejected / candidate。"""
    result = _parse("safe_html_adaptation")
    assert result.outcome == "stable_document_ready"

    gate = _gate("safe_html_adaptation")
    assert gate.outcome == "stable_document_ready"


def test_safe_fixture_no_executable_form_survives() -> None:
    """可执行形态（script/iframe 标签、事件属性、危险协议 href）绝不进入正文。"""
    result = _parse("safe_html_adaptation")
    fragments = _all_text_fragments(result)
    assert fragments, "fixture must produce visible text"
    for fragment in fragments:
        lowered = fragment.lower()
        for forbidden in _FORBIDDEN_TEXT_FRAGMENTS:
            assert forbidden not in lowered, (
                f"forbidden fragment {forbidden!r} survived in {fragment!r}"
            )


def test_safe_fixture_dangerous_content_is_adaptation_notice_not_rejected() -> None:
    """危险内容降级分类为 adaptation_notice（允许 silent），不得导致 rejected。"""
    result = _parse("safe_html_adaptation")
    classes = _classifications_by_code(result)
    assert classes["raw_html_block"] in {"silent", "adaptation_notice"}
    assert classes["inline_html"] in {"silent", "adaptation_notice"}
    assert classes["unsafe_link_protocol"] in {"silent", "adaptation_notice"}
    # 不允许出现 content_check —— 否则该样本文档会落入 candidate。
    assert "content_check" not in classes.values()


def test_unsafe_link_text_preserved_and_href_recorded() -> None:
    """危险协议链接：链接文字留在正文，href 只进 stripped_links 审计。"""
    result = _parse("safe_html_adaptation")
    unsafe_block = next(
        b for b in result.blocks if b.payload_json.get("stripped_links")
    )
    assert "javascript link" in (unsafe_block.text_content or "")
    assert "vbscript link" in (unsafe_block.text_content or "")
    stripped = unsafe_block.payload_json["stripped_links"]
    assert {entry["reason"] for entry in stripped} == {"unsafe_protocol"}
    # 危险 href 不得出现在 links / inline_marks。
    for link in unsafe_block.payload_json.get("links", []):
        assert not link["href"].lower().startswith(("javascript:", "data:", "vbscript:"))
    for mark in unsafe_block.payload_json.get("inline_marks", []):
        assert not str(mark.get("href", "")).lower().startswith(
            ("javascript:", "data:", "vbscript:")
        )


# ---------------------------------------------------------------------------
# 2. 安全内容清洗后继续
# ---------------------------------------------------------------------------


def test_safe_aside_text_visible_with_notice() -> None:
    """安全 <aside> 文本必须可见保留（聚合段落），且给 adaptation_notice。"""
    result = _parse("safe_html_adaptation")
    aside_block = next(
        b
        for b in result.blocks
        if b.text_content and "genuine reading note" in b.text_content
    )
    assert aside_block.block_type == "paragraph"
    classes = _classifications_by_code(result)
    assert classes["raw_html_block"] == "adaptation_notice"


def test_safe_links_preserved() -> None:
    """http/https/mailto 链接保留在 payload_json.links，不触发 candidate。"""
    result = _parse("safe_html_adaptation")
    link_block = next(
        b for b in result.blocks if b.payload_json.get("links")
    )
    hrefs = {link["href"] for link in link_block.payload_json["links"]}
    assert "https://example.com/docs" in hrefs
    assert "http://example.com" in hrefs
    assert "mailto:test@example.com" in hrefs
    assert result.outcome == "stable_document_ready"


def test_non_html_angle_bracket_text_preserved() -> None:
    """vector<T> / <name> 这类非 HTML 占位文本必须逐字保留、不产生 warning。"""
    result = _parse("safe_html_adaptation")
    cpp_block = next(
        b for b in result.blocks if b.text_content and "vector" in b.text_content
    )
    assert "vector<T>" in cpp_block.text_content
    assert "<name>" in cpp_block.text_content
    # 该 block 不得被标记为 inline HTML 提取。
    assert "extracted_from" not in cpp_block.payload_json


def test_gate_result_carries_adaptation_records() -> None:
    """gate 结果必须有结构化 adaptations（三级分类），且该样本不含 content_check。"""
    gate = _gate("safe_html_adaptation")
    assert gate.adaptations, "adaptations must be populated from parser warnings"
    for record in gate.adaptations:
        assert record.classification in _CLASSIFICATIONS
    codes = {record.code: record.classification for record in gate.adaptations}
    assert codes["raw_html_block"] == "adaptation_notice"
    assert codes["unsafe_link_protocol"] == "adaptation_notice"
    assert "content_check" not in codes.values()


# ---------------------------------------------------------------------------
# 3. 未闭合 fence → content_check
# ---------------------------------------------------------------------------


def test_unclosed_fence_is_content_check_candidate() -> None:
    """未闭合 fence 进入 content_check 分类（candidate 路径），不得静默。"""
    result = _parse("unclosed_fence")
    assert result.outcome == "candidate_document_required"
    classes = _classifications_by_code(result)
    assert classes["has_unclosed_fence"] == "content_check"

    gate = _gate_with_filler("unclosed_fence")
    assert gate.outcome == "candidate_document_required"
    gate_codes = {record.code: record.classification for record in gate.adaptations}
    assert gate_codes["has_unclosed_fence"] == "content_check"


def test_footnote_reference_is_content_check() -> None:
    """footnote_ref 从正文丢失属于 content_check（保持 candidate）。"""
    result = _parse("footnote")
    assert result.outcome == "candidate_document_required"
    assert _classifications_by_code(result)["footnote_reference"] == "content_check"


# ---------------------------------------------------------------------------
# 4. 三级分类字段合同
# ---------------------------------------------------------------------------


def test_every_parser_warning_carries_closed_classification() -> None:
    """所有 fixture 的每条 parser warning 都必须带闭合三级分类。"""
    for fixture_dir in sorted(_FIXTURES_ROOT.iterdir()):
        if not (fixture_dir / "input.md").exists():
            continue
        result = MarkdownSourceParser().parse(
            (fixture_dir / "input.md").read_text(encoding="utf-8")
        )
        for warning in result.warnings:
            assert warning.classification in _CLASSIFICATIONS, (
                f"fixture {fixture_dir.name}: warning {warning.code!r} "
                f"has invalid classification {warning.classification!r}"
            )


def test_outcome_follows_classification() -> None:
    """outcome 合同：存在 content_check → candidate；仅 silent/adaptation_notice → stable。"""
    for name in ("safe_html_adaptation", "gfm_table", "code_mermaid", "r14_complex"):
        result = _parse(name)
        classes = {w.classification for w in result.warnings}
        assert "content_check" not in classes, f"{name} must not need content_check"
        assert result.outcome == "stable_document_ready", (
            f"{name}: only silent/adaptation_notice warnings but outcome={result.outcome}"
        )
    for name in ("footnote", "unclosed_fence", "table_structure_uncertain"):
        result = _parse(name)
        classes = {w.classification for w in result.warnings}
        assert "content_check" in classes
        assert result.outcome == "candidate_document_required"


# ---------------------------------------------------------------------------
# 5. 行列不一致 table → content_check
# ---------------------------------------------------------------------------


def test_uncertain_table_is_content_check_candidate() -> None:
    """行列不一致（parser 会丢/补单元格）的 table 必须 content_check。"""
    result = _parse("table_structure_uncertain")
    assert result.outcome == "candidate_document_required"
    classes = _classifications_by_code(result)
    assert classes["table_structure_uncertain"] == "content_check"
    table_block = next(b for b in result.blocks if b.block_type == "table")
    assert table_block.payload_json.get("structure_uncertain") is True

    gate = _gate_with_filler("table_structure_uncertain")
    assert gate.outcome == "candidate_document_required"
    assert "table_structure_uncertain" in gate.flags


def test_deterministic_table_routes_stable() -> None:
    """表头分隔行齐全、行列一致的 GFM table → stable_document_ready。"""
    result = _parse("gfm_table")
    assert result.outcome == "stable_document_ready"
    table_block = next(b for b in result.blocks if b.block_type == "table")
    assert table_block.payload_json.get("structure_uncertain") is not True
    assert table_block.payload_json["header_rows"] == 1

    gate = _gate_with_filler("gfm_table")
    assert gate.outcome == "stable_document_ready"
    assert "table_structure_uncertain" not in gate.flags


def test_fixture_expected_diagnostics_declare_classification() -> None:
    """所有 fixture 的 expected_diagnostics.json 必须声明每条 warning 的分类。"""
    for fixture_dir in sorted(_FIXTURES_ROOT.iterdir()):
        diagnostics_path = fixture_dir / "expected_diagnostics.json"
        if not diagnostics_path.exists():
            continue
        diagnostics: dict[str, Any] = json.loads(
            diagnostics_path.read_text(encoding="utf-8")
        )
        for warning in diagnostics.get("warnings", []):
            assert warning.get("classification") in _CLASSIFICATIONS, (
                f"{fixture_dir.name}: warning {warning.get('code')!r} "
                f"missing closed-set classification"
            )
