"""Tests for planner_runtime module — quick-action and submission-mode helpers.

Round 15: the semantic planner execution path (``run_semantic_planner``,
``resolve_semantic_planning``, ``fallback_semantic_planner_decision``,
``planner_history_messages``, ``fallback_reference_query``) has been
removed from ``planner_runtime``. This test file now covers only the
live helpers that remain: ``annotation_quick_action_kind``,
``submission_mode``, ``quick_action_not_applicable``,
``quick_action_label``, ``quick_action_content``.
"""

from __future__ import annotations

from typing import Any

from app.schemas.reader_ask import (
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
)
from app.services.reader_ask import planner_runtime


# ---------------------------------------------------------------------------
# TestAnnotationQuickActionKind
# ---------------------------------------------------------------------------

class TestAnnotationQuickActionKind:
    def test_grammar_task_mode(self) -> None:
        assert planner_runtime.annotation_quick_action_kind("grammar", "ask_about_this") == "grammar_note"

    def test_why_here_entry_action(self) -> None:
        assert planner_runtime.annotation_quick_action_kind("explain", "why_here") == "grammar_note"

    def test_breakdown_task_mode(self) -> None:
        assert planner_runtime.annotation_quick_action_kind("breakdown", "ask_about_this") == "sentence_analysis"

    def test_explain_this_entry_action(self) -> None:
        assert planner_runtime.annotation_quick_action_kind("explain", "explain_this") == "sentence_analysis"

    def test_no_match_returns_none(self) -> None:
        assert planner_runtime.annotation_quick_action_kind("explain", "ask_about_this") is None


# ---------------------------------------------------------------------------
# TestSubmissionMode
# ---------------------------------------------------------------------------

def _attachment(*, source_surface: str = "inline") -> ReaderAskAttachment:
    return ReaderAskAttachment(
        kind="text_selection",
        subtype="text",
        label="text",
        metadata=ReaderAskAttachmentMetadata(source_surface=source_surface),
    )


class TestSubmissionMode:
    def test_toolbar_selection_is_quick_action(self) -> None:
        result = planner_runtime.submission_mode(
            entry_action="why_here",
            attachments=[_attachment(source_surface="selection_toolbar")],
        )
        assert result == "quick_action"

    def test_non_toolbar_is_chat(self) -> None:
        result = planner_runtime.submission_mode(
            entry_action="why_here",
            attachments=[_attachment(source_surface="inline")],
        )
        assert result == "chat"

    def test_non_quick_action_entry_is_chat(self) -> None:
        result = planner_runtime.submission_mode(
            entry_action="ask_about_this",
            attachments=[_attachment(source_surface="selection_toolbar")],
        )
        assert result == "chat"


# ---------------------------------------------------------------------------
# TestQuickActionLabel
# ---------------------------------------------------------------------------

class TestQuickActionLabel:
    def test_why_here(self) -> None:
        assert planner_runtime.quick_action_label("why_here") == "语法解析"

    def test_explain_this(self) -> None:
        assert planner_runtime.quick_action_label("explain_this") == "句子拆分"

    def test_other_action(self) -> None:
        assert planner_runtime.quick_action_label("ask_about_this") == "快捷分析"


# ---------------------------------------------------------------------------
# TestQuickActionContent
# ---------------------------------------------------------------------------

class TestQuickActionContent:
    def test_none_annotation(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="why_here",
            generated_annotation=None,
        )
        assert "语法解析" in result
        assert "暂时无法完成" in result

    def test_not_applicable_with_reason(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="why_here",
            generated_annotation={
                "status": "not_applicable",
                "reason": "句子太短",
                "suggestion": "请选更长的句子",
            },
        )
        assert "语法解析" in result
        assert "句子太短" in result
        assert "请选更长的句子" in result

    def test_grammar_note_kind(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="why_here",
            generated_annotation={
                "kind": "grammar_note",
                "label": "被动语态",
                "focus_text": "was built",
                "analysis_scope": "focus_span",
                "note_zh": "这是被动语态结构。",
            },
        )
        assert "被动语态" in result
        assert "was built" in result
        assert "这是被动语态结构" in result

    def test_sentence_analysis_kind(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="explain_this",
            generated_annotation={
                "kind": "sentence_analysis",
                "label": "SVO结构",
                "analysis_zh": "主谓宾结构。",
            },
        )
        assert "句型概述" in result
        assert "SVO结构" in result


# ---------------------------------------------------------------------------
# TestQuickActionNotApplicable
# ---------------------------------------------------------------------------

class TestQuickActionNotApplicable:
    def test_structure(self) -> None:
        result = planner_runtime.quick_action_not_applicable(
            kind="grammar_note",
            sentence_id="s1",
            sentence_text="Test sentence.",
            focus_text="Test",
            reason="Too short",
            suggestion="Pick a longer sentence",
        )
        assert result["status"] == "not_applicable"
        assert result["kind"] == "grammar_note"
        assert result["sentence_id"] == "s1"
        assert result["source_sentence"] == "Test sentence."
        assert result["focus_text"] == "Test"
        assert result["reason"] == "Too short"
        assert result["suggestion"] == "Pick a longer sentence"
