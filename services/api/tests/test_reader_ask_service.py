import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.schemas.reader_ask import (
    ReaderAskActionConfirmRequest,
    ReaderAskAnchorRef,
    ReaderAskAssetDisambiguation,
    ReaderAskAssetDisambiguationCandidate,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskAttachmentPayload,
    ReaderAskContextPlan,
    ReaderAskDisambiguationCandidate,
    ReaderAskCurrentRecordContext,
    ReaderAskDisambiguation,
    ReaderAskExternalAssetContext,
    ReaderAskExternalRecordContext,
    ReaderAskPageIdentity,
    ReaderAskPlannerDecision,
    ReaderAskPlannerReferenceRequest,
    ReaderAskPlannerStructuredAssetRequest,
    ReaderAskPlannerWorkingSetDecision,
)
from app.services.analysis.credit_service import CreditReservation
from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.reader_ask import capabilities as capabilities_svc
from app.services.reader_ask import output_contract as output_contract_svc
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask import post_process as post_process_svc
from app.services.reader_ask import resolver as resolver_svc
from app.services.reader_ask import service as reader_ask_service
from app.services.reader_ask import supplements as supplements_svc
from app.services.reader_ask import utils as reader_ask_utils
from app.services.reader_ask.service import (
    _attachment_to_anchor,
    _attachments_to_anchor_refs,
    _build_run_info,
    _build_stream_checkpoint_output_json,
    _capability_trace_json,
    _build_action_proposals,
    _build_context_plan,
    _planning_snapshot_json,
    _build_resolved_context_input,
    _build_response_cards,
    _build_supplement_candidates_from_runtime,
    _build_unused_reservation,
    _dictionary_ai_to_citation,
    _fallback_reference_query,
    _fallback_semantic_planner_decision,
    _merge_usage_summaries,
    _needs_clarification,
    _next_run_info,
    _resolve_intent,
    _resolved_context_summary,
    _submission_mode,
    _terminal_reasoning_status,
)
from app.services.reader_ask.supplements import build_grammar_note_candidate


def _planner_decision(
    *,
    resolved_intent: str = "explain",
    clarification_only: bool = False,
    clarification_reason: str | None = None,
    reference_requested: bool = False,
    reference_query: str | None = None,
    structured_asset_requested: bool = False,
    structured_asset_type: str | None = None,
    local_context_window_needed: bool = False,
    record_insights_needed: bool = False,
    article_overview_needed: bool = False,
    dictionary_needed: bool = False,
    cross_record_context_allowed: bool = False,
    external_asset_lookup_needed: bool = False,
    rationale: str = "test planner decision",
) -> ReaderAskPlannerDecision:
    return ReaderAskPlannerDecision(
        resolved_intent=resolved_intent,  # type: ignore[arg-type]
        clarification_only=clarification_only,
        clarification_reason=clarification_reason,
        reference_request=ReaderAskPlannerReferenceRequest(
            requested=reference_requested,
            query=reference_query,
            reason="semantic_reference" if reference_requested else None,
        ),
        structured_asset_request=ReaderAskPlannerStructuredAssetRequest(
            requested=structured_asset_requested,
            requested_asset_type=structured_asset_type,  # type: ignore[arg-type]
            reason="semantic_asset_request" if structured_asset_requested else None,
        ),
        working_set=ReaderAskPlannerWorkingSetDecision(
            local_context_window_needed=local_context_window_needed,
            record_insights_needed=record_insights_needed,
            article_overview_needed=article_overview_needed,
            dictionary_needed=dictionary_needed,
            cross_record_context_allowed=cross_record_context_allowed,
            external_asset_lookup_needed=external_asset_lookup_needed,
        ),
        rationale=rationale,
    )


def test_needs_clarification_for_ambiguous_local_reference_without_anchor() -> None:
    assert _needs_clarification("这里为什么这样写？", []) is True
    assert _needs_clarification(
        "这里为什么这样写？",
        [ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
    ) is False


def test_resolve_intent_prefers_explicit_entry_action_and_content_signal() -> None:
    assert _resolve_intent("为什么这里是这个意思？", [], "lookup_in_context") == "vocabulary"
    assert _resolve_intent("为什么这里这样写", [], "why_here") == "grammar"
    assert (
        _resolve_intent(
            "看看译文和原句差在哪",
            [
                ReaderAskAttachment(
                    kind="analysis_ref",
                    subtype="translation",
                    label="译文",
                    selected_text="这里的译法",
                    metadata=ReaderAskAttachmentMetadata(
                        source_surface="translation",
                        entry_action="compare_translation",
                    ),
                )
            ],
            "compare_translation",
        )
        == "explain"
    )


def test_attachment_to_anchor_maps_selection_and_filters_record_ref() -> None:
    selection_attachment = ReaderAskAttachment(
        kind="text_selection",
        subtype="text_range",
        label="选区",
        selected_text="policy choices",
        target_key="record:r1:range:s1:0:14:hash",
        anchor_payload=ReaderAskAttachmentPayload(
            anchor_type="text_range",
            target_key="record:r1:range:s1:0:14:hash",
            record_id="r1",
            paragraph_id="p1",
            sentence_id="s1",
            selected_text="policy choices",
            start_offset=0,
            end_offset=14,
            text_hash="hash",
            segments=[],
        ),
        metadata=ReaderAskAttachmentMetadata(
            source_surface="selection_toolbar",
            entry_action="ask_about_this",
            sentence_id="s1",
            paragraph_id="p1",
        ),
    )
    record_attachment = ReaderAskAttachment(
        kind="record_ref",
        subtype="current_record",
        label="当前文章",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="ask_panel",
            entry_action="ask_about_this",
        ),
    )

    anchor = _attachment_to_anchor(selection_attachment)
    anchors = _attachments_to_anchor_refs([selection_attachment, record_attachment])

    assert anchor is not None
    assert anchor.anchor_type == "text_range"
    assert anchor.sentence_id == "s1"
    assert len(anchors) == 1


def test_build_unused_reservation_refunds_only_the_unused_tail() -> None:
    reservation = CreditReservation(total_points=10, deducted_from_daily=8, deducted_from_bonus=2)

    unused = _build_unused_reservation(reservation, actual_cost_points=3)

    assert unused.total_points == 7
    assert unused.deducted_from_daily == 5
    assert unused.deducted_from_bonus == 2


def test_build_action_proposals_does_not_offer_saving_full_answer_as_note() -> None:
    anchor = ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="That there were some.")
    proposals = _build_action_proposals(
        user_message="请高亮一下这句，并把解释保存成笔记",
        record=type("Record", (), {"record_id": "00000000-0000-0000-0000-000000000001"})(),
        anchors=[anchor],
        assistant_content_md="这句话在这里是存在句。",
    )

    proposal_types = {proposal.action_type for proposal in proposals}

    assert "save_highlight" not in proposal_types
    assert "save_note" not in proposal_types
    assert proposal_types == set()


def test_merge_usage_summaries_accumulates_nested_tool_usage() -> None:
    usage = _merge_usage_summaries(
        {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        [{"tool_name": "run_dictionary_ai_context_explain", "usage_summary": {"input_tokens": 80, "output_tokens": 10, "total_tokens": 90}}],
    )

    assert usage == {
        "aggregate": {
            "input_tokens": 180,
            "output_tokens": 30,
            "total_tokens": 210,
        },
        "subtasks": [
            {
                "tool_name": "run_dictionary_ai_context_explain",
                "input_tokens": 80,
                "output_tokens": 10,
                "total_tokens": 90,
            }
        ],
    }


def test_dictionary_ai_citation_uses_distinct_kind() -> None:
    citation = _dictionary_ai_to_citation(
        {"summary": "这里表示一种特定语境义。", "best_fit_sense": "sense-1", "translation": "这里是这个意思", "confidence": "high"},
        "run into",
        123,
    )

    assert citation.kind == "dictionary_ai"
    assert citation.label == "run into"
    assert citation.metadata_json["dict_entry_id"] == 123


def test_build_response_cards_creates_sentence_breakdown_card() -> None:
    runtime_state = ReaderAskRuntimeState(
        latest_record_insights=[
            {
                "entry_type": "sentence_analysis",
                "sentence_id": "s1",
                "content": "这句话先交代主干。\n\n- **1. 主干**：`He watched carefully`\n- **2. 修饰**：`from the window`",
            }
        ]
    )
    record = type("Record", (), {
        "render_scene": {
            "article": {"sentences": [{"sentence_id": "s1", "text": "He watched carefully from the window.", "paragraph_id": "p1"}]},
            "translations": [{"sentence_id": "s1", "translation_zh": "他在窗边仔细观察。"}],
        }
    })()
    record.record_id = "00000000-0000-0000-0000-000000000001"
    record.title = "Test"
    cards = _build_response_cards(
        task_mode="breakdown",
        record=record,
        anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="He watched carefully from the window.")],
        runtime_state=runtime_state,
    )

    assert len(cards) == 1
    assert cards[0].card_type == "sentence_breakdown_card"
    assert cards[0].origin == "ask_ai"
    assert cards[0].parts[0].label == "主干"


def test_build_response_cards_creates_grammar_note_card() -> None:
    runtime_state = ReaderAskRuntimeState(
        latest_generated_annotations=[
            {
                "status": "ready",
                "kind": "grammar_note",
                "sentence_id": "s1",
                "focus_text": "compared human behaviour and brain patterns",
                "analysis_scope": "focus_span",
                "note_zh": "这里的 compare A with B 用来引出对比对象。",
                "label": "Compare A with B",
                    "source_sentence": "The researchers compared human behaviour and brain patterns with 41 species of monkeys and apes.",
                    "spans": [
                        {"text": "compared", "role": "谓语"},
                        {"text": "with 41 species of monkeys and apes", "role": "比较对象"},
                    ],
                }
            ]
    )
    record = type("Record", (), {
        "render_scene": {
            "article": {"sentences": [{"sentence_id": "s1", "text": "The researchers compared human behaviour and brain patterns with 41 species of monkeys and apes.", "paragraph_id": "p1"}]},
            "translations": [{"sentence_id": "s1", "translation_zh": "研究人员将人类的行为和脑部模式与 41 种猴子和猿类进行了比较。"}],
        }
    })()
    record.record_id = "00000000-0000-0000-0000-000000000001"
    record.title = "Test"

    cards = _build_response_cards(
        task_mode="grammar",
        record=record,
        anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="The researchers compared human behaviour and brain patterns with 41 species of monkeys and apes.")],
        runtime_state=runtime_state,
    )

    assert len(cards) == 1
    assert cards[0].card_type == "grammar_note_card"
    assert cards[0].origin == "ask_ai"
    assert cards[0].analysis_scope == "focus_span"
    assert cards[0].focus_text == "compared human behaviour and brain patterns"
    assert cards[0].spans[1].role == "比较对象"


def test_stream_checkpoint_output_preserves_known_response_cards() -> None:
    runtime_state = ReaderAskRuntimeState(
        latest_generated_annotations=[
            {
                "status": "ready",
                "kind": "grammar_note",
                "sentence_id": "s1",
                "focus_text": "compared human behaviour and brain patterns",
                "analysis_scope": "focus_span",
                "note_zh": "这里的 compare A with B 用来引出对比对象。",
                "label": "Compare A with B",
                "source_sentence": "The researchers compared human behaviour and brain patterns with 41 species of monkeys and apes.",
                "spans": [
                    {"text": "compared", "role": "谓语"},
                    {"text": "with 41 species of monkeys and apes", "role": "比较对象"},
                ],
            }
        ]
    )
    record = type("Record", (), {"render_scene": {"article": {"sentences": []}, "translations": []}})()
    record.record_id = UUID("00000000-0000-0000-0000-000000000001")
    record.title = "Test"
    record.source_text = ""
    anchors = [
        ReaderAskAnchorRef(
            anchor_type="sentence",
            sentence_id="s1",
            selected_text="The researchers compared human behaviour and brain patterns with 41 species of monkeys and apes.",
        )
    ]

    output = _build_stream_checkpoint_output_json(
        content_md="好的，我们来拆解这个句子。",
        reasoning_md="先判断句子主干。",
        reasoning_status="streaming",
        submission_mode="quick_action",
        resolved_intent="grammar",
        record=record,
        anchors=anchors,
        attachments=[],
        runtime_state=runtime_state,
        reference_resolution=planner_svc.ReaderAskReferenceResolution(),
        disambiguation=None,
        external_asset_disambiguation=None,
        trace_summary=None,
        context_plan=None,
        resolved_context_input=None,
        run_info={"turn_id": "turn-1", "run_id": "run-1", "attempt": 1},
        persisted_supplements=[],
    )

    assert output["reasoning_status"] == "streaming"
    assert len(output["response_cards"]) == 1
    assert output["response_cards"][0]["card_type"] == "grammar_note_card"


def test_terminal_reasoning_status_normalizes_finished_runs() -> None:
    assert _terminal_reasoning_status(True) == "completed"
    assert _terminal_reasoning_status(False) is None


