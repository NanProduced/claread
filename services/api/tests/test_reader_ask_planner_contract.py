"""Contract tests for ReaderAskPlannerDecision schema and planner decision behavior.

P3-S1: Verify that the planner decision contract can carry LLM semantic
decisions, that new fields are backward-compatible, and that focused
fixture cases cover Chinese, English, mixed, selection, weak reference,
explicit attachment, and sentence/article-level context scenarios.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

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
from app.services.reader_ask import planner_runtime
from app.services.reader_ask import planner as planner_svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record(
    *,
    record_id: Any = None,
    title: str = "Test Article",
    overview: str | None = "A test article about AI.",
    sentence_entries: list[dict[str, Any]] | None = None,
) -> Any:
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


def _fallback_decision(**kwargs: object) -> ReaderAskPlannerDecision:
    """Build a fallback decision with sensible defaults for testing."""
    defaults = {
        "user_message": "test",
        "entry_action": "ask_about_this",
        "page_identity": _page_identity(),
        "attachments": [],
        "anchors": [],
        "record": _record(),
        "failure_reason": "test",
        "render_overview_cb": _render_overview_cb,
        "has_sentence_entries_cb": _has_sentence_entries_cb,
    }
    defaults.update(kwargs)
    return planner_runtime.fallback_semantic_planner_decision(**defaults)  # type: ignore[arg-type]


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
# 5. Fallback decision contract: new fields
# ---------------------------------------------------------------------------

class TestFallbackDecisionNewFields:
    def test_fallback_decision_confidence_is_low(self) -> None:
        decision = _fallback_decision()
        assert decision.decision_confidence == "low"

    def test_fallback_context_scope_is_set(self) -> None:
        """P3-S4: Fallback decision now sets context_scope based on working_set."""
        decision = _fallback_decision()
        assert decision.context_scope is not None
        assert decision.context_scope in ("sentence", "paragraph", "article", "cross_article")

    def test_fallback_requires_local_anchor_is_none(self) -> None:
        decision = _fallback_decision()
        assert decision.requires_local_anchor is None

    def test_fallback_answer_policy_is_none(self) -> None:
        decision = _fallback_decision()
        assert decision.answer_policy is None

    def test_fallback_tool_hints_is_none(self) -> None:
        decision = _fallback_decision()
        assert decision.tool_hints is None


# ---------------------------------------------------------------------------
# 5b. Fallback explicit signal tests
# ---------------------------------------------------------------------------

class TestFallbackExplicitSignals:
    """Verify that fallback only uses explicit signals (entry_action, anchor type)
    to determine intent, not keyword matching."""

    def test_lookup_in_context_forces_vocabulary(self) -> None:
        decision = _fallback_decision(
            user_message="这里的语法结构",
            entry_action="lookup_in_context",
        )
        assert decision.resolved_intent == "vocabulary"

    def test_dictionary_anchor_forces_vocabulary(self) -> None:
        decision = _fallback_decision(
            user_message="这是什么",
            entry_action="ask_about_this",
            anchors=[ReaderAskAnchorRef(
                anchor_type="dictionary_entry",
                sentence_id=None,
                selected_text="test",
                dict_entry_id=1,
            )],
        )
        assert decision.resolved_intent == "vocabulary"

    def test_why_here_forces_grammar(self) -> None:
        decision = _fallback_decision(
            user_message="这个词什么意思",
            entry_action="why_here",
        )
        assert decision.resolved_intent == "grammar"

    def test_explain_this_defaults_to_explain(self) -> None:
        decision = _fallback_decision(
            user_message="explain this",
            entry_action="explain_this",
        )
        assert decision.resolved_intent == "explain"

    def test_ask_about_this_defaults_to_explain(self) -> None:
        decision = _fallback_decision(
            user_message="这篇文章讲了什么",
            entry_action="ask_about_this",
        )
        assert decision.resolved_intent == "explain"


# ---------------------------------------------------------------------------
# 6. Focused fixture cases: fallback decision behavior
# ---------------------------------------------------------------------------

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
        # P3-S3: weak natural language references should be handled by the LLM planner,
        # not deterministic fallback regex.
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


class TestFallbackDecisionFixtureCases:
    @pytest.mark.parametrize(
        "case",
        FIXTURE_CASES,
        ids=[c["id"] for c in FIXTURE_CASES],
    )
    def test_fallback_resolved_intent(self, case: dict[str, Any]) -> None:
        decision = _fallback_decision(
            user_message=case["user_message"],
            entry_action=case["entry_action"],
            anchors=case.get("anchors", []),
            attachments=case.get("attachments", []),
        )
        assert decision.resolved_intent == case["expected_intent"], (
            f"{case['id']}: expected intent {case['expected_intent']}, got {decision.resolved_intent}"
        )

    @pytest.mark.parametrize(
        "case",
        FIXTURE_CASES,
        ids=[c["id"] for c in FIXTURE_CASES],
    )
    def test_fallback_cross_record(self, case: dict[str, Any]) -> None:
        decision = _fallback_decision(
            user_message=case["user_message"],
            entry_action=case["entry_action"],
            anchors=case.get("anchors", []),
            attachments=case.get("attachments", []),
        )
        expected = case.get("expected_cross_record", False)
        assert decision.working_set.cross_record_context_allowed is expected, (
            f"{case['id']}: expected cross_record={expected}, got {decision.working_set.cross_record_context_allowed}"
        )

    @pytest.mark.parametrize(
        "case",
        FIXTURE_CASES,
        ids=[c["id"] for c in FIXTURE_CASES],
    )
    def test_fallback_reference_requested(self, case: dict[str, Any]) -> None:
        decision = _fallback_decision(
            user_message=case["user_message"],
            entry_action=case["entry_action"],
            anchors=case.get("anchors", []),
            attachments=case.get("attachments", []),
        )
        expected = case.get("expected_reference_requested", False)
        assert decision.reference_request.requested is expected, (
            f"{case['id']}: expected reference_requested={expected}, got {decision.reference_request.requested}"
        )

    @pytest.mark.parametrize(
        "case",
        FIXTURE_CASES,
        ids=[c["id"] for c in FIXTURE_CASES],
    )
    def test_fallback_decision_confidence_is_low(self, case: dict[str, Any]) -> None:
        decision = _fallback_decision(
            user_message=case["user_message"],
            entry_action=case["entry_action"],
            anchors=case.get("anchors", []),
            attachments=case.get("attachments", []),
        )
        assert decision.decision_confidence == "low"


# ---------------------------------------------------------------------------
# 7. Schema can carry LLM output for fixture cases
# ---------------------------------------------------------------------------

class TestPlannerDecisionCanCarryLLMOutput:
    """Verify that ReaderAskPlannerDecision can represent what an LLM planner
    would output for each fixture scenario."""

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
# 8. plan_request consumes new fields (P3-S4)
# ---------------------------------------------------------------------------

class TestContextScopeConsumption:
    """P3-S4: plan_request consumes context_scope to enhance working_set."""

    def test_context_scope_article_requests_overview_no_insights(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope="article",
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这篇文章讲了什么",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.working_set.article_overview_needed is True
        assert snapshot.working_set.record_insights_needed is False

    def test_context_scope_sentence_requests_local_context(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope="sentence",
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这句的语法",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.working_set.local_context_window_needed is True

    def test_context_scope_paragraph_requests_local_context(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope="paragraph",
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这段的主旨",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.working_set.local_context_window_needed is True

    def test_context_scope_cross_article_requests_overview(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope="cross_article",
            working_set=ReaderAskPlannerWorkingSetDecision(
                cross_record_context_allowed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="那篇文章也提过",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.working_set.article_overview_needed is True
        assert snapshot.working_set.cross_record_context_allowed is True

    def test_context_scope_none_preserves_existing_behavior(self) -> None:
        """context_scope=None should not change working_set from planner_decision."""
        decision_with_scope = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope=None,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
                article_overview_needed=False,
            ),
            rationale="test",
        )
        decision_without = ReaderAskPlannerDecision(
            resolved_intent="explain",
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
                article_overview_needed=False,
            ),
            rationale="test",
        )
        page_identity = _page_identity()
        anchors = [ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")]

        snap_with = planner_svc.plan_request(
            content="test",
            page_identity=page_identity,
            entry_action="ask_about_this",
            attachments=[],
            anchors=anchors,
            planner_decision=decision_with_scope,
        )
        snap_without = planner_svc.plan_request(
            content="test",
            page_identity=page_identity,
            entry_action="ask_about_this",
            attachments=[],
            anchors=anchors,
            planner_decision=decision_without,
        )
        assert snap_with.working_set.local_context_window_needed == snap_without.working_set.local_context_window_needed
        assert snap_with.working_set.article_overview_needed == snap_without.working_set.article_overview_needed
        assert snap_with.working_set.record_insights_needed == snap_without.working_set.record_insights_needed

    def test_context_scope_does_not_override_must_clarify_guard(self) -> None:
        """context_scope cannot reopen resources closed by must_clarify guard."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            context_scope="sentence",
            clarification_mode="must_clarify",
            clarification_reason="deictic_requires_sentence_anchor",
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
                record_insights_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这句的语法",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        # must_clarify guard closes all resource requests
        assert snapshot.clarification_mode == "must_clarify"
        assert snapshot.working_set.local_context_window_needed is False
        assert snapshot.working_set.record_insights_needed is False


