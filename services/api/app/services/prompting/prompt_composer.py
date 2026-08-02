"""Composable prompt sections for analysis agents."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.prompting.example_strategy import ExampleEntry


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
    focus_guidance: dict[str, object] | None = None,
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
    focus_lines: list[str] = []
    if isinstance(focus_guidance, dict):
        focus_text = str(focus_guidance.get("focus_text") or "").strip()
        if focus_text:
            focus_lines.append(f"focus_text: {focus_text}")
        selection_mode = str(focus_guidance.get("selection_mode") or "").strip()
        if selection_mode:
            focus_lines.append(f"selection_mode: {selection_mode}")
        sentence_id = str(focus_guidance.get("sentence_id") or "").strip()
        if sentence_id:
            focus_lines.append(f"focus_sentence_id: {sentence_id}")
        if focus_guidance.get("analysis_scope_hint"):
            focus_lines.append(f"analysis_scope_hint: {focus_guidance['analysis_scope_hint']}")
        start_offset = focus_guidance.get("start_offset")
        end_offset = focus_guidance.get("end_offset")
        if isinstance(start_offset, int) and isinstance(end_offset, int):
            focus_lines.append(f"focus_offsets: {start_offset}-{end_offset}")

    sections = merge_prompt_sections(
        strategy_sections,
        (
            PromptSection("examples", tuple(example_lines)),
            PromptSection("focus", tuple(focus_lines)),
            PromptSection("input_sentences", tuple(sentence_lines)),
        ),
    )
    return render_prompt_sections(sections)
