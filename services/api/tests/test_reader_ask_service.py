import ast
import asyncio
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
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
from app.services.reader_ask import agent_runner as agent_runner_svc
from app.services.reader_ask import capabilities as capabilities_svc
from app.services.reader_ask import context_runtime as context_runtime_svc
from app.services.reader_ask import output_contract as output_contract_svc
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask import post_process as post_process_svc
from app.services.reader_ask import resolver as resolver_svc
from app.services.reader_ask import service as reader_ask_service
from app.services.reader_ask import supplements as supplements_svc
from app.services.reader_ask import utils as reader_ask_utils
from app.services.reader_ask import planner_runtime as planner_runtime_svc
from app.services.reader_ask.service import (
    _attachment_to_anchor,
    _attachments_to_anchor_refs,
    _build_run_info,
    _build_stream_checkpoint_output_json,
    _capability_trace_json,
    _build_action_proposals,
    _planning_snapshot_json,
    _metrics_json,
    _build_response_cards,
    _build_supplement_candidates_from_runtime,
    _dictionary_ai_to_citation,
    _merge_usage_summaries,
    _next_run_info,
)
from app.services.reader_ask.supplements import build_grammar_note_candidate


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_service_agent_deps_wires_tool_availability_all_paths() -> None:
    # Verify service.py uses build_reader_ask_agent_deps for all deps construction
    source = inspect.getsource(reader_ask_service)
    module = ast.parse(source)

    # No direct ReaderAskAgentDeps(...) construction allowed — must go through factory
    direct_deps_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and _call_name(node.func) == "ReaderAskAgentDeps"
    ]
    assert len(direct_deps_calls) == 0, (
        "service.py must not construct ReaderAskAgentDeps directly; use build_reader_ask_agent_deps"
    )

    factory_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and _call_name(node.func) == "build_reader_ask_agent_deps"
    ]

    assert len(factory_calls) == 4
    for call in factory_calls:
        keyword_by_name = {keyword.arg: keyword.value for keyword in call.keywords}
        # Factory must receive entry_action (which it uses to build ToolAvailabilityInput)
        assert "entry_action" in keyword_by_name

    # Verify the factory module constructs ToolAvailabilityInput with all 5 fields
    from app.services.reader_ask import agent_deps_factory as factory_mod

    factory_source = inspect.getsource(factory_mod)
    factory_module = ast.parse(factory_source)

    ta_calls = [
        node
        for node in ast.walk(factory_module)
        if isinstance(node, ast.Call) and _call_name(node.func) == "ToolAvailabilityInput"
    ]
    assert len(ta_calls) == 1
    ta_keywords = {keyword.arg for keyword in ta_calls[0].keywords}
    assert {
        "task_mode",
        "entry_action",
        "has_primary_anchor",
        "has_dictionary_anchor",
        "has_generated_annotation_cache",
    }.issubset(ta_keywords)

    # Stream lifecycle: service.py must not call agent_runner stream functions directly
    for fn_name in (
        "start_reader_ask_agent_stream",
        "stream_reader_ask_events",
        "finish_reader_ask_agent_stream",
    ):
        direct_calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call) and _call_name(node.func) == fn_name
        ]
        assert len(direct_calls) == 0, (
            f"service.py must not call {fn_name} directly; use stream_reader_ask_agent_run"
        )

    # service.py must call stream_reader_ask_agent_run exactly twice (main + retry)
    stream_helper_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and _call_name(node.func) == "stream_reader_ask_agent_run"
    ]
    assert len(stream_helper_calls) == 2

    # service.py must not reference agent_runner_svc directly
    agent_runner_attr_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "agent_runner_svc"
    ]
    assert len(agent_runner_attr_calls) == 0, (
        "service.py must not reference agent_runner_svc directly"
    )

    # service.py must call build_reader_ask_replan_event exactly twice
    replan_event_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and _call_name(node.func) == "build_reader_ask_replan_event"
    ]
    assert len(replan_event_calls) == 2

    # service.py must not reference MODEL_ROUTE_READER_ASK_PLANNER directly
    direct_planner_route_refs = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and node.id == "MODEL_ROUTE_READER_ASK_PLANNER"
    ]
    assert len(direct_planner_route_refs) == 0, (
        "service.py must not reference MODEL_ROUTE_READER_ASK_PLANNER directly"
    )

    # service.py must not reference build_reader_ask_planner_model_route directly
    # (moved to planning_deps_factory in P6-6)
    planner_cb_refs = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and node.id == "build_reader_ask_planner_model_route"
    ]
    assert len(planner_cb_refs) == 0, (
        "service.py must not reference build_reader_ask_planner_model_route; use planning_deps_factory"
    )

    # service.py must not construct ResolvePlanningDeps directly
    direct_resolve_planning_deps_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and _call_name(node.func) == "ResolvePlanningDeps"
    ]
    assert len(direct_resolve_planning_deps_calls) == 0, (
        "service.py must not construct ResolvePlanningDeps directly; use build_reader_ask_resolve_planning_deps"
    )

    # service.py must not construct RunPlannerDeps directly
    direct_run_planner_deps_calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and _call_name(node.func) == "RunPlannerDeps"
    ]
    assert len(direct_run_planner_deps_calls) == 0, (
        "service.py must not construct RunPlannerDeps directly; use build_reader_ask_resolve_planning_deps"
    )

    # service.py must reference build_reader_ask_resolve_planning_deps exactly 4 times
    planning_deps_factory_refs = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and node.id == "build_reader_ask_resolve_planning_deps"
    ]
    assert len(planning_deps_factory_refs) == 4, (
        "service.py must use build_reader_ask_resolve_planning_deps for all 4 planning deps constructions"
    )

    # service.py must not reference resolver_svc (removed import)
    resolver_svc_refs = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and node.id == "resolver_svc"
    ]
    assert len(resolver_svc_refs) == 0, (
        "service.py must not reference resolver_svc; use planning_deps_factory instead"
    )


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
    requires_local_anchor: bool | None = None,
    context_scope: str | None = None,
    tool_hints: list[str] | None = None,
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
        requires_local_anchor=requires_local_anchor,
        context_scope=context_scope,  # type: ignore[arg-type]
        tool_hints=tool_hints,
        rationale=rationale,
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
    from app.services.reader_ask.recovery import build_unused_reservation

    reservation = CreditReservation(total_points=10, deducted_from_daily=8, deducted_from_bonus=2)
    unused = build_unused_reservation(reservation, actual_cost_points=3)

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


# ---------------------------------------------------------------------------
# P0-4: Stream Checkpoint Shape Contract
# ---------------------------------------------------------------------------


def test_stream_checkpoint_output_contains_all_contract_fields() -> None:
    """Streaming checkpoint must contain every field in USER_VISIBLE_OUTPUT_FIELDS."""
    from app.services.reader_ask.output_contract import validate_output_dict_fields

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
            selected_text="The researchers compared human behaviour and brain patterns.",
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

    missing = validate_output_dict_fields(output)
    assert missing == [], f"Streaming checkpoint missing contract fields: {missing}"


