from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskAttachmentPayload,
    ReaderAskPageIdentity,
)
from app.services.analysis.credit_service import CreditReservation
from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.reader_ask.service import (
    _attachment_to_anchor,
    _attachments_to_anchor_refs,
    _build_action_proposals,
    _build_context_plan,
    _build_resolved_context_input,
    _build_response_cards,
    _build_unused_reservation,
    _dictionary_ai_to_citation,
    _merge_usage_summaries,
    _matches_history_intent,
    _needs_clarification,
    _resolve_intent,
    _resolved_context_summary,
)
from app.services.reader_ask.supplements import build_grammar_note_candidate


def test_matches_history_intent_for_cross_article_language() -> None:
    assert _matches_history_intent("我以前在哪见过这个结构？") is True
    assert _matches_history_intent("Can you explain this sentence?") is False


def test_needs_clarification_for_ambiguous_local_reference_without_anchor() -> None:
    assert _needs_clarification("这里为什么这样写？", []) is True
    assert _needs_clarification(
        "这里为什么这样写？",
        [ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
    ) is False


def test_resolve_intent_prefers_explicit_entry_action_and_content_signal() -> None:
    assert _resolve_intent("为什么这里是这个意思？", [], "lookup_in_context") == "vocabulary"
    assert _resolve_intent("帮我拆句", [], "ask_about_this") == "breakdown"
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


def test_build_action_proposals_generates_confirmable_actions() -> None:
    anchor = ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="That there were some.")
    proposals = _build_action_proposals(
        user_message="请把这条解释保存成笔记并收藏这句",
        record=type("Record", (), {"record_id": "00000000-0000-0000-0000-000000000001"})(),
        anchors=[anchor],
        assistant_content_md="这句话在这里是存在句。",
    )

    proposal_types = {proposal.action_type for proposal in proposals}

    assert "save_answer_note" in proposal_types
    assert "favorite_anchor" in proposal_types
    assert all(proposal.requires_confirmation for proposal in proposals)


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
    assert cards[0].parts[0].label == "主干"


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
        used_history_lookup=True,
        citations=[],
    )

    assert summary.current_sentence_used is True
    assert summary.current_paragraph_used is True
    assert summary.used_record_assets is True
    assert summary.used_history_lookup is True
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
            has_user_assets=True,
        ),
        entry_action="why_here",
        attachments=[attachment],
        anchors=[],
    )

    assert len(context_input.attachments) == 1
    assert context_input.attachments[0].label == "语法旁注"
    assert context_input.normalized_anchors == []


def test_build_context_plan_records_history_and_dictionary_usage() -> None:
    runtime_state = ReaderAskRuntimeState(
        used_history_lookup=True,
        latest_record_context={"sentence_windows": []},
        latest_record_excerpt_assets=[{"id": "asset-1"}],
        source_labels={"current_record", "history_assets", "dictionary"},
    )

    context_plan = _build_context_plan(
        entry_action="ask_about_this",
        attachments=[],
        anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
        runtime_state=runtime_state,
        citations=[_dictionary_ai_to_citation({"summary": "x"}, "test", 1)],
    )

    assert context_plan.used_history_lookup is True
    assert context_plan.used_record_context is True
    assert context_plan.used_record_insights is True
    assert context_plan.used_dictionary is True


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
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。",
        created_from_turn_run_id="run-1",
    )

    assert candidate is not None
    assert candidate.supplement_type == "grammar_note"
    assert candidate.created_from_turn_run_id == "run-1"
