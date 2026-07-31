from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.agents.reader_ask_agent import ReaderAskRuntimeState
from app.config.settings import get_settings
from app.llm.types import RunModelSettings
from app.schemas.reader_ask import (
    ReaderAskActionProposal,
    ReaderAskMessage,
    ReaderAskMessageRetryRequest,
    ReaderAskReadingRecordAnchor,
    ReaderAskResolvedIntent,
    ReaderAskSupplementCandidate,
    ReaderRecordAskMessageRequest,
)
from app.services.ai_usage import (
    BILLING_MODE_USER_POINTS,
    CAPABILITY_READER_ASK,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_USER_BILLED,
    AIUsageEventCreate,
    build_model_metadata,
    build_reader_ask_billing_metadata,
    compute_reader_ask_cost_points,
    record_ai_usage_event,
)
from app.services.analysis.credit_service import (
    LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
    CreditReservation,
    check_quota,
    ensure_credit_account,
    refund_reserved_points,
    reserve_points,
)
from app.services.ask_runtime import record_context as rr_context_svc
from app.services.reader_ask import planner
from app.services.reader_ask import planner_runtime as planner_runtime_svc
from app.services.reader_ask import prompt_preparation as prompt_preparation_svc
from app.services.reader_ask import repository as repo
from app.services.reader_ask import runtime_contract as runtime_contract_svc
from app.services.reader_ask import stream_checkpoint as stream_checkpoint_svc
from app.services.reader_ask import stream_events as stream_events_svc
from app.services.reader_ask import supplements as supplements_svc
from app.services.reader_ask.agent_deps_factory import build_reader_ask_agent_deps
from app.services.reader_ask.agent_invocation import (
    ReaderAskStreamCompleted,
    ReaderAskStreamSseEvent,
    resolve_reader_ask_agent,
    stream_reader_ask_agent_run,
)
from app.services.reader_ask.service import (
    _SCHEMA_VERSION,
    _TASK_MODE_LABELS,
    _WORKFLOW_NAME,
    _WORKFLOW_VERSION,
    _assistant_message_metadata,
    _build_completed_payload,
    _build_evidence_items,
    _build_response_cards,
    _build_stream_checkpoint_output_json,
    _build_supplement_action_proposals,
    _build_user_visible_output,
    _merge_action_proposals,
    _merge_usage_summaries,
    _new_run_info,
    _next_run_info,
    _reader_ask_model_metadata,
    _resolve_reader_ask_model_option_or_422,
    _runtime_budget_kwargs,
    _settle_reader_ask_reservation,
    _tool_suggest_prompts,
    _upsert_eval_trace_record,
    _user_message_metadata,
    _vocabulary_item_to_citation,
)
from app.services.user_assets import vocabulary as vocabulary_svc


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _rr_unavailable_payload(*, summary: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "summary": summary,
        "next_actions": [],
        "artifacts": [],
        "ok": False,
        "reason": reason,
    }


async def _list_user_vocabulary(
    *,
    user_id: UUID,
    lemma: str | None,
    limit: int,
    sort_by: str,
) -> list[dict[str, Any]]:
    rows, _ = await vocabulary_svc.list_vocabulary(user_id, page=1, limit=max(limit, 10), lite=False)
    items = list(rows)
    if lemma:
        lowered = lemma.strip().lower()
        items = [
            item
            for item in items
            if lowered in str(item.get("lemma") or "").lower()
            or lowered in str(item.get("display_word") or "").lower()
        ]
    if sort_by == "lemma_asc":
        items.sort(key=lambda item: str(item.get("lemma") or "").lower())
    return items[:limit]


async def _resolve_thread_model_option(
    *,
    user_id: UUID,
    thread_id: UUID,
    thread: dict[str, Any],
    requested_key: str | None,
) -> tuple[dict[str, Any], Any]:
    selected_option = _resolve_reader_ask_model_option_or_422(
        selected_key=requested_key or cast(str | None, thread.get("selected_model_key")),
        strict=requested_key is not None,
    )
    if thread.get("selected_model_key") != selected_option.key:
        updated_thread = await repo.update_thread_selected_model(
            user_id,
            thread_id,
            selected_model_key=selected_option.key,
        )
        if updated_thread is not None:
            thread = updated_thread
    return thread, selected_option


