"""Daily Reader prompt strategy.

Redesigned per redesign-tracker.tmp.md:
- vocab_highlight: per-batch generation with coverage emphasis
- paragraph_notes: focuses on focus_question/micro_summary/translation
- close_reading_takeaways: replaces full_interpretation with structured
  language points instead of a 500-1000 word essay
- quality_review: 8 dimensions including coverage and content overload
- refinement: targets new schema fields
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.prompting.prompt_composer import PromptSection
from app.services.prompting.prompt_loader import load_agent_instructions, load_policy_lines

# A-2: difficulty-adaptive parsing. Prompt text lives in
# prompts/agents/daily_*.yaml as ``difficulty_{level}_content`` sections.
_KNOWN_CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


def normalize_daily_difficulty(difficulty: str | None) -> str:
    """Map a difficulty string to a CEFR level key; unknown values default to B2."""
    level = (difficulty or "").strip().upper()
    if level in _KNOWN_CEFR_LEVELS:
        return level
    return "B2"


def load_difficulty_section(agent_name: str, difficulty: str | None) -> tuple[str, ...]:
    """Load the difficulty-adaptive prompt section for an agent yaml.

    Returns an empty tuple when the section is missing so callers can skip it.
    """
    level = normalize_daily_difficulty(difficulty)
    text = load_agent_instructions(agent_name, section=f"difficulty_{level.lower()}")
    if not text:
        return ()
    return tuple(text.splitlines())


def difficulty_prompt_section(agent_name: str, difficulty: str | None) -> PromptSection | None:
    lines = load_difficulty_section(agent_name, difficulty)
    if not lines:
        return None
    return PromptSection("difficulty_profile", lines)


def resolve_refined_difficulty(paragraph_notes: dict | None) -> str | None:
    """Valid CEFR level from paragraph_notes.refined_difficulty, else None.

    A-2 whole-text re-grading: the paragraph-notes node re-estimates the
    article level; downstream prompts and the stored difficulty override
    use it only when it is a well-formed level.
    """
    if not isinstance(paragraph_notes, dict):
        return None
    refined = paragraph_notes.get("refined_difficulty")
    if not isinstance(refined, str) or not refined.strip():
        return None
    level = normalize_daily_difficulty(refined)
    return level if level == refined.strip().upper() else None


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
