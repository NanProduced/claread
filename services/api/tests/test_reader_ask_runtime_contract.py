"""Tests for reader_ask runtime_contract history handling."""

from __future__ import annotations

from uuid import uuid4

from app.services.reader_ask.runtime_contract import (
    ReaderAskAnswerRuntimeInput,
    build_prompt_payload,
    build_structured_history_summary,
    format_structured_history_summary,
)
from app.services.reader_ask import planner as planner_svc
from app.services.reader_ask.planner_runtime import planner_history_messages
from app.schemas.reader_ask import (
    ReaderAskPageIdentity,
)


def _make_history_message(
    role: str = "user",
    content_md: str = "test",
    resolved_intent: str | None = None,
    context_plan: dict | None = None,
    context_anchors: list | None = None,
    disambiguation: dict | None = None,
) -> dict:
    msg: dict = {"role": role, "content_md": content_md}
    if resolved_intent:
        msg["resolved_intent"] = resolved_intent
    if context_plan:
        msg["context_plan"] = context_plan
    if context_anchors:
        msg["context_anchors"] = context_anchors
    if disambiguation:
        msg["disambiguation"] = disambiguation
    return msg


# ---------------------------------------------------------------------------
# build_structured_history_summary
# ---------------------------------------------------------------------------


class TestBuildStructuredHistorySummary:
    def test_no_messages_outside_window_returns_none(self) -> None:
        """When all messages fit in the recent window, no summary needed."""
        messages = [_make_history_message(resolved_intent="explain") for _ in range(5)]
        result = build_structured_history_summary(messages, recent_window=8)
        assert result is None

    def test_exactly_at_window_boundary_returns_none(self) -> None:
        """When message count equals window size, no older messages exist."""
        messages = [_make_history_message(resolved_intent="explain") for _ in range(8)]
        result = build_structured_history_summary(messages, recent_window=8)
        assert result is None

    def test_extracts_prior_intents(self) -> None:
        """Intents from older messages are preserved in summary."""
        messages = [
            _make_history_message(resolved_intent="grammar"),
            _make_history_message(resolved_intent="explain"),
            # Recent window starts here
            _make_history_message(resolved_intent="vocabulary"),
            _make_history_message(resolved_intent="explain"),
        ]
        result = build_structured_history_summary(messages, recent_window=2)
        assert result is not None
        assert "prior_intents" in result
        assert "grammar" in result["prior_intents"]
        assert "explain" in result["prior_intents"]
        # Recent intents should NOT be in the summary
        assert "vocabulary" not in result["prior_intents"]

    def test_deduplicates_intents(self) -> None:
        """Same intent from multiple older messages appears only once."""
        messages = [
            _make_history_message(resolved_intent="explain"),
            _make_history_message(resolved_intent="explain"),
            _make_history_message(resolved_intent="explain"),
        ]
        result = build_structured_history_summary(messages, recent_window=1)
        assert result is not None
        assert result["prior_intents"] == ["explain"]

    def test_extracts_resolved_references_with_alias(self) -> None:
        """Resolved references include reference_query as alias for semantic value."""
        messages = [
            _make_history_message(
                resolved_intent="explain",
                context_plan={
                    "reference_resolution_status": "resolved",
                    "reference_query": "climate change",
                    "expanded_record_ids": ["rec-1", "rec-2"],
                },
            ),
            _make_history_message(resolved_intent="explain"),
        ]
        result = build_structured_history_summary(messages, recent_window=1)
        assert result is not None
        assert "prior_resolved_references" in result
        record_ids = [r["record_id"] for r in result["prior_resolved_references"]]
        assert "rec-1" in record_ids
        assert "rec-2" in record_ids
        # alias should be the reference_query, not bare record_id
        aliases = [r.get("alias") for r in result["prior_resolved_references"]]
        assert "climate change" in aliases

    def test_extracts_disambiguation_candidates_with_title(self) -> None:
        """Disambiguation candidates go into prior_disambiguation_candidates,
        NOT prior_resolved_references — they are unconfirmed."""
        messages = [
            _make_history_message(
                resolved_intent="explain",
                disambiguation={
                    "query": "AI article",
                    "candidates": [
                        {"record_id": "r-1", "title": "AI and the Future"},
                        {"record_id": "r-2", "title": "Climate Policy"},
                    ],
                },
            ),
            _make_history_message(resolved_intent="explain"),
        ]
        result = build_structured_history_summary(messages, recent_window=1)
        assert result is not None
        # Should be in disambiguation candidates, NOT resolved references
        assert "prior_disambiguation_candidates" in result
        assert "prior_resolved_references" not in result
        candidates = result["prior_disambiguation_candidates"]
        aliases = [c.get("alias") for c in candidates]
        assert "AI and the Future" in aliases
        assert "Climate Policy" in aliases

    def test_extracts_anchor_summaries(self) -> None:
        """Anchor selected_text from older messages is preserved."""
        messages = [
            _make_history_message(
                resolved_intent="grammar",
                context_anchors=[
                    {"selected_text": "The quick brown fox", "anchor_type": "sentence"},
                ],
            ),
            _make_history_message(resolved_intent="explain"),
        ]
        result = build_structured_history_summary(messages, recent_window=1)
        assert result is not None
        assert "prior_anchors" in result
        assert result["prior_anchors"][0]["selected_text"] == "The quick brown fox"
        assert result["prior_anchors"][0]["anchor_type"] == "sentence"

    def test_skips_assistant_messages(self) -> None:
        """Only user messages are scanned for structured state."""
        messages = [
            {"role": "user", "content_md": "q1", "resolved_intent": "explain"},
            {"role": "assistant", "content_md": "a1", "resolved_intent": "explain"},
            {"role": "user", "content_md": "q2", "resolved_intent": "grammar"},
        ]
        result = build_structured_history_summary(messages, recent_window=1)
        assert result is not None
        assert "explain" in result["prior_intents"]
        assert "grammar" not in result["prior_intents"]

    def test_no_extractable_state_returns_none(self) -> None:
        """Messages without intents, refs, or anchors produce no summary."""
        messages = [
            {"role": "user", "content_md": "q1"},
            {"role": "assistant", "content_md": "a1"},
            {"role": "user", "content_md": "q2"},
        ]
        result = build_structured_history_summary(messages, recent_window=1)
        assert result is None

    def test_consecutive_grammar_followups_preserve_intent(self) -> None:
        """When user asks consecutive grammar questions, the prior grammar
        intent is preserved in the summary even after truncation."""
        messages = [
            _make_history_message(resolved_intent="grammar", content_md="What tense is this?"),
            {"role": "assistant", "content_md": "It's present perfect."},
            _make_history_message(resolved_intent="grammar", content_md="And this next sentence?"),
            {"role": "assistant", "content_md": "That's past simple."},
            _make_history_message(resolved_intent="grammar", content_md="What about the last one?"),
            {"role": "assistant", "content_md": "Present continuous."},
            # Recent window starts here
            _make_history_message(resolved_intent="grammar", content_md="How about this verb?"),
            {"role": "assistant", "content_md": "Future perfect."},
            _make_history_message(resolved_intent="grammar", content_md="And this participle?"),
        ]
        result = build_structured_history_summary(messages, recent_window=4)
        assert result is not None
        assert "grammar" in result["prior_intents"]

    def test_cross_article_comparison_preserves_references_with_alias(self) -> None:
        """When user compares articles across turns, resolved references
        from older turns are preserved with semantic aliases."""
        messages = [
            _make_history_message(
                resolved_intent="explain",
                context_plan={
                    "reference_resolution_status": "resolved",
                    "reference_query": "climate article",
                    "expanded_record_ids": ["article-climate-change"],
                },
            ),
            {"role": "assistant", "content_md": "Climate article discusses..."},
            _make_history_message(
                resolved_intent="explain",
                context_plan={
                    "reference_resolution_status": "resolved",
                    "reference_query": "AI article",
                    "expanded_record_ids": ["article-ai-future"],
                },
            ),
            {"role": "assistant", "content_md": "AI article discusses..."},
            # Recent window starts here
            _make_history_message(resolved_intent="explain", content_md="Compare them both"),
        ]
        result = build_structured_history_summary(messages, recent_window=2)
        assert result is not None
        refs = result["prior_resolved_references"]
        aliases = [r.get("alias") for r in refs]
        assert "climate article" in aliases
        assert "AI article" in aliases

    def test_capped_intents(self) -> None:
        """Intents are capped at max_intents."""
        messages = [
            _make_history_message(resolved_intent=f"intent-{i}")
            for i in range(10)
        ]
        result = build_structured_history_summary(messages, recent_window=1, max_intents=3)
        assert result is not None
        assert len(result["prior_intents"]) <= 3

    def test_capped_refs(self) -> None:
        """References are capped at max_refs."""
        messages = [
            _make_history_message(
                resolved_intent="explain",
                context_plan={
                    "reference_resolution_status": "resolved",
                    "reference_query": f"query-{i}",
                    "expanded_record_ids": [f"rec-{i}"],
                },
            )
            for i in range(10)
        ]
        result = build_structured_history_summary(messages, recent_window=1, max_refs=3)
        assert result is not None
        assert len(result["prior_resolved_references"]) <= 3

    def test_capped_anchors(self) -> None:
        """Anchors are capped at max_anchors."""
        messages = [
            _make_history_message(
                resolved_intent="grammar",
                context_anchors=[
                    {"selected_text": f"text-{i}", "anchor_type": "sentence"},
                ],
            )
            for i in range(10)
        ]
        result = build_structured_history_summary(messages, recent_window=1, max_anchors=2)
        assert result is not None
        assert len(result["prior_anchors"]) <= 2