def _rr_write_action_proposals(
    *,
    runtime_state: ReaderAskRuntimeState,
    reading_record_id: str,
    reading_record_anchor: ReaderAskReadingRecordAnchor | None,
) -> list[ReaderAskActionProposal]:
    proposals: list[ReaderAskActionProposal] = []
    for request in runtime_state.action_requests:
        payload_json = dict(request.payload_json)
        payload_json["record_id"] = reading_record_id
        if reading_record_anchor is not None:
            payload_json["anchor"] = reading_record_anchor.model_dump(mode="json")
            payload_json.pop("target_key", None)
            payload_json.pop("target_sentence_id", None)
        proposals.append(
            ReaderAskActionProposal(
                id=str(uuid4()),
                action_type=request.action_type,
                label=request.label,
                description=request.description,
                requires_confirmation=request.requires_confirmation,
                payload_json=payload_json,
            )
        )
    return proposals


def _rr_supplement_candidates(
    *,
    reading_record_anchor: ReaderAskReadingRecordAnchor | None,
    runtime_state: ReaderAskRuntimeState,
    assistant_content_md: str,
    created_from_turn_run_id: str,
) -> list[ReaderAskSupplementCandidate]:
    if reading_record_anchor is None:
        return []
    generated_grammar_note = next(
        (
            item
            for item in runtime_state.latest_generated_annotations
            if item.get("kind") == "grammar_note" and item.get("status") == "ready"
        ),
        None,
    )
    if generated_grammar_note is None:
        return []
    content = str(generated_grammar_note.get("content") or assistant_content_md).strip()
    if not content:
        return []
    candidate = supplements_svc.build_grammar_note_candidate(
        anchor=reading_record_anchor,
        assistant_content_md=content,
        created_from_turn_run_id=created_from_turn_run_id,
    )
    return [candidate] if candidate is not None else []


def _reading_record_anchor_from_local_context(message: ReaderAskMessage) -> ReaderAskReadingRecordAnchor | None:
    local_context = (
        message.resolved_context_input.current_record_context.local_context
        if message.resolved_context_input is not None
        and message.resolved_context_input.current_record_context is not None
        else None
    )
    if not isinstance(local_context, dict):
        return None
    payload = local_context.get("reading_record_anchor")
    if not isinstance(payload, dict):
        return None
    return ReaderAskReadingRecordAnchor.model_validate(payload)


