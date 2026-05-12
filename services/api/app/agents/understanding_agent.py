from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_ai import Agent

from app.schemas.internal.academic_drafts import UnderstandingDraft
from app.services.analysis.prompting.example_strategy import ExampleEntry
from app.services.analysis.prompting.prompt_composer import build_agent_prompt
from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.services.analysis.prompting.prompt_strategy import PromptStrategy, build_prompt_sections


@dataclass
class UnderstandingAgentDeps:
    sentences: list[dict[str, object]]
    prompt_strategy: PromptStrategy
    examples: list[ExampleEntry] = field(default_factory=list)
    term_draft_json: dict | None = None


def build_understanding_prompt(deps: UnderstandingAgentDeps) -> str:
    sections = list(build_prompt_sections(deps.prompt_strategy))

    if deps.term_draft_json and deps.term_draft_json.get("term_notes"):
        term_lines = ["以下是术语标注结果，仅供参考（如与原文矛盾，以原文为准）："]
        for note in deps.term_draft_json["term_notes"]:
            term_lines.append(
                f"- {note.get('text', '')} ({note.get('term_category', '')}): {note.get('zh', '')} — {note.get('context_definition', '')}"
            )
        from app.services.analysis.prompting.prompt_composer import PromptSection
        sections.append(PromptSection("term_reference", tuple(term_lines)))

    return build_agent_prompt(
        strategy_sections=sections,
        examples=deps.examples,
        sentences=deps.sentences,
    )


@lru_cache(maxsize=1)
def get_understanding_agent() -> Agent[UnderstandingAgentDeps, UnderstandingDraft]:
    return Agent[UnderstandingAgentDeps, UnderstandingDraft](
        model=None,
        output_type=UnderstandingDraft,
        deps_type=UnderstandingAgentDeps,
        instructions=load_agent_instructions("understanding"),
        name="understanding_agent",
        retries=2,
        output_retries=2,
        instrument=False,
    )
