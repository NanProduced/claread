"""Daily Reader prompt strategy.

Redesigned per redesign-tracker.tmp.md:
- vocab_highlight: per-batch generation with coverage emphasis
- paragraph_notes: replaces footer_analysis (summary/thesis/structure → focus_question/micro_summary/translation)
- close_reading_takeaways: replaces full_interpretation (500-1000 word essay → structured language points)
- quality_review: 8 dimensions including coverage and content overload
- refinement: targets new schema fields
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.analysis.prompting.prompt_composer import PromptSection
from app.services.analysis.prompting.prompt_loader import load_policy_lines


@dataclass
class DailyPromptStrategy:
    profile_id: str
    node_type: str
    policy_lines: tuple[str, ...] = ()
    extra_instructions: tuple[str, ...] = ()
    extra_sections: tuple[PromptSection, ...] = ()


def build_daily_prompt_sections(strategy: DailyPromptStrategy) -> tuple[PromptSection, ...]:
    sections: list[PromptSection] = [
        PromptSection("profile", (
            f"profile_id: {strategy.profile_id}",
            f"node_type: {strategy.node_type}",
        )),
    ]
    if strategy.policy_lines:
        sections.append(PromptSection("policy", strategy.policy_lines))
    if strategy.extra_instructions:
        sections.append(PromptSection("runtime_constraints", strategy.extra_instructions))
    sections.extend(strategy.extra_sections)
    return tuple(sections)


def build_vocab_highlight_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="vocab_highlight",
        policy_lines=tuple(load_policy_lines("daily", "vocab_highlight")),
    )


def build_phrase_gloss_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="phrase_gloss",
        policy_lines=tuple(load_policy_lines("daily", "phrase_gloss")),
    )


def build_paragraph_notes_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="paragraph_notes",
        policy_lines=tuple(load_policy_lines("daily", "paragraph_notes")),
    )


def build_close_reading_takeaways_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="close_reading_takeaways",
        policy_lines=tuple(load_policy_lines("daily", "close_reading_takeaways")),
    )


def build_quality_review_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="quality_review",
        policy_lines=tuple(load_policy_lines("daily", "quality_review")),
    )


def build_refinement_strategy() -> DailyPromptStrategy:
    return DailyPromptStrategy(
        profile_id="daily_reader",
        node_type="refinement",
        policy_lines=tuple(load_policy_lines("daily", "refinement")),
    )