async def _stream_rr_message(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread: dict[str, Any],
    request: ReaderRecordAskMessageRequest,
    history_messages: list[dict[str, Any]],
    existing_user_message: dict[str, Any] | None = None,
    existing_assistant_message: dict[str, Any] | None = None,
    run_info: dict[str, Any] | None = None,
    run_history: list[dict[str, Any]] | None = None,
    submission_hook: Any | None = None,
) -> AsyncIterator[str]:
    start_perf = perf_counter()
    runtime_state = ReaderAskRuntimeState(
        source_labels={
            "current_record",
            *({"current_anchor"} if request.anchor is not None else set()),
        }
    )
    reservation: CreditReservation | None = None
    user_message = existing_user_message
    assistant_message = existing_assistant_message
    active_turn_run_id: UUID | None = None
    final_content_md = ""
    nested_tool_usages: list[dict[str, Any]] = []
    usage_summary: dict[str, Any] | None = None
    selected_model_option = None
    stream_runtime = None
    # R6: track whether we already fired a real terminal so finally can
    # cancel only when the stream died without completed/failed.
    submission_terminal_status: str | None = None

    try:
        # R3 P2 — the legacy lane receives the SAME focus set as the
        # agentic lane (plural focus_anchors, or the singular legacy
        # anchor as fallback); build_reading_record_context re-gates
        # every anchor fail-closed against the live document.
        legacy_focus_anchors = rr_context_svc.resolve_focus_anchors(request)
        context = await rr_context_svc.build_reading_record_context(
            user_id=user_id,
            reading_record_id=reading_record_id,
            request_anchor=(
                legacy_focus_anchors[0] if legacy_focus_anchors else None
            ),
            entry_action=request.entry_action,
            focus_anchors=legacy_focus_anchors or None,
        )
        thread_id = _parse_uuid(thread["id"], "thread id is invalid")
        thread, selected_model_option = await _resolve_thread_model_option(
            user_id=user_id,
            thread_id=thread_id,
            thread=thread,
            requested_key=request.model,
        )
        runtime_budget_kwargs = _runtime_budget_kwargs(selected_model_option)
        submission_mode = planner_runtime_svc.submission_mode(
            entry_action=request.entry_action,
            attachments=[],
        )
        resolved_intent, _ = runtime_contract_svc.build_minimal_resolved_intent(request.entry_action)

        await ensure_credit_account(user_id)
        remaining = await check_quota(user_id)
        if remaining < selected_model_option.billing.reserved_points:
            submission_terminal_status = "failed"
            if submission_hook is not None:
                await submission_hook.mark("failed")  # may fail; finally retries
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.insufficient_credits_payload(
                    remaining,
                    required_points=selected_model_option.billing.reserved_points,
                ),
            )
            return

        reservation = await reserve_points(
            user_id,
            selected_model_option.billing.reserved_points,
            task_id=None,
            entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
            metadata={
                "capability_code": CAPABILITY_READER_ASK,
                "thread_id": str(thread_id),
                "reading_record_id": str(reading_record_id),
                **build_reader_ask_billing_metadata(None, selected_model_option.billing),
                **_reader_ask_model_metadata(selected_model_option),
                "user_message": request.content[:200],
            },
        )
        if reservation is None:
            remaining = await check_quota(user_id)
            submission_terminal_status = "failed"
            if submission_hook is not None:
                await submission_hook.mark("failed")  # may fail; finally retries
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.insufficient_credits_payload(
                    remaining,
                    required_points=selected_model_option.billing.reserved_points,
                ),
            )
            return

        if user_message is None:
            user_message = await repo.create_message(
                thread_id=thread_id,
                role="user",
                status="completed",
                content_md=request.content,
                context_anchors=[context.legacy_anchor.model_dump(mode="json")] if context.legacy_anchor else [],
                metadata=_user_message_metadata(
                    resolved_intent=resolved_intent,
                    resolved_context_input=context.resolved_context_input,
                    submission_mode=submission_mode,
                ),
            )
            run_info = _new_run_info(turn_id=user_message["id"])
            run_history = []
        else:
            run_info = run_info or _new_run_info(turn_id=user_message["id"])
            run_history = list(run_history or [])

        if assistant_message is None:
            assistant_message = await repo.create_message(
                thread_id=thread_id,
                role="assistant",
                status="streaming",
                content_md="",
                context_anchors=[context.legacy_anchor.model_dump(mode="json")] if context.legacy_anchor else [],
                metadata=_assistant_message_metadata(
                    resolved_intent=resolved_intent,
                    run_info=run_info,
                    run_history=run_history,
                    resolved_context_input=context.resolved_context_input,
                    submission_mode=submission_mode,
                ),
            )
        # Bind assistant id for CAS terminal updates once the pair exists.
        if (
            submission_hook is not None
            and submission_hook.assistant_message_id is None
            and assistant_message is not None
        ):
            try:
                submission_hook.assistant_message_id = UUID(
                    str(assistant_message["id"])
                )
            except (ValueError, TypeError, KeyError):
                pass

        turn_run = await repo.create_turn_run_for_reading_record(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            thread_id=thread_id,
            user_id=user_id,
            reading_record_id=reading_record_id,
            base_id=_parse_uuid(context.facts.build_result.base.base_id, "base id is invalid"),
            generation=context.facts.record.generation,
            turn_id=_parse_uuid(user_message["id"], "user message id is invalid"),
            run_attempt=int(run_info.get("run_attempt") or 1),
            supersedes_run_id=_parse_uuid(str(run_info["supersedes_run_id"]), "supersedes run id is invalid")
            if run_info.get("supersedes_run_id")
            else None,
            status="streaming",
            resolved_intent=resolved_intent,
        )
        active_turn_run_id = _parse_uuid(turn_run["id"], "turn run id is invalid")
        run_info = {
            "turn_id": user_message["id"],
            "run_id": turn_run["id"],
            "run_attempt": int(run_info.get("run_attempt") or 1),
            "supersedes_run_id": run_info.get("supersedes_run_id"),
        }
        assistant_message = await repo.update_message(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            status="streaming",
            content_md="",
            context_anchors=[context.legacy_anchor.model_dump(mode="json")] if context.legacy_anchor else [],
            citations=[],
            action_proposals=[],
            tool_trace=[],
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                run_info=run_info,
                run_history=run_history,
                resolved_context_input=context.resolved_context_input,
                submission_mode=submission_mode,
            ),
            usage_event_id=None,
            current_turn_run_id=active_turn_run_id,
        )

        yield stream_events_svc.encode_sse(
            stream_events_svc.EVENT_THREAD_READY,
            stream_events_svc.thread_ready_payload(str(thread_id), str(reading_record_id)),
        )
        yield stream_events_svc.encode_sse(
            stream_events_svc.EVENT_MESSAGE_STARTED,
            stream_events_svc.message_started_payload(assistant_message["id"], user_message["id"]),
        )

        query_seed = request.content[:80]
        reference_resolution = planner.ReaderAskReferenceResolution()
        context_plan = planner.build_context_plan(
            entry_action=request.entry_action,
            attachments=[],
            anchors=[context.legacy_anchor] if context.legacy_anchor else [],
            runtime_state=runtime_state,
            citations=runtime_state.citations,
            reference_resolution=reference_resolution,
            planning_snapshot=None,
        )
        trace_summary = planner.build_trace_summary(
            runtime_state=runtime_state,
            context_plan=context_plan,
            planning_snapshot=None,
            clarification_mode="none",
        )
        await _upsert_eval_trace_record(
            turn_run_id=active_turn_run_id,
            planning_snapshot=None,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
        )

        prompt_payload = runtime_contract_svc.build_prompt_payload(
            runtime_contract_svc.ReaderAskAnswerRuntimeInput(
                thread=thread,
                record=context.record,
                user_message=request.content,
                history_messages=history_messages,
                page_identity=context.page_identity,
                attachments=[],
                anchors=[context.legacy_anchor] if context.legacy_anchor else [],
                resolved_intent=resolved_intent,
                resolved_intent_label=_TASK_MODE_LABELS[resolved_intent],
                entry_action=request.entry_action,
                submission_mode=submission_mode,
                cross_record_context_allowed=False,
                resolved_context_input=context.resolved_context_input,
                quick_action_annotation=None,
                reference_resolution=reference_resolution,
                planning_snapshot=None,
                followup_hint=None,
                cross_record_intent_hint=None,
                external_attachment_hint=None,
                dictionary_anchor_hint=None,
                long_history_hint=None,
                max_history_messages=12,
                max_message_text=1200,
            )
        )
        prompt_payload, max_output_tokens, _compaction_audit, context_too_large = prompt_preparation_svc.prepare_prompt_payload(
            prompt_payload,
            max_input_tokens=runtime_budget_kwargs["max_input_tokens"],
            budget_buffer_tokens=runtime_budget_kwargs["prompt_buffer_tokens"],
            default_max_output_tokens=runtime_budget_kwargs["max_output_tokens"],
            min_max_output_tokens=256,
        )
        if context_too_large:
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.context_too_large_payload(),
            )
            return

        resolved = resolve_reader_ask_agent(selected_model_option.selection)
        model = resolved.model
        model_config = resolved.model_config
        route_settings = RunModelSettings(
            max_tokens=min(runtime_budget_kwargs["max_output_tokens"], max_output_tokens),
            temperature=0.3,
            timeout=90,
        )
        if model_config and model_config.model_settings is not None:
            route_settings = route_settings.merged_with(model_config.model_settings)

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        async def get_record_context_cb(
            _deps: Any = None,
            scope: str = "window",
            target_sentence_id: str | None = None,
        ) -> dict[str, Any]:
            return rr_context_svc.build_record_context_payload(
                context,
                scope=scope,
                target_sentence_id=target_sentence_id,
            )

        async def get_record_insights_cb(
            _deps: Any = None,
            target_sentence_id: str | None = None,
            kind: str | None = None,
            limit: int = 5,
        ) -> list[dict[str, Any]]:
            return rr_context_svc.collect_record_insights(
                context,
                target_sentence_id=target_sentence_id,
                kind=kind,
                limit=limit,
            )

        async def get_user_vocabulary_book_cb(
            _deps: Any = None,
            lemma: str | None = None,
            limit: int = 10,
            sort_by: str = "recent",
        ) -> list[dict[str, Any]]:
            return await _list_user_vocabulary(user_id=user_id, lemma=lemma, limit=limit, sort_by=sort_by)

        async def resolve_known_reference_cb(
            _deps: Any = None,
            query: str = "",
            top_k: int = 5,
        ) -> dict[str, Any]:
            return _rr_unavailable_payload(
                summary=f"resolve_known_reference is unavailable for this Reading Record Ask run ({top_k} requested).",
                reason="reading_record_cross_record_disabled",
            )

        async def load_explicit_attachment_context_cb(
            _deps: Any = None,
            record_id: str = "",
            asset_id: str | None = None,
        ) -> dict[str, Any]:
            _ = record_id, asset_id
            return _rr_unavailable_payload(
                summary="load_explicit_attachment_context is unavailable for this Reading Record Ask run.",
                reason="reading_record_external_attachment_disabled",
            )

        async def suggest_prompts_cb(
            suggestions: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return await _tool_suggest_prompts(suggestions)

        async def generate_sentence_annotation_cb(
            kind: str,
        ) -> dict[str, Any] | None:
            item = await rr_context_svc.generate_sentence_annotation(
                context,
                kind=cast(ReaderAskResolvedIntent, kind),
            )
            if item is not None and isinstance(item.get("usage_summary"), dict):
                nested_tool_usages.append(
                    {
                        "tool_name": "generate_sentence_annotation",
                        "usage_summary": item["usage_summary"],
                    }
                )
            return item

        deps = build_reader_ask_agent_deps(
            payload=prompt_payload,
            event_queue=event_queue,
            state=runtime_state,
            query_seed=query_seed,
            task_mode=resolved_intent,
            entry_action=request.entry_action,
            record_id=str(reading_record_id),
            record_title=context.record.title,
            primary_anchor=context.legacy_anchor,
            get_record_context_fn=get_record_context_cb,
            get_record_insights_fn=get_record_insights_cb,
            get_user_vocabulary_book_fn=get_user_vocabulary_book_cb,
            resolve_known_reference_fn=resolve_known_reference_cb,
            load_explicit_attachment_context_fn=load_explicit_attachment_context_cb,
            allowed_external_attachments=[],
            generate_sentence_annotation_fn=generate_sentence_annotation_cb,
            suggest_prompts_fn=suggest_prompts_cb,
            vocabulary_item_to_citation_fn=_vocabulary_item_to_citation,
        )
        checkpoint = stream_checkpoint_svc.TurnRunStreamCheckpoint(
            turn_run_id=active_turn_run_id,
            build_output_json=lambda content_md, reasoning_md, reasoning_status: _build_stream_checkpoint_output_json(
                content_md=content_md,
                reasoning_md=reasoning_md,
                reasoning_status=reasoning_status,
                submission_mode=submission_mode,
                resolved_intent=resolved_intent,
                record=context.record,
                anchors=[context.legacy_anchor] if context.legacy_anchor else [],
                attachments=[],
                runtime_state=runtime_state,
                reference_resolution=reference_resolution,
                disambiguation=None,
                external_asset_disambiguation=None,
                trace_summary=trace_summary,
                context_plan=context_plan,
                resolved_context_input=context.resolved_context_input,
                run_info=run_info,
                persisted_supplements=[],
            ),
            update_turn_run_cb=repo.update_turn_run,
        )
        runtime_state.run_started_at = datetime.now(UTC).isoformat()
        async for stream_item in stream_reader_ask_agent_run(
            agent=resolved.agent,
            deps=deps,
            model=model,
            route_settings=route_settings,
            assistant_message_id=assistant_message["id"],
            model_config=model_config,
            checkpoint_flush=stream_checkpoint_svc.make_checkpoint_flush(checkpoint),
        ):
            if isinstance(stream_item, ReaderAskStreamSseEvent):
                yield stream_item.encoded_sse
            elif isinstance(stream_item, ReaderAskStreamCompleted):
                final_content_md = stream_item.outcome.content_md
                usage_summary = stream_item.outcome.usage_summary
                stream_runtime = stream_item.stream_runtime

        runtime_proposals = _rr_write_action_proposals(
            runtime_state=runtime_state,
            reading_record_id=str(reading_record_id),
            reading_record_anchor=request.anchor,
        )
        supplement_candidates = _rr_supplement_candidates(
            reading_record_anchor=request.anchor,
            runtime_state=runtime_state,
            assistant_content_md=final_content_md,
            created_from_turn_run_id=str(run_info["run_id"]),
        )
        action_proposals = _merge_action_proposals(
            runtime_proposals,
            _build_supplement_action_proposals([item.model_dump(mode="json") for item in supplement_candidates]),
        )
        usage_summary = _merge_usage_summaries(usage_summary, nested_tool_usages)
        response_cards = _build_response_cards(
            task_mode=resolved_intent,
            record=context.record,
            anchors=[context.legacy_anchor] if context.legacy_anchor else [],
            runtime_state=runtime_state,
        )
        resolved_context = planner.build_resolved_context_summary(
            record_id=str(reading_record_id),
            record_title=context.record.title,
            anchors=[context.legacy_anchor] if context.legacy_anchor else [],
            explicit_attachment_count=0,
            runtime_state=runtime_state,
            used_cross_record_context=False,
            citations=runtime_state.citations,
        )
        trace_summary = trace_summary.model_copy(
            update={
                "supplement_generation_used": bool(supplement_candidates),
                "supplement_persisted_count": 0,
                "supplement_deleted_count": 0,
            }
        )
        computed_cost_points = compute_reader_ask_cost_points(
            usage_summary,
            selected_model_option.billing,
        )
        billed_points, under_collected_points = await _settle_reader_ask_reservation(
            user_id=user_id,
            reservation=reservation,
            actual_cost_points=computed_cost_points,
            metadata={
                "reason": "reader_record_ask_settlement",
                "thread_id": str(thread_id),
                "reading_record_id": str(reading_record_id),
                "computed_cost_points": computed_cost_points,
                **_reader_ask_model_metadata(selected_model_option),
            },
        )
        reservation = CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)
        usage_event_id = await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_USER_BILLED,
                capability_code=CAPABILITY_READER_ASK,
                billing_mode=BILLING_MODE_USER_POINTS,
                status=STATUS_SUCCEEDED,
                user_id=user_id,
                record_id=None,
                reading_record_id=reading_record_id,
                workflow_name=_WORKFLOW_NAME,
                workflow_version=_WORKFLOW_VERSION,
                schema_version=_SCHEMA_VERSION,
                prompt_version="reader_record_ask_v1",
                usage_data=usage_summary,
                latency_ms=int((perf_counter() - start_perf) * 1000),
                billed_points=billed_points,
                billing_policy_version=build_reader_ask_billing_metadata(
                    usage_summary,
                    selected_model_option.billing,
                ).get("billing_policy_version"),
                metadata_json={
                    "entrypoint": "/reader/records/{reading_record_id}/ask/messages",
                    "thread_id": str(thread_id),
                    "reading_record_id": str(reading_record_id),
                    "anchor_count": 1 if request.anchor is not None else 0,
                    "reservation_points": selected_model_option.billing.reserved_points,
                    "computed_cost_points": computed_cost_points,
                    "under_collected_points": under_collected_points,
                    **_reader_ask_model_metadata(selected_model_option),
                },
                **build_model_metadata(resolved.model_config),
            )
        )
        output = _build_user_visible_output(
            content_md=final_content_md,
            submission_mode=submission_mode,
            resolved_intent=resolved_intent,
            citations=runtime_state.citations,
            action_proposals=action_proposals,
            tool_trace=runtime_state.tool_trace,
            evidence=_build_evidence_items(
                attachments=[],
                citations=runtime_state.citations,
                current_record_id=str(reading_record_id),
                current_record_title=context.record.title,
                external_record_contexts=[],
                external_asset_contexts=[],
                reference_resolution=reference_resolution,
                supplement_candidates=supplement_candidates,
                disambiguation=None,
                external_asset_disambiguation=None,
            ),
            trace_summary=trace_summary,
            disambiguation=None,
            external_asset_disambiguation=None,
            response_cards=response_cards,
            usage_summary=usage_summary,
            billed_points=billed_points,
            resolved_context=resolved_context,
            context_plan=context_plan,
            resolved_context_input=context.resolved_context_input,
            run_info=run_info,
            supplement_candidates=supplement_candidates,
            persisted_supplements=[],
            reasoning_md=stream_runtime.emitted_reasoning if stream_runtime is not None else None,
            reasoning_status=stream_checkpoint_svc.terminal_reasoning_status(
                stream_runtime.reasoning_started if stream_runtime is not None else False
            ),
            follow_up_suggestions=runtime_state.latest_suggestions or None,
        )
        await repo.update_message(
            message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
            status="completed",
            content_md=final_content_md,
            context_anchors=[context.legacy_anchor.model_dump(mode="json")] if context.legacy_anchor else [],
            citations=[citation.model_dump(mode="json") for citation in runtime_state.citations],
            action_proposals=[proposal.model_dump(mode="json") for proposal in action_proposals],
            tool_trace=[entry.model_dump(mode="json") for entry in runtime_state.tool_trace],
            metadata=_assistant_message_metadata(
                resolved_intent=resolved_intent,
                run_info=run_info,
                run_history=run_history,
                resolved_context_input=context.resolved_context_input,
                submission_mode=submission_mode,
            ),
            usage_event_id=usage_event_id,
            current_turn_run_id=active_turn_run_id,
        )
        await repo.update_turn_run(
            turn_run_id=active_turn_run_id,
            status="completed",
            resolved_intent=resolved_intent,
            user_visible_output_json=output.model_dump(mode="json"),
            usage_summary_json=usage_summary,
            usage_event_id=usage_event_id,
            completed_at=datetime.now(UTC),
        )
        await _upsert_eval_trace_record(
            turn_run_id=active_turn_run_id,
            planning_snapshot=None,
            runtime_state=runtime_state,
            context_plan=context_plan,
            trace_summary=trace_summary,
            supplement_audit_json=[
                {
                    "event": "candidate_generated",
                    "supplement_type": item.supplement_type,
                    "candidate_id": item.candidate_id,
                    "created_from_turn_run_id": item.created_from_turn_run_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                for item in supplement_candidates
            ],
            billed_points=billed_points,
            usage_event_id=usage_event_id,
        )
        payload = _build_completed_payload(
            message_id=assistant_message["id"],
            thread_id=str(thread_id),
            output=output,
            usage_event_id=usage_event_id,
        )
        submission_terminal_status = "completed"
        if submission_hook is not None:
            # R7: remember real terminal first; mark may fail transiently.
            await submission_hook.mark("completed")
        yield stream_events_svc.encode_sse(
            stream_events_svc.EVENT_MESSAGE_COMPLETED,
            payload.model_dump(mode="json"),
        )
    except (asyncio.CancelledError, GeneratorExit):
        # Client/BFF disconnect — cancel only if no stronger terminal yet.
        if submission_hook is not None and submission_terminal_status is None:
            submission_terminal_status = "cancelled"
            try:
                await submission_hook.mark("cancelled")
            except Exception:  # noqa: BLE001
                pass
        raise
    except Exception as exc:
        if reservation is not None and reservation.total_points > 0:
            await refund_reserved_points(
                user_id,
                reservation,
                metadata={
                    "reason": "reader_record_ask_failed",
                    "thread_id": thread.get("id"),
                    "reading_record_id": str(reading_record_id),
                },
            )
        if assistant_message is not None:
            await repo.update_message(
                message_id=_parse_uuid(assistant_message["id"], "assistant message id is invalid"),
                status="failed",
                content_md=final_content_md,
                context_anchors=[],
                citations=[],
                action_proposals=[],
                tool_trace=[],
                metadata={},
                usage_event_id=None,
                current_turn_run_id=active_turn_run_id,
            )
        if active_turn_run_id is not None:
            await repo.update_turn_run(
                turn_run_id=active_turn_run_id,
                status="failed",
                failed_at=datetime.now(UTC),
            )
        submission_terminal_status = "failed"
        if submission_hook is not None:
            await submission_hook.mark("failed")
        if isinstance(exc, HTTPException):
            yield stream_events_svc.encode_sse(
                stream_events_svc.EVENT_ERROR,
                stream_events_svc.http_exception_payload(exc.status_code, exc.detail),
            )
            return
        detail = str(exc) if get_settings().app_env != "production" else "Ask Claread is temporarily unavailable."
        yield stream_events_svc.encode_sse(
            stream_events_svc.EVENT_ERROR,
            stream_events_svc.reader_ask_failed_payload(detail),
        )
    finally:
        # R7: compensate terminal sync. Prefer intended real terminal
        # (completed/failed/cancelled) over fabricating cancelled after
        # a failed first write. Never demote completed → cancelled.
        if submission_hook is not None:
            try:
                if submission_terminal_status is not None:
                    await submission_hook.ensure_synced(
                        fallback=submission_terminal_status,  # type: ignore[arg-type]
                    )
                elif not submission_hook.synced:
                    await submission_hook.ensure_synced(fallback="cancelled")
            except Exception:  # noqa: BLE001
                pass


async def stream_thread_message(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    request: ReaderRecordAskMessageRequest,
    existing_user_message: dict[str, Any] | None = None,
    existing_assistant_message: dict[str, Any] | None = None,
    client_submission_id: Any | None = None,
    claim_generation: int | None = None,
) -> AsyncIterator[str]:
    """Legacy RR stream. When pair was pre-created by R5/R6 gateway, pass it in.

    R6: ``client_submission_id`` / ``claim_generation`` drive an internal
    ``SubmissionTerminalHook`` so completed / failed / cancelled always
    sync the durable submission row (never parse public SSE text).
    """
    from app.services.reader_record_ask.submission_gateway import (
        SubmissionTerminalHook,
    )

    submission_hook: SubmissionTerminalHook | None = None
    if client_submission_id is not None:
        asst_id: UUID | None = None
        if existing_assistant_message is not None and existing_assistant_message.get(
            "id"
        ):
            try:
                asst_id = UUID(str(existing_assistant_message["id"]))
            except (ValueError, TypeError):
                asst_id = None
        submission_hook = SubmissionTerminalHook(
            thread_id=thread_id,
            client_submission_id=(
                client_submission_id
                if isinstance(client_submission_id, UUID)
                else UUID(str(client_submission_id))
            ),
            claim_generation=claim_generation,
            assistant_message_id=asst_id,
        )
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if thread.get("record_scope") != "reading_record":
        raise HTTPException(status_code=404, detail="Reader ask thread not found for this Reading Record")
    if thread.get("reading_record_id") != str(reading_record_id):
        raise HTTPException(status_code=404, detail="Reader ask thread not found for this Reading Record")
    history_messages = await repo.list_messages(thread_id, limit=100)
    async for chunk in _stream_rr_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread=thread,
        request=request,
        history_messages=history_messages,
        existing_user_message=existing_user_message,
        existing_assistant_message=existing_assistant_message,
        submission_hook=submission_hook,
    ):
        yield chunk


