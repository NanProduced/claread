import asyncio
from uuid import uuid4

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskAttachmentPayload,
    ReaderAskCurrentRecordContext,
    ReaderAskExternalRecordContext,
    ReaderAskPageIdentity,
)
from app.services.analysis.credit_service import CreditReservation
from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.services.reader_ask import capabilities as capabilities_svc
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask import post_process as post_process_svc
from app.services.reader_ask import resolver as resolver_svc
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
            has_user_assets=True,
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
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。",
        created_from_turn_run_id="run-1",
    )

    assert candidate is not None
    assert candidate.supplement_type == "grammar_note"
    assert candidate.created_from_turn_run_id == "run-1"


def test_reference_needs_extracts_known_title_query() -> None:
    needs = planner_svc.build_reference_needs("我之前那篇 climate policy 的解析里也提过这个吗？")

    assert needs.requested is True
    assert needs.query == "climate policy"


def test_reference_resolution_single_hit_returns_resolved_record() -> None:
    async def finder(user_id, *, query, exclude_record_id, limit):  # type: ignore[no-untyped-def]
        del user_id, exclude_record_id, limit
        assert query == "climate policy"
        return [{"id": "r-2", "title": "Climate Policy"}]

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
    assert resolution.resolved_records == [{"record_id": "r-2", "title": "Climate Policy"}]


def test_reference_resolution_multiple_hits_requires_clarification() -> None:
    async def finder(user_id, *, query, exclude_record_id, limit):  # type: ignore[no-untyped-def]
        del user_id, query, exclude_record_id, limit
        return [
            {"id": "r-2", "title": "Climate Policy"},
            {"id": "r-3", "title": "Climate Policy Notes"},
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


def test_build_context_plan_records_reference_resolution_reason() -> None:
    runtime_state = ReaderAskRuntimeState(
        source_labels={"current_record", "history_assets"},
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
            has_user_assets=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
    )

    context_plan = _build_context_plan(
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        runtime_state=ReaderAskRuntimeState(source_labels={"current_record"}),
        citations=[],
        planning_snapshot=snapshot,
    )

    assert context_plan.clarification_reason == "missing_local_anchor"


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
            has_user_assets=True,
        ),
        entry_action="why_here",
        attachments=[],
        anchors=[anchor],
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
            has_user_assets=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
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
            has_user_assets=True,
        ),
        entry_action="ask_about_this",
        attachments=[attachment],
        anchors=[],
    )

    assert plan.working_set.external_record_refs == [
        {
            "record_id": "00000000-0000-0000-0000-000000000002",
            "title": "Climate Policy",
            "reason": "explicit_attachment",
        }
    ]
    assert plan.trace_summary.working_set_mode == "explicit_external_record"


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
            has_user_assets=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            attempted=True,
            status="resolved",
            query="Climate Policy",
            reason="已命中历史文章“Climate Policy”。",
            resolved_records=[{"record_id": "r-2", "title": "Climate Policy"}],
        ),
    )
    trace = planner_svc.build_trace_summary(
        runtime_state=ReaderAskRuntimeState(
            source_labels={"current_record", "history_assets"},
            used_history_lookup=True,
            latest_external_record_contexts=[
                {
                    "record_id": "r-2",
                    "record_title": "Climate Policy",
                    "article_overview": None,
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
    assert any("没有可用概览" in note for note in trace.notes)


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
                "source_labels": ["external_record"],
                "reason": "known_reference_resolved",
            }
        ],
    )

    assert evidence[0].scope == "external_record"
    assert evidence[0].record_title == "Climate Policy"
    assert evidence[0].reason == "known_reference_resolved"


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
            has_user_assets=True,
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
    plan = planner_svc.plan_request(
        content="这里为什么这样写？",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context", "record_insights", "dictionary"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=True,
            has_user_assets=True,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
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
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。",
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
        include_clarification=True,
    )

    assert len(evidence) == 1
    assert evidence[0].kind == "clarification"
