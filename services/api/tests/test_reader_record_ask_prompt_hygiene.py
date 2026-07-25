"""Prompt hygiene guards after the provenance/prompt realignment.

Locks the single-assembly, principle-only prompt contract:
- the system prompt carries stable product principles only (no Host intent
  policy, no sample-derived correctness rules, no coverage prose);
- the production user prompt has exactly one assembly path and contains no
  policy/correctness sections.
"""

from __future__ import annotations

import pytest

from app.services.reader_record_ask.agent import _SYSTEM_INSTRUCTIONS
from app.services.reader_record_ask.model_view_budget import (
    ModelViewRenderer,
    ModelVisibleTurnBudget,
)
from app.services.reader_record_ask.turn_prompt import (
    build_production_agent_user_prompt,
    mint_turn_frame_prompt_capability,
)

_FORBIDDEN_POLICY_TOKENS = (
    "Turn answer policy",
    "article_only",
    "citation_required",
    "requested_citation_scope",
    "web_capability",
    "answer_correctness",
    "Answer correctness",
)

_DELETED_SAMPLE_QUESTIONS = (
    "这篇文章在讲什么",
    "这篇文章主要说了什么",
    "文章提到了哪些城市",
    "文章没有提到的年份是什么",
    "这篇文章的发布日期是什么时候",
    "基于文章出一道选择题",
    "只允许一题",
)

_REQUIRED_PRINCIPLE_TOKENS = (
    "Ask Claread",
    "English",
    "expand_evidence",
    "search_current_article",
    "untrusted",
    "evh_",
    "knowledge_mode",
)


def _mint_user_prompt(
    *,
    user_question: str = "这篇文章的主旨是什么？",
    baseline_is_complete: bool = True,
) -> str:
    turn_frame = mint_turn_frame_prompt_capability(
        system_instructions=_SYSTEM_INSTRUCTIONS,
        projection_json="{}",
        handles_block="",
        baseline_is_complete=baseline_is_complete,
        user_question=user_question,
        budget=ModelVisibleTurnBudget(),
        renderer=ModelViewRenderer(),
        charge=False,
    )
    return build_production_agent_user_prompt(turn_frame=turn_frame)


def test_system_prompt_has_no_host_intent_policy_tokens() -> None:
    for token in _FORBIDDEN_POLICY_TOKENS:
        assert token not in _SYSTEM_INSTRUCTIONS


def test_system_prompt_has_no_deleted_sample_rules() -> None:
    for question in _DELETED_SAMPLE_QUESTIONS:
        assert question not in _SYSTEM_INSTRUCTIONS


def test_system_prompt_keeps_stable_principles() -> None:
    for token in _REQUIRED_PRINCIPLE_TOKENS:
        assert token in _SYSTEM_INSTRUCTIONS
    # Web Search not wired: basis=web must stay forbidden.
    assert "basis=web" in _SYSTEM_INSTRUCTIONS
    # Article as foundation, not boundary.
    assert "foundation" in _SYSTEM_INSTRUCTIONS


def test_coverage_prose_lives_only_in_the_user_prompt_block() -> None:
    assert "## Baseline coverage" not in _SYSTEM_INSTRUCTIONS
    assert "Status: complete" not in _SYSTEM_INSTRUCTIONS
    assert "Status: partial" not in _SYSTEM_INSTRUCTIONS
    user_prompt = _mint_user_prompt()
    assert user_prompt.count("## Baseline coverage") == 1


def test_user_prompt_has_no_policy_or_correctness_sections() -> None:
    for complete in (True, False):
        user_prompt = _mint_user_prompt(baseline_is_complete=complete)
        assert "## Turn answer policy" not in user_prompt
        assert "## Answer correctness" not in user_prompt
        for token in _FORBIDDEN_POLICY_TOKENS:
            assert token not in user_prompt


def test_user_prompt_section_order_and_verbatim_question() -> None:
    question = "  原文问题：保留前后空白?  "
    user_prompt = _mint_user_prompt(user_question=question)
    context_at = user_prompt.index("## Current turn context")
    coverage_at = user_prompt.index("## Baseline coverage")
    question_at = user_prompt.index("## User question")
    assert context_at < coverage_at < question_at
    assert user_prompt.endswith(f"## User question\n{question}\n")


def test_user_prompt_assembly_is_byte_stable() -> None:
    assert _mint_user_prompt() == _mint_user_prompt()


def test_mint_rejects_deleted_policy_and_correctness_kwargs() -> None:
    base_kwargs = {
        "system_instructions": _SYSTEM_INSTRUCTIONS,
        "projection_json": "{}",
        "handles_block": "",
        "baseline_is_complete": True,
        "user_question": "q",
        "budget": ModelVisibleTurnBudget(),
        "renderer": ModelViewRenderer(),
        "charge": False,
    }
    for deleted_kwarg in ("correctness_block", "answer_policy_json"):
        with pytest.raises(TypeError):
            mint_turn_frame_prompt_capability(  # type: ignore[call-arg]
                **{**base_kwargs, deleted_kwarg: None},
            )


def test_agent_module_has_no_legacy_prompt_assembly_entry() -> None:
    import app.services.reader_record_ask.agent as agent_module

    assert not hasattr(agent_module, "build_agent_user_prompt")
    assert not hasattr(agent_module, "_render_coverage_block")