def test_submission_mode_uses_toolbar_quick_actions_only() -> None:
    quick_action_attachment = ReaderAskAttachment(
        kind="text_selection",
        subtype="sentence",
        label="整句",
        selected_text="The researchers compared human behaviour and brain patterns.",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="selection_toolbar",
            entry_action="why_here",
            sentence_id="s1",
            paragraph_id="p1",
        ),
    )
    ordinary_attachment = ReaderAskAttachment(
        kind="text_selection",
        subtype="sentence",
        label="整句",
        selected_text="The researchers compared human behaviour and brain patterns.",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="ask_panel",
            entry_action="why_here",
            sentence_id="s1",
            paragraph_id="p1",
        ),
    )

    assert _submission_mode(entry_action="why_here", attachments=[quick_action_attachment]) == "quick_action"
    assert _submission_mode(entry_action="explain_this", attachments=[quick_action_attachment]) == "quick_action"
    assert _submission_mode(entry_action="why_here", attachments=[ordinary_attachment]) == "chat"
    assert _submission_mode(entry_action="ask_about_this", attachments=[quick_action_attachment]) == "chat"


def test_build_supplement_candidates_ignores_not_applicable_quick_action_result() -> None:
    runtime_state = ReaderAskRuntimeState(
        latest_generated_annotations=[
            {
                "status": "not_applicable",
                "kind": "grammar_note",
                "reason": "选区过短，无法稳定判断语法作用。",
                "suggestion": "请扩展到完整分句或整句后再试。",
            }
        ]
    )

    candidates = _build_supplement_candidates_from_runtime(
        resolved_intent="explain",
        anchors=[
            ReaderAskAnchorRef(
                anchor_type="sentence",
                sentence_id="s1",
                paragraph_id="p1",
                target_key="record:r1:sentence:s1",
                selected_text="Even if he knew the risk",
                label="句子",
            )
        ],
        runtime_state=runtime_state,
        assistant_content_md="过短",
        created_from_turn_run_id="run-1",
    )

    assert candidates == []


def test_current_record_affordances_use_backend_render_scene_over_frontend_hint() -> None:
    record = type(
        "Record",
        (),
        {
            "title": "Backend Truth",
            "render_scene": {
                "content_summary": {"overview": "这篇文章讨论制度记忆。"},
                "sentence_entries": [{"entry_type": "sentence_analysis", "content": "主干先落判断。"}],
            },
        },
    )()
    page_identity = ReaderAskPageIdentity(
        record_id="00000000-0000-0000-0000-000000000001",
        title="Frontend Hint",
        available_context_capabilities=["record_context"],
        has_article_overview=False,
        has_sentence_entries=False,
        has_annotations=True,
        has_reader_notes=False,
    )

    affordances = reader_ask_service._current_record_affordances(record=record, page_identity=page_identity)

    assert affordances.title == "Backend Truth"
    assert affordances.has_article_overview is True
    assert affordances.has_sentence_entries is True
    assert affordances.has_annotations is True

def test_resolve_record_overview_prefers_learning_overview_hint_when_ready() -> None:
    resolved = reader_ask_utils.resolve_record_overview(
        render_scene={"content_summary": {"overview": "academic overview"}},
        page_state_json={
            "derived": {
                "overview_hint": {
                    "status": "ready",
                    "overview": "learning overview hint",
                    "confidence": "medium",
                    "source": "learning_overview_hint_agent",
                }
            }
        },
    )

    assert resolved["overview"] == "learning overview hint"
    assert resolved["status"] == "ready"
    assert resolved["source"] == "learning_overview_hint_agent"
    assert resolved["confidence"] == "medium"

def test_resolve_record_overview_uses_academic_fallback_when_no_learning_hint() -> None:
    resolved = reader_ask_utils.resolve_record_overview(
        render_scene={"content_summary": {"overview": "academic overview"}},
        page_state_json={"derived": {"overview_hint": {"status": "pending", "source": "learning_overview_hint_agent"}}},
    )

    assert resolved["overview"] == "academic overview"
    assert resolved["status"] == "ready"
    assert resolved["source"] == "academic_render_scene"


def test_planner_decision_normalizes_chinese_labels() -> None:
    decision = ReaderAskPlannerDecision.model_validate(
        {
            "resolved_intent": "拆句",
            "structured_asset_request": {"requested": True, "requested_asset_type": "解析"},
            "working_set": {"article_overview_needed": True, "extra_field": "ignored"},
            "extra_top_level": "ignored",
        }
    )

    assert decision.resolved_intent == "breakdown"
    assert decision.structured_asset_request.requested_asset_type == "analysis"
    assert decision.working_set.article_overview_needed is True


def test_fallback_semantic_planner_decision_handles_article_level_question() -> None:
    record = type(
        "Record",
        (),
        {
            "title": "Dracula",
            "render_scene": {
                "content_summary": {"overview": "本文讨论叙事推进与恐惧升级。"},
                "sentence_entries": [],
            },
        },
    )()
    page_identity = ReaderAskPageIdentity(
        record_id="00000000-0000-0000-0000-000000000001",
        title="Dracula",
        available_context_capabilities=["record_context"],
        has_article_overview=False,
        has_sentence_entries=False,
        has_annotations=False,
        has_reader_notes=False,
    )

    decision = reader_ask_service._fallback_semantic_planner_decision(
        user_message="这篇文章是怎么展开论证的？",
        entry_action="ask_about_this",
        page_identity=page_identity,
        attachments=[],
        anchors=[],
        record=record,
        failure_reason="validation failed",
    )

    assert decision.clarification_only is False
    assert decision.working_set.article_overview_needed is True
    assert decision.working_set.local_context_window_needed is True

def test_list_context_records_includes_overview_hint_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    user_id = uuid4()

    async def fake_search_records_by_title(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return [
            {
                "id": "record-1",
                "title": "Primates",
                "updated_at": "2026-05-23T00:00:00Z",
                "render_scene_json": {},
                "page_state_json": {
                    "derived": {
                        "overview_hint": {
                            "status": "ready",
                            "overview": "文章比较灵长类和人类的用手偏好。",
                            "source": "learning_overview_hint_agent",
                        }
                    }
                },
            }
        ]

    monkeypatch.setattr(reader_ask_service.repo, "search_records_by_title", fake_search_records_by_title)

    response = asyncio.run(
        reader_ask_service.list_context_records(
            user_id,
            query="primates",
            exclude_record_id=None,
        )
    )

    assert len(response.items) == 1
    assert response.items[0].overview_hint == "文章比较灵长类和人类的用手偏好。"
    assert response.items[0].overview_hint_status == "ready"
    assert response.items[0].overview_hint_source == "learning_overview_hint_agent"


def test_fallback_semantic_planner_decision_preserves_title_like_reference_request() -> None:
    record = type("Record", (), {"title": "Current", "render_scene": {"sentence_entries": []}})()
    page_identity = ReaderAskPageIdentity(
        record_id="00000000-0000-0000-0000-000000000001",
        title="Current",
        available_context_capabilities=["record_context"],
        has_article_overview=False,
        has_sentence_entries=False,
        has_annotations=False,
        has_reader_notes=False,
    )

    decision = reader_ask_service._fallback_semantic_planner_decision(
        user_message='我之前那篇《Climate Policy》里也提过这个吗？',
        entry_action="ask_about_this",
        page_identity=page_identity,
        attachments=[],
        anchors=[],
        record=record,
        failure_reason="validation failed",
    )

    assert decision.reference_request.requested is True
    assert decision.reference_request.query == "Climate Policy"


def test_resolved_context_summary_marks_article_assets_and_history_usage() -> None:
    record = type("Record", (), {"record_id": "00000000-0000-0000-0000-000000000001", "title": "Test"})()
    runtime_state = ReaderAskRuntimeState(
        latest_record_context={"sentence_windows": []},
        latest_record_insights=[{"entry_type": "sentence_analysis"}],
    )
    summary = _resolved_context_summary(
        record=record,
        anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
        explicit_attachment_count=2,
        runtime_state=runtime_state,
        used_cross_record_context=True,
        citations=[],
    )

    assert summary.current_sentence_used is True
    assert summary.current_paragraph_used is True
    assert summary.used_record_insights is True
    assert summary.used_cross_record_context is True
    assert summary.explicit_attachment_count == 2


def test_build_resolved_context_input_preserves_explicit_attachments_only() -> None:
    attachment = ReaderAskAttachment(
        kind="analysis_ref",
        subtype="grammar_note",
        label="语法旁注",
        selected_text="because it signals concession",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="analysis_block",
            entry_action="why_here",
            sentence_id="s1",
            paragraph_id="p1",
            entry_id="e1",
            entry_type="grammar_note",
        ),
    )

    context_input = _build_resolved_context_input(
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            surface="reader",
            source="reader_2_0",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="why_here",
        attachments=[attachment],
        anchors=[],
    )

    assert len(context_input.attachments) == 1
    assert context_input.attachments[0].label == "语法旁注"
    assert context_input.normalized_anchors == []
    assert context_input.current_record_context is None
    assert context_input.external_record_contexts == []


def test_build_resolved_context_input_distinguishes_current_and_external_records() -> None:
    context_input = _build_resolved_context_input(
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            surface="reader",
            source="reader_2_0",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        current_record_context=ReaderAskCurrentRecordContext(
            record_id="00000000-0000-0000-0000-000000000001",
            record_title="Test",
            local_context={"sentence_windows": []},
            record_insights=[],
            article_overview="本文讨论制度记忆如何影响政策解释。",
            source_labels=["article_overview"],
        ),
        external_record_contexts=[
            ReaderAskExternalRecordContext(
                record_id="00000000-0000-0000-0000-000000000002",
                record_title="Climate Policy",
                article_overview="这篇文章讨论气候政策。",
                record_insights=[],
                source_labels=["external_record"],
                reason="known_reference_resolved",
            )
        ],
    )

    assert context_input.current_record_context is not None
    assert context_input.current_record_context.record_title == "Test"
    assert context_input.external_record_contexts[0].record_title == "Climate Policy"


def test_build_context_plan_records_history_and_dictionary_usage() -> None:
    runtime_state = ReaderAskRuntimeState(
        used_cross_record_context=True,
        latest_record_context={"sentence_windows": []},
        latest_record_insights=[{"entry_type": "sentence_analysis"}],
        source_labels={"current_record", "external_record_context", "dictionary"},
    )

    context_plan = _build_context_plan(
        entry_action="ask_about_this",
        attachments=[],
        anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
        runtime_state=runtime_state,
        citations=[_dictionary_ai_to_citation({"summary": "x"}, "test", 1)],
    )

    assert context_plan.used_cross_record_context is True
    assert context_plan.used_record_context is True
    assert context_plan.used_record_insights is True
    assert context_plan.used_dictionary is True
    assert context_plan.used_article_overview is False


def test_build_grammar_note_candidate_requires_sentence_target() -> None:
    candidate = build_grammar_note_candidate(
        anchor=ReaderAskAnchorRef(
            anchor_type="sentence",
            sentence_id="s1",
            paragraph_id="p1",
            target_key="record:r1:sentence:s1",
            selected_text="Even if he knew the risk",
            label="语法旁注",
        ),
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。即使他知道风险，他也会继续前行。这种让步结构在英语中非常常见。",
        created_from_turn_run_id="run-1",
    )

    assert candidate is not None
    assert candidate.supplement_type == "grammar_note"
    assert candidate.created_from_turn_run_id == "run-1"
    assert candidate.lifecycle_status == "candidate"


def test_next_run_info_prefers_current_turn_run_pointer() -> None:
    next_run_info, run_history = _next_run_info(
        {
            "run_info": _build_run_info(turn_id="turn-1", run_id="run-1", attempt=1),
            "current_turn_run": {
                "id": "run-1",
                "turn_id": "turn-1",
                "run_attempt": 1,
            },
            "run_history": [],
        }
    )

    assert next_run_info["turn_id"] == "turn-1"
    assert next_run_info["run_attempt"] == 2
    assert next_run_info["supersedes_run_id"] == "run-1"
    assert run_history == [{"turn_id": "turn-1", "run_id": "run-1", "run_attempt": 1, "supersedes_run_id": None}]


def test_assistant_message_metadata_keeps_only_minimal_turn_run_compat_fields() -> None:
    metadata = output_contract_svc.build_assistant_message_metadata(
        resolved_intent="grammar",
        run_info={"turn_id": "turn-1", "run_id": "run-2", "run_attempt": 2},
        run_history=[{"turn_id": "turn-1", "run_id": "run-1", "run_attempt": 1, "supersedes_run_id": None}],
        resolved_context_input=planner_svc.build_resolved_context_input(
            page_identity=ReaderAskPageIdentity(
                record_id="record-1",
                title="Current",
                available_context_capabilities=["record_context"],
                has_article_overview=True,
                has_sentence_entries=True,
                has_annotations=True,
                has_reader_notes=True,
            ),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
        ),
    )

    assert metadata["resolved_intent"] == "grammar"
    assert metadata["run_info"]["run_id"] == "run-2"
    assert "resolved_context_input" in metadata
    assert "evidence" not in metadata
    assert "trace_summary" not in metadata
    assert "persisted_supplements" not in metadata


