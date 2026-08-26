"""Daily Reader teaching-v2 prompt strategy.

Five-stage node lines (blueprint / language_support / translation /
semantic_review / refinement). Contract sentences live verbatim in the
agents' registry instructions; the policy lines are operational framing
only.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.prompting.prompt_composer import PromptSection
from app.services.prompting.prompt_loader import load_policy_lines


@dataclass
class DailyPromptStrategy:
    profile_id: str
    node_type: str
    policy_lines: tuple[str, ...] = ()
    extra_instructions: tuple[str, ...] = ()
    extra_sections: tuple[PromptSection, ...] = ()


def build_daily_prompt_sections(strategy: DailyPromptStrategy) -> tuple[PromptSection, ...]:
    sections: list[PromptSection] = [
        PromptSection(
            "profile",
            (
                f"profile_id: {strategy.profile_id}",
                f"node_type: {strategy.node_type}",
            ),
        ),
    ]
    if strategy.policy_lines:
        sections.append(PromptSection("policy", strategy.policy_lines))
    if strategy.extra_instructions:
        sections.append(PromptSection("runtime_constraints", strategy.extra_instructions))
    sections.extend(strategy.extra_sections)
    return tuple(sections)


def build_teaching_blueprint_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="blueprint",
        policy_lines=tuple(load_policy_lines("daily", "teaching_blueprint")),
    )


def build_teaching_language_support_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="language_support",
        policy_lines=tuple(load_policy_lines("daily", "teaching_language_support")),
    )


def build_teaching_translation_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="translation",
        policy_lines=tuple(load_policy_lines("daily", "teaching_translation")),
    )


def build_teaching_semantic_review_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="semantic_review",
        policy_lines=tuple(load_policy_lines("daily", "teaching_semantic_review")),
    )


def build_teaching_refinement_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="refinement",
        policy_lines=tuple(load_policy_lines("daily", "teaching_refinement")),
    )