# ---------------------------------------------------------------------------
# format_structured_history_summary
# ---------------------------------------------------------------------------


class TestFormatStructuredHistorySummary:
    def test_formats_intents(self) -> None:
        summary = {"prior_intents": ["grammar", "explain"]}
        result = format_structured_history_summary(summary)
        assert "Previous intents: grammar, explain" in result
        assert result.startswith("[History summary]")

    def test_formats_references_with_alias(self) -> None:
        """References are formatted using alias, not bare record_id."""
        summary = {
            "prior_resolved_references": [
                {"record_id": "rec-1", "alias": "Climate Change Article"},
            ],
        }
        result = format_structured_history_summary(summary)
        assert "Climate Change Article" in result
        assert "Previously resolved references" in result

    def test_formats_anchors(self) -> None:
        summary = {
            "prior_anchors": [
                {"selected_text": "The fox jumps", "anchor_type": "sentence"},
            ],
        }
        result = format_structured_history_summary(summary)
        assert '"The fox jumps" (sentence)' in result
        assert "Previously discussed text" in result

    def test_formats_all_fields(self) -> None:
        summary = {
            "prior_intents": ["grammar"],
            "prior_resolved_references": [{"record_id": "r1", "alias": "AI Article"}],
            "prior_anchors": [{"selected_text": "hello", "anchor_type": "sentence"}],
        }
        result = format_structured_history_summary(summary)
        assert "Previous intents" in result
        assert "Previously resolved references" in result
        assert "AI Article" in result
        assert "Previously discussed text" in result
        assert " | " in result

    def test_max_chars_truncation(self) -> None:
        """Output is truncated when exceeding max_chars."""
        summary = {
            "prior_intents": ["grammar"] * 100,
        }
        result = format_structured_history_summary(summary, max_chars=100)
        assert len(result) <= 103  # 100 + "..."
        assert result.endswith("...")

    def test_falls_back_to_record_id_when_no_alias(self) -> None:
        """When alias is missing, record_id is used as fallback."""
        summary = {
            "prior_resolved_references": [
                {"record_id": "rec-abc-123"},
            ],
        }
        result = format_structured_history_summary(summary)
        assert "rec-abc-123" in result

    def test_formats_disambiguation_candidates_separately(self) -> None:
        """Disambiguation candidates use a different label than resolved refs."""
        summary = {
            "prior_resolved_references": [{"record_id": "r1", "alias": "Climate Article"}],
            "prior_disambiguation_candidates": [{"record_id": "r2", "alias": "AI Article"}],
        }
        result = format_structured_history_summary(summary)
        assert "Previously resolved references: Climate Article" in result
        assert "Previously suggested candidates (not confirmed): AI Article" in result