class TestRequiresLocalAnchorConsumption:
    """P3-S4: plan_request consumes requires_local_anchor for deictic guard."""

    def test_requires_local_anchor_true_no_anchor_triggers_must_clarify(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            requires_local_anchor=True,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这里的语法结构",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.clarification_mode == "must_clarify"

    def test_requires_local_anchor_false_deictic_no_anchor_stays_followup(self) -> None:
        """When planner says requires_local_anchor=False, deictic without anchor
        should stay at can_answer_with_followup, not escalate to must_clarify."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            requires_local_anchor=False,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这里的语法结构",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        # requires_local_anchor=False prevents must_clarify escalation
        assert snapshot.clarification_mode != "must_clarify"

    def test_requires_local_anchor_none_preserves_deictic_guard(self) -> None:
        """When requires_local_anchor=None, deictic regex + intent guard applies."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            requires_local_anchor=None,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这里的语法结构",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        # Legacy behavior: deictic + grammar intent → must_clarify
        assert snapshot.clarification_mode == "must_clarify"

    def test_requires_local_anchor_true_with_anchor_no_clarify(self) -> None:
        """requires_local_anchor=True with anchor should NOT trigger must_clarify."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            requires_local_anchor=True,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这句的语法",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            planner_decision=decision,
        )
        assert snapshot.clarification_mode != "must_clarify"


class TestDeicticGuardBoundary:
    """P3-S5: Verify deictic guard boundary with requires_local_anchor."""

    def test_requires_local_anchor_false_deictic_triggers_followup_not_must_clarify(self) -> None:
        """When planner says requires_local_anchor=False, deictic content
        without anchor should trigger can_answer_with_followup, not
        must_clarify. The deictic regex still fires (user IS pointing at
        something), but planner says sentence-level anchor isn't needed."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            requires_local_anchor=False,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这里为什么这样写",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.clarification_mode == "can_answer_with_followup"
        assert snapshot.clarification_mode != "must_clarify"

    def test_requires_local_anchor_true_overrides_reference_ambiguous(self) -> None:
        """When requires_local_anchor=True + no anchor, deictic guard
        produces must_clarify even if reference resolution is ambiguous
        (which would normally downgrade to can_answer_with_followup)."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            requires_local_anchor=True,
            reference_request=ReaderAskPlannerReferenceRequest(
                requested=True,
                query="AI Ethics",
            ),
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这句的语法",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query="AI Ethics",
                ambiguous_records=[
                    {"record_id": "r1", "title": "AI Ethics A"},
                    {"record_id": "r2", "title": "AI Ethics B"},
                ],
            ),
        )
        # requires_local_anchor=True + no anchor → must_clarify
        # (deictic guard fires after reference resolution logic)
        assert snapshot.clarification_mode == "must_clarify"

    def test_requires_local_anchor_false_reference_not_found_stays_must_clarify(self) -> None:
        """requires_local_anchor=False only controls the deictic guard.
        Reference resolution not_found without anchor still produces
        must_clarify independently."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            requires_local_anchor=False,
            reference_request=ReaderAskPlannerReferenceRequest(
                requested=True,
                query="Nonexistent Article",
            ),
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这里那篇文章说了什么",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
            reference_resolution=planner_svc.ReaderAskReferenceResolution(
                attempted=True,
                status="not_found",
                query="Nonexistent Article",
            ),
        )
        # reference not_found + no anchor + no fallback_ reason → must_clarify
        # (requires_local_anchor=False only prevents deictic escalation)
        assert snapshot.clarification_mode == "must_clarify"

    def test_no_deictic_no_anchor_no_requires_local_anchor_no_clarify(self) -> None:
        """No deictic content + no anchor + requires_local_anchor=None
        should not trigger the deictic guard at all."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            requires_local_anchor=None,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这篇文章讲了什么",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.clarification_mode == "none"

    def test_deictic_with_anchor_never_triggers_guard(self) -> None:
        """When there IS an anchor, the deictic guard never fires,
        regardless of requires_local_anchor value."""
        anchor = ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")
        for requires_anchor in (True, False, None):
            decision = ReaderAskPlannerDecision(
                resolved_intent="grammar",
                requires_local_anchor=requires_anchor,
                working_set=ReaderAskPlannerWorkingSetDecision(
                    local_context_window_needed=True,
                ),
                rationale=f"test requires_local_anchor={requires_anchor}",
            )
            snapshot = planner_svc.plan_request(
                content="这句的语法结构是什么",
                page_identity=_page_identity(),
                entry_action="ask_about_this",
                attachments=[],
                anchors=[anchor],
                planner_decision=decision,
            )
            assert snapshot.clarification_mode != "must_clarify", (
                f"requires_local_anchor={requires_anchor} with anchor should not trigger must_clarify"
            )


class TestToolHintsConsumption:
    """P3-S4: plan_request consumes tool_hints to enhance working_set."""

    def test_tool_hints_record_insights_requests_insights(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            tool_hints=["record_insights"],
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="test",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            planner_decision=decision,
        )
        assert snapshot.working_set.record_insights_needed is True

    def test_tool_hints_dictionary_lookup_requests_dictionary(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            tool_hints=["dictionary_lookup"],
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="test",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
            planner_decision=decision,
        )
        assert snapshot.working_set.dictionary_needed is True

    def test_tool_hints_external_record_context_updates_retrieval_needs(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="general",
            tool_hints=["external_record_context"],
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="和那篇文章有什么关系",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.working_set.cross_record_context_allowed is True
        assert snapshot.retrieval_needs == "known_reference_only"

    def test_tool_hints_cannot_override_must_clarify_guard(self) -> None:
        """tool_hints cannot reopen resources closed by must_clarify guard."""
        decision = ReaderAskPlannerDecision(
            resolved_intent="grammar",
            tool_hints=["record_insights", "dictionary_lookup"],
            clarification_mode="must_clarify",
            clarification_reason="deictic_requires_sentence_anchor",
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
                record_insights_needed=True,
                dictionary_needed=True,
            ),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这句的语法",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.clarification_mode == "must_clarify"
        assert snapshot.working_set.record_insights_needed is False
        assert snapshot.working_set.dictionary_needed is False

    def test_tool_hints_none_preserves_existing_behavior(self) -> None:
        """tool_hints=None should not change working_set from planner_decision."""
        decision_with_hints = ReaderAskPlannerDecision(
            resolved_intent="explain",
            tool_hints=None,
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        decision_without = ReaderAskPlannerDecision(
            resolved_intent="explain",
            working_set=ReaderAskPlannerWorkingSetDecision(
                local_context_window_needed=True,
            ),
            rationale="test",
        )
        page_identity = _page_identity()
        anchors = [ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")]

        snap_with = planner_svc.plan_request(
            content="test",
            page_identity=page_identity,
            entry_action="ask_about_this",
            attachments=[],
            anchors=anchors,
            planner_decision=decision_with_hints,
        )
        snap_without = planner_svc.plan_request(
            content="test",
            page_identity=page_identity,
            entry_action="ask_about_this",
            attachments=[],
            anchors=anchors,
            planner_decision=decision_without,
        )
        assert snap_with.working_set.local_context_window_needed == snap_without.working_set.local_context_window_needed
        assert snap_with.working_set.record_insights_needed == snap_without.working_set.record_insights_needed
        assert snap_with.working_set.dictionary_needed == snap_without.working_set.dictionary_needed


class TestCombinedFieldConsumption:
    """P3-S4: Test combinations of context_scope, requires_local_anchor, tool_hints."""

    def test_context_scope_article_with_tool_hints_dictionary(self) -> None:
        decision = ReaderAskPlannerDecision(
            resolved_intent="explain",
            context_scope="article",
            tool_hints=["dictionary_lookup"],
            working_set=ReaderAskPlannerWorkingSetDecision(),
            rationale="test",
        )
        snapshot = planner_svc.plan_request(
            content="这篇文章的词汇",
            page_identity=_page_identity(),
            entry_action="ask_about_this",
            attachments=[],
            anchors=[],
            planner_decision=decision,
        )
        assert snapshot.working_set.article_overview_needed is True
        assert snapshot.working_set.dictionary_needed is True
        assert snapshot.working_set.record_insights_needed is False

    def test_fallback_decision_context_scope_matches_working_set(self) -> None:
        """Fallback decision's context_scope should match its working_set derivation."""
        # With anchor → sentence scope
        decision = _fallback_decision(
            user_message="test",
            entry_action="ask_about_this",
            anchors=[ReaderAskAnchorRef(anchor_type="sentence", sentence_id="s1", selected_text="Test.")],
        )
        assert decision.context_scope == "sentence"

        # Without anchor, no cross_record → article scope
        decision = _fallback_decision(
            user_message="test",
            entry_action="ask_about_this",
        )
        assert decision.context_scope == "article"

        # With cross_record attachment → cross_article scope
        attachment = ReaderAskAttachment(
            kind="record_ref",
            subtype="related_record",
            label="Related Article",
            metadata=ReaderAskAttachmentMetadata(
                source_surface="test",
                record_id="00000000-0000-0000-0000-000000000002",
            ),
        )
        decision = _fallback_decision(
            user_message="test",
            entry_action="ask_about_this",
            attachments=[attachment],
        )
        assert decision.context_scope == "cross_article"


# ---------------------------------------------------------------------------
# 9. Context scope type validation
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
    tuple without consulting the LLM planner."""

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