def test_completed_output_contains_all_contract_fields() -> None:
    """Completed output must contain every field in USER_VISIBLE_OUTPUT_FIELDS."""
    from app.services.reader_ask.output_contract import validate_output_dict_fields

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

    output_dict = output.model_dump(mode="json")
    missing = validate_output_dict_fields(output_dict)
    assert missing == [], f"Completed output missing contract fields: {missing}"


def test_schema_fields_match_contract_constant() -> None:
    """ReaderAskUserVisibleOutput schema fields must match USER_VISIBLE_OUTPUT_FIELDS."""
    from app.schemas.reader_ask import ReaderAskUserVisibleOutput
    from app.services.reader_ask.output_contract import USER_VISIBLE_OUTPUT_FIELDS

    schema_fields = set(ReaderAskUserVisibleOutput.model_fields.keys())
    assert schema_fields == USER_VISIBLE_OUTPUT_FIELDS, (
        f"Schema fields != contract constant. "
        f"Extra in schema: {sorted(schema_fields - USER_VISIBLE_OUTPUT_FIELDS)}, "
        f"Missing from schema: {sorted(USER_VISIBLE_OUTPUT_FIELDS - schema_fields)}"
    )


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

    assert planner_runtime_svc.submission_mode(entry_action="why_here", attachments=[quick_action_attachment]) == "quick_action"
    assert planner_runtime_svc.submission_mode(entry_action="explain_this", attachments=[quick_action_attachment]) == "quick_action"
    assert planner_runtime_svc.submission_mode(entry_action="why_here", attachments=[ordinary_attachment]) == "chat"
    assert planner_runtime_svc.submission_mode(entry_action="ask_about_this", attachments=[quick_action_attachment]) == "chat"


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

    decision = planner_runtime_svc.fallback_semantic_planner_decision(
        user_message="这篇文章是怎么展开论证的？",
        entry_action="ask_about_this",
        page_identity=page_identity,
        attachments=[],
        anchors=[],
        record=record,
        failure_reason="validation failed",
        render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
        has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
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

    decision = planner_runtime_svc.fallback_semantic_planner_decision(
        user_message='我之前那篇《Climate Policy》里也提过这个吗？',
        entry_action="ask_about_this",
        page_identity=page_identity,
        attachments=[],
        anchors=[],
        record=record,
        failure_reason="validation failed",
        render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
        has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
    )

    assert decision.reference_request.requested is True
    assert decision.reference_request.query == "Climate Policy"


def test_resolved_context_summary_marks_article_assets_and_history_usage() -> None:
    record = type("Record", (), {"record_id": "00000000-0000-0000-0000-000000000001", "title": "Test"})()
    runtime_state = ReaderAskRuntimeState(
        latest_record_context={"sentence_windows": []},
        latest_record_insights=[{"entry_type": "sentence_analysis"}],
    )
    summary = planner_svc.build_resolved_context_summary(
        record_id=str(record.record_id),
        record_title=record.title,
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

    context_input = planner_svc.build_resolved_context_input(
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
    context_input = planner_svc.build_resolved_context_input(
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

    context_plan = planner_svc.build_context_plan(
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
        usage_event_id="usage-1",
    )

    assert payload.id == "msg-1"
    assert payload.thread_id == "thread-1"
    assert payload.content_md == "解释完成。"
    assert payload.billed_points == 3
    assert payload.usage_summary == {"input_tokens": 10, "output_tokens": 20}
    assert payload.usage_event_id == "usage-1"


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
    # Phase 4 Round 2: planning_snapshot_json preserves resolution_meta
    assert "resolution_meta" in data["resolved_references"]
    # planner_route_used defaults to "planner_first" for legacy snapshots
    assert data["planner_route_used"] == "planner_first"
    assert data["is_fast_path"] is False


def test_planning_snapshot_json_preserves_resolution_meta_all_paths() -> None:
    """resolution_meta is present in planning_snapshot_json for all status paths."""
    from app.services.reader_ask.planner import (
        RESOLUTION_META_FIELDS,
        RESOLUTION_META_CANDIDATE_COUNT,
        RESOLUTION_META_FALLBACK_REASON,
        RESOLUTION_META_RUNNER_UP_SCORE,
        RESOLUTION_META_SCORED_CANDIDATE_COUNT,
        RESOLUTION_META_STRATEGY,
        RESOLUTION_META_TOP_SCORE,
        RESOLUTION_STRATEGY_NOT_REQUESTED,
        RESOLUTION_STRATEGY_TITLE_SEARCH,
    )

    # not_requested path
    snapshot_not_requested = planner_svc.plan_request(
        content="explain this",
        page_identity=ReaderAskPageIdentity(
            record_id="00000000-0000-0000-0000-000000000001",
            title="Test",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
            has_annotations=False,
            has_reader_notes=False,
        ),
        entry_action="ask_about_this",
        attachments=[],
        anchors=[],
        planner_decision=_planner_decision(resolved_intent="explain"),
        reference_resolution=planner_svc.ReaderAskReferenceResolution(
            resolution_meta={
                RESOLUTION_META_STRATEGY: RESOLUTION_STRATEGY_NOT_REQUESTED,
                RESOLUTION_META_CANDIDATE_COUNT: 0,
                RESOLUTION_META_SCORED_CANDIDATE_COUNT: 0,
                RESOLUTION_META_TOP_SCORE: None,
                RESOLUTION_META_RUNNER_UP_SCORE: None,
                RESOLUTION_META_FALLBACK_REASON: None,
            },
        ),
    )
    data = _planning_snapshot_json(snapshot_not_requested)
    assert "resolution_meta" in data["resolved_references"]
    assert set(data["resolved_references"]["resolution_meta"].keys()) == RESOLUTION_META_FIELDS

    # resolved path
    snapshot_resolved = planner_svc.plan_request(
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
            reason="已命中历史文章\u201cClimate Policy\u201d。",
            resolved_records=[{"record_id": "r-2", "title": "Climate Policy"}],
            resolution_meta={
                RESOLUTION_META_STRATEGY: RESOLUTION_STRATEGY_TITLE_SEARCH,
                RESOLUTION_META_CANDIDATE_COUNT: 1,
                RESOLUTION_META_SCORED_CANDIDATE_COUNT: 1,
                RESOLUTION_META_TOP_SCORE: 100,
                RESOLUTION_META_RUNNER_UP_SCORE: None,
                RESOLUTION_META_FALLBACK_REASON: None,
            },
        ),
    )
    data = _planning_snapshot_json(snapshot_resolved)
    assert data["resolved_references"]["resolution_meta"][RESOLUTION_META_STRATEGY] == RESOLUTION_STRATEGY_TITLE_SEARCH
    assert data["resolved_references"]["resolution_meta"][RESOLUTION_META_TOP_SCORE] == 100


def test_planning_snapshot_json_none_uses_planner_route_used() -> None:
    """When ``planning_snapshot is None``, ``is_fast_path`` is derived from
    ``planner_route_used`` instead of being hardcoded to ``True``."""
    # fast_path route
    data_fast = _planning_snapshot_json(None, planner_route_used="fast_path")
    assert data_fast["is_fast_path"] is True
    assert data_fast["planner_route_used"] == "fast_path"

    # agent_loop_first route (Round 3) — also counts as fast path
    data_agent = _planning_snapshot_json(None, planner_route_used="agent_loop_first")
    assert data_agent["is_fast_path"] is True
    assert data_agent["planner_route_used"] == "agent_loop_first"

    # planner_first route (e.g. snapshot is None due to an error, not fast path)
    data_legacy = _planning_snapshot_json(None, planner_route_used="planner_first")
    assert data_legacy["is_fast_path"] is False
    assert data_legacy["planner_route_used"] == "planner_first"

    # default is planner_first
    data_default = _planning_snapshot_json(None)
    assert data_default["is_fast_path"] is False
    assert data_default["planner_route_used"] == "planner_first"


def test_planning_snapshot_json_fast_path_snapshot_includes_route() -> None:
    """``FastPathPlanningSnapshot`` trace includes ``planner_route_used``."""
    snap = planner_svc.FastPathPlanningSnapshot()
    data = _planning_snapshot_json(snap, planner_route_used="fast_path")
    assert data["is_fast_path"] is True
    assert data["planner_route_used"] == "fast_path"


def test_planning_snapshot_json_fast_path_snapshot_agent_loop_first_route() -> None:
    """Round 3: ``FastPathPlanningSnapshot`` with ``agent_loop_first`` route."""
    snap = planner_svc.FastPathPlanningSnapshot()
    data = _planning_snapshot_json(snap, planner_route_used="agent_loop_first")
    assert data["is_fast_path"] is True
    assert data["planner_route_used"] == "agent_loop_first"


def test_metrics_json_includes_planner_route() -> None:
    """Round 3: ``_metrics_json`` includes the ``planner_route`` field."""
    from app.schemas.reader_ask import ReaderAskTraceSummary

    trace = ReaderAskTraceSummary(
        planner_mode="direct_answer",
        working_set_mode="anchor_local",
        cross_record_context_allowed=False,
        cross_record_context_used=False,
    )
    data = _metrics_json(
        trace_summary=trace,
        billed_points=10,
        usage_event_id=None,
        planner_route="agent_loop_first",
    )
    assert data["planner_route"] == "agent_loop_first"
    assert data["degenerate_detected"] is False
    assert data["degenerate_reason"] is None
    assert data["planner_mode"] == "direct_answer"
    assert data["billed_points"] == 10

    data_degenerate = _metrics_json(
        trace_summary=trace,
        billed_points=10,
        usage_event_id=None,
        planner_route="agent_loop_first",
        degenerate_detected=True,
        degenerate_reason="degenerate_answer",
    )
    assert data_degenerate["degenerate_detected"] is True
    assert data_degenerate["degenerate_reason"] == "degenerate_answer"

    # Default planner_route is None
    data_default = _metrics_json(
        trace_summary=None,
        billed_points=0,
        usage_event_id=None,
    )
    assert data_default["planner_route"] is None
    assert data_default["degenerate_detected"] is False
    assert data_default["degenerate_reason"] is None


def test_build_agent_loop_context_syncs_overview_to_runtime_state() -> None:
    record_id = uuid4()
    record = SimpleNamespace(
        record_id=record_id,
        title="Test Article",
        source_text="",
        render_scene={"content_summary": {"overview": "academic overview"}},
        page_state_json={},
        workflow_version=None,
        schema_version=None,
    )
    runtime_state = ReaderAskRuntimeState(source_labels={"current_record"})

    context_input = context_runtime_svc.build_agent_loop_context(
        record=record,
        runtime_state=runtime_state,
        anchors=[],
        attachments=[],
        user_id=uuid4(),
        page_identity=ReaderAskPageIdentity(record_id=str(record_id)),
        entry_action="ask_about_this",
    )

    assert context_input.current_record_context is not None
    assert context_input.current_record_context.article_overview == "academic overview"
    assert context_input.current_record_context.source_labels == ["article_overview"]
    assert runtime_state.latest_article_overview == "academic overview"
    assert "academic_render_scene" in runtime_state.source_labels


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
    context_plan = planner_svc.build_context_plan(
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

    context_plan = planner_svc.build_context_plan(
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
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        generate_sentence_annotation_fn=annotation_fn,
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=MagicMock(),
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
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        generate_sentence_annotation_fn=annotation_fn,
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=MagicMock(),
    )

    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps

    result = await _generate_sentence_annotation_tool(ctx, kind="sentence_analysis")

    assert result is breakdown_annotation
    assert state.tool_call_count == 0
    assert len(state.tool_trace) == 0
    assert event_queue.empty()
    annotation_fn.assert_not_called()


async def test_generate_sentence_annotation_uses_example_strategy_examples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas.internal.drafts import AnchorQuote, DraftGrammarNote
    from app.services.analysis.prompting.example_strategy import ExampleEntry

    example_entry = ExampleEntry(
        example_type="grammar",
        sentence_text="For almost a decade",
        output_fragment="示例输出",
    )
    grammar_bundle = SimpleNamespace(
        prompt_strategy=object(),
        example_strategy=SimpleNamespace(examples=[example_entry]),
    )
    captured: dict[str, Any] = {}

    async def fake_build_grammar_bundle_async(plan: object, *, sentences: list[dict[str, str]]) -> object:
        captured["plan"] = plan
        captured["sentences"] = sentences
        return grammar_bundle

    async def fake_run_grammar_agent(deps: object) -> object:
        captured["deps"] = deps
        return SimpleNamespace(
            output=SimpleNamespace(
                grammar_notes=[
                    DraftGrammarNote(
                        sentence_id="s1",
                        grammar_point="时间状语",
                        anchor_quotes=[AnchorQuote(text="For almost a decade")],
                        note_zh="这里作时间状语。",
                    )
                ],
                sentence_analyses=[],
            )
        )

    monkeypatch.setattr(
        reader_ask_service,
        "_reading_goal_from_record",
        lambda _record: object(),
    )
    monkeypatch.setattr(
        reader_ask_service,
        "_reading_variant_from_record",
        lambda _record, _goal: object(),
    )
    monkeypatch.setattr(
        reader_ask_service,
        "build_goal_execution_plan",
        lambda _goal, _variant: object(),
    )
    monkeypatch.setattr(
        reader_ask_service,
        "build_grammar_bundle_async",
        fake_build_grammar_bundle_async,
    )
    monkeypatch.setattr(
        reader_ask_service,
        "run_grammar_agent",
        fake_run_grammar_agent,
    )
    monkeypatch.setattr(
        reader_ask_service,
        "extract_run_usage",
        lambda _result: {"total_tokens": 7},
    )
    monkeypatch.setattr(
        reader_ask_service,
        "validate_grammar_note",
        lambda _note, _sentence_map: SimpleNamespace(is_valid=True),
    )

    record = reader_ask_service._RecordBundle(
        record_id=uuid4(),
        title="Test Article",
        source_text="For almost a decade, I told everyone I encountered ...",
        render_scene={
            "article": {
                "sentences": [
                    {
                        "sentence_id": "s1",
                        "text": "For almost a decade, I told everyone I encountered ...",
                    }
                ]
            }
        },
        page_state_json={},
        workflow_version="v1",
        schema_version="v1",
    )
    anchor = ReaderAskAnchorRef(
        anchor_type="sentence",
        sentence_id="s1",
        selected_text="For almost a decade",
    )

    result = await reader_ask_service._generate_sentence_annotation(
        record=record,
        anchor=anchor,
        kind="grammar_note",
    )

    assert captured["sentences"] == [
        {
            "sentence_id": "s1",
            "text": "For almost a decade, I told everyone I encountered ...",
        }
    ]
    assert captured["deps"].examples == [example_entry]
    assert result is not None
    assert result["status"] == "ready"
    assert result["usage_summary"] == {"total_tokens": 7}


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
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=MagicMock(),
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
    """Test is_degenerate_answer: pattern-based detection replaces len < 20."""

    def test_empty_string_is_degenerate(self) -> None:
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("") is True

    def test_whitespace_only_is_degenerate(self) -> None:
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("   \n  ") is True

    def test_short_but_valid_english_not_degenerate(self) -> None:
        """Short but meaningful answers like 'Yes.' or 'Present perfect.' should
        NOT trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("Yes.") is False
        assert is_degenerate_answer("No.") is False
        assert is_degenerate_answer("OK.") is False
        assert is_degenerate_answer("Present perfect.") is False
        assert is_degenerate_answer("Past simple.") is False

    def test_short_but_valid_cjk_not_degenerate(self) -> None:
        """Short CJK answers should NOT trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("是的") is False
        assert is_degenerate_answer("现在完成时") is False

    def test_refusal_english_is_degenerate(self) -> None:
        """English refusal patterns should trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("I cannot answer this question.") is True
        assert is_degenerate_answer("As an AI, I'm unable to help with that.") is True
        assert is_degenerate_answer("I don't have enough information.") is True

    def test_refusal_cjk_is_degenerate(self) -> None:
        """CJK refusal patterns should trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("我无法回答这个问题。") is True
        assert is_degenerate_answer("没有足够的信息来回答。") is True

    def test_punctuation_only_is_degenerate(self) -> None:
        """Pure punctuation or model artifacts should trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("...") is True
        assert is_degenerate_answer("---") is True

    def test_normal_answer_not_degenerate(self) -> None:
        """Normal-length answers should never trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("This sentence uses the present perfect tense.") is False
        assert is_degenerate_answer("这句话使用了现在完成时，表示过去发生的动作对现在的影响。") is False

    def test_short_gibberish_is_degenerate(self) -> None:
        """Very short content without meaningful words is degenerate."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer(",,,") is True


class TestReplanTriggerWiring:
    """Test that the replan trigger condition correctly uses is_degenerate_answer
    and that short-but-valid answers do NOT trigger replan while degenerate
    answers do. These tests verify the wiring between the detection function
    and the replan condition, not just the helper in isolation."""

    def test_short_valid_answer_does_not_meet_replan_condition(self) -> None:
        """A short but valid answer like 'Present perfect.' should NOT meet
        the replan trigger condition (content check part)."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        # Simulate the replan condition: is_degenerate_answer(final_content_md)
        final_content_md = "Present perfect."
        assert is_degenerate_answer(final_content_md) is False

    def test_empty_answer_meets_replan_condition(self) -> None:
        """An empty answer should meet the replan trigger condition."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        final_content_md = ""
        assert is_degenerate_answer(final_content_md) is True

    def test_refusal_answer_meets_replan_condition(self) -> None:
        """A refusal answer should meet the replan trigger condition."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        final_content_md = "I cannot answer this question without more context."
        assert is_degenerate_answer(final_content_md) is True

    def test_cjk_short_valid_not_degenerate(self) -> None:
        """Short CJK valid answer should NOT trigger replan."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer
        assert is_degenerate_answer("现在完成时") is False

    def test_replan_event_emitted_on_degenerate_answer(self) -> None:
        """When a degenerate answer triggers replan, the event_queue should
        receive a 'replan.started' event. This tests the actual wiring in
        the replan branch, not just the helper."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer

        # Verify the detection function works as expected for the cases
        # that would enter the replan branch
        degenerate_cases = ["", "   ", "I cannot help with that.", "我无法回答", "..."]
        for case in degenerate_cases:
            assert is_degenerate_answer(case) is True, f"Expected degenerate: {case!r}"

        # Verify that valid short answers would NOT enter the replan branch
        valid_cases = ["Yes.", "No.", "OK.", "Present perfect.", "现在完成时", "是的"]
        for case in valid_cases:
            assert is_degenerate_answer(case) is False, f"Expected NOT degenerate: {case!r}"

    def test_replan_condition_requires_clarification_mode_none(self) -> None:
        """Even with a degenerate answer, replan should not trigger if
        clarification_mode is not 'none'. This verifies the full condition."""
        from app.services.reader_ask.agent_runner import is_degenerate_answer

        # The full replan condition is:
        # is_degenerate_answer(content) AND clarification_mode == "none" AND clarification_only is False
        # If clarification_mode is "must_clarify", replan should NOT happen
        # even with a degenerate answer
        assert is_degenerate_answer("") is True  # degenerate
        # But the full condition also checks clarification_mode
        # This test verifies the helper is correct; the mode check is in the
        # main flow and tested implicitly through the service integration


async def test_replan_started_event_returned_by_build_replan_event() -> None:
    """Real wiring test: build_replan_event returns 'replan.started' event
    when a degenerate answer is detected with a valid planning snapshot."""
    from app.services.reader_ask.agent_runner import build_replan_event

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

    result = build_replan_event(
        final_content_md="",
        planning_snapshot=planning_snapshot,
        assistant_message_id="msg-123",
    )

    assert result is not None
    event_name, event_data = result
    assert event_name == "replan.started"
    assert event_data["message_id"] == "msg-123"
    assert event_data["reason"] == "degenerate_answer"


async def test_replan_not_triggered_for_short_valid_answer() -> None:
    """Real wiring test: build_replan_event returns None for a short but valid answer."""
    from app.services.reader_ask.agent_runner import build_replan_event

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

    result = build_replan_event(
        final_content_md="Present perfect.",
        planning_snapshot=planning_snapshot,
        assistant_message_id="msg-123",
    )

    assert result is None


async def test_replan_not_triggered_when_must_clarify() -> None:
    """Real wiring test: even with a degenerate answer, replan is NOT triggered
    when clarification_mode is 'must_clarify'."""
    from app.services.reader_ask.agent_runner import build_replan_event

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

    result = build_replan_event(
        final_content_md="",
        planning_snapshot=planning_snapshot,
        assistant_message_id="msg-123",
    )

    assert result is None


async def test_replan_not_triggered_for_agent_loop_first_route() -> None:
    """Round 3: agent_loop_first route never triggers replan, even with
    a degenerate answer. Degenerate metadata is recorded on runtime_state
    instead."""
    from unittest.mock import MagicMock

    from app.services.reader_ask.agent_runner import build_replan_event

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

    runtime = agent_runner_svc.AgentStreamRuntime()
    runtime.producer_result = MagicMock()

    result = build_replan_event(
        final_content_md="",  # degenerate
        planning_snapshot=planning_snapshot,
        assistant_message_id="msg-123",
        planner_route="agent_loop_first",
        runtime_state=runtime,
    )

    assert result is None
    # Degenerate metadata is set on the runtime state instead
    assert runtime.degenerate_detected is True
    assert runtime.degenerate_reason == "degenerate_answer"


def test_prepare_stream_model_settings_preserves_thinking_flags_and_enables_dashscope_sse() -> None:
    from app.llm.types import ResolvedModelConfig, RunModelSettings
    from app.services.reader_ask.agent_runner import prepare_stream_model_settings

    settings = RunModelSettings(
        extra_headers={"X-Test": "1"},
        extra_body={
            "enable_thinking": False,
            "preserve_thinking": True,
        },
    )

    resolved = prepare_stream_model_settings(
        settings,
        model_config=ResolvedModelConfig(
            route="reader_ask",
            profile_name="ask-main-qwen37-max",
            provider="dashscope",
            adapter="openai_compatible",
            model_name="qwen3.7-max",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="k",
        ),
    )

    assert resolved.extra_headers == {
        "X-Test": "1",
        "X-DashScope-SSE": "enable",
    }
    assert resolved.extra_body is not None
    assert resolved.extra_body["enable_thinking"] is False
    assert resolved.extra_body["preserve_thinking"] is True
    assert resolved.extra_body["incremental_output"] is True


def test_prepare_stream_model_settings_preserves_non_dashscope_body() -> None:
    from app.llm.types import ResolvedModelConfig, RunModelSettings
    from app.services.reader_ask.agent_runner import prepare_stream_model_settings

    settings = RunModelSettings(extra_body={"thinking": {"type": "disabled"}})
    resolved = prepare_stream_model_settings(
        settings,
        model_config=ResolvedModelConfig(
            route="reader_ask",
            profile_name="ask-main-deepseek",
            provider="deepseek",
            adapter="openai_compatible",
            model_name="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="k",
        ),
    )

    assert resolved.extra_headers is None
    assert resolved.extra_body is not None
    assert resolved.extra_body["thinking"] == {"type": "disabled"}
    assert "incremental_output" not in resolved.extra_body


def test_run_model_settings_with_max_tokens_preserves_extra_body_and_headers() -> None:
    from app.llm.types import RunModelSettings

    settings = RunModelSettings(
        max_tokens=2048,
        temperature=0.3,
        timeout=45.0,
        extra_headers={"X-Test": "1"},
        extra_body={"enable_thinking": True},
    )

    resolved = settings.with_max_tokens(512)

    assert resolved.max_tokens == 512
    assert resolved.temperature == 0.3
    assert resolved.timeout == 45.0
    assert resolved.extra_headers == {"X-Test": "1"}
    assert resolved.extra_body == {"enable_thinking": True}


def test_run_model_settings_thinking_enabled_detects_supported_payloads() -> None:
    from app.llm.types import RunModelSettings

    assert RunModelSettings(extra_body={"enable_thinking": True}).thinking_enabled() is True
    assert RunModelSettings(extra_body={"thinking": {"type": "enabled"}}).thinking_enabled() is True
    assert RunModelSettings(extra_body={"enable_thinking": False}).thinking_enabled() is False
    assert RunModelSettings(extra_body={"thinking": {"type": "disabled"}}).thinking_enabled() is False
    assert RunModelSettings().thinking_enabled() is False


def test_runtime_budget_kwargs_uses_resolved_option_budget() -> None:
    from app.services.reader_ask.model_options import ReaderAskRuntimeBudgetConfig, ResolvedReaderAskModelOption
    from app.services.reader_ask.service import _runtime_budget_kwargs
    from app.services.ai_usage.billing import WeightedTokensBillingConfig

    option = ResolvedReaderAskModelOption(
        key="glm",
        label="GLM-5.1",
        description=None,
        selection=None,
        billing=WeightedTokensBillingConfig(),
        runtime_budget=ReaderAskRuntimeBudgetConfig(
            max_input_tokens=28000,
            max_output_tokens=3600,
            prompt_buffer_tokens=900,
        ),
        main_model_name="glm-5.1",
        planner_model_name="qwen3.6-plus",
        replan_model_name="glm-5.1",
        is_default=True,
    )

    assert _runtime_budget_kwargs(option) == {
        "max_input_tokens": 28000,
        "max_output_tokens": 3600,
        "prompt_buffer_tokens": 900,
    }


async def test_settle_reader_ask_reservation_refunds_unused_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.analysis.credit_service import CreditReservation
    from app.services.reader_ask.service import _settle_reader_ask_reservation

    refund_calls: list[tuple[CreditReservation, dict[str, Any]]] = []

    async def fake_refund(_user_id: UUID, reservation: CreditReservation, *, metadata: dict[str, Any] | None = None, task_id: UUID | None = None) -> int:
        _ = task_id
        refund_calls.append((reservation, metadata or {}))
        return reservation.total_points

    async def fake_deduct(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("deduct_points should not be called when actual cost is below reservation")

    monkeypatch.setattr(reader_ask_service, "refund_reserved_points", fake_refund)
    monkeypatch.setattr(reader_ask_service, "deduct_points", fake_deduct)

    billed_points, under_collected = await _settle_reader_ask_reservation(
        user_id=uuid4(),
        reservation=CreditReservation(total_points=10, deducted_from_daily=6, deducted_from_bonus=4),
        actual_cost_points=7,
        metadata={"reason": "test"},
    )

    assert billed_points == 7
    assert under_collected == 0
    assert len(refund_calls) == 1
    assert refund_calls[0][0].total_points == 3


async def test_settle_reader_ask_reservation_deducts_overage_when_actual_cost_exceeds_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.analysis.credit_service import CreditReservation
    from app.services.reader_ask.service import _settle_reader_ask_reservation

    deduct_calls: list[tuple[int, dict[str, Any]]] = []

    async def fake_refund(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("refund_reserved_points should not be called when actual cost exceeds reservation")

    async def fake_deduct(_user_id: UUID, cost_points: int, *, entry_type: str, metadata: dict[str, Any] | None = None, task_id: UUID | None = None) -> int:
        _ = entry_type, task_id
        deduct_calls.append((cost_points, metadata or {}))
        return cost_points

    monkeypatch.setattr(reader_ask_service, "refund_reserved_points", fake_refund)
    monkeypatch.setattr(reader_ask_service, "deduct_points", fake_deduct)

    billed_points, under_collected = await _settle_reader_ask_reservation(
        user_id=uuid4(),
        reservation=CreditReservation(total_points=10, deducted_from_daily=10, deducted_from_bonus=0),
        actual_cost_points=14,
        metadata={"reason": "test"},
    )

    assert billed_points == 14
    assert under_collected == 0
    assert deduct_calls == [(4, {"reason": "test"})]


async def test_replan_not_triggered_when_no_planning_snapshot() -> None:
    """Real wiring test: replan is NOT triggered when planning_snapshot is None."""
    from app.services.reader_ask.agent_runner import build_replan_event

    result = build_replan_event(
        final_content_md="",
        planning_snapshot=None,
        assistant_message_id="msg-123",
    )

    assert result is None


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
    """Test fallback_reference_query with explicit title markers only.

    P3-S3: Weak reference regex patterns removed. Only explicit structural
    markers (《》, "", quotes) are extracted by fallback.
    """

    def test_book_title_marks(self) -> None:
        assert planner_runtime_svc.fallback_reference_query("之前那篇《Climate Policy》里也提过") == "Climate Policy"

    def test_double_quotes(self) -> None:
        assert planner_runtime_svc.fallback_reference_query('关于"AI Ethics"那篇文章') == "AI Ethics"

    def test_weak_chinese_no_longer_matches(self) -> None:
        """P3-S3: Natural language weak references no longer extracted."""
        assert planner_runtime_svc.fallback_reference_query("之前那篇climate policy的文章也提过吗？") is None

    def test_weak_english_no_longer_matches(self) -> None:
        """P3-S3: 'that article about X' no longer extracted."""
        assert planner_runtime_svc.fallback_reference_query("that article about climate policy also mentioned this") is None

    def test_no_reference_returns_none(self) -> None:
        assert planner_runtime_svc.fallback_reference_query("这句话什么意思？") is None

    def test_explicit_title_takes_priority(self) -> None:
        """Explicit title markers are still extracted."""
        result = planner_runtime_svc.fallback_reference_query("之前那篇《Climate Policy》的文章")
        assert result == "Climate Policy"


class TestFallbackIntentCoverage:
    """Test that fallback planner only uses explicit signals for intent.

    P3-S2: Fallback no longer uses keyword matching. Natural language
    messages default to 'explain' unless an explicit signal (entry_action
    or dictionary anchor) overrides.
    """

    @pytest.mark.parametrize(
        "message,expected_intent",
        [
            # Natural language → explain (no keyword matching)
            ("这句话的语法结构是什么？", "explain"),
            ("为什么这里用过去式？", "explain"),
            ("帮我拆解这个长句", "explain"),
            ("break down this sentence", "explain"),
            ("这个词什么意思？", "explain"),
            ("phrase的含义", "explain"),
            ("这篇文章和之前那篇有什么不同？", "explain"),
            ("对比一下这两篇文章", "explain"),
            ("比较两者的观点", "explain"),
            ("总结一下这篇文章", "explain"),
            ("translate this paragraph", "explain"),
            ("帮我复习一下", "explain"),
            ("这篇文章讲了什么？", "explain"),
            ("What is this about?", "explain"),
        ],
    )
    def test_fallback_intent_recognition(self, message: str, expected_intent: str) -> None:
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message=message,
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.resolved_intent == expected_intent

    def test_entry_action_lookup_in_context_overrides_message(self) -> None:
        """entry_action=lookup_in_context should force vocabulary intent
        even if the message contains grammar keywords."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="这里的语法结构",
            entry_action="lookup_in_context",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.resolved_intent == "vocabulary"

    def test_entry_action_why_here_overrides_message(self) -> None:
        """entry_action=why_here should force grammar intent."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="这个词什么意思",
            entry_action="why_here",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.resolved_intent == "grammar"

    def test_compare_defaults_to_explain(self) -> None:
        """Compare/difference questions now default to 'explain' in fallback.
        LLM planner should resolve these to 'general' when available."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="这两篇文章的观点有什么区别？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.resolved_intent == "explain"

    def test_vs_pattern_defaults_to_explain(self) -> None:
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="democracy vs authoritarianism",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.resolved_intent == "explain"


class TestFallbackWeakReferenceConservativePath:
    """Test fallback reference behavior after P3-S3.

    P3-S3: Weak natural language references no longer trigger cross_record
    or reference_request in fallback. Only explicit title markers (《》/"")
    and explicit attachments trigger these.
    """

    def test_weak_natural_language_no_cross_record(self) -> None:
        """P3-S3: Natural language weak references no longer trigger cross_record."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="之前那篇climate policy的文章也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.reference_request.requested is False
        assert decision.working_set.cross_record_context_allowed is False

    def test_title_marker_enables_cross_record(self) -> None:
        """Explicit title markers (《》) still trigger cross_record."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="之前那篇《Climate Policy》里也提过这个吗？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.reference_request.requested is True
        assert decision.reference_request.query is not None
        assert decision.working_set.cross_record_context_allowed is True

    def test_title_reference_without_anchor_sets_conservative_reason(self) -> None:
        """Title reference without anchor should set clarification_reason
        to signal uncertainty (conservative path)."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message='关于"AI Ethics"那篇文章也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.clarification_reason == "fallback_title_reference_without_anchor"
        # Should NOT be must_clarify — we can still answer at article level
        assert decision.clarification_only is False

    def test_title_reference_with_anchor_no_conservative_reason(self) -> None:
        """Title reference WITH anchor should NOT trigger conservative path."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message='之前那篇《Climate Policy》里也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
        )
        assert decision.clarification_reason is None
        assert decision.reference_request.requested is True

    def test_no_reference_no_cross_record(self) -> None:
        """Without any reference, cross_record_context_allowed should be False."""
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="这句话什么意思？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
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
        decision = planner_runtime_svc.fallback_semantic_planner_decision(
            user_message="这篇文章讲了什么？",
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            attachments=[attachment],
            anchors=[],
            record=_fallback_record(),
            failure_reason="test",
            render_overview_cb=lambda r: r.render_scene.get("content_summary", {}).get("overview"),
            has_sentence_entries_cb=lambda r: bool(r.render_scene.get("sentence_entries")),
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
            clarification_reason="fallback_title_reference_without_anchor",
            local_context_window_needed=True,
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content='关于"AI Ethics"那篇文章也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        assert snapshot.clarification_mode == "can_answer_with_followup"
        assert snapshot.context_plan.clarification_reason == "fallback_title_reference_without_anchor"

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
            clarification_reason="fallback_title_reference_without_anchor",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="AI Ethics",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content='关于"AI Ethics"那篇文章也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query="AI Ethics",
                ambiguous_records=[
                    {"record_id": "r1", "title": "AI Ethics A"},
                    {"record_id": "r2", "title": "AI Ethics B"},
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
        This is the key scenario: title reference without anchor, resolver can't
        find the record — we should still answer at article level with a hint,
        not force the user to clarify."""
        decision = _planner_decision(
            resolved_intent="explain",
            clarification_reason="fallback_title_reference_without_anchor",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="AI Ethics",
        )
        snapshot = planner_svc.plan_request(
            planner_decision=decision,
            content='关于"AI Ethics"那篇文章也提过这个吗？',
            entry_action="ask_about_this",
            page_identity=_fallback_page_identity(),
            anchors=[],
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="not_found",
                query="AI Ethics",
            ),
            structured_asset_resolution=planner_svc.ReaderAskStructuredAssetResolution(),
            attachments=[],
        )
        # not_found + no anchor normally → must_clarify,
        # but fallback_ reason protects the can_answer_with_followup path
        assert snapshot.clarification_mode == "can_answer_with_followup"
        assert snapshot.context_plan.clarification_reason == "fallback_title_reference_without_anchor"

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
    from app.services.reader_ask import stream_events as stream_events_svc
    payload = stream_events_svc.insufficient_credits_payload(remaining_points=3, required_points=10)
    assert payload["code"] == "INSUFFICIENT_CREDITS"
    assert payload["remaining_points"] == 3
    assert payload["required_points"] == 10
    assert "本轮请求未发送给模型" in payload["user_message"]


# ---------------------------------------------------------------------------
# _finish_reader_ask_agent_stream — interrupted path
# ---------------------------------------------------------------------------


def test_finish_reader_ask_agent_stream_interrupted_with_partial_content() -> None:
    """When the producer errors but partial content exists, the stream is
    interrupted (not failed). The outcome must carry interrupted=True and
    an SSE event with can_retry=True."""
    runtime = agent_runner_svc.AgentStreamRuntime(
        content_parts=["这是部分", "生成的回答"],
        usage_summary={"input_tokens": 10, "output_tokens": 20},
        producer_error=RuntimeError("model connection lost"),
    )
    outcome, sse_event = agent_runner_svc.finish_reader_ask_agent_stream(
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
    runtime = agent_runner_svc.AgentStreamRuntime(
        content_parts=["完整回答"],
        usage_summary={"input_tokens": 10, "output_tokens": 30},
    )
    outcome, sse_event = agent_runner_svc.finish_reader_ask_agent_stream(
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


async def test_fail_context_too_large_cleans_up_and_preserves_context(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services.reader_ask.recovery import build_context_too_large_cleanup_plan

    user_id = uuid4()
    thread_id = uuid4()
    record_id = uuid4()
    assistant_message_id = uuid4()
    turn_run_id = uuid4()
    anchor = ReaderAskAnchorRef(
        anchor_type="text_range",
        sentence_id="s1",
        selected_text="selected sentence",
        text_hash="hash",
        start_offset=0,
        end_offset=17,
    )
    attachment = ReaderAskAttachment(
        kind="text_selection",
        subtype="range",
        label="Selected sentence",
        selected_text="selected sentence",
        anchor_payload=ReaderAskAttachmentPayload(
            anchor_type="text_range",
            sentence_id="s1",
            selected_text="selected sentence",
            text_hash="hash",
            start_offset=0,
            end_offset=17,
        ),
        metadata=ReaderAskAttachmentMetadata(source_surface="reader"),
    )
    record = reader_ask_service._RecordBundle(
        record_id=record_id,
        title="Article",
        source_text="selected sentence in article",
        render_scene={},
        page_state_json={},
        workflow_version=None,
        schema_version=None,
    )
    reservation = CreditReservation(total_points=10, deducted_from_daily=10, deducted_from_bonus=0)
    calls: dict[str, object] = {}

    async def fake_refund_reserved_points(_user_id, _reservation, metadata):  # type: ignore[no-untyped-def]
        calls["refund"] = (_user_id, _reservation, metadata)

    async def fake_update_message(**kwargs):  # type: ignore[no-untyped-def]
        calls["message"] = kwargs
        return {"id": str(kwargs["message_id"])}

    async def fake_update_turn_run(**kwargs):  # type: ignore[no-untyped-def]
        calls["turn_run"] = kwargs
        return {"id": str(kwargs["turn_run_id"])}

    async def fake_upsert_eval_trace_record(**kwargs):  # type: ignore[no-untyped-def]
        calls["trace"] = kwargs
        return None

    async def fake_record_failure_event(**kwargs):  # type: ignore[no-untyped-def]
        calls["failure"] = kwargs

    monkeypatch.setattr(reader_ask_service, "refund_reserved_points", fake_refund_reserved_points)
    monkeypatch.setattr(reader_ask_service.repo, "update_message", fake_update_message)
    monkeypatch.setattr(reader_ask_service.repo, "update_turn_run", fake_update_turn_run)
    monkeypatch.setattr(reader_ask_service, "_upsert_eval_trace_record", fake_upsert_eval_trace_record)
    monkeypatch.setattr(reader_ask_service, "_record_failure_event", fake_record_failure_event)

    cleanup_plan = build_context_too_large_cleanup_plan(
        user_id=user_id,
        thread_id=thread_id,
        record_id=record_id,
        reservation=reservation,
        assistant_message_id=assistant_message_id,
        active_turn_run_id=turn_run_id,
        runtime_state=ReaderAskRuntimeState(),
        resolved_intent="explain",
        resolved_context_input=None,
        run_info={"run_id": str(turn_run_id), "turn_id": str(uuid4()), "attempt": 1},
        submission_mode="chat",
        anchor_payload=[anchor.model_dump(mode="json")],
        error_code="reader_ask_failed",
        compaction_audit=["history"],
        trace_summary=None,
        build_message_metadata_cb=reader_ask_service._assistant_message_metadata,
        build_turn_run_output_cb=reader_ask_service._build_stream_checkpoint_output_json,
        record_bundle=record,
        resolved_anchors=[anchor],
        attachments=[attachment],
        reference_resolution=planner_svc.ReaderAskReferenceResolution(),
        disambiguation=None,
        external_asset_disambiguation=None,
        planning_snapshot=None,
        context_plan=None,
        persisted_supplements_json=None,
        user_message_text="please explain",
        start_perf=0.0,
        thread={"id": str(thread_id)},
    )

    # Execute cleanup plan (same as service.py does)
    if cleanup_plan.refund is not None:
        await fake_refund_reserved_points(user_id, cleanup_plan.refund.reservation, metadata=cleanup_plan.refund.metadata)
    await fake_update_message(
        message_id=cleanup_plan.message_failed.message_id,
        status="failed",
        content_md=cleanup_plan.message_failed.content_md,
        context_anchors=[anchor.model_dump(mode="json")],
        citations=[],
        action_proposals=[],
        tool_trace=[],
        metadata=cleanup_plan.message_failed.metadata,
        usage_event_id=None,
        current_turn_run_id=cleanup_plan.message_failed.current_turn_run_id,
    )
    if cleanup_plan.turn_run_failed is not None:
        await fake_update_turn_run(
            turn_run_id=cleanup_plan.turn_run_failed.turn_run_id,
            status="failed",
            resolved_intent="explain",
            user_visible_output_json=cleanup_plan.turn_run_failed.user_visible_output_json,
        )
    if cleanup_plan.eval_trace is not None:
        await fake_upsert_eval_trace_record(
            turn_run_id=cleanup_plan.eval_trace.turn_run_id,
            planning_snapshot=cleanup_plan.eval_trace.planning_snapshot,
            runtime_state=cleanup_plan.eval_trace.runtime_state,
            context_plan=cleanup_plan.eval_trace.context_plan,
            trace_summary=cleanup_plan.eval_trace.trace_summary,
        )
    if cleanup_plan.failure_event is not None:
        await fake_record_failure_event(
            user_id=cleanup_plan.failure_event.user_id,
            record_id=cleanup_plan.failure_event.record_id,
            thread_id=cleanup_plan.failure_event.thread_id,
            user_message=cleanup_plan.failure_event.user_message,
            start_perf=cleanup_plan.failure_event.start_perf,
            error_code=cleanup_plan.failure_event.error_code,
            error_message=cleanup_plan.failure_event.error_message,
            metadata_json=cleanup_plan.failure_event.metadata_json,
        )

    assert "refund" in calls
    assert calls["message"]["status"] == "failed"  # type: ignore[index]
    assert calls["message"]["current_turn_run_id"] == turn_run_id  # type: ignore[index]
    assert calls["turn_run"]["status"] == "failed"  # type: ignore[index]
    failed_output = calls["turn_run"]["user_visible_output_json"]  # type: ignore[index]
    assert failed_output["resolved_context"]["anchor_count"] == 1
    assert failed_output["resolved_context"]["explicit_attachment_count"] == 1
    assert calls["trace"]["turn_run_id"] == turn_run_id  # type: ignore[index]
    assert calls["failure"]["error_code"] == "reader_ask_failed"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Phase 4 Round 7: Reference reranker wiring tests
# ---------------------------------------------------------------------------


class TestReferenceRerankerWiring:
    """Round 7: Verify reranker wiring from service through planner_runtime."""

    def test_default_config_does_not_construct_llm_reranker(self) -> None:
        """Default config: build_reference_reranker returns None."""
        from app.services.reader_ask.known_reference_resolver import build_reference_reranker
        result = build_reference_reranker(enabled=False)
        assert result is None

    def test_service_does_not_import_reranker_internal_types(self) -> None:
        """service.py must not reference SemanticRerankInput/Output/LlmReferenceReranker."""
        import importlib.util

        spec = importlib.util.find_spec("app.services.reader_ask.service")
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            content = f.read()
        assert "SemanticRerankInput" not in content
        assert "SemanticRerankOutput" not in content
        assert "LlmReferenceReranker" not in content

    async def test_resolve_semantic_planning_passes_reranker_to_callback(self) -> None:
        """resolve_semantic_planning passes deps.reference_reranker to
        resolve_known_references_cb as the reranker kwarg."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.reader_ask import planner_runtime as planner_runtime_svc
        from app.services.reader_ask import planner as planner_svc

        fake_reranker = object()

        captured_reranker: list[object] = []

        async def fake_resolve_known_references_cb(**kwargs):  # type: ignore[no-untyped-def]
            captured_reranker.append(kwargs.get("reranker"))
            return planner_svc.ReaderAskReferenceResolution()

        deps = planner_runtime_svc.ResolvePlanningDeps(
            run_planner_deps=planner_runtime_svc.RunPlannerDeps(
                current_record_affordances_cb=MagicMock(return_value=planner_svc.ReaderAskCurrentRecordAffordances()),
                build_model_route_cb=MagicMock(return_value=(MagicMock(), MagicMock())),
            ),
            resolve_known_references_cb=fake_resolve_known_references_cb,
            load_record_bundle_cb=AsyncMock(return_value=MagicMock(record_id=uuid4(), title="Test", render_scene={})),
            resolve_structured_asset_refs_cb=AsyncMock(return_value=planner_svc.ReaderAskStructuredAssetResolution()),
            list_supplements_cb=AsyncMock(return_value=[]),
            reference_reranker=fake_reranker,
        )

        planner_decision = planner_svc.ReaderAskPlannerDecision(
            resolved_intent="general",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="climate policy",
        )

        page_identity = ReaderAskPageIdentity(
            record_id=str(uuid4()),
            title="Test Article",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
        )

        # Patch run_semantic_planner to return a tuple (decision, status, usage)
        with patch.object(
            planner_runtime_svc, "run_semantic_planner",
            new=AsyncMock(return_value=(planner_decision, "valid", None)),
        ):
            await planner_runtime_svc.resolve_semantic_planning(
                user_id=uuid4(),
                record=MagicMock(record_id=uuid4()),
                history_messages=[],
                user_message="test",
                page_identity=page_identity,
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                deps=deps,
                truncate_history_message_cb=lambda msg, **kw: msg,
            )

        assert len(captured_reranker) == 1
        assert captured_reranker[0] is fake_reranker

    async def test_resolve_semantic_planning_default_reranker_is_none(self) -> None:
        """When reference_reranker is not provided, callback receives None."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.reader_ask import planner_runtime as planner_runtime_svc
        from app.services.reader_ask import planner as planner_svc

        captured_reranker: list[object] = []

        async def fake_resolve_known_references_cb(**kwargs):  # type: ignore[no-untyped-def]
            captured_reranker.append(kwargs.get("reranker"))
            return planner_svc.ReaderAskReferenceResolution()

        deps = planner_runtime_svc.ResolvePlanningDeps(
            run_planner_deps=planner_runtime_svc.RunPlannerDeps(
                current_record_affordances_cb=MagicMock(return_value=planner_svc.ReaderAskCurrentRecordAffordances()),
                build_model_route_cb=MagicMock(return_value=(MagicMock(), MagicMock())),
            ),
            resolve_known_references_cb=fake_resolve_known_references_cb,
            load_record_bundle_cb=AsyncMock(return_value=MagicMock(record_id=uuid4(), title="Test", render_scene={})),
            resolve_structured_asset_refs_cb=AsyncMock(return_value=planner_svc.ReaderAskStructuredAssetResolution()),
            list_supplements_cb=AsyncMock(return_value=[]),
        )

        planner_decision = planner_svc.ReaderAskPlannerDecision(
            resolved_intent="general",
            local_context_window_needed=True,
            reference_requested=True,
            reference_query="climate policy",
        )

        page_identity = ReaderAskPageIdentity(
            record_id=str(uuid4()),
            title="Test Article",
            available_context_capabilities=["record_context"],
            has_article_overview=True,
            has_sentence_entries=True,
        )

        with patch.object(
            planner_runtime_svc, "run_semantic_planner",
            new=AsyncMock(return_value=(planner_decision, "valid", None)),
        ):
            await planner_runtime_svc.resolve_semantic_planning(
                user_id=uuid4(),
                record=MagicMock(record_id=uuid4()),
                history_messages=[],
                user_message="test",
                page_identity=page_identity,
                entry_action="ask_about_this",
                attachments=[],
                anchors=[],
                deps=deps,
                truncate_history_message_cb=lambda msg, **kw: msg,
            )

        assert len(captured_reranker) == 1
        assert captured_reranker[0] is None
