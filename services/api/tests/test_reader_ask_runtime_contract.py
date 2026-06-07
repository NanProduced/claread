"""Tests for reader_ask runtime_contract history handling."""

from __future__ import annotations

from uuid import uuid4

from app.services.reader_ask.runtime_contract import (
    ReaderAskAnswerRuntimeInput,
    _compact_prompt_payload,
    _estimate_token_count,
    _layer_trim_external_assets,
    _layer_trim_history,
    _layer_trim_source_excerpt,
    _progressive_compact,
    build_prompt_payload,
    build_structured_history_summary,
    format_structured_history_summary,
    prepare_prompt_payload,
)
from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskEntryAction,
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
    """Test that _planner_history_messages (service.py) correctly inserts
    the structured summary as a system message before recent raw turns."""

    def test_summary_prepended_to_planner_history(self) -> None:
        """When history exceeds the window, a system summary message is
        prepended to the planner history."""
        from app.services.reader_ask.service import _planner_history_messages

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
        result = _planner_history_messages(messages, max_messages=4)

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
        from app.services.reader_ask.service import _planner_history_messages

        messages = [
            _make_history_message(resolved_intent="explain", content_md="What?"),
            {"role": "assistant", "content_md": "It means..."},
        ]
        result = _planner_history_messages(messages, max_messages=8)
        roles = [m["role"] for m in result]
        assert "system" not in roles


class TestAnswerPayloadHistory:
    """Test that build_prompt_payload correctly inserts the structured summary
    and that _compact_prompt_payload preserves it."""

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

    def test_compact_preserves_system_summary(self) -> None:
        """When _compact_prompt_payload truncates history, system messages
        are preserved and only user/assistant messages are truncated."""
        # Build a payload with a system summary + many conversation messages
        history = [
            {"role": "system", "content_md": "[History summary] Previous intents: grammar"},
        ] + [
            {"role": "user", "content_md": f"Question {i}"}
            for i in range(10)
        ]
        payload = {"history": history, "other_field": "value"}

        compact = _compact_prompt_payload(payload, max_history=4)

        result_history = compact["history"]
        # System message should be preserved
        system_msgs = [m for m in result_history if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "[History summary]" in system_msgs[0]["content_md"]

        # Only 4 most recent conversation messages should remain
        conv_msgs = [m for m in result_history if m.get("role") != "system"]
        assert len(conv_msgs) == 4
        # Should be the last 4 questions
        assert conv_msgs[-1]["content_md"] == "Question 9"


# ---------------------------------------------------------------------------
# Progressive compaction tests
# ---------------------------------------------------------------------------


class TestProgressiveCompaction:
    """Test that _progressive_compact applies layers in priority order
    and stops as soon as the budget is met."""

    def _make_large_payload(self) -> dict[str, Any]:
        """Create a payload that exceeds the typical 16000 token budget."""
        return {
            "external_asset_contexts": [
                {"content_md": "External asset " + "x" * 2000} for _ in range(5)
            ],
            "record_assets": [
                {"title": f"Asset {i}", "content": "y" * 1500} for i in range(5)
            ],
            "vocabulary_items": [
                {"word": f"word{i}", "definition": "z" * 1000} for i in range(5)
            ],
            "record_insights": [
                {"insight": "insight " + "w" * 1000} for i in range(5)
            ],
            "history": [
                {"role": "system", "content_md": "[History summary] Previous intents: grammar"},
            ] + [
                {"role": "user", "content_md": f"Question {i} " + "a" * 500}
                for i in range(10)
            ],
            "record_context": {
                "sentence_windows": [
                    {"text": "Sentence " + "s" * 800} for _ in range(8)
                ],
                "source_excerpt": "Source text " + "t" * 20000,
            },
            "article_overview": "Overview " + "o" * 10000,
        }

    def test_payload_over_budget_triggers_compaction(self) -> None:
        """When payload exceeds budget, compaction reduces it to fit within budget."""
        payload = self._make_large_payload()
        original_tokens = _estimate_token_count(payload)
        assert original_tokens > 16000  # Verify test setup

        budget = 16000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)
        result_tokens = _estimate_token_count(result)
        # Must fit within budget — same estimation function used inside compact
        assert result_tokens <= budget, f"Result {result_tokens} exceeds budget {budget}"
        assert result_tokens < original_tokens

    def test_high_priority_preserved_longer_than_low_priority(self) -> None:
        """External assets (low priority) should be trimmed before source_excerpt
        (high priority). With a tight budget, external_assets get dropped
        entirely while source_excerpt still has content."""
        payload = self._make_large_payload()

        # Tight budget that requires multiple layers
        budget = 10000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        # External assets should be reduced or gone (low priority)
        ext_assets = result.get("external_asset_contexts", [])
        original_ext = len(payload["external_asset_contexts"])
        assert len(ext_assets) < original_ext, "External assets should be trimmed before high-priority fields"

        # Source excerpt should still exist (high priority)
        assert result.get("record_context", {}).get("source_excerpt") is not None

    def test_system_summary_not_dropped_before_conversation(self) -> None:
        """System summary messages should be preserved when trimming history.
        Only conversation messages should be truncated."""
        payload = self._make_large_payload()
        budget = 10000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        history = result.get("history", [])
        system_msgs = [m for m in history if isinstance(m, dict) and m.get("role") == "system"]
        # System summary should still be present
        assert len(system_msgs) >= 1
        assert "[History summary]" in system_msgs[0]["content_md"]

    def test_compaction_stops_when_budget_met(self) -> None:
        """Progressive compaction should stop as soon as the budget is met,
        not apply all layers unnecessarily. High-priority fields that don't
        need trimming should remain intact."""
        payload = self._make_large_payload()

        # Moderate budget — should trim low-priority fields but preserve high-priority
        budget = 14000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        result_tokens = _estimate_token_count(result)
        # Must fit within budget
        assert result_tokens <= budget, f"Result {result_tokens} exceeds budget {budget}"

        # Article overview (highest priority) should still be substantial
        # since the budget is moderate and low-priority fields were trimmed first
        overview = result.get("article_overview", "")
        assert len(overview) > 0, "Article overview should not be empty with moderate budget"

    def test_multiple_layers_applied_for_tight_budget(self) -> None:
        """When budget is very tight, multiple layers should be applied
        progressively — verify by checking that low-priority fields are
        smaller than original while higher-priority fields still have content."""
        payload = self._make_large_payload()

        # Very tight budget
        budget = 6000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)
        result_tokens = _estimate_token_count(result)

        # Must fit within budget
        assert result_tokens <= budget, f"Result {result_tokens} exceeds budget {budget}"

        # External assets should be reduced (lowest priority)
        ext_assets = result.get("external_asset_contexts", [])
        assert len(ext_assets) < len(payload["external_asset_contexts"]), \
            "External assets should be trimmed"

        # Source excerpt should still exist (high priority)
        assert result.get("record_context", {}).get("source_excerpt") is not None

    def test_article_overview_preserved_as_long_as_possible(self) -> None:
        """Article overview is highest priority — it should be the last
        field to be compressed. With a moderate budget, it should still
        have substantial content."""
        payload = self._make_large_payload()

        # Moderate budget — should trim low-priority fields first
        budget = 12000
        result, _audit = _progressive_compact(payload, budget_tokens=budget)

        overview = result.get("article_overview", "")
        assert len(overview) > 0, "Article overview should still exist"

    def test_within_budget_payload_unchanged(self) -> None:
        """When payload is already within budget, no compaction occurs."""
        payload = {
            "history": [{"role": "user", "content_md": "Hello"}],
            "record_context": {"source_excerpt": "Short text"},
        }
        result, _audit = _progressive_compact(payload, budget_tokens=16000)
        assert result["history"] == payload["history"]
        assert result["record_context"] == payload["record_context"]


