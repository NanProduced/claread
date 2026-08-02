from __future__ import annotations

from types import SimpleNamespace

from app.services.prompting.planning import get_annotation_style
from app.services.prompting.prompt_composer import PromptSection, merge_prompt_sections
from app.services.prompting.prompt_strategy import PromptStrategy, build_prompt_sections


def test_merge_prompt_sections_replaces_by_tag_and_preserves_order() -> None:
    merged = merge_prompt_sections(
        (
            PromptSection("profile", ("profile_id: daily_reader",)),
            PromptSection("policy", ("old policy",)),
        ),
        (
            PromptSection("policy", ("new policy",)),
            PromptSection("input_sentences", ("s1: hello",)),
        ),
    )

    assert [section.tag for section in merged] == [
        "profile",
        "policy",
        "input_sentences",
    ]
    assert merged[1].lines == ("new policy",)


def test_prompt_strategy_builds_neutral_sections() -> None:
    sections = build_prompt_sections(
        PromptStrategy(
            profile_id="reader_layer",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            annotation_style="plain_and_supportive",
            policy_lines=("只标注有理解价值的内容。",),
            extra_instructions=("Return only structured output.",),
            extra_sections=(PromptSection("input_sentences", ("s1: hello",)),),
        )
    )

    assert [section.tag for section in sections] == [
        "profile",
        "policy",
        "runtime_constraints",
        "input_sentences",
    ]
    assert "annotation_style: plain_and_supportive" in sections[0].lines
    assert sections[1].lines == ("只标注有理解价值的内容。",)


def test_annotation_style_is_neutral_and_goal_specific() -> None:
    assert get_annotation_style(
        SimpleNamespace(goal_id="daily_reading", variant_id="intermediate_reading")
    ) == "plain_and_supportive"
    assert get_annotation_style(
        SimpleNamespace(goal_id="exam", variant_id="gaokao")
    ) == "exam_gaokao"