# ---------------------------------------------------------------------------
# Real payload tests: planner history and answer payload
# ---------------------------------------------------------------------------


class TestPlannerHistoryMessages:
    """Test that planner_history_messages correctly inserts
    the structured summary as a system message before recent raw turns."""

    def test_summary_prepended_to_planner_history(self) -> None:
        """When history exceeds the window, a system summary message is
        prepended to the planner history."""
        messages = [
            _make_history_message(
                resolved_intent="grammar",
                content_md="What tense?",
                context_anchors=[{"selected_text": "has been running", "anchor_type": "sentence"}],
            ),
            {"role": "assistant", "content_md": "Present perfect continuous."},
            _make_history_message(resolved_intent="explain", content_md="Why?"),
            {"role": "assistant", "content_md": "Because..."},
            # Recent window starts here
            _make_history_message(resolved_intent="grammar", content_md="And here?"),
            {"role": "assistant", "content_md": "Past simple."},
            _make_history_message(resolved_intent="explain", content_md="What about this?"),
            {"role": "assistant", "content_md": "It means..."},
            _make_history_message(resolved_intent="vocabulary", content_md="Define this word"),
        ]
        result = planner_history_messages(messages, max_messages=4)

        # First message should be system summary
        assert result[0]["role"] == "system"
        assert "[History summary]" in str(result[0]["content_md"])
        assert "grammar" in str(result[0]["content_md"])

        # Remaining messages should be user/assistant from recent window
        recent_roles = [m["role"] for m in result[1:]]
        assert "system" not in recent_roles
        assert all(r in {"user", "assistant"} for r in recent_roles)

    def test_no_summary_when_within_window(self) -> None:
        """When history fits within the window, no system summary is added."""
        messages = [
            _make_history_message(resolved_intent="explain", content_md="What?"),
            {"role": "assistant", "content_md": "It means..."},
        ]
        result = planner_history_messages(messages, max_messages=8)
        roles = [m["role"] for m in result]
        assert "system" not in roles