class TestPreparePromptPayloadCompaction:
    """Test that prepare_prompt_payload uses real budget for compaction
    and the final payload fits within the calculated input budget."""

    def test_prepare_prompt_payload_compacts_to_real_budget(self) -> None:
        """When payload exceeds the real input budget (derived from
        reserved_points), prepare_prompt_payload should compact it to fit
        within that budget."""
        # Build a large payload that exceeds the budget
        large_payload = {
            "external_asset_contexts": [
                {"content_md": "x" * 4000} for _ in range(5)
            ],
            "record_assets": [{"title": f"A{i}", "content": "y" * 3000} for i in range(5)],
            "vocabulary_items": [{"word": f"w{i}", "definition": "z" * 2000} for i in range(5)],
            "record_insights": [{"insight": "i" * 2000} for _ in range(5)],
            "history": [{"role": "user", "content_md": "q" * 1000} for _ in range(10)],
            "record_context": {
                "sentence_windows": [{"text": "s" * 1500} for _ in range(8)],
                "source_excerpt": "t" * 40000,
            },
            "article_overview": "o" * 20000,
        }

        original_tokens = _estimate_token_count(large_payload)
        assert original_tokens > 30000  # Verify test setup

        # Use budget parameters that create a tight input budget
        # Total budget = 100 * 200 = 20000
        # Input budget = 20000 - 1000 - 1024*3 = 15928
        reserved_points = 100
        tokens_per_point = 200
        multiplier_output = 3
        budget_buffer_tokens = 1000
        min_max_output_tokens = 1024
        max_input_budget = (
            reserved_points * tokens_per_point
            - budget_buffer_tokens
            - min_max_output_tokens * multiplier_output
        )
        assert max_input_budget == 15928  # Verify expected budget

        result_payload, output_tokens, compaction_audit, context_too_large = prepare_prompt_payload(
            large_payload,
            reserved_points=reserved_points,
            tokens_per_point=tokens_per_point,
            multiplier_output=multiplier_output,
            budget_buffer_tokens=budget_buffer_tokens,
            default_max_output_tokens=4096,
            min_max_output_tokens=min_max_output_tokens,
        )

        result_tokens = _estimate_token_count(result_payload)
        # Core assertion: result must fit within the real input budget
        assert result_tokens <= max_input_budget, (
            f"Result {result_tokens} exceeds real input budget {max_input_budget}"
        )
        assert result_tokens < original_tokens, "Payload should have been compacted"
        assert output_tokens >= 1024
        # Compaction was applied, so audit should be non-empty
        assert len(compaction_audit) > 0
        assert context_too_large is False

    def test_prepare_prompt_payload_no_compaction_when_within_budget(self) -> None:
        """When payload fits within the real input budget, no compaction occurs."""
        small_payload = {
            "history": [{"role": "user", "content_md": "Hello"}],
            "record_context": {"source_excerpt": "Short text"},
        }

        result_payload, output_tokens, compaction_audit, context_too_large = prepare_prompt_payload(
            small_payload,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        # Should be unchanged
        assert result_payload["history"] == small_payload["history"]
        assert result_payload["record_context"] == small_payload["record_context"]
        assert compaction_audit == []
        assert context_too_large is False

    def test_prepare_prompt_payload_low_budget_no_floor_inflation(self) -> None:
        """When the real input budget is below 8000, max_input_budget must
        NOT be inflated by an artificial floor. The payload should be
        compacted to fit the real budget, not a padded one."""
        large_payload = {
            "external_asset_contexts": [
                {"content_md": "x" * 4000} for _ in range(3)
            ],
            "record_assets": [{"title": f"A{i}", "content": "y" * 3000} for i in range(3)],
            "history": [{"role": "user", "content_md": "q" * 1000} for _ in range(5)],
            "record_context": {
                "sentence_windows": [{"text": "s" * 1500} for _ in range(4)],
                "source_excerpt": "t" * 20000,
            },
            "article_overview": "o" * 15000,
        }

        original_tokens = _estimate_token_count(large_payload)
        assert original_tokens > 15000  # Verify test setup

        # Budget parameters that produce a real input budget well below 8000
        # Total budget = 30 * 200 = 6000
        # Input budget = 6000 - 500 - 1024*3 = 2428
        reserved_points = 30
        tokens_per_point = 200
        multiplier_output = 3
        budget_buffer_tokens = 500
        min_max_output_tokens = 1024
        max_input_budget = (
            reserved_points * tokens_per_point
            - budget_buffer_tokens
            - min_max_output_tokens * multiplier_output
        )
        assert max_input_budget == 2428  # Well below 8000
        assert max_input_budget < 8000  # Verify this tests the floor-removal

        result_payload, output_tokens, compaction_audit, context_too_large = prepare_prompt_payload(
            large_payload,
            reserved_points=reserved_points,
            tokens_per_point=tokens_per_point,
            multiplier_output=multiplier_output,
            budget_buffer_tokens=budget_buffer_tokens,
            default_max_output_tokens=4096,
            min_max_output_tokens=min_max_output_tokens,
        )

        result_tokens = _estimate_token_count(result_payload)
        # Core assertion: result must fit within the real (low) input budget,
        # NOT an inflated 8000 floor
        assert result_tokens <= max_input_budget, (
            f"Result {result_tokens} exceeds real input budget {max_input_budget} "
            f"(would have been inflated to 8000 with old floor)"
        )
        assert result_tokens < original_tokens
        assert len(compaction_audit) > 0
        assert context_too_large is False


class TestContextCompressionUxContract:
    """P0-6: Context Compression UX Contract.

    User sees "上下文压缩中" (not token budget / chunk details).
    Explicit attachments are never silently dropped.
    context_too_large triggers an actionable error.
    Compaction audit is recorded for eval trace / logs.
    """

    def test_compaction_audit_records_applied_layers(self) -> None:
        """When compaction is applied, the audit list contains the layer names
        that were actually applied (for eval trace / logs)."""
        large_payload = {
            "external_asset_contexts": [
                {"content_md": "x" * 4000} for _ in range(5)
            ],
            "record_assets": [{"title": f"A{i}", "content": "y" * 3000} for i in range(5)],
            "vocabulary_items": [{"word": f"w{i}", "definition": "z" * 2000} for i in range(5)],
            "record_insights": [{"insight": "i" * 2000} for _ in range(5)],
            "history": [{"role": "user", "content_md": "q" * 1000} for _ in range(10)],
            "record_context": {
                "sentence_windows": [{"text": "s" * 1500} for _ in range(8)],
                "source_excerpt": "t" * 40000,
            },
            "article_overview": "o" * 20000,
        }

        _, _, compaction_audit, context_too_large = prepare_prompt_payload(
            large_payload,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        # Audit must be a non-empty list of strings
        assert isinstance(compaction_audit, list)
        assert len(compaction_audit) > 0
        assert all(isinstance(name, str) for name in compaction_audit)
        # Layer names should come from known compression layers
        known_layers = {
            "external_assets", "record_assets", "vocabulary", "insights",
            "history", "sentence_windows", "source_excerpt", "article_overview",
            "external_assets_drop", "record_assets_drop", "vocabulary_drop",
            "insights_drop", "history_aggressive", "sentence_windows_drop",
            "source_excerpt_aggressive", "article_overview_aggressive",
        }
        for name in compaction_audit:
            assert name in known_layers, f"Unknown compaction layer: {name}"
        assert context_too_large is False

    def test_no_compaction_audit_when_within_budget(self) -> None:
        """When no compaction is needed, audit is empty."""
        small_payload = {
            "history": [{"role": "user", "content_md": "Hi"}],
        }

        _, _, compaction_audit, context_too_large = prepare_prompt_payload(
            small_payload,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        assert compaction_audit == []
        assert context_too_large is False

    def test_context_too_large_when_budget_exceeded_after_compaction(self) -> None:
        """When the payload still exceeds the budget after all compaction
        layers, context_too_large is True."""
        # Build a payload that is so large that even aggressive compaction
        # cannot bring it within a tiny budget
        huge_payload = {
            "history": [
                {"role": "user", "content_md": "q" * 5000} for _ in range(20)
            ],
            "record_context": {
                "source_excerpt": "t" * 50000,
            },
            "article_overview": "o" * 40000,
        }

        # Tiny budget: 10 * 100 = 1000 total, input = 1000 - 100 - 256*3 = 132
        _, _, compaction_audit, context_too_large = prepare_prompt_payload(
            huge_payload,
            reserved_points=10,
            tokens_per_point=100,
            multiplier_output=3,
            budget_buffer_tokens=100,
            default_max_output_tokens=1024,
            min_max_output_tokens=256,
        )

        assert context_too_large is True
        # Compaction was attempted
        assert len(compaction_audit) > 0

    def test_context_too_large_when_attachments_lost(self) -> None:
        """When explicit attachments would be lost during compaction,
        context_too_large is True — attachments must not be silently dropped."""
        payload_with_attachments = {
            "canonical_context": {
                "attachments": [
                    {"kind": "text_selection", "label": "选中的句子"},
                    {"kind": "record_ref", "label": "相关文章"},
                ],
            },
            "history": [{"role": "user", "content_md": "q" * 8000} for _ in range(10)],
            "record_context": {
                "source_excerpt": "t" * 30000,
            },
            "article_overview": "o" * 20000,
        }

        # Budget that forces compaction but not so small that budget is exceeded
        # Total = 50 * 200 = 10000, input = 10000 - 500 - 512*3 = 7964
        result_payload, _, compaction_audit, context_too_large = prepare_prompt_payload(
            payload_with_attachments,
            reserved_points=50,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=500,
            default_max_output_tokens=4096,
            min_max_output_tokens=512,
        )

        # If attachments survived, context_too_large should be False
        result_attachments = result_payload.get("canonical_context", {}).get("attachments", [])
        if len(result_attachments) < 2:
            # Attachments were lost → must flag context_too_large
            assert context_too_large is True
        else:
            # Attachments survived → no error
            assert context_too_large is False

    def test_attachments_preserved_when_within_budget(self) -> None:
        """When payload with attachments fits within budget, attachments are
        preserved and context_too_large is False."""
        payload_with_attachments = {
            "canonical_context": {
                "attachments": [
                    {"kind": "text_selection", "label": "选中的句子"},
                ],
            },
            "history": [{"role": "user", "content_md": "Hello"}],
        }

        result_payload, _, compaction_audit, context_too_large = prepare_prompt_payload(
            payload_with_attachments,
            reserved_points=100,
            tokens_per_point=200,
            multiplier_output=3,
            budget_buffer_tokens=1000,
            default_max_output_tokens=4096,
            min_max_output_tokens=1024,
        )

        # Attachments must be preserved
        result_attachments = result_payload.get("canonical_context", {}).get("attachments", [])
        assert len(result_attachments) == 1
        assert result_attachments[0]["kind"] == "text_selection"
        assert compaction_audit == []
        assert context_too_large is False
