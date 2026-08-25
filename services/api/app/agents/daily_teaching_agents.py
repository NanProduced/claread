"""Daily Reader teaching-v2 stage agents.

Five stages of the P-5B v2 workflow: blueprint → language_support →
translation → semantic_review → refinement (at most one per article).

Prompt shape: the system instructions come verbatim from the prompt
registry (``prompts/agents/daily_*.yaml`` — the evals-canonical contract
text; drift is pinned by
``tests/test_daily_reader_teaching_v2_workflow.py``), and the user prompt
carries the canonical payload block (section header + stable JSON, same
serialization as the shared ``teaching.prototype`` builders).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic_ai import Agent

from app.schemas.internal.daily_lesson_v2 import (
    BlueprintDraft,
    LanguageSupportDraft,
    RefinementDraft,
    SemanticReviewDraft,
    TranslationDraft,
)
from app.services.daily_reader.teaching.prototype import (
    _stable_json,
    _validate_review_evidence,
)
from app.services.prompting.daily_prompt_strategy import (
    DailyPromptStrategy,
    build_teaching_blueprint_strategy,
    build_teaching_language_support_strategy,
    build_teaching_refinement_strategy,
    build_teaching_semantic_review_strategy,
    build_teaching_translation_strategy,
)
from app.services.prompting.prompt_composer import PromptSection, render_prompt_sections
from app.services.prompting.prompt_loader import load_agent_instructions

MAX_REVIEW_ORIGINAL_TEXT_CHARS = 20_000


def _render_stage_prompt(strategy: DailyPromptStrategy, payload_header: str, payload: Any) -> str:
    """Policy section (registry policies) + the canonical payload block."""
    sections = [
        PromptSection("policy", strategy.policy_lines),
        PromptSection(
            "profile",
            (
                f"profile_id: {strategy.profile_id}",
                f"node_type: {strategy.node_type}",
            ),
        ),
    ]
    return render_prompt_sections(sections) + f"\n{payload_header}\n" + _stable_json(payload)


@dataclass
class DailyBlueprintAgentDeps:
    article: dict[str, Any]
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_teaching_blueprint_strategy)


def build_daily_blueprint_prompt(deps: DailyBlueprintAgentDeps) -> str:
    return _render_stage_prompt(deps.prompt_strategy, "ARTICLE:", deps.article)


@dataclass
class DailyLanguageSupportAgentDeps:
    selected_units: list[dict[str, Any]]
    effective_difficulty: str
    prompt_strategy: DailyPromptStrategy = field(
        default_factory=build_teaching_language_support_strategy
    )


def build_daily_language_support_prompt(deps: DailyLanguageSupportAgentDeps) -> str:
    payload = {
        "effective_difficulty": deps.effective_difficulty,
        "selected_units": deps.selected_units,
    }
    return _render_stage_prompt(deps.prompt_strategy, "SELECTED INPUT:", payload)


@dataclass
class DailyTranslationAgentDeps:
    target_units: list[dict[str, Any]]
    sentence_maps: list[dict[str, Any]]
    effective_difficulty: str
    prompt_strategy: DailyPromptStrategy = field(
        default_factory=build_teaching_translation_strategy
    )


def build_daily_translation_prompt(deps: DailyTranslationAgentDeps) -> str:
    payload = {
        "effective_difficulty": deps.effective_difficulty,
        "sentence_maps": deps.sentence_maps,
        "target_units": deps.target_units,
    }
    return _render_stage_prompt(deps.prompt_strategy, "TARGET INPUT:", payload)


@dataclass
class DailySemanticReviewAgentDeps:
    original_text: str
    blueprint: dict[str, Any]
    learning_package: dict[str, Any]
    deterministic_checks: dict[str, Any]
    prompt_strategy: DailyPromptStrategy = field(
        default_factory=build_teaching_semantic_review_strategy
    )


def build_daily_semantic_review_prompt(deps: DailySemanticReviewAgentDeps) -> str:
    payload = {
        "blueprint": deps.blueprint,
        "deterministic_checks": deps.deterministic_checks,
        "learning_package": deps.learning_package,
        # Daily discovery accepts at most 2,500 English words; 20k chars
        # normally exposes the full article while bounding review cost.
        "original_text": deps.original_text[:MAX_REVIEW_ORIGINAL_TEXT_CHARS],
    }
    return _render_stage_prompt(deps.prompt_strategy, "REVIEW INPUT:", payload)


@dataclass
class DailyTeachingRefinementAgentDeps:
    review_before_refinement: dict[str, Any]
    fields_to_fix: dict[str, Any]
    evidence_context: dict[str, Any]
    prompt_strategy: DailyPromptStrategy = field(default_factory=build_teaching_refinement_strategy)


def build_daily_teaching_refinement_prompt(deps: DailyTeachingRefinementAgentDeps) -> str:
    # Same validation as the canonical builder: a failing before-review is
    # required before a refinement prompt may be rendered.
    before = _validate_review_evidence(deps.review_before_refinement)
    failed_contracts = [
        result["contract"] for result in before["contract_results"] if not result["passed"]
    ]
    payload = {
        "fields_to_fix": deps.fields_to_fix,
        "failed_contracts": failed_contracts,
        "issues": before["issues"],
        "evidence_context": deps.evidence_context,
    }
    return _render_stage_prompt(deps.prompt_strategy, "DIRECTED INPUT:", payload)


@lru_cache(maxsize=1)
def get_daily_blueprint_agent() -> Agent[DailyBlueprintAgentDeps, BlueprintDraft]:
    return Agent[DailyBlueprintAgentDeps, BlueprintDraft](
        model=None,
        output_type=BlueprintDraft,
        deps_type=DailyBlueprintAgentDeps,
        instructions=load_agent_instructions("daily_blueprint"),
        name="daily_blueprint_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )


@lru_cache(maxsize=1)
def get_daily_language_support_agent() -> Agent[
    DailyLanguageSupportAgentDeps, LanguageSupportDraft
]:
    return Agent[DailyLanguageSupportAgentDeps, LanguageSupportDraft](
        model=None,
        output_type=LanguageSupportDraft,
        deps_type=DailyLanguageSupportAgentDeps,
        instructions=load_agent_instructions("daily_language_support"),
        name="daily_language_support_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )


@lru_cache(maxsize=1)
def get_daily_translation_agent() -> Agent[DailyTranslationAgentDeps, TranslationDraft]:
    return Agent[DailyTranslationAgentDeps, TranslationDraft](
        model=None,
        output_type=TranslationDraft,
        deps_type=DailyTranslationAgentDeps,
        instructions=load_agent_instructions("daily_translation"),
        name="daily_translation_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )


@lru_cache(maxsize=1)
def get_daily_semantic_review_agent() -> Agent[DailySemanticReviewAgentDeps, SemanticReviewDraft]:
    return Agent[DailySemanticReviewAgentDeps, SemanticReviewDraft](
        model=None,
        output_type=SemanticReviewDraft,
        deps_type=DailySemanticReviewAgentDeps,
        instructions=load_agent_instructions("daily_semantic_review"),
        name="daily_semantic_review_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )


@lru_cache(maxsize=1)
def get_daily_teaching_refinement_agent() -> Agent[
    DailyTeachingRefinementAgentDeps, RefinementDraft
]:
    return Agent[DailyTeachingRefinementAgentDeps, RefinementDraft](
        model=None,
        output_type=RefinementDraft,
        deps_type=DailyTeachingRefinementAgentDeps,
        instructions=load_agent_instructions("daily_teaching_refinement"),
        name="daily_teaching_refinement_agent",
        retries=2,
        output_retries=3,
        instrument=False,
    )