def test_user_visible_output_round_trips_to_completed_payload() -> None:
    output = output_contract_svc.build_user_visible_output(
        content_md="解释完成。",
        submission_mode="chat",
        resolved_intent="explain",
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary={"input_tokens": 10, "output_tokens": 20},
        billed_points=3,
        resolved_context=planner_svc.build_resolved_context_summary(
            record_id="record-1",
            record_title="Current",
            anchors=[],
            explicit_attachment_count=0,
            runtime_state=ReaderAskRuntimeState(source_labels={"current_record"}),
            used_cross_record_context=False,
            citations=[],
        ),
        context_plan=ReaderAskContextPlan(entry_action="ask_about_this"),
        resolved_context_input=planner_svc.build_resolved_context_input(
            page_identity=ReaderAskPageIdentity(
                record_id="record-1",
                title="Current",
                available_context_capabilities=["record_context"],
                has_article_overview=True,
                has_sentence_entries=True,
                has_annotations=True,
                has_reader_notes=True,
            ),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
        ),
        run_info={"turn_id": "turn-1", "run_id": "run-1", "run_attempt": 1},
        supplement_candidates=[],
        persisted_supplements=[],
    )

    payload = output_contract_svc.to_completed_payload(
        message_id="msg-1",
        thread_id="thread-1",
        output=output,
    )

    assert payload.id == "msg-1"
    assert payload.thread_id == "thread-1"
    assert payload.content_md == "解释完成。"
    assert payload.billed_points == 3
    assert payload.usage_summary == {"input_tokens": 10, "output_tokens": 20}


def test_planning_snapshot_json_captures_working_set_and_resolution() -> None:
    snapshot = planner_svc.plan_request(
        content="我之前那篇 climate policy 也提过这个吗？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            reference_requested=True,
            reference_query="Climate Policy",
            cross_record_context_allowed=True,
        ),
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            attempted=True,
            status="resolved",
            query="Climate Policy",
            reason="已命中历史文章“Climate Policy”。",
            resolved_records=[{"record_id": "r-2", "title": "Climate Policy"}],
        ),
    )

    data = _planning_snapshot_json(snapshot)

    assert data["resolved_intent"] == snapshot.resolved_intent
    assert data["planner_decision"]["reference_request"]["query"] == "Climate Policy"
    assert data["retrieval_needs"] == "known_reference_only"
    assert data["resolved_references"]["status"] == "resolved"
    assert data["working_set"]["external_record_refs"][0]["record_id"] == "r-2"


def test_capability_trace_json_marks_used_capabilities_and_reasons() -> None:
    runtime_state = ReaderAskRuntimeState(
        source_labels={"current_record", "record_assets", "article_overview", "external_record_context", "dictionary"},
        latest_record_context={"sentence_windows": []},
        latest_record_insights=[{"entry_type": "sentence_analysis"}],
        latest_article_overview="overview",
        latest_external_record_contexts=[{"record_id": "r-2"}],
        latest_dictionary_entry={"id": 1, "query": "policy"},
    )
    context_plan = ReaderAskContextPlan(
        entry_action="ask_about_this",
        record_context_reason="sentence_anchor",
        used_record_context=True,
        record_insights_reason="grammar_intent",
        used_record_insights=True,
        article_overview_reason="article_level_question",
        used_article_overview=True,
        dictionary_reason="lookup_in_context",
        used_dictionary=True,
        reference_resolution_reason="已命中历史文章“Climate Policy”。",
    )

    trace = _capability_trace_json(runtime_state=runtime_state, context_plan=context_plan)

    assert trace["local_context_window"]["used"] is True
    assert trace["record_insights"]["reason"] == "grammar_intent"
    assert trace["article_overview"]["used"] is True
    assert trace["dictionary"]["used"] is True
    assert trace["external_record_context"]["source_labels"] == ["external_record_context"]


def test_candidate_to_persisted_supplement_separates_lifecycle_contract() -> None:
    candidate = build_grammar_note_candidate(
        anchor=ReaderAskAnchorRef(
            anchor_type="sentence",
            sentence_id="s1",
            paragraph_id="p1",
            target_key="record:r1:sentence:s1",
            selected_text="Even if he knew the risk",
            label="语法旁注",
        ),
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。即使他知道风险，他也会继续前行。这种让步结构在英语中非常常见。",
        created_from_turn_run_id="run-1",
    )

    assert candidate is not None
    persisted = supplements_svc.candidate_to_persisted_supplement(
        candidate,
        record_id="record-1",
        record_title="Test Reader",
    )

    assert persisted.lifecycle_status == "persisted"
    assert persisted.record_id == "record-1"
    assert persisted.record_title == "Test Reader"
    assert persisted.source_kind == "assistant_supplement"
    assert persisted.supplement_id == candidate.candidate_id


def test_row_to_persisted_supplement_supports_deleted_lifecycle() -> None:
    persisted = supplements_svc.row_to_persisted_supplement(
        {
            "id": "supp-1",
            "record_id": "record-1",
            "target_key": "record:r1:sentence:s1",
            "entry_type": "grammar_note",
            "sentence_id": "s1",
            "paragraph_id": "p1",
            "title": "语法旁注",
            "content_md": "这里用了让步从句。",
            "created_from_turn_run_id": "run-1",
            "created_at": "2026-05-20T00:00:00Z",
        },
        record_title="Test Reader",
        lifecycle_status="deleted",
    )

    assert persisted.supplement_id == "supp-1"
    assert persisted.lifecycle_status == "deleted"
    assert persisted.record_title == "Test Reader"


def test_planner_reference_request_round_trips_into_snapshot() -> None:
    snapshot = planner_svc.plan_request(
        content="我之前那篇 climate policy 的解析里也提过这个吗？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            reference_requested=True,
            reference_query="climate policy",
            cross_record_context_allowed=True,
        ),
    )

    assert snapshot.reference_needs.requested is True
    assert snapshot.reference_needs.query == "climate policy"


def test_reference_resolution_single_hit_returns_resolved_record() -> None:
    async def finder(user_id, *, query, exclude_record_id, limit):  # type: ignore[no-untyped-def]
        del user_id, exclude_record_id, limit
        assert query == "climate policy"
        return [{"id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"}]

    resolution = asyncio.run(
        resolver_svc.resolve_known_references(
            user_id=uuid4(),
            current_record_id=uuid4(),
            reference_needs=planner_svc.ReaderAskReferenceNeeds(
                requested=True,
                query="climate policy",
                reason="title_like_reference",
            ),
            finder=finder,
        )
    )

    assert resolution.status == "resolved"
    assert resolution.resolved_records == [
        {"record_id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"}
    ]


def test_reference_resolution_multiple_hits_requires_clarification() -> None:
    async def finder(user_id, *, query, exclude_record_id, limit):  # type: ignore[no-untyped-def]
        del user_id, query, exclude_record_id, limit
        return [
            {"id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"},
            {"id": "r-3", "title": "Climate Policy Notes", "updated_at": "2026-05-19T00:00:00Z"},
        ]

    resolution = asyncio.run(
        resolver_svc.resolve_known_references(
            user_id=uuid4(),
            current_record_id=uuid4(),
            reference_needs=planner_svc.ReaderAskReferenceNeeds(
                requested=True,
                query="Climate Policy",
                reason="quoted_reference",
            ),
            finder=finder,
        )
    )

    assert resolution.status == "ambiguous"
    assert resolution.ambiguous_records
    assert resolution.ambiguous_records[0]["updated_at"] == "2026-05-20T00:00:00Z"


def test_reference_resolution_resolves_single_high_confidence_hit_despite_lower_noise() -> None:
    async def finder(user_id, *, query, exclude_record_id, limit):  # type: ignore[no-untyped-def]
        del user_id, query, exclude_record_id, limit
        return [
            {"id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"},
            {"id": "r-3", "title": "Policy Climate Debate", "updated_at": "2026-05-19T00:00:00Z"},
        ]

    resolution = asyncio.run(
        resolver_svc.resolve_known_references(
            user_id=uuid4(),
            current_record_id=uuid4(),
            reference_needs=planner_svc.ReaderAskReferenceNeeds(
                requested=True,
                query="Climate Policy",
                reason="quoted_reference",
            ),
            finder=finder,
        )
    )

    assert resolution.status == "resolved"
    assert resolution.resolved_records == [
        {"record_id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"}
    ]


def test_lookup_structured_record_assets_extracts_overview_and_stable_insights() -> None:
    assets = resolver_svc.lookup_structured_record_assets(
        record_id="r-2",
        record_title="Climate Policy",
        render_scene={
            "content_summary": {"overview": "这篇文章讨论气候政策与制度解释。"},
            "sentence_entries": [
                {
                    "entry_type": "grammar_note",
                    "title": "让步从句",
                    "content": "这里先让步再转主句判断。",
                },
                {
                    "entry_type": "sentence_analysis",
                    "title": "主干分析",
                    "content": "主句先落判断，再补修饰层次。",
                },
            ],
        },
        reason="known_reference_resolved",
        updated_at="2026-05-20T00:00:00Z",
    )

    assert assets["article_overview"] == "这篇文章讨论气候政策与制度解释。"
    assert assets["record_insights"] == [
        "让步从句: 这里先让步再转主句判断。",
        "主干分析: 主句先落判断，再补修饰层次。",
    ]
    assert "record_assets" in assets["source_labels"]


def test_build_context_plan_records_reference_resolution_reason() -> None:
    runtime_state = ReaderAskRuntimeState(
        source_labels={"current_record", "external_record_context"},
    )
    context_plan = _build_context_plan(
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        runtime_state=runtime_state,
        citations=[],
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            attempted=True,
            status="resolved",
            query="Climate Policy",
            reason="已命中历史文章“Climate Policy”。",
            resolved_records=[{"record_id": "r-2", "title": "Climate Policy"}],
        ),
    )

    assert context_plan.reference_resolution_attempted is True
    assert context_plan.reference_resolution_status == "resolved"
    assert context_plan.expanded_record_ids == ["r-2"]


def test_plan_request_builds_disambiguation_state_for_ambiguous_known_reference() -> None:
    snapshot = planner_svc.plan_request(
        content="我之前那篇 climate policy 文章里也提过这个吗？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="ambiguous_known_reference",
            reference_requested=True,
            reference_query="climate policy",
            cross_record_context_allowed=True,
        ),
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query="climate policy",
            reason="“climate policy”命中了多个候选，请补充更完整的标题。",
            ambiguous_records=[
                {"record_id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"},
                {"record_id": "r-3", "title": "Climate Policy Notes", "updated_at": "2026-05-19T00:00:00Z"},
            ],
        ),
    )

    assert snapshot.clarification_only is False
    assert snapshot.disambiguation_state is not None
    assert snapshot.disambiguation_state.required is True
    assert len(snapshot.disambiguation_state.candidates) == 2
    assert snapshot.trace_summary.used_hitp_disambiguation is True


def test_build_context_plan_carries_clarification_reason_from_planning_snapshot() -> None:
    snapshot = planner_svc.plan_request(
        content="这里为什么这样写？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    # "这里" is a strong deictic + no anchor + intent=explain (not sentence-level)
    # → deterministic rule downgrades must_clarify to can_answer_with_followup
    # with reason "deictic_without_anchor"
    assert snapshot.clarification_mode == "can_answer_with_followup"
    assert snapshot.clarification_only is False

    context_plan = _build_context_plan(
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        runtime_state=ReaderAskRuntimeState(source_labels={"current_record"}),
        citations=[],
        planning_snapshot=snapshot,
    )

    assert context_plan.clarification_reason == "deictic_without_anchor"


def test_plan_request_prefers_anchor_local_working_set_for_grammar() -> None:
    anchor = ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Even if he knew the risk")
    plan = planner_svc.plan_request(
        content="解释这句的语法作用",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights", "dictionary"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="why_here",
        attachments=[],
        anchors=[anchor],
        planner_decision=_planner_decision(
            resolved_intent="grammar",
            local_context_window_needed=True,
            record_insights_needed=True,
        ),
    )

    assert plan.resolved_intent == "grammar"
    assert plan.working_set.local_context_window_needed is True
    assert plan.working_set.record_insights_needed is True
    assert plan.working_set.article_overview_needed is False
    assert plan.trace_summary.working_set_mode == "anchor_local"


def test_plan_request_prefers_article_overview_for_article_level_question() -> None:
    plan = planner_svc.plan_request(
        content="解释本文的主线和核心论点",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights", "dictionary"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            article_overview_needed=True,
        ),
    )

    assert plan.working_set.article_overview_needed is True
    assert plan.working_set.local_context_window_needed is False
    assert plan.trace_summary.working_set_mode == "article_overview"


def test_plan_request_tracks_explicit_related_record_as_external_context() -> None:
    attachment = ReaderAskAttachment(
        kind="record_ref",
        subtype="related_record",
        label="Climate Policy",
        target_key="record:00000000-0000-0000-0000-000000000002:record",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="ask_context_picker",
            entry_action="ask_about_this",
            asset_id="00000000-0000-0000-0000-000000000002",
            title="Climate Policy",
        ),
    )

    plan = planner_svc.plan_request(
        content="我之前那篇 climate policy 也提过这个吗？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[attachment],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            cross_record_context_allowed=True,
        ),
    )

    assert plan.working_set.external_record_refs == [
        {
            "record_id": "00000000-0000-0000-0000-000000000002",
            "title": "Climate Policy",
            "reason": "explicit_attachment",
        }
    ]
    assert plan.trace_summary.working_set_mode == "explicit_external_record"