class TestAnswerPayloadHistory:
    """Test that build_prompt_payload correctly inserts structured history."""

    def _make_contract(
        self,
        history_messages: list[dict],
        max_history: int = 8,
    ) -> ReaderAskAnswerRuntimeInput:
        from app.services.reader_ask.service import _RecordBundle

        record = _RecordBundle(
            record_id=uuid4(),
            title="Test Article",
            source_text="Test source text content.",
            render_scene={},
            page_state_json={},
            workflow_version="1",
            schema_version="1",
        )
        return ReaderAskAnswerRuntimeInput(
            thread={"id": "t-1", "record_id": "r-1", "title": "Test"},
            record=record,
            user_message="test question",
            history_messages=history_messages,
            page_identity=ReaderAskPageIdentity(
                record_id="r-1",
                title="Test",
                available_context_capabilities=["record_context"],
                has_article_overview=True,
                has_sentence_entries=True,
                has_annotations=False,
                has_reader_notes=False,
            ),
            attachments=[],
            anchors=[],
            resolved_intent="explain",
            resolved_intent_label="Explain",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=None,
            planning_snapshot=None,
            max_history_messages=max_history,
            max_message_text=800,
        )

    def test_summary_in_answer_payload(self) -> None:
        """When history exceeds the window, the answer payload includes
        a system summary message at the start of history."""
        history = [
            _make_history_message(
                resolved_intent="grammar",
                context_plan={
                    "reference_resolution_status": "resolved",
                    "reference_query": "climate article",
                    "expanded_record_ids": ["rec-climate"],
                },
            ),
            {"role": "assistant", "content_md": "The climate article says..."},
            # Recent window starts here
            _make_history_message(resolved_intent="explain", content_md="Tell me more"),
            {"role": "assistant", "content_md": "More details..."},
        ]
        contract = self._make_contract(history, max_history=2)
        payload = build_prompt_payload(contract)

        history_list = payload.get("history", [])
        assert len(history_list) >= 1
        # First item should be system summary
        assert history_list[0]["role"] == "system"
        assert "[History summary]" in history_list[0]["content_md"]
        # "climate article" should appear as alias, not bare "rec-climate"
        assert "climate article" in history_list[0]["content_md"]

    def test_no_summary_in_answer_payload_when_within_window(self) -> None:
        """When history fits within the window, no system summary in payload."""
        history = [
            _make_history_message(resolved_intent="explain", content_md="What?"),
            {"role": "assistant", "content_md": "It means..."},
        ]
        contract = self._make_contract(history, max_history=8)
        payload = build_prompt_payload(contract)

        history_list = payload.get("history", [])
        roles = [m["role"] for m in history_list]
        assert "system" not in roles

    def test_recent_history_keeps_latest_user_resolved_intent(self) -> None:
        history = [
            _make_history_message(role="user", resolved_intent="grammar", content_md="What tense is this?"),
            {"role": "assistant", "content_md": "Present perfect."},
            _make_history_message(role="user", resolved_intent="general", content_md="Compare this with the translation"),
        ]
        contract = self._make_contract(history, max_history=3)
        contract.resolved_intent = "general"
        contract.entry_action = "ask_about_this"
        payload = build_prompt_payload(contract)

        history_list = payload.get("history", [])
        assert history_list[-1]["role"] == "user"
        assert history_list[-1]["resolved_intent"] == "general"
        # ask_about_this has no special entry_action_guidance
        assert payload["entry_action_guidance"] is None

    def test_assistant_history_truncation_keeps_head_and_tail(self) -> None:
        long_answer = "开头信息 " + ("中间内容" * 220) + " 结尾结论"
        history = [
            _make_history_message(role="user", resolved_intent="explain", content_md="Explain this"),
            {"role": "assistant", "content_md": long_answer},
        ]
        contract = self._make_contract(history, max_history=2)
        contract.max_message_text = 400
        payload = build_prompt_payload(contract)

        assistant_message = payload["history"][-1]
        assert assistant_message["role"] == "assistant"
        assert "开头信息" in assistant_message["content_md"]
        assert "结尾结论" in assistant_message["content_md"]
        assert "\n...\n" in assistant_message["content_md"]


