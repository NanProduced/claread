"""Tests for planner_runtime module — semantic decision logic."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskPageIdentity,
)
from app.services.reader_ask import planner_runtime
from app.services.reader_ask import config as cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record(
    *,
    record_id: UUID | None = None,
    title: str = "Test Article",
    overview: str | None = "A test article about AI.",
    sentence_entries: list[dict[str, Any]] | None = None,
) -> Any:
    """Build a lightweight record-like object for testing."""
    render_scene: dict[str, Any] = {}
    if overview is not None:
        render_scene["content_summary"] = {"overview": overview}
    render_scene["sentence_entries"] = sentence_entries or []
    return type("Record", (), {
        "record_id": record_id or uuid4(),
        "title": title,
        "render_scene": render_scene,
    })()


def _page_identity(**overrides: object) -> ReaderAskPageIdentity:
    defaults = {
        "record_id": "00000000-0000-0000-0000-000000000001",
        "title": "Test Article",
        "available_context_capabilities": ["record_context"],
        "has_article_overview": True,
        "has_sentence_entries": False,
        "has_annotations": False,
        "has_reader_notes": False,
    }
    defaults.update(overrides)
    return ReaderAskPageIdentity(**defaults)  # type: ignore[arg-type]


def _render_overview_cb(record: Any) -> str | None:
    return record.render_scene.get("content_summary", {}).get("overview")


def _has_sentence_entries_cb(record: Any) -> bool:
    entries = record.render_scene.get("sentence_entries") or record.render_scene.get("sentenceEntries")
    return isinstance(entries, list) and bool(entries)


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

class TestSubmissionMode:
    def test_toolbar_selection_is_quick_action(self) -> None:
        attachment = ReaderAskAttachment(
            kind="text_selection",
            subtype="sentence",
            label="整句",
            selected_text="Test.",
            metadata=ReaderAskAttachmentMetadata(
                source_surface="selection_toolbar",
                entry_action="why_here",
                sentence_id="s1",
                paragraph_id="p1",
            ),
        )
        assert planner_runtime.submission_mode(entry_action="why_here", attachments=[attachment]) == "quick_action"

    def test_non_toolbar_is_chat(self) -> None:
        attachment = ReaderAskAttachment(
            kind="text_selection",
            subtype="sentence",
            label="整句",
            selected_text="Test.",
            metadata=ReaderAskAttachmentMetadata(
                source_surface="ask_panel",
                entry_action="why_here",
                sentence_id="s1",
                paragraph_id="p1",
            ),
        )
        assert planner_runtime.submission_mode(entry_action="why_here", attachments=[attachment]) == "chat"

    def test_non_quick_action_entry_is_chat(self) -> None:
        attachment = ReaderAskAttachment(
            kind="text_selection",
            subtype="sentence",
            label="整句",
            selected_text="Test.",
            metadata=ReaderAskAttachmentMetadata(
                source_surface="selection_toolbar",
                entry_action="ask_about_this",
                sentence_id="s1",
                paragraph_id="p1",
            ),
        )
        assert planner_runtime.submission_mode(entry_action="ask_about_this", attachments=[attachment]) == "chat"


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
        result = planner_runtime.quick_action_content(entry_action="why_here", generated_annotation=None)
        assert "暂时无法完成" in result

    def test_not_applicable_with_reason(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="why_here",
            generated_annotation={
                "status": "not_applicable",
                "reason": "选区过短",
                "suggestion": "请扩展选区",
            },
        )
        assert "没有直接生成" in result
        assert "选区过短" in result
        assert "请扩展选区" in result

    def test_grammar_note_kind(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="why_here",
            generated_annotation={
                "kind": "grammar_note",
                "label": "让步状语从句",
                "note_zh": "although引导让步",
                "analysis_scope": "focus_span",
                "focus_text": "although it rained",
            },
        )
        assert "关键语法点" in result
        assert "让步状语从句" in result
        assert "聚焦片段" in result

    def test_sentence_analysis_kind(self) -> None:
        result = planner_runtime.quick_action_content(
            entry_action="explain_this",
            generated_annotation={
                "kind": "sentence_analysis",
                "label": "SVO结构",
                "analysis_zh": "主语+谓语+宾语",
            },
        )
        assert "句型概述" in result
        assert "SVO结构" in result


# ---------------------------------------------------------------------------
# TestFallbackReferenceQuery
# ---------------------------------------------------------------------------

class TestFallbackReferenceQuery:
    def test_book_title_marks(self) -> None:
        assert planner_runtime.fallback_reference_query("之前那篇《Climate Policy》里也提过") == "Climate Policy"

    def test_double_quotes(self) -> None:
        assert planner_runtime.fallback_reference_query('关于"AI Ethics"那篇文章') == "AI Ethics"

    def test_weak_chinese_pattern_no_longer_matches(self) -> None:
        """P3-S3: Weak reference regex removed; natural language patterns
        like '之前那篇...的文章' no longer extract a reference query."""
        assert planner_runtime.fallback_reference_query("之前那篇climate policy的文章也提过吗？") is None

    def test_weak_english_pattern_no_longer_matches(self) -> None:
        """P3-S3: Weak reference regex removed; 'that article about X'
        no longer extracts a reference query."""
        assert planner_runtime.fallback_reference_query("that article about climate policy also mentioned this") is None

    def test_no_reference_returns_none(self) -> None:
        assert planner_runtime.fallback_reference_query("这句话什么意思？") is None

    def test_short_topic_in_title_marks_still_works(self) -> None:
        """Short topics inside explicit title markers should still be extracted."""
        assert planner_runtime.fallback_reference_query("关于《AI》的文章") == "AI"


# ---------------------------------------------------------------------------
# TestPlannerHistoryMessages
# ---------------------------------------------------------------------------

class TestPlannerHistoryMessages:
    def test_truncates_to_max_messages(self) -> None:
        messages = [{"role": "user", "content_md": f"msg{i}"} for i in range(20)]
        result = planner_runtime.planner_history_messages(messages, max_messages=5)
        # Should have at most 5 recent messages (plus possible structured summary)
        recent = [m for m in result if m["role"] != "system"]
        assert len(recent) == 5

    def test_filters_non_user_assistant_roles(self) -> None:
        messages = [
            {"role": "system", "content_md": "sys"},
            {"role": "user", "content_md": "hello"},
            {"role": "tool", "content_md": "tool output"},
            {"role": "assistant", "content_md": "hi"},
        ]
        result = planner_runtime.planner_history_messages(messages, max_messages=10)
        roles = [m["role"] for m in result if m["role"] != "system"]
        assert "system" not in roles
        assert "tool" not in roles
        assert roles == ["user", "assistant"]

    def test_truncate_callback_applied(self) -> None:
        messages = [{"role": "user", "content_md": "a" * 1000}]

        def truncate(content_md: str, **kwargs: object) -> str:
            return content_md[:10]

        result = planner_runtime.planner_history_messages(
            messages,
            max_messages=10,
            truncate_history_message_cb=truncate,
        )
        recent = [m for m in result if m["role"] == "user"]
        assert len(recent[0]["content_md"]) == 10

    def test_default_truncation_without_callback(self) -> None:
        long_msg = "x" * (cfg.MAX_MESSAGE_TEXT + 100)
        messages = [{"role": "user", "content_md": long_msg}]
        result = planner_runtime.planner_history_messages(messages, max_messages=10)
        recent = [m for m in result if m["role"] == "user"]
        assert len(recent[0]["content_md"]) == cfg.MAX_MESSAGE_TEXT


# ---------------------------------------------------------------------------
# TestFallbackSemanticPlannerDecision
# ---------------------------------------------------------------------------

class TestFallbackSemanticPlannerDecision:
    def test_default_intent_is_explain(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这篇文章讲了什么？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "explain"

    def test_lookup_in_context_forces_vocabulary(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这里的语法结构",
            entry_action="lookup_in_context",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "vocabulary"

    def test_why_here_forces_grammar(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这个词什么意思",
            entry_action="why_here",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "grammar"

    def test_natural_language_defaults_to_explain_breakdown(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="帮我拆解这个长句",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "explain"

    def test_natural_language_defaults_to_explain_compare(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这两篇文章的观点有什么区别？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "explain"

    def test_natural_language_defaults_to_explain_grammar_keyword(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这里的语法是什么",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "explain"

    def test_natural_language_defaults_to_explain_vocabulary_keyword(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="What does this word mean here?",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "explain"

    def test_natural_language_defaults_to_explain_difference(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这两篇文章有什么区别",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "explain"

    def test_weak_natural_language_no_cross_record(self) -> None:
        """P3-S3: Weak natural language references no longer trigger
        cross_record in fallback. LLM planner handles these."""
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.working_set.cross_record_context_allowed is False
        assert decision.reference_request.requested is False

    def test_title_marker_reference_enables_cross_record(self) -> None:
        """Explicit title markers (《》) still trigger cross_record."""
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="之前那篇《Climate Policy》里也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.working_set.cross_record_context_allowed is True
        assert decision.reference_request.requested is True

    def test_title_reference_without_anchor_sets_conservative_reason(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message='关于"AI Ethics"那篇文章也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.clarification_reason == "fallback_title_reference_without_anchor"
        assert decision.clarification_only is False

    def test_title_reference_with_anchor_no_conservative_reason(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="之前那篇《Climate Policy》里也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.clarification_reason is None

    def test_external_attachment_enables_cross_record(self) -> None:
        attachment = ReaderAskAttachment(
            kind="record_ref",
            subtype="related_record",
            label="Related Article",
            metadata=ReaderAskAttachmentMetadata(
                source_surface="test",
                record_id="00000000-0000-0000-0000-000000000002",
            ),
        )
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这篇文章讲了什么？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[attachment],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.working_set.cross_record_context_allowed is True

    def test_no_reference_no_cross_record(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这句话什么意思？",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.reference_request.requested is False
        assert decision.working_set.cross_record_context_allowed is False

    def test_rationale_includes_failure_reason(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="test",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[],
            record=_record(),
            failure_reason="timeout",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert "timeout" in decision.rationale

    def test_local_anchor_with_sentence_entries_no_insights_without_explicit_intent(self) -> None:
        """Fallback no longer infers grammar/breakdown from keywords, so
        record_insights_needed is not set for natural language with local anchor."""
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这里的语法结构",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            record=_record(sentence_entries=[{"id": "s1"}]),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.working_set.local_context_window_needed is True
        assert decision.working_set.record_insights_needed is False

    def test_why_here_with_sentence_entries_needs_insights(self) -> None:
        """Explicit why_here entry_action forces grammar intent, which
        triggers record_insights_needed when sentence entries exist."""
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="为什么这里这样写",
            entry_action="why_here",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            record=_record(sentence_entries=[{"id": "s1"}]),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "grammar"
        assert decision.working_set.local_context_window_needed is True
        assert decision.working_set.record_insights_needed is True

    def test_dictionary_anchor_forces_vocabulary(self) -> None:
        decision = planner_runtime.fallback_semantic_planner_decision(
            user_message="这是什么",
            entry_action="ask_about_this",
            page_identity=_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="dictionary_entry", sentence_id=None, selected_text="test", dict_entry_id=1)],
            record=_record(),
            failure_reason="test",
            render_overview_cb=_render_overview_cb,
            has_sentence_entries_cb=_has_sentence_entries_cb,
        )
        assert decision.resolved_intent == "vocabulary"
        assert decision.working_set.dictionary_needed is True


# ---------------------------------------------------------------------------
# TestRenderSceneHasSentenceEntries
# ---------------------------------------------------------------------------

class TestRenderSceneHasSentenceEntries:
    def test_with_sentence_entries(self) -> None:
        record = _record(sentence_entries=[{"id": "s1"}])
        assert planner_runtime._render_scene_has_sentence_entries(record) is True

    def test_without_sentence_entries(self) -> None:
        record = _record(sentence_entries=[])
        assert planner_runtime._render_scene_has_sentence_entries(record) is False

    def test_camel_case_key(self) -> None:
        record = type("Record", (), {
            "record_id": uuid4(),
            "title": "Test",
            "render_scene": {"sentenceEntries": [{"id": "s1"}]},
        })()
        assert planner_runtime._render_scene_has_sentence_entries(record) is True


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
            reason="选区过短",
            suggestion="请扩展选区",
        )
        assert result["status"] == "not_applicable"
        assert result["kind"] == "grammar_note"
        assert result["sentence_id"] == "s1"
        assert result["reason"] == "选区过短"