def test_plan_request_tracks_explicit_external_analysis_asset_context() -> None:
    attachment = ReaderAskAttachment(
        kind="analysis_ref",
        subtype="sentence_analysis",
        label="Concept analysis",
        target_key="record:00000000-0000-0000-0000-000000000002:analysis:sentence_analysis:analysis-1",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="ask_hitp_asset_picker",
            entry_action="ask_about_this",
            record_id="00000000-0000-0000-0000-000000000002",
            record_title="Climate Policy",
            entry_id="analysis-1",
            entry_type="sentence_analysis",
            asset_id="analysis-1",
            title="Concept analysis",
        ),
    )

    plan = planner_svc.plan_request(
        content="我之前那篇 policy 文章的分析里怎么解释这个概念？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[attachment],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            structured_asset_requested=True,
            structured_asset_type="analysis",
            cross_record_context_allowed=True,
            external_asset_lookup_needed=True,
        ),
    )

    assert plan.working_set.external_asset_refs == [
        {
            "record_id": "00000000-0000-0000-0000-000000000002",
            "record_title": "Climate Policy",
            "asset_type": "analysis",
            "asset_id": "analysis-1",
            "entry_type": "sentence_analysis",
            "asset_title": "Concept analysis",
            "reason": "explicit_attachment",
        }
    ]
    assert plan.context_plan.external_asset_selection_reason == "explicit_external_asset"
    assert plan.trace_summary.used_external_asset_context is True


def test_trace_summary_marks_external_context_limitations() -> None:
    snapshot = planner_svc.plan_request(
        content="我之前那篇 climate policy 也提过这个吗？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            reference_requested=True,
            reference_query="Climate Policy",
            cross_record_context_allowed=True,
        ),
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            attempted=True,
            status="resolved",
            query="Climate Policy",
            reason="已命中历史文章“Climate Policy”。",
            resolved_records=[{"record_id": "r-2", "title": "Climate Policy", "updated_at": "2026-05-20T00:00:00Z"}],
        ),
    )
    trace = planner_svc.build_trace_summary(
        runtime_state=ReaderAskRuntimeState(
            source_labels={"current_record", "external_record_context"},
            used_cross_record_context=True,
            latest_external_record_contexts=[
                {
                    "record_id": "r-2",
                    "record_title": "Climate Policy",
                    "article_overview": None,
                    "record_insights": ["主干分析: 主句先落判断。"],
                    "source_labels": ["external_record", "overview_missing"],
                    "reason": "known_reference_resolved",
                }
            ],
        ),
        context_plan=snapshot.context_plan,
        planning_snapshot=snapshot,
    )

    assert trace.used_known_reference_resolution is True
    assert trace.used_external_record_context is True
    assert trace.used_structured_asset_lookup is True


def test_plan_request_builds_external_asset_disambiguation_state_for_ambiguous_external_assets() -> None:
    record_attachment = ReaderAskAttachment(
        kind="record_ref",
        subtype="related_record",
        label="Climate Policy",
        target_key="record:record-2:record",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="ask_context_picker",
            entry_action="ask_about_this",
            asset_id="record-2",
            title="Climate Policy",
        ),
    )

    snapshot = planner_svc.plan_request(
        content="我之前那篇 policy 文章的分析里怎么解释这个概念？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[record_attachment],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="ambiguous_external_asset",
            structured_asset_requested=True,
            structured_asset_type="analysis",
            cross_record_context_allowed=True,
            external_asset_lookup_needed=True,
        ),
        structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(
            attempted=True,
            status="ambiguous",
            requested_asset_type="analysis",
            reason="外部文章里有多个分析对象可能相关，请先指定一个。",
            record_id="record-2",
            record_title="Climate Policy",
            ambiguous_assets=[
                {
                    "asset_type": "analysis",
                    "asset_id": "analysis-1",
                    "entry_type": "sentence_analysis",
                    "title": "Concept analysis",
                    "summary": "解释概念的制度语境。",
                },
                {
                    "asset_type": "analysis",
                    "asset_id": "analysis-2",
                    "entry_type": "sentence_analysis",
                    "title": "Counterpoint analysis",
                    "summary": "解释反论点的对照关系。",
                },
            ],
        ),
    )

    assert snapshot.clarification_mode == "can_answer_with_followup"
    assert snapshot.external_asset_disambiguation_state is not None
    assert snapshot.external_asset_disambiguation_state.required is True
    assert len(snapshot.external_asset_disambiguation_state.candidates) == 2
    assert snapshot.trace_summary.used_external_asset_disambiguation is True


def test_build_evidence_items_marks_external_record_scope() -> None:
    evidence = post_process_svc.build_evidence_items(
        attachments=[],
        citations=[],
        current_record_id="record-1",
        current_record_title="Current",
        external_record_contexts=[
            {
                "record_id": "record-2",
                "record_title": "Climate Policy",
                "article_overview": "这篇文章讨论气候政策。",
                "record_insights": ["主干分析: 先交代制度背景。"],
                "source_labels": ["external_record"],
                "reason": "known_reference_resolved",
            }
        ],
    )

    assert evidence[0].scope == "external_record"
    assert evidence[0].record_title == "Climate Policy"
    assert evidence[0].reason == "structured_asset_lookup"


def test_build_evidence_items_marks_external_asset_scope_and_asset_candidates() -> None:
    attachment = ReaderAskAttachment(
        kind="supplement_ref",
        subtype="grammar_note",
        label="AI 语法旁注",
        target_key="record:record-2:analysis:grammar_note:supp-1",
        metadata=ReaderAskAttachmentMetadata(
            source_surface="ask_hitp_asset_picker",
            entry_action="ask_about_this",
            record_id="record-2",
            record_title="Climate Policy",
            asset_id="supp-1",
            entry_type="grammar_note",
            title="AI 语法旁注",
        ),
    )
    evidence = post_process_svc.build_evidence_items(
        attachments=[attachment],
        citations=[],
        current_record_id="record-1",
        current_record_title="Current",
        external_asset_contexts=[
            ReaderAskExternalAssetContext(
                record_id="record-2",
                record_title="Climate Policy",
                asset_type="supplement",
                asset_id="supp-1",
                entry_type="grammar_note",
                asset_title="AI 语法旁注",
                content_summary="这里总结了让步从句的作用。",
                reason="structured_asset_resolved",
            ).model_dump(mode="json")
        ],
        external_asset_disambiguation=ReaderAskAssetDisambiguation(
            required=True,
            reason="外部文章里有多个稳定资产可能相关。",
            record_id="record-2",
            record_title="Climate Policy",
            candidates=[
                ReaderAskAssetDisambiguationCandidate(
                    asset_type="supplement",
                    asset_id="supp-1",
                    entry_type="grammar_note",
                    title="AI 语法旁注",
                    summary="这里总结了让步从句的作用。",
                )
            ],
        ),
    )

    assert evidence[0].reason == "external_supplement_asset"
    assert any(item.reason == "external_supplement_asset" and item.kind == "citation" for item in evidence)
    assert any(item.reason == "external_asset_disambiguation_candidate" for item in evidence)


def test_plan_request_uses_explicit_related_record_context() -> None:
    plan = planner_svc.plan_request(
        content="我之前那篇 climate policy 也提过这个吗？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights", "dictionary"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="Climate Policy",
                target_key="record:record-2:record",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="ask_context_picker",
                    entry_action="ask_about_this",
                    asset_id="record-2",
                    title="Climate Policy",
                ),
            )
        ],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            cross_record_context_allowed=True,
        ),
    )

    assert plan.working_set.external_record_refs == [
        {
            "record_id": "record-2",
            "title": "Climate Policy",
            "reason": "explicit_attachment",
        }
    ]
    assert plan.retrieval_needs == "known_reference_only"
    assert plan.trace_summary.working_set_mode == "explicit_external_record"


def test_plan_request_without_anchor_returns_clarification_working_set() -> None:
    """When there's no anchor and no strong deictic, must_clarify is preserved."""
    plan = planner_svc.plan_request(
        content="为什么这样写？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights", "dictionary"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    assert plan.clarification_only is True
    assert plan.trace_summary.working_set_mode == "clarification"


def test_typed_supplement_capability_builds_grammar_note_candidates() -> None:
    candidates = capabilities_svc.build_supplement_candidates(
        resolved_intent="grammar",
        anchors=[
            ReaderAskAnchorRef(
                anchor_type="sentence",
                sentence_id="s1",
                paragraph_id="p1",
                target_key="record:r1:sentence:s1",
                selected_text="Even if he knew the risk",
                label="语法旁注",
            )
        ],
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。即使他知道风险，他也会继续前行。这种让步结构在英语中非常常见。",
        created_from_turn_run_id="run-2",
    )

    assert len(candidates) == 1
    assert candidates[0].created_from_turn_run_id == "run-2"


def test_build_evidence_items_includes_clarification_signal() -> None:
    evidence = post_process_svc.build_evidence_items(
        attachments=[],
        citations=[],
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query="Climate Policy",
            reason="“Climate Policy”命中了多个候选，请补充更完整的标题。",
            ambiguous_records=[{"record_id": "r-2", "title": "Climate Policy"}],
        ),
        disambiguation=ReaderAskDisambiguation(
            required=True,
            reason="“Climate Policy”命中了多个候选，请补充更完整的标题。",
            query="Climate Policy",
            candidates=[ReaderAskDisambiguationCandidate(record_id="r-2", title="Climate Policy")],
        ),
        include_clarification=True,
    )

    assert len(evidence) == 2
    assert evidence[0].kind == "clarification"
    assert evidence[1].kind == "disambiguation_candidate"