class TestResolutionMetaObservationContract:
    """Phase 4 Round 2: resolution_meta is observation-only metadata.

    It must appear in planning_snapshot / eval trace, but NOT in the
    answer agent prompt payload (build_prompt_payload).
    """

    def _make_contract_with_reference_resolution(
        self,
        resolution_status: str = "resolved",
        resolution_meta: dict | None = None,
    ) -> ReaderAskAnswerRuntimeInput:
        from app.services.reader_ask.planner import ReaderAskReferenceResolution
        from app.services.reader_ask.service import _RecordBundle

        record = _RecordBundle(
            record_id=uuid4(),
            title="Test Article",
            source_text="Test source text content.",
            render_scene={},
            page_state_json={},
            workflow_version="1",
            schema_version="1",
        )
        ref_resolution = ReaderAskReferenceResolution(
            attempted=True,
            status=resolution_status,
            query="Climate Policy",
            reason="已命中历史文章\u201cClimate Policy\u201d。",
            resolved_records=[{"record_id": "r-2", "title": "Climate Policy"}] if resolution_status == "resolved" else [],
            ambiguous_records=[] if resolution_status == "resolved" else [{"record_id": "r-3", "title": "Climate Change"}],
            resolution_meta=resolution_meta or {
                planner_svc.RESOLUTION_META_STRATEGY: planner_svc.RESOLUTION_STRATEGY_TITLE_SEARCH,
                planner_svc.RESOLUTION_META_CANDIDATE_COUNT: 1,
                planner_svc.RESOLUTION_META_SCORED_CANDIDATE_COUNT: 1,
                planner_svc.RESOLUTION_META_TOP_SCORE: 100,
                planner_svc.RESOLUTION_META_RUNNER_UP_SCORE: None,
                planner_svc.RESOLUTION_META_FALLBACK_REASON: None,
            },
        )
        return ReaderAskAnswerRuntimeInput(
            thread={"id": "t-1", "record_id": "r-1", "title": "Test"},
            record=record,
            user_message="test question",
            history_messages=[],
            page_identity=ReaderAskPageIdentity(
                record_id="r-1",
                title="Test",
                available_context_capabilities=["record_context"],
                has_article_overview=True,
                has_sentence_entries=True,
                has_annotations=False,
                has_reader_notes=False,
            ),
            attachments=[],
            anchors=[],
            resolved_intent="explain",
            resolved_intent_label="Explain",
            entry_action="ask_about_this",
            submission_mode="chat",
            cross_record_context_allowed=False,
            resolved_context_input=None,
            quick_action_annotation=None,
            reference_resolution=ref_resolution,
            planning_snapshot=None,
            max_history_messages=8,
            max_message_text=800,
        )

    def test_prompt_payload_excludes_resolution_meta(self) -> None:
        """build_prompt_payload must NOT include resolution_meta in reference_resolution."""
        contract = self._make_contract_with_reference_resolution()
        payload = build_prompt_payload(contract)

        assert "reference_resolution" in payload
        ref_res = payload["reference_resolution"]
        # Standard fields present
        assert "status" in ref_res
        assert "query" in ref_res
        assert "reason" in ref_res
        assert "resolved_records" in ref_res
        assert "ambiguous_records" in ref_res
        # resolution_meta must NOT be in the prompt payload
        assert "resolution_meta" not in ref_res

    def test_prompt_payload_excludes_meta_for_ambiguous(self) -> None:
        """Even for ambiguous results, resolution_meta must not leak into prompt."""
        contract = self._make_contract_with_reference_resolution(
            resolution_status="ambiguous",
            resolution_meta={
                planner_svc.RESOLUTION_META_STRATEGY: planner_svc.RESOLUTION_STRATEGY_RECENT_FALLBACK,
                planner_svc.RESOLUTION_META_CANDIDATE_COUNT: 5,
                planner_svc.RESOLUTION_META_SCORED_CANDIDATE_COUNT: 2,
                planner_svc.RESOLUTION_META_TOP_SCORE: 55,
                planner_svc.RESOLUTION_META_RUNNER_UP_SCORE: 50,
                planner_svc.RESOLUTION_META_FALLBACK_REASON: planner_svc.RESOLUTION_FALLBACK_ILIKE_EMPTY,
            },
        )
        payload = build_prompt_payload(contract)
        assert "resolution_meta" not in payload["reference_resolution"]
