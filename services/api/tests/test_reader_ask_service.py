import asyncio
from uuid import UUID, uuid4

from app.schemas.reader_ask import (
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
from app.services.reader_ask.service import (
    _attachment_to_anchor,
    _attachments_to_anchor_refs,
    _build_run_info,
    _capability_trace_json,
    _build_action_proposals,
    _build_context_plan,
    _planning_snapshot_json,
    _build_resolved_context_input,
    _build_response_cards,
    _build_unused_reservation,
    _dictionary_ai_to_citation,
    _merge_usage_summaries,
    _needs_clarification,
    _next_run_info,
    _resolve_intent,
    _resolved_context_summary,
)
from app.services.reader_ask.supplements import build_grammar_note_candidate


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


def test_build_action_proposals_does_not_offer_saving_full_answer_as_note() -> None:
    anchor = ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="That there were some.")
    proposals = _build_action_proposals(
        user_message="请高亮一下这句，并把解释保存成笔记",
        record=type("Record", (), {"record_id": "00000000-0000-0000-0000-000000000001"})(),
        anchors=[anchor],
        assistant_content_md="这句话在这里是存在句。",
    )

    proposal_types = {proposal.action_type for proposal in proposals}

    assert "save_excerpt" in proposal_types
    assert "save_note" not in proposal_types
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
        used_cross_record_context=True,
        citations=[],
    )

    assert summary.current_sentence_used is True
    assert summary.current_paragraph_used is True
    assert summary.used_record_assets is True
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
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。",
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
        resolved_intent="explain",
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        asset_disambiguation=None,
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
        assistant_content_md="这里的 even if 引出让步从句，用来先让步再转主句判断。",
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


def test_reference_needs_extracts_known_title_query() -> None:
    needs = planner_svc.build_reference_needs("我之前那篇 climate policy 的解析里也提过这个吗？")

    assert needs.requested is True
    assert needs.query == "climate policy"


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

    assert snapshot.clarification_only is True
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
            has_reader_notes=True,
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
            has_reader_notes=True,
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
            has_reader_notes=True,
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


def test_plan_request_builds_asset_disambiguation_state_for_ambiguous_external_assets() -> None:
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

    assert snapshot.clarification_only is True
    assert snapshot.asset_disambiguation_state is not None
    assert snapshot.asset_disambiguation_state.required is True
    assert len(snapshot.asset_disambiguation_state.candidates) == 2
    assert snapshot.trace_summary.used_hitp_asset_disambiguation is True


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
        asset_disambiguation=ReaderAskAssetDisambiguation(
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
    assert any(item.reason == "asset_disambiguation_candidate" for item in evidence)


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
            has_reader_notes=True,
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
                content_summary="解释概念的制度语境。",
                reason="structured_asset_resolved",
            )
        ],
    )

    assert len(context.external_asset_contexts) == 1
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