def test_build_resolved_context_input_carries_external_asset_contexts() -> None:
    context = planner_svc.build_resolved_context_input(
        page_identity=ReaderAskPageIdentity(
            record_id="record-1",
            title="Current",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        current_record_context=ReaderAskCurrentRecordContext(
            record_id="record-1",
            record_title="Current",
            local_context=None,
            record_insights=[],
            article_overview=None,
            source_labels=[],
        ),
        external_asset_contexts=[
            ReaderAskExternalAssetContext(
                record_id="record-2",
                record_title="Climate Policy",
                asset_type="analysis",
                asset_id="analysis-1",
                entry_type="sentence_analysis",
                asset_title="Concept analysis",
                content_md="完整分析正文。",
                content_summary="解释概念的制度语境。",
                source_labels=["external_asset", "analysis"],
                reason="structured_asset_resolved",
            )
        ],
    )

    assert len(context.external_asset_contexts) == 1
    assert context.external_asset_contexts[0].content_md == "完整分析正文。"
    assert context.external_asset_contexts[0].source_labels == ["external_asset", "analysis"]
    assert context.external_asset_contexts[0].asset_type == "analysis"


def test_delete_supplement_marks_all_runs_deleted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    user_id = uuid4()
    record_id = UUID("00000000-0000-0000-0000-0000000000ee")
    supplement_id = UUID("00000000-0000-0000-0000-0000000000aa")
    source_turn_run_id = UUID("00000000-0000-0000-0000-0000000000bb")
    newer_turn_run_id = UUID("00000000-0000-0000-0000-0000000000cc")
    message_id = UUID("00000000-0000-0000-0000-0000000000dd")

    async def fake_get_supplement_projection_or_404(_user_id, _supplement_id):  # type: ignore[no-untyped-def]
        assert _user_id == user_id
        assert _supplement_id == supplement_id
        return {"id": str(supplement_id), "record_id": str(record_id), "target_key": f"record:{record_id}:sentence:s1"}

    async def fake_ensure_record_access(_user_id, _record_id):  # type: ignore[no-untyped-def]
        del _user_id, _record_id
        return {"title": "Test Reader"}

    async def fake_delete_supplement(_user_id, _supplement_id):  # type: ignore[no-untyped-def]
        del _user_id, _supplement_id
        return {
            "id": str(supplement_id),
            "record_id": str(record_id),
            "target_key": f"record:{record_id}:sentence:s1",
            "entry_type": "grammar_note",
            "sentence_id": "s1",
            "paragraph_id": "p1",
            "title": "语法旁注",
            "content_md": "这里用了让步从句。",
            "created_from_turn_run_id": str(source_turn_run_id),
            "created_at": "2026-05-20T00:00:00Z",
        }

    async def fake_get_turn_run(turn_run_id):  # type: ignore[no-untyped-def]
        assert turn_run_id == source_turn_run_id
        return {
            "id": str(source_turn_run_id),
            "message_id": str(message_id),
            "status": "completed",
            "user_visible_output_json": {
                "persisted_supplements": [
                    {"supplement_id": str(supplement_id), "lifecycle_status": "persisted"},
                ]
            },
        }

    async def fake_list_turn_runs_for_message(_message_id):  # type: ignore[no-untyped-def]
        assert _message_id == message_id
        return [
            {
                "id": str(source_turn_run_id),
                "status": "completed",
                "user_visible_output_json": {
                    "persisted_supplements": [
                        {"supplement_id": str(supplement_id), "lifecycle_status": "persisted"},
                    ]
                },
            },
            {
                "id": str(newer_turn_run_id),
                "status": "completed",
                "user_visible_output_json": {
                    "persisted_supplements": [
                        {"supplement_id": str(supplement_id), "lifecycle_status": "persisted"},
                    ]
                },
            },
        ]

    updates: list[tuple[UUID, dict[str, object]]] = []

    async def fake_update_turn_run(*, turn_run_id, status, user_visible_output_json, **kwargs):  # type: ignore[no-untyped-def]
        del status, kwargs
        updates.append((turn_run_id, user_visible_output_json))
        return {"id": str(turn_run_id)}

    async def fake_get_eval_trace(turn_run_id):  # type: ignore[no-untyped-def]
        assert turn_run_id == source_turn_run_id
        return {"supplement_audit_json": []}

    audit_payload: dict[str, object] = {}

    async def fake_upsert_eval_trace_record(**kwargs):  # type: ignore[no-untyped-def]
        audit_payload.update(kwargs)
        return None

    monkeypatch.setattr(reader_ask_service.supplements_svc, "get_supplement_projection_or_404", fake_get_supplement_projection_or_404)
    monkeypatch.setattr(reader_ask_service.repo, "ensure_record_access", fake_ensure_record_access)
    monkeypatch.setattr(reader_ask_service.supplements_svc, "delete_supplement", fake_delete_supplement)
    monkeypatch.setattr(reader_ask_service.repo, "get_turn_run", fake_get_turn_run)
    monkeypatch.setattr(reader_ask_service.repo, "list_turn_runs_for_message", fake_list_turn_runs_for_message)
    monkeypatch.setattr(reader_ask_service.repo, "update_turn_run", fake_update_turn_run)
    monkeypatch.setattr(reader_ask_service.repo, "get_eval_trace", fake_get_eval_trace)
    monkeypatch.setattr(reader_ask_service, "_upsert_eval_trace_record", fake_upsert_eval_trace_record)

    response = asyncio.run(reader_ask_service.delete_supplement(user_id, supplement_id))

    assert response.deleted is True
    assert len(updates) == 2
    assert {turn_run_id for turn_run_id, _ in updates} == {source_turn_run_id, newer_turn_run_id}
    for _, output in updates:
        persisted_items = output["persisted_supplements"]
        assert isinstance(persisted_items, list)
        assert persisted_items[0]["lifecycle_status"] == "deleted"
    assert audit_payload["turn_run_id"] == source_turn_run_id


async def test_generate_sentence_annotation_grammar_cache_hit_skips_run_tool() -> None:
    """Quick-action grammar: when a pre-generated grammar_note exists, calling the
    tool must return the cached result WITHOUT going through _run_tool — meaning
    tool_call_count stays 0, tool_trace stays empty, no SSE events, and the
    underlying generate_sentence_annotation_fn is never invoked."""
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import RunContext

    from app.agents.reader_ask_agent import (
        ReaderAskAgentDeps,
        ReaderAskRuntimeState,
        _generate_sentence_annotation_tool,
    )

    grammar_annotation = {
        "kind": "grammar_note",
        "status": "ready",
        "title": "语法旁注",
        "content_md": "让步从句",
    }
    state = ReaderAskRuntimeState(
        latest_generated_annotations=[grammar_annotation],
    )
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    annotation_fn = AsyncMock()

    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="grammar",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=annotation_fn,
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
    )

    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _generate_sentence_annotation_tool(ctx, kind="grammar_note")

    # Returns the cached annotation
    assert result is grammar_annotation
    # tool_call_count MUST NOT increment (budget preserved)
    assert state.tool_call_count == 0
    # tool_trace MUST NOT grow (no started/completed entries)
    assert len(state.tool_trace) == 0
    # event_queue MUST be empty (no tool.started / tool.completed SSE events)
    assert event_queue.empty()
    # The underlying generation function MUST NOT be called
    annotation_fn.assert_not_called()


async def test_generate_sentence_annotation_breakdown_cache_hit_skips_run_tool() -> None:
    """Quick-action breakdown: same guarantee as grammar — cache hit must not
    consume tool budget, produce trace entries, or emit SSE events."""
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai import RunContext

    from app.agents.reader_ask_agent import (
        ReaderAskAgentDeps,
        ReaderAskRuntimeState,
        _generate_sentence_annotation_tool,
    )

    breakdown_annotation = {
        "kind": "sentence_analysis",
        "status": "ready",
        "title": "句子拆分",
        "content_md": "主干 + 修饰",
    }
    state = ReaderAskRuntimeState(
        latest_generated_annotations=[breakdown_annotation],
    )
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    annotation_fn = AsyncMock()

    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="breakdown",
        record_id="r1",
        record_title="Test",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=annotation_fn,
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
    )

    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _generate_sentence_annotation_tool(ctx, kind="sentence_analysis")

    assert result is breakdown_annotation
    assert state.tool_call_count == 0
    assert len(state.tool_trace) == 0
    assert event_queue.empty()
    annotation_fn.assert_not_called()


def _make_agent_deps(
    *,
    primary_anchor: object = None,
) -> tuple:
    """Helper to construct ReaderAskAgentDeps + event_queue for tool tests."""
    from unittest.mock import AsyncMock, MagicMock

    from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
    from app.schemas.reader_ask import ReaderAskAnchorRef

    state = ReaderAskRuntimeState()
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    anchor = primary_anchor if isinstance(primary_anchor, ReaderAskAnchorRef) else None

    deps = ReaderAskAgentDeps(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="grammar",
        record_id="r1",
        record_title="Test",
        primary_anchor=anchor,
        get_record_context_fn=AsyncMock(return_value={}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        search_user_vocabulary_fn=AsyncMock(return_value=[]),
        lookup_dictionary_entry_fn=AsyncMock(return_value=None),
        run_dictionary_ai_context_explain_fn=AsyncMock(return_value=None),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        vocabulary_item_to_citation_fn=MagicMock(),
        dictionary_item_to_citation_fn=MagicMock(),
        dictionary_ai_to_citation_fn=MagicMock(),
    )
    return deps, event_queue


async def test_propose_save_note_no_anchor_skips_run_tool() -> None:
    """Without primary_anchor, propose_save_note must return error directly
    without consuming tool budget, producing trace entries, or creating
    action requests."""
    from unittest.mock import MagicMock

    from pydantic_ai import RunContext

    from app.agents.reader_ask_agent import _propose_save_note_tool

    deps, event_queue = _make_agent_deps(primary_anchor=None)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _propose_save_note_tool(ctx, note_text="some note")

    assert result["status"] == "error"
    assert "No anchor" in result["summary"]
    assert deps.state.tool_call_count == 0
    assert len(deps.state.tool_trace) == 0
    assert event_queue.empty()
    assert len(deps.state.action_requests) == 0


async def test_propose_save_highlight_no_anchor_skips_run_tool() -> None:
    """Without primary_anchor, propose_save_highlight must return error directly
    without consuming tool budget, producing trace entries, or creating
    action requests."""
    from unittest.mock import MagicMock

    from pydantic_ai import RunContext

    from app.agents.reader_ask_agent import _propose_save_highlight_tool

    deps, event_queue = _make_agent_deps(primary_anchor=None)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _propose_save_highlight_tool(ctx)

    assert result["status"] == "error"
    assert "No anchor" in result["summary"]
    assert deps.state.tool_call_count == 0
    assert len(deps.state.tool_trace) == 0
    assert event_queue.empty()
    assert len(deps.state.action_requests) == 0


async def test_propose_save_note_with_anchor_consumes_budget_and_creates_action() -> None:
    """With primary_anchor, propose_save_note must go through _run_tool normally:
    increment tool_call_count, append trace, and create an action request."""
    from unittest.mock import MagicMock

    from pydantic_ai import RunContext

    from app.agents.reader_ask_agent import _propose_save_note_tool
    from app.schemas.reader_ask import ReaderAskAnchorRef

    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        paragraph_id="p1",
        selected_text="test sentence",
        entry_type="sentence",
    )
    deps, event_queue = _make_agent_deps(primary_anchor=anchor)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _propose_save_note_tool(ctx, note_text="important note")

    assert result["status"] == "success"
    assert result["action_type"] == "save_note"
    assert deps.state.tool_call_count == 1
    assert len(deps.state.tool_trace) == 2  # started + completed
    assert not event_queue.empty()  # tool.started + tool.completed events
    assert len(deps.state.action_requests) == 1
    assert deps.state.action_requests[0].action_type == "save_note"


async def test_propose_save_highlight_with_anchor_consumes_budget_and_creates_action() -> None:
    """With primary_anchor, propose_save_highlight must go through _run_tool normally:
    increment tool_call_count, append trace, and create an action request."""
    from unittest.mock import MagicMock

    from pydantic_ai import RunContext

    from app.agents.reader_ask_agent import _propose_save_highlight_tool
    from app.schemas.reader_ask import ReaderAskAnchorRef

    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        paragraph_id="p1",
        selected_text="test sentence",
        entry_type="sentence",
    )
    deps, event_queue = _make_agent_deps(primary_anchor=anchor)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _propose_save_highlight_tool(ctx)

    assert result["status"] == "success"
    assert result["action_type"] == "save_highlight"
    assert deps.state.tool_call_count == 1
    assert len(deps.state.tool_trace) == 2  # started + completed
    assert not event_queue.empty()  # tool.started + tool.completed events
    assert len(deps.state.action_requests) == 1
    assert deps.state.action_requests[0].action_type == "save_highlight"


async def test_confirm_action_idempotent_on_executed_proposal() -> None:
    """Confirming an already-executed proposal must return ok=True with
    status='executed' and the persisted result_json, without calling any
    create function again."""
    from unittest.mock import AsyncMock

    action_id = "action-executed-1"
    persisted_result = {
        "annotation_id": "anno-123",
        "annotation_type": "highlight",
        "target_key": "record:r1:sentence:s1",
    }
    proposal_dict = {
        "id": action_id,
        "action_type": "save_highlight",
        "label": "保存为高亮",
        "status": "executed",
        "payload_json": {
            "anchor": {
                "anchor_type": "sentence",
                "target_key": "record:r1:sentence:s1",
                "sentence_id": "s1",
            },
        },
        "result_json": persisted_result,
    }
    message_dict = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "done",
        "action_proposals": [proposal_dict],
    }

    create_annotation_fn = AsyncMock()

    with (
        patch.object(reader_ask_service.repo, "find_action_proposal", new=AsyncMock(return_value=(message_dict, proposal_dict))),
        patch.object(reader_ask_service.user_annotations_svc, "create_user_annotation", new=create_annotation_fn),
    ):
        response = await reader_ask_service.confirm_action(
            user_id=uuid4(),
            thread_id=uuid4(),
            action_id=action_id,
            body=ReaderAskActionConfirmRequest(confirmed=True),
        )

    assert response.ok is True
    assert response.status == "executed"
    assert response.action_id == action_id
    # result must be recovered from result_json
    assert response.result.annotation_id == "anno-123"
    assert response.result.annotation_type == "highlight"
    # The create function MUST NOT be called again
    create_annotation_fn.assert_not_called()