async def retry_thread_message(
    *,
    user_id: UUID,
    reading_record_id: UUID,
    thread_id: UUID,
    message_id: UUID,
    retry_body: ReaderAskMessageRetryRequest | None = None,
) -> AsyncIterator[str]:
    thread = await repo.get_thread(user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Reader ask thread not found")
    if thread.get("record_scope") != "reading_record":
        raise HTTPException(status_code=404, detail="Reader ask thread not found for this Reading Record")
    if thread.get("reading_record_id") != str(reading_record_id):
        raise HTTPException(status_code=404, detail="Reader ask thread not found for this Reading Record")

    assistant_message_dict = await repo.get_message(message_id)
    if assistant_message_dict is None or assistant_message_dict.get("thread_id") != str(thread_id):
        raise HTTPException(status_code=404, detail="Reader ask message not found")
    if assistant_message_dict.get("role") != "assistant":
        raise HTTPException(status_code=400, detail="Only assistant messages can be regenerated")

    messages = await repo.list_messages(thread_id, limit=100)
    assistant_index = next((index for index, item in enumerate(messages) if item["id"] == str(message_id)), -1)
    if assistant_index <= 0:
        raise HTTPException(status_code=400, detail="No user turn found for this assistant message")

    user_message_dict = None
    history_messages: list[dict[str, Any]] = []
    for index in range(assistant_index - 1, -1, -1):
        candidate = messages[index]
        if candidate["role"] == "user":
            user_message_dict = candidate
            history_messages = messages[:assistant_index]
            break
    if user_message_dict is None:
        raise HTTPException(status_code=400, detail="No user turn found for this assistant message")

    user_message_model = ReaderAskMessage.model_validate(user_message_dict)
    if user_message_model.resolved_context_input is None:
        raise HTTPException(status_code=400, detail="User turn is missing retry context")
    request = ReaderRecordAskMessageRequest(
        content=user_message_model.content_md,
        entry_action=user_message_model.resolved_context_input.entry_action,
        anchor=_reading_record_anchor_from_local_context(user_message_model),
        model=retry_body.model if retry_body is not None else None,
    )
    run_info, run_history = _next_run_info(assistant_message_dict)
    async for chunk in _stream_rr_message(
        user_id=user_id,
        reading_record_id=reading_record_id,
        thread=thread,
        request=request,
        history_messages=history_messages,
        existing_user_message=user_message_dict,
        existing_assistant_message=assistant_message_dict,
        run_info=run_info,
        run_history=run_history,
    ):
        yield chunk
