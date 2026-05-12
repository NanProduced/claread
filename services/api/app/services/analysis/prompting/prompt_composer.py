"""Composable prompt sections for analysis agents."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.analysis.prompting.example_strategy import ExampleEntry


@dataclass(frozen=True, slots=True)
class PromptSection:
    """A tagged prompt block that can be replaced by later layers."""

    tag: str
    lines: tuple[str, ...]


def merge_prompt_sections(*groups: Iterable[PromptSection]) -> list[PromptSection]:
    """Merge section groups and let later sections replace earlier ones by tag."""

    merged: dict[str, PromptSection] = {}
    order: list[str] = []

    for group in groups:
        for section in group:
            if not section.lines:
                continue
            if section.tag not in merged:
                order.append(section.tag)
            merged[section.tag] = section

    return [merged[tag] for tag in order]


def render_prompt_sections(sections: Sequence[PromptSection]) -> str:
    """Render prompt sections with explicit XML-style delimiters."""

    blocks: list[str] = []
    for section in sections:
        blocks.append(f"<{section.tag}>")
        blocks.extend(section.lines)
        blocks.append(f"</{section.tag}>")
    return "\n".join(blocks)


def build_agent_prompt(
    *,
    strategy_sections: Sequence[PromptSection],
    examples: Sequence[ExampleEntry],
    sentences: Sequence[dict[str, object]],
) -> str:
    """Assemble a runtime prompt from modular strategy, example, and input sections."""

    example_lines: list[str] = []
    for idx, example in enumerate(examples, start=1):
        example_lines.extend(
            [
                f"{idx}. [{example.example_type}] {example.sentence_text}",
                example.output_fragment,
            ]
        )

    sentence_lines = [
        f"{sentence['sentence_id']}: {sentence['text']}"
        for sentence in sentences
    ]

    sections = merge_prompt_sections(
        strategy_sections,
        (
            PromptSection("examples", tuple(example_lines)),
            PromptSection("input_sentences", tuple(sentence_lines)),
        ),
    )
    return render_prompt_sections(sections)