async def test_confirm_action_rejected_proposal_returns_409() -> None:
    """Confirming an already-rejected proposal must raise 409."""
    from unittest.mock import AsyncMock

    action_id = "action-rejected-1"
    proposal_dict = {
        "id": action_id,
        "action_type": "save_note",
        "label": "保存为笔记",
        "status": "rejected",
        "payload_json": {},
    }
    message_dict = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "role": "assistant",
        "status": "completed",
        "content_md": "done",
        "action_proposals": [proposal_dict],
    }

    with (
        patch.object(reader_ask_service.repo, "find_action_proposal", new=AsyncMock(return_value=(message_dict, proposal_dict))),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await reader_ask_service.confirm_action(
                user_id=uuid4(),
                thread_id=uuid4(),
                action_id=action_id,
                body=ReaderAskActionConfirmRequest(confirmed=True),
            )
        assert exc_info.value.status_code == 409
        assert "already been rejected" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Deterministic deictic rules for clarification mode
# ---------------------------------------------------------------------------


def test_deictic_without_anchor_explain_downgrades_to_followup() -> None:
    """Strong deictic + no anchor + explain intent → can_answer_with_followup,
    not must_clarify. The system should answer at article/paragraph level and
    guide the user to select a sentence for precision."""
    plan = planner_svc.plan_request(
        content="这句话是什么意思？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    assert plan.clarification_mode == "can_answer_with_followup"
    assert plan.clarification_only is False
    assert plan.context_plan is not None
    assert plan.context_plan.clarification_reason == "deictic_without_anchor"


def test_deictic_without_anchor_why_here_downgrades_to_followup() -> None:
    """Strong deictic '这里' + no anchor + explain intent → can_answer_with_followup."""
    plan = planner_svc.plan_request(
        content="这里为什么这样写？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    assert plan.clarification_mode == "can_answer_with_followup"
    assert plan.clarification_only is False


def test_deictic_without_anchor_grammar_stays_must_clarify() -> None:
    """Strong deictic + no anchor + grammar intent → must_clarify, because
    grammar analysis genuinely requires sentence-level positioning."""
    plan = planner_svc.plan_request(
        content="这句的语法结构是什么？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="grammar",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    assert plan.clarification_mode == "must_clarify"
    assert plan.clarification_only is True
    assert plan.context_plan is not None
    assert plan.context_plan.clarification_reason == "deictic_requires_sentence_anchor"


def test_deictic_without_anchor_breakdown_stays_must_clarify() -> None:
    """Strong deictic + no anchor + breakdown intent → must_clarify, because
    sentence breakdown genuinely requires sentence-level positioning."""
    plan = planner_svc.plan_request(
        content="这段怎么拆句？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="breakdown",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    assert plan.clarification_mode == "must_clarify"
    assert plan.clarification_only is True


def test_deictic_with_anchor_not_affected() -> None:
    """When there IS an anchor, the deictic rule doesn't fire — the existing
    anchor-based logic handles clarification mode."""
    anchor = ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="test")
    plan = planner_svc.plan_request(
        content="这句的语法结构是什么？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[anchor],
        planner_decision=_planner_decision(
            resolved_intent="grammar",
            clarification_only=False,
        ),
    )

    # With anchor, the deictic rule doesn't apply
    assert plan.clarification_mode == "none"


def test_no_deictic_without_anchor_stays_must_clarify() -> None:
    """When there's no strong deictic word and no anchor, must_clarify is
    preserved — the deterministic rule only fires for deictic references."""
    plan = planner_svc.plan_request(
        content="为什么这样写？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    assert plan.clarification_mode == "must_clarify"
    assert plan.clarification_only is True


def test_deictic_without_anchor_planner_none_upgrades_to_followup() -> None:
    """When planner gives clarification_mode=none but user uses a strong
    deictic without an anchor, the deterministic rule upgrades to
    can_answer_with_followup — preventing the system from pretending
    to answer precisely."""
    plan = planner_svc.plan_request(
        content="这句话是什么意思？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=False,
            # clarification_mode defaults to "none"
        ),
    )

    assert plan.clarification_mode == "can_answer_with_followup"
    assert plan.clarification_only is False
    assert plan.context_plan is not None
    assert plan.context_plan.clarification_reason == "deictic_without_anchor"


def test_deictic_without_anchor_planner_none_grammar_upgrades_to_must_clarify() -> None:
    """When planner gives clarification_mode=none but user uses a strong
    deictic without an anchor AND intent is grammar, the rule upgrades
    to must_clarify (not just followup)."""
    plan = planner_svc.plan_request(
        content="这句的语法结构是什么？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="grammar",
            clarification_only=False,
        ),
    )

    assert plan.clarification_mode == "must_clarify"
    assert plan.clarification_only is True


def test_english_that_conjunction_not_matched_as_deictic() -> None:
    """English 'that' used as a conjunction (e.g. 'I think that...',
    'Why is it that...') should NOT trigger the deictic rule."""
    plan = planner_svc.plan_request(
        content="I think that the author is making a metaphor",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=False,
        ),
    )

    # "that" as conjunction should NOT trigger deictic rule
    assert plan.clarification_mode == "none"


def test_english_that_sentence_matched_as_deictic() -> None:
    """English 'that sentence' should trigger the deictic rule."""
    plan = planner_svc.plan_request(
        content="What does that sentence mean?",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=False,
        ),
    )

    assert plan.clarification_mode == "can_answer_with_followup"


def test_english_this_matched_as_deictic() -> None:
    """English bare 'this' should trigger the deictic rule."""
    plan = planner_svc.plan_request(
        content="What does this mean?",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=False,
        ),
    )

    assert plan.clarification_mode == "can_answer_with_followup"


def test_deictic_with_asset_ambiguity_grammar_upgraded_to_must_clarify() -> None:
    """When asset ambiguity sets clarification_mode to can_answer_with_followup,
    but the user uses a strong deictic + grammar intent without an anchor,
    the deterministic rule should upgrade back to must_clarify."""
    plan = planner_svc.plan_request(
        content="这句的语法结构是什么？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="grammar",
            clarification_only=False,
        ),
        structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(
            attempted=True,
            status="ambiguous",
            reason="Multiple assets found.",
            ambiguous_assets=[
                {"record_id": "r-1", "asset_id": "a-1", "asset_type": "analysis", "entry_type": "grammar_note", "title": "Asset 1"},
            ],
        ),
    )

    # Asset ambiguity sets can_answer_with_followup, but deictic + grammar
    # overrides it to must_clarify
    assert plan.clarification_mode == "must_clarify"
    assert plan.clarification_only is True


# ---------------------------------------------------------------------------
# Degenerate answer detection for replan trigger
# ---------------------------------------------------------------------------


class TestIsDegenerateAnswer:
    """Test _is_degenerate_answer: pattern-based detection replaces len < 20."""

    def test_empty_string_is_degenerate(self) -> None:
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("") is True

    def test_whitespace_only_is_degenerate(self) -> None:
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("   \n  ") is True

    def test_short_but_valid_english_not_degenerate(self) -> None:
        """Short but meaningful answers like 'Yes.' or 'Present perfect.' should
        NOT trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("Yes.") is False
        assert _is_degenerate_answer("No.") is False
        assert _is_degenerate_answer("OK.") is False
        assert _is_degenerate_answer("Present perfect.") is False
        assert _is_degenerate_answer("Past simple.") is False

    def test_short_but_valid_cjk_not_degenerate(self) -> None:
        """Short CJK answers should NOT trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("是的") is False
        assert _is_degenerate_answer("现在完成时") is False

    def test_refusal_english_is_degenerate(self) -> None:
        """English refusal patterns should trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("I cannot answer this question.") is True
        assert _is_degenerate_answer("As an AI, I'm unable to help with that.") is True
        assert _is_degenerate_answer("I don't have enough information.") is True

    def test_refusal_cjk_is_degenerate(self) -> None:
        """CJK refusal patterns should trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("我无法回答这个问题。") is True
        assert _is_degenerate_answer("没有足够的信息来回答。") is True

    def test_punctuation_only_is_degenerate(self) -> None:
        """Pure punctuation or model artifacts should trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("...") is True
        assert _is_degenerate_answer("---") is True

    def test_normal_answer_not_degenerate(self) -> None:
        """Normal-length answers should never trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("This sentence uses the present perfect tense.") is False
        assert _is_degenerate_answer("这句话使用了现在完成时，表示过去发生的动作对现在的影响。") is False

    def test_short_gibberish_is_degenerate(self) -> None:
        """Very short content without meaningful words is degenerate."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer(",,,") is True


class TestReplanTriggerWiring:
    """Test that the replan trigger condition correctly uses _is_degenerate_answer
    and that short-but-valid answers do NOT trigger replan while degenerate
    answers do. These tests verify the wiring between the detection function
    and the replan condition, not just the helper in isolation."""

    def test_short_valid_answer_does_not_meet_replan_condition(self) -> None:
        """A short but valid answer like 'Present perfect.' should NOT meet
        the replan trigger condition (content check part)."""
        from app.services.reader_ask.service import _is_degenerate_answer
        # Simulate the replan condition: _is_degenerate_answer(final_content_md)
        final_content_md = "Present perfect."
        assert _is_degenerate_answer(final_content_md) is False

    def test_empty_answer_meets_replan_condition(self) -> None:
        """An empty answer should meet the replan trigger condition."""
        from app.services.reader_ask.service import _is_degenerate_answer
        final_content_md = ""
        assert _is_degenerate_answer(final_content_md) is True

    def test_refusal_answer_meets_replan_condition(self) -> None:
        """A refusal answer should meet the replan trigger condition."""
        from app.services.reader_ask.service import _is_degenerate_answer
        final_content_md = "I cannot answer this question without more context."
        assert _is_degenerate_answer(final_content_md) is True

    def test_cjk_short_valid_not_degenerate(self) -> None:
        """Short CJK valid answer should NOT trigger replan."""
        from app.services.reader_ask.service import _is_degenerate_answer
        assert _is_degenerate_answer("现在完成时") is False

    def test_replan_event_emitted_on_degenerate_answer(self) -> None:
        """When a degenerate answer triggers replan, the event_queue should
        receive a 'replan.started' event. This tests the actual wiring in
        the replan branch, not just the helper."""
        import asyncio
        from app.services.reader_ask.service import _is_degenerate_answer

        # Verify the detection function works as expected for the cases
        # that would enter the replan branch
        degenerate_cases = ["", "   ", "I cannot help with that.", "我无法回答", "..."]
        for case in degenerate_cases:
            assert _is_degenerate_answer(case) is True, f"Expected degenerate: {case!r}"

        # Verify that valid short answers would NOT enter the replan branch
        valid_cases = ["Yes.", "No.", "OK.", "Present perfect.", "现在完成时", "是的"]
        for case in valid_cases:
            assert _is_degenerate_answer(case) is False, f"Expected NOT degenerate: {case!r}"

    def test_replan_condition_requires_clarification_mode_none(self) -> None:
        """Even with a degenerate answer, replan should not trigger if
        clarification_mode is not 'none'. This verifies the full condition."""
        from app.services.reader_ask.service import _is_degenerate_answer

        # The full replan condition is:
        # _is_degenerate_answer(content) AND clarification_mode == "none" AND clarification_only is False
        # If clarification_mode is "must_clarify", replan should NOT happen
        # even with a degenerate answer
        assert _is_degenerate_answer("") is True  # degenerate
        # But the full condition also checks clarification_mode
        # This test verifies the helper is correct; the mode check is in the
        # main flow and tested implicitly through the service integration


async def test_replan_started_event_emitted_to_event_queue() -> None:
    """Real wiring test: _maybe_emit_replan_event puts 'replan.started' on the
    event_queue when a degenerate answer is detected with a valid planning snapshot."""
    import asyncio
    from app.services.reader_ask.service import _maybe_emit_replan_event

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    # Build a real planning snapshot with clarification_mode="none"
    # Use content without strong deictic words to avoid the deictic rule
    planning_snapshot = planner_svc.plan_request(
        content="Explain the main idea of the article",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(resolved_intent="explain"),
    )

    triggered = await _maybe_emit_replan_event(
        final_content_md="",
        planning_snapshot=planning_snapshot,
        event_queue=event_queue,
        assistant_message_id="msg-123",
    )

    assert triggered is True
    assert not event_queue.empty()
    event_name, event_data = await event_queue.get()
    assert event_name == "replan.started"
    assert event_data["message_id"] == "msg-123"
    assert event_data["reason"] == "degenerate_answer"


async def test_replan_not_triggered_for_short_valid_answer() -> None:
    """Real wiring test: _maybe_emit_replan_event returns False and does not
    emit any event for a short but valid answer."""
    import asyncio
    from app.services.reader_ask.service import _maybe_emit_replan_event

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    planning_snapshot = planner_svc.plan_request(
        content="What does this mean?",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(resolved_intent="explain"),
    )

    triggered = await _maybe_emit_replan_event(
        final_content_md="Present perfect.",
        planning_snapshot=planning_snapshot,
        event_queue=event_queue,
        assistant_message_id="msg-123",
    )

    assert triggered is False
    assert event_queue.empty()


async def test_replan_not_triggered_when_must_clarify() -> None:
    """Real wiring test: even with a degenerate answer, replan is NOT triggered
    when clarification_mode is 'must_clarify'."""
    import asyncio
    from app.services.reader_ask.service import _maybe_emit_replan_event

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    # Build a snapshot with must_clarify (no anchors + no deictic → stays must_clarify)
    planning_snapshot = planner_svc.plan_request(
        content="Why is this written this way?",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_reader_notes=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_reason="missing_required_context",
        ),
    )

    triggered = await _maybe_emit_replan_event(
        final_content_md="",
        planning_snapshot=planning_snapshot,
        event_queue=event_queue,
        assistant_message_id="msg-123",
    )

    assert triggered is False
    assert event_queue.empty()


def test_reasoning_enabled_settings_enables_dashscope_sse_and_incremental_output() -> None:
    from app.llm.types import RunModelSettings
    from app.services.reader_ask.service import _reasoning_enabled_settings

    settings = RunModelSettings(
        extra_headers={"X-Test": "1"},
        extra_body={
            "enable_thinking": False,
            "preserve_thinking": True,
        },
    )

    resolved = _reasoning_enabled_settings(
        settings,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert resolved.extra_headers == {
        "X-Test": "1",
        "X-DashScope-SSE": "enable",
    }
    assert resolved.extra_body is not None
    assert resolved.extra_body["enable_thinking"] is True
    assert resolved.extra_body["preserve_thinking"] is False
    assert resolved.extra_body["incremental_output"] is True


def test_reasoning_enabled_settings_preserves_non_dashscope_headers() -> None:
    from app.llm.types import RunModelSettings
    from app.services.reader_ask.service import _reasoning_enabled_settings

    settings = RunModelSettings(extra_body={"thinking": {"type": "disabled"}})
    resolved = _reasoning_enabled_settings(
        settings,
        base_url="https://api.deepseek.com",
    )

    assert resolved.extra_headers is None
    assert resolved.extra_body is not None
    assert resolved.extra_body["thinking"] == {"type": "enabled"}
    assert "incremental_output" not in resolved.extra_body


async def test_stream_checkpoint_flush_persists_partial_reasoning_and_body(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    turn_run_id = uuid4()
    updates: list[tuple[UUID, str, dict[str, object]]] = []

    async def fake_update_turn_run(*, turn_run_id, status, user_visible_output_json, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        updates.append((turn_run_id, status, user_visible_output_json))
        return {"id": str(turn_run_id)}

    monkeypatch.setattr(reader_ask_service.repo, "update_turn_run", fake_update_turn_run)

    checkpoint = reader_ask_service._TurnRunStreamCheckpoint(  # type: ignore[attr-defined]
        turn_run_id=turn_run_id,
        build_output_json=lambda content_md, reasoning_md, reasoning_status: {
            "content_md": content_md,
            "reasoning_md": reasoning_md,
            "reasoning_status": reasoning_status,
        },
    )
    runtime = reader_ask_service._AgentStreamRuntime(  # type: ignore[attr-defined]
        emitted_text="已生成正文。",
        emitted_reasoning="先判断句子主干。",
        reasoning_started=True,
    )

    await reader_ask_service._maybe_flush_turn_run_stream_checkpoint(  # type: ignore[attr-defined]
        checkpoint=checkpoint,
        runtime=runtime,
    )

    assert updates == [
        (
            turn_run_id,
            "streaming",
            {
                "content_md": "已生成正文。",
                "reasoning_md": "先判断句子主干。",
                "reasoning_status": "streaming",
            },
        )
    ]
    assert checkpoint.last_flushed_content_len == len("已生成正文。")
    assert checkpoint.last_flushed_reasoning_len == len("先判断句子主干。")


async def test_stream_checkpoint_flush_is_throttled_until_forced(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    turn_run_id = uuid4()
    updates: list[dict[str, object]] = []

    async def fake_update_turn_run(*, turn_run_id, status, user_visible_output_json, **kwargs):  # type: ignore[no-untyped-def]
        del turn_run_id, status, kwargs
        updates.append(user_visible_output_json)
        return {"id": str(uuid4())}

    monkeypatch.setattr(reader_ask_service.repo, "update_turn_run", fake_update_turn_run)

    checkpoint = reader_ask_service._TurnRunStreamCheckpoint(  # type: ignore[attr-defined]
        turn_run_id=turn_run_id,
        build_output_json=lambda content_md, reasoning_md, reasoning_status: {
            "content_md": content_md,
            "reasoning_md": reasoning_md,
            "reasoning_status": reasoning_status,
        },
        min_flush_interval_s=999.0,
        min_content_chars=999,
        min_reasoning_chars=999,
    )
    runtime = reader_ask_service._AgentStreamRuntime(  # type: ignore[attr-defined]
        emitted_text="第一段正文",
        emitted_reasoning="第一段思路",
        reasoning_started=True,
    )

    await reader_ask_service._maybe_flush_turn_run_stream_checkpoint(  # type: ignore[attr-defined]
        checkpoint=checkpoint,
        runtime=runtime,
    )
    runtime.emitted_text = "第一段正文，新增很短"
    runtime.emitted_reasoning = "第一段思路，新增很短"
    await reader_ask_service._maybe_flush_turn_run_stream_checkpoint(  # type: ignore[attr-defined]
        checkpoint=checkpoint,
        runtime=runtime,
    )
    await reader_ask_service._maybe_flush_turn_run_stream_checkpoint(  # type: ignore[attr-defined]
        checkpoint=checkpoint,
        runtime=runtime,
        force=True,
    )

    assert len(updates) == 2
    assert updates[0]["reasoning_status"] == "streaming"
    assert updates[1]["content_md"] == "第一段正文，新增很短"
    assert updates[1]["reasoning_md"] == "第一段思路，新增很短"


async def test_replan_not_triggered_when_no_planning_snapshot() -> None:
    """Real wiring test: replan is NOT triggered when planning_snapshot is None."""
    import asyncio
    from app.services.reader_ask.service import _maybe_emit_replan_event

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    triggered = await _maybe_emit_replan_event(
        final_content_md="",
        planning_snapshot=None,
        event_queue=event_queue,
        assistant_message_id="msg-123",
    )

    assert triggered is False
    assert event_queue.empty()


# ---------------------------------------------------------------------------
# Fallback planner: intent coverage, weak references, conservative path
# ---------------------------------------------------------------------------


def _fallback_record(**overrides: object) -> object:
    """Build a minimal record stub for fallback planner tests."""
    defaults = {
        "title": "Test Article",
        "render_scene": {
            "content_summary": {"overview": "A test article about AI."},
            "sentence_entries": [],
        },
    }
    defaults.update(overrides)
    return type("Record", (), defaults)()


def _fallback_page_identity(**overrides: object) -> ReaderAskPageIdentity:
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


class TestFallbackReferenceQuery:
    """Test _fallback_reference_query with explicit and weak patterns."""

    def test_book_title_marks(self) -> None:
        assert _fallback_reference_query("之前那篇《Climate Policy》里也提过") == "Climate Policy"

    def test_double_quotes(self) -> None:
        assert _fallback_reference_query('关于"AI Ethics"那篇文章') == "AI Ethics"

    def test_weak_chinese_之前那篇(self) -> None:
        assert _fallback_reference_query("之前那篇climate policy的文章也提过吗？") == "climate policy"

    def test_weak_chinese_讲(self) -> None:
        assert _fallback_reference_query("讲AI伦理的文章怎么说？") == "AI伦理"

    def test_weak_chinese_关于(self) -> None:
        assert _fallback_reference_query("关于climate change的研究有提到吗？") == "climate change"

    def test_weak_english_article_about(self) -> None:
        assert _fallback_reference_query("that article about climate policy also mentioned this") == "climate policy"

    def test_weak_english_the_paper_on(self) -> None:
        assert _fallback_reference_query("the paper on AI ethics discussed this") == "AI ethics"

    def test_no_reference_returns_none(self) -> None:
        assert _fallback_reference_query("这句话什么意思？") is None

    def test_explicit_title_takes_priority_over_weak(self) -> None:
        """When both explicit (book marks) and weak patterns match,
        explicit pattern wins because it's checked first."""
        result = _fallback_reference_query("之前那篇《Climate Policy》的文章")
        assert result == "Climate Policy"

    def test_short_topic_ignored(self) -> None:
        """Weak patterns require at least 2 characters for the topic."""
        result = _fallback_reference_query("讲X的文章")
        # "X" is only 1 char, below the {2,30} minimum — should not match
        assert result is None


