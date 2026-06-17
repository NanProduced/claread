"""Contract tests for legacy planner-shaped schemas and live minimal helpers.

Round 17 removes ``planner.plan_request`` and the semantic-planner decision
consumption path. ``ReaderAskPlannerDecision`` remains as a historical schema
contract for stored traces and DTO compatibility; live agent-loop-first code
uses the minimal helper tests at the bottom of this file.

No test in this file calls a planner LLM or the removed ``plan_request`` API.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.reader_ask import (
    ReaderAskAnchorRef,
    ReaderAskAttachment,
    ReaderAskAttachmentMetadata,
    ReaderAskPageIdentity,
    ReaderAskPlannerDecision,
    ReaderAskPlannerReferenceRequest,
    ReaderAskPlannerStructuredAssetRequest,
    ReaderAskPlannerWorkingSetDecision,
)
from app.services.reader_ask import planner as planner_svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 1. Schema contract: new fields default to None
# ---------------------------------------------------------------------------

class TestPlannerDecisionNewFieldsDefault:
    def test_context_scope_default_none(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain")
        assert d.context_scope is None

    def test_decision_confidence_default_none(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain")
        assert d.decision_confidence is None

    def test_requires_local_anchor_default_none(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain")
        assert d.requires_local_anchor is None

    def test_answer_policy_default_none(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain")
        assert d.answer_policy is None

    def test_tool_hints_default_none(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain")
        assert d.tool_hints is None


# ---------------------------------------------------------------------------
# 2. Schema contract: new fields round-trip through serialization
# ---------------------------------------------------------------------------

class TestPlannerDecisionNewFieldsRoundTrip:
    def test_context_scope_round_trip(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain", context_scope="sentence")
        dumped = d.model_dump(mode="json")
        loaded = ReaderAskPlannerDecision.model_validate(dumped)
        assert loaded.context_scope == "sentence"

    def test_decision_confidence_round_trip(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain", decision_confidence="high")
        dumped = d.model_dump(mode="json")
        loaded = ReaderAskPlannerDecision.model_validate(dumped)
        assert loaded.decision_confidence == "high"

    def test_requires_local_anchor_round_trip(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="grammar", requires_local_anchor=True)
        dumped = d.model_dump(mode="json")
        loaded = ReaderAskPlannerDecision.model_validate(dumped)
        assert loaded.requires_local_anchor is True

    def test_answer_policy_round_trip(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain", answer_policy="detailed")
        dumped = d.model_dump(mode="json")
        loaded = ReaderAskPlannerDecision.model_validate(dumped)
        assert loaded.answer_policy == "detailed"

    def test_tool_hints_round_trip(self) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="vocabulary", tool_hints=["dictionary_lookup"])
        dumped = d.model_dump(mode="json")
        loaded = ReaderAskPlannerDecision.model_validate(dumped)
        assert loaded.tool_hints == ["dictionary_lookup"]

    def test_all_new_fields_together(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            context_scope="sentence",
            decision_confidence="high",
            requires_local_anchor=True,
            answer_policy="step_by_step",
            tool_hints=["record_insights", "dictionary_lookup"],
        )
        dumped = d.model_dump(mode="json")
        loaded = ReaderAskPlannerDecision.model_validate(dumped)
        assert loaded.context_scope == "sentence"
        assert loaded.decision_confidence == "high"
        assert loaded.requires_local_anchor is True
        assert loaded.answer_policy == "step_by_step"
        assert loaded.tool_hints == ["record_insights", "dictionary_lookup"]


# ---------------------------------------------------------------------------
# 3. Schema contract: extra fields ignored (backward compat)
# ---------------------------------------------------------------------------

class TestPlannerDecisionExtraIgnore:
    def test_unknown_fields_ignored(self) -> None:
        data = {
            "resolved_intent": "explain",
            "future_field": "some_value",
            "another_new_field": 42,
        }
        d = ReaderAskPlannerDecision.model_validate(data)
        assert d.resolved_intent == "explain"
        assert not hasattr(d, "future_field")


# ---------------------------------------------------------------------------
# 4. Schema contract: clarification_mode / clarification_only sync
# ---------------------------------------------------------------------------

class TestPlannerDecisionClarificationSync:
    def test_must_clarify_syncs_clarification_only(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="explain",
            clarification_mode="must_clarify",
        )
        assert d.clarification_only is True

    def test_can_answer_with_followup_syncs_clarification_only(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="explain",
            clarification_mode="can_answer_with_followup",
        )
        assert d.clarification_only is False

    def test_clarification_only_true_upgrades_mode(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="explain",
            clarification_only=True,
            clarification_mode="none",
        )
        assert d.clarification_mode == "must_clarify"

    def test_none_mode_and_false_clarification_only(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="explain",
            clarification_mode="none",
            clarification_only=False,
        )
        assert d.clarification_mode == "none"
        assert d.clarification_only is False


# ---------------------------------------------------------------------------
# 6. Focused fixture cases: planner decision shape
# ---------------------------------------------------------------------------
# Round 15: the fallback decision builder has been removed. The fixture
# cases below are retained as data for the schema-level
# ``TestPlannerDecisionCanCarryLLMOutput`` tests, which verify that
# ``ReaderAskPlannerDecision`` can still represent an LLM-style decision
# for each scenario. The ``expected_*`` keys reflect the fallback's
# historical behavior and are no longer asserted against a live builder.

FIXTURE_CASES: list[dict[str, Any]] = [
    {
        "id": "zh_sentence_grammar",
        # Fallback no longer recognizes "语法" keyword → defaults to explain
        "user_message": "这句话的语法结构是什么",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "grammar",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "step_by_step",
        "expected_tool_hints": ["record_insights"],
    },
    {
        "id": "zh_word_lookup",
        # Fallback no longer recognizes "词/意思" keyword → defaults to explain
        "user_message": "这个词什么意思",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "vocabulary",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "concise",
        "expected_tool_hints": ["dictionary_lookup"],
    },
    {
        "id": "zh_article_summary",
        # "讲了什么" doesn't match any fallback keyword → defaults to explain
        "user_message": "这篇文章讲了什么",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "general",
        "expected_context_scope": "article",
        "expected_requires_local_anchor": False,
        "expected_answer_policy": "detailed",
        "expected_tool_hints": ["article_overview"],
    },
    {
        # Fallback no longer recognizes "拆解" keyword → defaults to explain
        "id": "zh_breakdown",
        "user_message": "帮我拆解这个长句",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "breakdown",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "step_by_step",
        "expected_tool_hints": ["record_context", "record_insights"],
    },
    {
        # P3-S3: Weak natural language reference no longer triggers cross_record in fallback
        "id": "zh_weak_ref",
        "user_message": "之前那篇climate policy的文章也提过",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "general",
        "expected_cross_record": False,
        "expected_reference_requested": False,
        "expected_context_scope": "cross_article",
        "expected_requires_local_anchor": False,
        "expected_answer_policy": "comparative",
        "expected_tool_hints": ["reference_resolution", "external_record_context"],
    },
    {
        # Fallback no longer recognizes "grammar" keyword → defaults to explain
        "id": "en_sentence_grammar",
        "user_message": "What's the grammar of this sentence?",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "grammar",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "step_by_step",
        "expected_tool_hints": ["record_insights"],
    },
    {
        # Fallback no longer recognizes "word/mean" keyword → defaults to explain
        "id": "en_word_lookup",
        "user_message": "What does this word mean here?",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "vocabulary",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "concise",
        "expected_tool_hints": ["dictionary_lookup"],
    },
    {
        "id": "en_article_summary",
        # "What is this article about" doesn't match fallback keywords → defaults to explain
        "user_message": "What is this article about?",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "general",
        "expected_context_scope": "article",
        "expected_requires_local_anchor": False,
        "expected_answer_policy": "detailed",
        "expected_tool_hints": ["article_overview"],
    },
    {
        # P3-S3: weak natural language references are now handled by the
        # answer agent/tool loop, not deterministic fallback regex.
        "id": "mixed_weak_ref",
        "user_message": "那篇 about AI 的文章呢",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "general",
        "expected_cross_record": False,
        "expected_reference_requested": False,
        "expected_context_scope": "cross_article",
        "expected_requires_local_anchor": False,
        "expected_answer_policy": "comparative",
        "expected_tool_hints": ["reference_resolution", "external_record_context"],
    },
    {
        # explain_this entry_action does NOT force breakdown intent in fallback;
        # only why_here → grammar and lookup_in_context → vocabulary
        "id": "selection_toolbar",
        "user_message": "explain this",
        "entry_action": "explain_this",
        "anchors": [ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test sentence.")],
        "expected_intent": "explain",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": False,
        "expected_answer_policy": "step_by_step",
        "expected_tool_hints": ["record_context"],
    },
    {
        "id": "explicit_attachment",
        "user_message": "这篇文章讲了什么",
        "entry_action": "ask_about_this",
        "anchors": [],
        "attachments": [
            ReaderAskAttachment(
                kind="record_ref",
                subtype="related_record",
                label="Related Article",
                metadata=ReaderAskAttachmentMetadata(
                    source_surface="test",
                    record_id="00000000-0000-0000-0000-000000000002",
                ),
            ),
        ],
        "expected_intent": "explain",
        "llm_target_intent": "general",
        "expected_cross_record": True,
        "expected_context_scope": "cross_article",
        "expected_requires_local_anchor": False,
        "expected_answer_policy": "detailed",
        "expected_tool_hints": ["external_record_context"],
    },
    {
        "id": "deictic_no_anchor",
        "user_message": "这里为什么这样写",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "expected_context_scope": "sentence",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "step_by_step",
        "expected_tool_hints": ["record_context"],
    },
    {
        # "这一段的主旨是什么" doesn't match any fallback keyword → defaults to explain
        "id": "paragraph_context",
        "user_message": "这一段的主旨是什么",
        "entry_action": "ask_about_this",
        "anchors": [],
        "expected_intent": "explain",
        "llm_target_intent": "general",
        "expected_context_scope": "paragraph",
        "expected_requires_local_anchor": True,
        "expected_answer_policy": "detailed",
        "expected_tool_hints": ["record_context"],
    },
]


# ---------------------------------------------------------------------------
# 7. Schema can carry LLM output for fixture cases
# ---------------------------------------------------------------------------
# Round 15: ``TestFallbackDecisionFixtureCases`` (which asserted fallback
# builder behavior against ``FIXTURE_CASES``) has been removed. The
# schema-level ``TestPlannerDecisionCanCarryLLMOutput`` class below
# continues to use ``FIXTURE_CASES`` to verify that
# ``ReaderAskPlannerDecision`` can represent an LLM-style decision for
# each scenario.

class TestPlannerDecisionCanCarryLLMOutput:
    """Verify that ReaderAskPlannerDecision remains valid for historical
    planner-shaped fixture scenarios."""

    @pytest.mark.parametrize(
        "case",
        FIXTURE_CASES,
        ids=[c["id"] for c in FIXTURE_CASES],
    )
    def test_llm_style_decision_validates(self, case: dict[str, Any]) -> None:
        """An LLM-style decision with new fields should validate correctly."""
        llm_intent = case.get("llm_target_intent", case["expected_intent"])
        llm_context_scope = case.get("expected_context_scope")
        llm_requires_anchor = case.get("expected_requires_local_anchor")
        llm_answer_policy = case.get("expected_answer_policy")
        llm_tool_hints = case.get("expected_tool_hints")
        d = ReaderAskPlannerDecision(
            resolved_intent=llm_intent,
            context_scope=llm_context_scope,
            decision_confidence="high",
            requires_local_anchor=llm_requires_anchor,
            answer_policy=llm_answer_policy,
            tool_hints=llm_tool_hints,
            rationale=f"LLM decision for {case['id']}",
        )
        assert d.resolved_intent == llm_intent
        assert d.decision_confidence == "high"
        assert d.context_scope == llm_context_scope
        assert d.requires_local_anchor == llm_requires_anchor
        assert d.answer_policy == llm_answer_policy
        assert d.tool_hints == llm_tool_hints

    def test_llm_decision_with_all_new_fields(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            context_scope="sentence",
            decision_confidence="high",
            requires_local_anchor=True,
            answer_policy="step_by_step",
            tool_hints=["record_insights", "dictionary_lookup"],
            reference_request=ReaderAskPlannerReferenceRequest(
                requested=False,
            ),
            structured_asset_request=ReaderAskPlannerStructuredAssetRequest(
                requested=False,
            ),
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
                record_insights_needed=True,
                dictionary_needed=True,
            ),
            rationale="Grammar question about a specific sentence",
        )
        assert d.context_scope == "sentence"
        assert d.decision_confidence == "high"
        assert d.requires_local_anchor is True
        assert d.answer_policy == "step_by_step"
        assert d.tool_hints == ["record_insights", "dictionary_lookup"]

    def test_llm_decision_cross_article_with_context_scope(self) -> None:
        d = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope="cross_article",
            decision_confidence="medium",
            requires_local_anchor=False,
            reference_request=ReaderAskPlannerReferenceRequest(
                requested=True,
                query="climate policy",
                reason="用户引用了另一篇文章",
            ),
            working_set=ReaderAskPlannerWorkingSetDecision(
                cross_record_context_allowed=True,
                article_overview_needed=True,
            ),
            rationale="User references another article",
        )
        assert d.context_scope == "cross_article"
        assert d.reference_request.query == "climate policy"


# ---------------------------------------------------------------------------
# 8. Context scope type validation
# ---------------------------------------------------------------------------

class TestContextScopeValidation:
    @pytest.mark.parametrize("scope", ["sentence", "paragraph", "article", "cross_article"])
    def test_valid_context_scopes(self, scope: str) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain", context_scope=scope)  # type: ignore[arg-type]
        assert d.context_scope == scope

    def test_invalid_context_scope_rejected(self) -> None:
        with pytest.raises(Exception):
            ReaderAskPlannerDecision(resolved_intent="explain", context_scope="invalid")  # type: ignore[arg-type]


class TestAnswerPolicyValidation:
    @pytest.mark.parametrize("policy", ["concise", "detailed", "step_by_step", "comparative"])
    def test_valid_answer_policies(self, policy: str) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain", answer_policy=policy)  # type: ignore[arg-type]
        assert d.answer_policy == policy

    def test_invalid_answer_policy_rejected(self) -> None:
        with pytest.raises(Exception):
            ReaderAskPlannerDecision(resolved_intent="explain", answer_policy="invalid")  # type: ignore[arg-type]


class TestDecisionConfidenceValidation:
    @pytest.mark.parametrize("confidence", ["high", "medium", "low"])
    def test_valid_confidence_levels(self, confidence: str) -> None:
        d = ReaderAskPlannerDecision(resolved_intent="explain", decision_confidence=confidence)  # type: ignore[arg-type]
        assert d.decision_confidence == confidence

    def test_invalid_confidence_rejected(self) -> None:
        with pytest.raises(Exception):
            ReaderAskPlannerDecision(resolved_intent="explain", decision_confidence="invalid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Round 1 — Planner-minimal helpers for the agent-loop-first path
# ---------------------------------------------------------------------------


def _anchor(anchor_type: str) -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type=anchor_type, label="a", sentence_id="s1")  # type: ignore[arg-type]


def _dict_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(anchor_type="dictionary_entry", label="dict", dict_entry_id=1)


def _attachment(kind: str, subtype: str = "x") -> ReaderAskAttachment:
    return ReaderAskAttachment(
        kind=kind,  # type: ignore[arg-type]
        subtype=subtype,
        label="att",
        metadata=ReaderAskAttachmentMetadata(source_surface="test"),
    )


class TestBuildMinimalContextPlan:
    """`build_minimal_context_plan` produces a `ReaderAskContextPlan` for the
    agent-loop-first path with conservative defaults — no cross-record, no external refs."""

    def test_ask_about_this_with_anchor(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        plan = build_minimal_context_plan(
            entry_action="ask_about_this",
            attachments=[],
            anchors=[_anchor("sentence")],
        )
        assert plan.entry_action == "ask_about_this"
        assert plan.explicit_attachment_count == 0
        assert plan.normalized_anchor_count == 1
        assert plan.primary_anchor_type == "sentence"
        assert plan.used_record_context is True
        assert plan.used_dictionary is False
        assert plan.used_cross_record_context is False
        assert plan.reference_resolution_status == "not_needed"

    def test_lookup_in_context_with_dictionary_attachment(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        plan = build_minimal_context_plan(
            entry_action="lookup_in_context",
            attachments=[_attachment("text_selection", "dictionary_entry")],
            anchors=[_dict_anchor()],
        )
        assert plan.used_dictionary is True
        assert plan.primary_anchor_type == "dictionary_entry"
        assert plan.used_record_context is True

    def test_why_here_grammar_mode(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        plan = build_minimal_context_plan(
            entry_action="why_here",
            attachments=[],
            anchors=[_anchor("sentence")],
        )
        assert plan.used_record_context is True
        assert plan.used_dictionary is False
        assert plan.used_cross_record_context is False

    def test_no_anchors(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        plan = build_minimal_context_plan(
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
        )
        assert plan.primary_anchor_type is None
        assert plan.normalized_anchor_count == 0
        assert plan.used_record_context is False
        assert plan.used_dictionary is False

    def test_explicit_attachment_count(self) -> None:
        from app.services.reader_ask.planner import build_minimal_context_plan

        plan = build_minimal_context_plan(
            entry_action="ask_about_this",
            attachments=[_attachment("annotation_ref"), _attachment("supplement_ref")],
            anchors=[],
        )
        assert plan.explicit_attachment_count == 2


class TestBuildMinimalTraceSummary:
    """`build_minimal_trace_summary` produces a `ReaderAskTraceSummary` for the
    agent-loop-first path with `planner_mode='direct_answer'` and no cross-record signal."""

    def test_default_planner_mode_direct_answer(self) -> None:
        from app.services.reader_ask.planner import build_minimal_trace_summary

        trace = build_minimal_trace_summary(
            entry_action="ask_about_this",
            attachments=[],
            anchors=[_anchor("sentence")],
            planner_skipped=True,
        )
        assert trace.planner_mode == "direct_answer"
        assert trace.used_known_reference_resolution is False
        assert trace.cross_record_context_allowed is False
        assert trace.cross_record_context_used is False

    def test_records_planner_skipped_note(self) -> None:
        from app.services.reader_ask.planner import build_minimal_trace_summary

        trace = build_minimal_trace_summary(
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_skipped=True,
        )
        assert any("skipped" in note.lower() or "planner_skipped" in note.lower() for note in trace.notes)

    def test_records_attachment_count_in_notes(self) -> None:
        from app.services.reader_ask.planner import build_minimal_trace_summary

        trace = build_minimal_trace_summary(
            entry_action="ask_about_this",
            attachments=[_attachment("annotation_ref"), _attachment("supplement_ref")],
            anchors=[],
            planner_skipped=True,
        )
        assert any("2" in note and "attachment" in note.lower() for note in trace.notes)

    def test_no_cross_record_signals(self) -> None:
        from app.services.reader_ask.planner import build_minimal_trace_summary

        trace = build_minimal_trace_summary(
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_skipped=True,
        )
        assert trace.cross_record_context_used is False
        assert trace.used_known_reference_resolution is False
        assert trace.used_external_record_context is False


class TestBuildMinimalResolvedIntent:
    """`build_minimal_resolved_intent` maps `entry_action` to a (intent, label)
    tuple without consulting the removed semantic planner."""

    def test_ask_about_this_returns_general(self) -> None:
        from app.services.reader_ask.planner import build_minimal_resolved_intent

        assert build_minimal_resolved_intent("ask_about_this") == ("general", "ask_about_this")

    def test_explain_this_returns_explain(self) -> None:
        from app.services.reader_ask.planner import build_minimal_resolved_intent

        assert build_minimal_resolved_intent("explain_this") == ("explain", "explain_this")

    def test_why_here_returns_grammar(self) -> None:
        from app.services.reader_ask.planner import build_minimal_resolved_intent

        assert build_minimal_resolved_intent("why_here") == ("grammar", "why_here")

    def test_lookup_in_context_returns_vocabulary(self) -> None:
        from app.services.reader_ask.planner import build_minimal_resolved_intent

        assert build_minimal_resolved_intent("lookup_in_context") == ("vocabulary", "lookup_in_context")

    def test_unknown_action_falls_back_to_general(self) -> None:
        from app.services.reader_ask.planner import build_minimal_resolved_intent

        assert build_minimal_resolved_intent("custom_action") == ("general", "custom_action")