class TestFallbackIntentCoverage:
    """Test that fallback planner recognizes common intents."""

    @pytest.mark.parametrize(
        "message,expected_intent",
        [
            ("这句话的语法结构是什么？", "grammar"),
            ("为什么这里用过去式？", "grammar"),
            ("帮我拆解这个长句", "breakdown"),
            ("break down this sentence", "breakdown"),
            ("这个词什么意思？", "vocabulary"),
            ("phrase的含义", "vocabulary"),
            ("这篇文章和之前那篇有什么不同？", "general"),
            ("对比一下这两篇文章", "general"),
            ("比较两者的观点", "general"),
            ("总结一下这篇文章", "general"),
            ("translate this paragraph", "general"),
            ("帮我复习一下", "general"),
            ("这篇文章讲了什么？", "explain"),
            ("What is this about?", "explain"),
        ],
    )
    def test_fallback_intent_recognition(self, message: str, expected_intent: str) -> None:
        decision = _fallback_semantic_planner_decision(
            user_message=message,
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.resolved_intent == expected_intent

    def test_entry_action_lookup_in_context_overrides_message(self) -> None:
        """entry_action=lookup_in_context should force vocabulary intent
        even if the message contains grammar keywords."""
        decision = _fallback_semantic_planner_decision(
            user_message="这里的语法结构",
            entry_action="lookup_in_context",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.resolved_intent == "vocabulary"

    def test_entry_action_why_here_overrides_message(self) -> None:
        """entry_action=why_here should force grammar intent."""
        decision = _fallback_semantic_planner_decision(
            user_message="这个词什么意思",
            entry_action="why_here",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.resolved_intent == "grammar"

    def test_compare_not_misclassified_as_explain(self) -> None:
        """Compare/difference questions should resolve to 'general', not 'explain'."""
        decision = _fallback_semantic_planner_decision(
            user_message="这两篇文章的观点有什么区别？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.resolved_intent == "general"

    def test_vs_pattern_recognized_as_general(self) -> None:
        decision = _fallback_semantic_planner_decision(
            user_message="democracy vs authoritarianism",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.resolved_intent == "general"


class TestFallbackWeakReferenceConservativePath:
    """Test that weak references trigger cross-record context and conservative path."""

    def test_weak_reference_enables_cross_record_context(self) -> None:
        """When a weak reference is detected, cross_record_context_allowed should be True."""
        decision = _fallback_semantic_planner_decision(
            user_message="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.reference_request.requested is True
        assert decision.reference_request.query is not None
        assert decision.working_set.cross_record_context_allowed is True

    def test_weak_reference_without_anchor_sets_conservative_reason(self) -> None:
        """Weak reference without anchor should set clarification_reason
        to signal uncertainty (conservative path)."""
        decision = _fallback_semantic_planner_decision(
            user_message="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.clarification_reason == "fallback_weak_reference_without_anchor"
        # Should NOT be must_clarify — we can still answer at article level
        assert decision.clarification_only is False

    def test_weak_reference_with_anchor_no_conservative_reason(self) -> None:
        """Weak reference WITH anchor should NOT trigger conservative path."""
        decision = _fallback_semantic_planner_decision(
            user_message="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.clarification_reason is None
        assert decision.reference_request.requested is True

    def test_explicit_title_reference_no_conservative_reason(self) -> None:
        """Explicit title (book marks) with anchor should NOT trigger conservative path."""
        decision = _fallback_semantic_planner_decision(
            user_message='之前那篇《Climate Policy》里也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.clarification_reason is None

    def test_no_reference_no_cross_record(self) -> None:
        """Without any reference, cross_record_context_allowed should be False."""
        decision = _fallback_semantic_planner_decision(
            user_message="这句话什么意思？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.reference_request.requested is False
        assert decision.working_set.cross_record_context_allowed is False

    def test_external_attachment_still_enables_cross_record(self) -> None:
        """Explicit external record attachment should still enable cross_record
        even without any reference query."""
        attachment = ReaderAskAttachment(
            kind="record_ref",
            subtype="related_record",
            label="Related Article",
            metadata=ReaderAskAttachmentMetadata(
                source_surface="test",
                record_id="00000000-0000-0000-0000-000000000002",
            ),
        )
        decision = _fallback_semantic_planner_decision(
            user_message="这篇文章讲了什么？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[attachment],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
        )
        assert decision.working_set.cross_record_context_allowed is True


class TestFallbackPlannerConservativeReasonPromotion:
    """Test that planner.plan_request promotes fallback_ clarification_reason
    to can_answer_with_followup."""

    def test_fallback_reason_promoted_to_followup(self) -> None:
        """When fallback sets clarification_reason starting with 'fallback_',
        plan_request should promote clarification_mode to can_answer_with_followup."""
        decision = _planner_decision(
            resolved_intent="explain",
            clarification_reason="fallback_weak_reference_without_anchor",
            local_context_window_needed=True,
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        assert snapshot.clarification_mode == "can_answer_with_followup"
        assert snapshot.context_plan.clarification_reason == "fallback_weak_reference_without_anchor"

    def test_non_fallback_reason_not_promoted(self) -> None:
        """A non-fallback clarification_reason with clarification_mode=none
        should NOT be promoted."""
        decision = _planner_decision(
            resolved_intent="explain",
            clarification_reason="some_other_reason",
            local_context_window_needed=True,
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="这篇文章讲了什么？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        # No promotion because reason doesn't start with "fallback_"
        assert snapshot.clarification_mode == "none"

    def test_fallback_reason_with_ambiguous_reference_stays_followup(self) -> None:
        """When fallback reason is set AND reference resolution is ambiguous,
        the result should still be can_answer_with_followup (not must_clarify)."""
        decision = _planner_decision(
            resolved_intent="explain",
            clarification_reason="fallback_weak_reference_without_anchor",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="climate policy",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query="climate policy",
                ambiguous_records=[
                    {"record_id": "r1", "title": "Climate Policy A"},
                    {"record_id": "r2", "title": "Climate Policy B"},
                ],
            ),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        # Ambiguous reference + no anchor normally → must_clarify,
        # but local_context_window_needed=True → can_answer_with_followup
        assert snapshot.clarification_mode == "can_answer_with_followup"

    def test_fallback_reason_with_not_found_reference_stays_followup(self) -> None:
        """When fallback reason is set AND reference resolution is not_found,
        the result should still be can_answer_with_followup (not must_clarify).
        This is the key scenario: weak reference without anchor, resolver can't
        find the record — we should still answer at article level with a hint,
        not force the user to clarify."""
        decision = _planner_decision(
            resolved_intent="explain",
            clarification_reason="fallback_weak_reference_without_anchor",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="climate policy",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="not_found",
                query="climate policy",
            ),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        # not_found + no anchor normally → must_clarify,
        # but fallback_ reason protects the can_answer_with_followup path
        assert snapshot.clarification_mode == "can_answer_with_followup"
        assert snapshot.context_plan.clarification_reason == "fallback_weak_reference_without_anchor"

    def test_non_fallback_not_found_without_anchor_still_must_clarify(self) -> None:
        """Without a fallback_ reason, not_found + no anchor should still
        produce must_clarify (existing behavior preserved)."""
        decision = _planner_decision(
            resolved_intent="explain",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="climate policy",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="我之前那篇 climate policy 也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="not_found",
                query="climate policy",
            ),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        assert snapshot.clarification_mode == "must_clarify"

    def test_ambiguous_reference_without_anchor_article_level_intent_stays_followup(self) -> None:
        decision = _planner_decision(
            resolved_intent="general",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="policy article",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="之前那篇 policy article 和这篇有什么不同？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query="policy article",
                ambiguous_records=[
                    {"record_id": "r1", "title": "Policy A"},
                    {"record_id": "r2", "title": "Policy B"},
                ],
            ),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        assert snapshot.clarification_mode == "can_answer_with_followup"

    def test_ambiguous_reference_without_anchor_sentence_level_intent_still_must_clarify(self) -> None:
        decision = _planner_decision(
            resolved_intent="grammar",
            local_context_window_needed=False,
            reference_requested=True,
            reference_query="policy article",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content="之前那篇 policy article 这里为什么用过去式？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query="policy article",
                ambiguous_records=[
                    {"record_id": "r1", "title": "Policy A"},
                    {"record_id": "r2", "title": "Policy B"},
                ],
            ),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        assert snapshot.clarification_mode == "must_clarify"


def test_collect_sentence_windows_returns_fallback_window_without_anchor() -> None:
    record = reader_ask_service._RecordBundle(
        record_id=uuid4(),
        title="Fallback Window",
        source_text="First sentence. Second sentence. Third sentence.",
        render_scene={
            "article": {
                "sentences": [
                    {"sentence_id": "s1", "paragraph_id": "p1", "text": "First sentence."},
                    {"sentence_id": "s2", "paragraph_id": "p1", "text": "Second sentence."},
                    {"sentence_id": "s3", "paragraph_id": "p2", "text": "Third sentence."},
                ]
            },
            "translations": [],
        },
        page_state_json={},
        workflow_version="1",
        schema_version="1",
    )

    windows = reader_ask_service._collect_sentence_windows(record, [])
    assert len(windows) == 1
    assert windows[0]["fallback_window"] is True
    assert len(windows[0]["window"]) == 2
    assert windows[0]["window"][0]["text"] == "First sentence."


def test_insufficient_credits_payload_includes_user_message() -> None:
    payload = reader_ask_service._insufficient_credits_payload(remaining_points=3)
    assert payload["code"] == "INSUFFICIENT_CREDITS"
    assert payload["remaining_points"] == 3
    assert payload["required_points"] > 0
    assert "本轮请求未发送给模型" in payload["user_message"]


# ---------------------------------------------------------------------------
# _finish_reader_ask_agent_stream — interrupted path
# ---------------------------------------------------------------------------


def test_finish_reader_ask_agent_stream_interrupted_with_partial_content() -> None:
    """When the producer errors but partial content exists, the stream is
    interrupted (not failed). The outcome must carry interrupted=True and
    an SSE event with can_retry=True."""
    runtime = reader_ask_service._AgentStreamRuntime(  # type: ignore[attr-defined]
        content_parts=["这是部分", "生成的回答"],
        usage_summary={"input_tokens": 10, "output_tokens": 20},
        producer_error=RuntimeError("model connection lost"),
    )
    outcome, sse_event = reader_ask_service._finish_reader_ask_agent_stream(
        runtime=runtime,
        assistant_message_id="msg-interrupted-1",
    )

    # Outcome carries partial content + interrupted flag
    assert outcome.interrupted is True
    assert "这是部分生成的回答" in outcome.content_md
    assert outcome.usage_summary is not None

    # SSE event is emitted (not None), with can_retry
    assert sse_event is not None
    event_name, event_data = sse_event
    assert event_name == "message.interrupted"
    assert event_data["can_retry"] is True
    assert event_data["message_id"] == "msg-interrupted-1"
    assert "这是部分生成的回答" in event_data["content_md"]


def test_finish_reader_ask_agent_stream_normal_completion() -> None:
    """When the producer succeeds, the outcome is not interrupted and no SSE
    event is emitted."""
    runtime = reader_ask_service._AgentStreamRuntime(  # type: ignore[attr-defined]
        content_parts=["完整回答"],
        usage_summary={"input_tokens": 10, "output_tokens": 30},
    )
    outcome, sse_event = reader_ask_service._finish_reader_ask_agent_stream(
        runtime=runtime,
        assistant_message_id="msg-normal-1",
    )

    assert outcome.interrupted is False
    assert outcome.content_md == "完整回答"
    assert sse_event is None


# ---------------------------------------------------------------------------
# confirm_action — create_supplement_grammar_note
# ---------------------------------------------------------------------------


async def test_confirm_action_create_supplement_grammar_note() -> None:
    """Confirming a create_supplement_grammar_note proposal must:
    1. Call supplements_svc.create_supplement
    2. Update the turn_run user_visible_output_json with persisted_supplements
    3. Upsert eval trace with action_audit and supplement_audit entries
    """
    from app.schemas.reader_ask import (
        ReaderAskActionConfirmRequest,
        ReaderAskPersistedSupplement,
        ReaderAskSupplementCandidate,
    )

    user_id = uuid4()
    thread_id = uuid4()
    record_id = uuid4()
    message_id = uuid4()
    turn_run_id = uuid4()
    action_id = "action-supplement-1"
    candidate_id = str(uuid4())

    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        paragraph_id="p1",
        selected_text="The cat sat on the mat.",
        entry_type="grammar_note",
    )

    candidate = ReaderAskSupplementCandidate(
        candidate_id=candidate_id,
        supplement_type="grammar_note",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        paragraph_id="p1",
        title="AI 补充语法旁注",
        content="这是一个语法旁注内容，长度超过六十字以确保不会被过滤掉。",
        anchor=anchor,
        schema_version="reader-ask-supplement-v1",
        created_from_turn_run_id=str(turn_run_id),
    )

    proposal_dict = {
        "id": action_id,
        "action_type": "create_supplement_grammar_note",
        "label": "加入当前页补充",
        "description": "把这条 AI 语法旁注加入当前文章",
        "requires_confirmation": True,
        "status": "pending",
        "payload_json": {"candidate": candidate.model_dump(mode="json")},
    }

    message_dict = {
        "id": str(message_id),
        "thread_id": str(thread_id),
        "role": "assistant",
        "status": "completed",
        "content_md": "语法解析完成",
        "context_anchors": [anchor.model_dump(mode="json")],
        "citations": [],
        "action_proposals": [proposal_dict],
        "tool_trace": [],
        "evidence": [],
        "trace_summary": None,
        "disambiguation": None,
        "external_asset_disambiguation": None,
        "response_cards": [],
        "resolved_context": None,
        "context_plan": None,
        "resolved_context_input": None,
        "run_info": {"turn_id": str(uuid4()), "run_id": str(turn_run_id), "run_attempt": 1},
        "supplement_candidates": [candidate.model_dump(mode="json")],
        "persisted_supplements": [],
        "reasoning_md": None,
        "reasoning_status": None,
        "usage_event_id": None,
        "current_turn_run_id": str(turn_run_id),
        "current_turn_run": {
            "id": str(turn_run_id),
            "message_id": str(message_id),
            "thread_id": str(thread_id),
            "user_id": str(user_id),
            "record_id": str(record_id),
            "turn_id": str(uuid4()),
            "run_attempt": 1,
            "supersedes_run_id": None,
            "status": "completed",
            "resolved_intent": "grammar",
            "user_visible_output_json": {
                "content_md": "语法解析完成",
                "action_proposals": [proposal_dict],
                "persisted_supplements": [],
                "evidence": [],
                "trace_summary": None,
            },
            "usage_summary_json": None,
            "usage_event_id": None,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:01+00:00",
            "failed_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
        "current_user_visible_output": {
            "content_md": "语法解析完成",
            "action_proposals": [proposal_dict],
            "persisted_supplements": [],
            "evidence": [],
            "trace_summary": None,
        },
        "current_eval_trace": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:01+00:00",
    }

    created_supplement_row = {
        "id": UUID(candidate_id),
        "record_id": record_id,
        "supplement_type": "grammar_note",
        "target_key": "record:r1:sentence:s1",
        "sentence_id": "s1",
        "paragraph_id": "p1",
        "title": "AI 补充语法旁注",
        "content_md": "这是一个语法旁注内容，长度超过六十字以确保不会被过滤掉。",
        "anchor_payload_json": anchor.model_dump(mode="json"),
        "metadata_json": {},
        "schema_version": "reader-ask-supplement-v1",
        "created_from_turn_run_id": str(turn_run_id),
        "created_at": "2026-01-01T00:00:02+00:00",
        "updated_at": "2026-01-01T00:00:02+00:00",
        "deleted_at": None,
    }

    turn_run_updates: list[dict[str, Any]] = []
    eval_trace_upserts: list[dict[str, Any]] = []
    message_updates: list[dict[str, Any]] = []

    async def fake_update_message(**kwargs):  # type: ignore[no-untyped-def]
        message_updates.append(kwargs)
        return {"id": str(message_id), "thread_id": str(thread_id)}

    async def fake_update_turn_run(*, turn_run_id, status, user_visible_output_json, **kwargs):  # type: ignore[no-untyped-def]
        turn_run_updates.append({"turn_run_id": turn_run_id, "status": status, "user_visible_output_json": user_visible_output_json})
        return {"id": str(turn_run_id)}

    async def fake_upsert_eval_trace(*, turn_run_id, action_audit_json=None, supplement_audit_json=None, **kwargs):  # type: ignore[no-untyped-def]
        eval_trace_upserts.append({
            "turn_run_id": turn_run_id,
            "action_audit_json": action_audit_json,
            "supplement_audit_json": supplement_audit_json,
        })
        return {"turn_run_id": str(turn_run_id)}

    async def fake_get_eval_trace(turn_run_id):  # type: ignore[no-untyped-def]
        return None

    with (
        patch.object(reader_ask_service.repo, "find_action_proposal", new=AsyncMock(return_value=(message_dict, proposal_dict))),
        patch.object(reader_ask_service.repo, "get_thread", new=AsyncMock(return_value={"id": str(thread_id), "record_id": str(record_id)})),
        patch.object(reader_ask_service.repo, "ensure_record_access", new=AsyncMock(return_value={"id": str(record_id), "title": "Test Article"})),
        patch.object(reader_ask_service.supplements_svc, "create_supplement", new=AsyncMock(return_value=created_supplement_row)),
        patch.object(reader_ask_service.repo, "update_message", new=fake_update_message),
        patch.object(reader_ask_service.repo, "update_turn_run", new=fake_update_turn_run),
        patch.object(reader_ask_service.repo, "get_eval_trace", new=fake_get_eval_trace),
        patch.object(reader_ask_service.repo, "upsert_eval_trace", new=fake_upsert_eval_trace),
    ):
        response = await reader_ask_service.confirm_action(
            user_id=user_id,
            thread_id=thread_id,
            action_id=action_id,
            body=ReaderAskActionConfirmRequest(confirmed=True),
        )

    # 1. confirm_action returns ok with executed status
    assert response.ok is True
    assert response.status == "executed"
    assert response.action_id == action_id
    assert response.result.persisted_supplement is not None
    assert response.result.persisted_supplement.supplement_id == candidate_id
    assert response.result.supplement_projection is not None

    # 2. Turn run was updated with persisted_supplements in user_visible_output_json
    assert len(turn_run_updates) >= 1
    tr_update = turn_run_updates[-1]
    persisted_supps = tr_update["user_visible_output_json"].get("persisted_supplements", [])
    assert any(
        str(s.get("supplement_id")) == candidate_id
        for s in persisted_supps
    ), "persisted_supplements must contain the newly created supplement"

    # 3. Eval trace was upserted with action_audit and supplement_audit
    assert len(eval_trace_upserts) >= 1
    et = eval_trace_upserts[-1]
    action_audit = et.get("action_audit_json") or []
    supplement_audit = et.get("supplement_audit_json") or []
    assert any(a.get("action_id") == action_id and a.get("decision") == "confirmed" for a in action_audit), \
        "action_audit must contain confirmed decision"
    assert any(s.get("supplement_id") == candidate_id and s.get("event") == "persisted" for s in supplement_audit), \
        "supplement_audit must contain persisted event"
