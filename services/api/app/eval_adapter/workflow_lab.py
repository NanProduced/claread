from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.agents.repair_agent import RepairAgentDeps, build_repair_prompt
from app.eval_adapter.schemas import (
    NodeLabExampleEntry,
    WorkflowLabBaselineBundle,
    WorkflowLabBaselineBundleRequest,
    WorkflowLabPromptLayer,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.prompting.prompt_composer import build_agent_prompt
from app.services.analysis.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)
from app.services.analysis.prompting.prompt_strategy import build_prompt_sections
from app.services.analysis.prompting.strategy_builder import (
    StrategyBundle,
    build_grammar_bundle,
    build_translation_bundle,
    build_vocabulary_bundle,
)

_SAMPLE_SENTENCES = (
    {"sentence_id": "s1", "text": "[示例句子 1]"},
    {"sentence_id": "s2", "text": "[示例句子 2]"},
)

_AGENT_LABELS = {
    "vocabulary": "词汇",
    "grammar": "语法",
    "translation": "翻译",
    "repair": "修复",
}


def _sample_sentences(raw: list[dict[str, Any]] | None) -> list[dict[str, object]]:
    if not raw:
        return [dict(sentence) for sentence in _SAMPLE_SENTENCES]
    sentences: list[dict[str, object]] = []
    for index, sentence in enumerate(raw, start=1):
        if not isinstance(sentence, dict):
            continue
        sentence_id = str(sentence.get("sentence_id") or f"s{index}").strip()
        text = str(sentence.get("text") or "").strip()
        if text:
            sentences.append({"sentence_id": sentence_id or f"s{index}", "text": text})
    return sentences or [dict(sentence) for sentence in _SAMPLE_SENTENCES]


def _example_rows(bundle: StrategyBundle) -> list[NodeLabExampleEntry]:
    return [
        NodeLabExampleEntry.model_validate(asdict(entry))
        for entry in bundle.example_strategy.examples
    ]


def _learning_layer(
    *,
    agent_name: str,
    policy_focus: str,
    bundle: StrategyBundle,
    sentences: list[dict[str, object]],
    reading_variant: str,
) -> WorkflowLabPromptLayer:
    return WorkflowLabPromptLayer(
        agent_name=agent_name,  # type: ignore[arg-type]
        label=_AGENT_LABELS[agent_name],
        instructions=load_agent_instructions(agent_name),
        policy_name=agent_name,
        policy_focus=policy_focus,
        policy_variant=reading_variant,
        policy_lines=list(bundle.prompt_strategy.policy_lines),
        examples=_example_rows(bundle),
        prompt_template=build_agent_prompt(
            strategy_sections=build_prompt_sections(bundle.prompt_strategy),
            examples=bundle.example_strategy.examples,
            sentences=sentences,
        ),
    )


def _repair_layer(sentences: list[dict[str, object]]) -> WorkflowLabPromptLayer:
    prompt = build_repair_prompt(
        RepairAgentDeps(
            sentences=sentences,
            original_drafts={
                "vocabulary_draft": {},
                "grammar_draft": {},
                "translation_draft": {},
            },
        ),
        "示例错误上下文：normalized_result 锚点失败率过高或结构异常。",
    )
    return WorkflowLabPromptLayer(
        agent_name="repair",
        label=_AGENT_LABELS["repair"],
        instructions=load_agent_instructions("repair"),
        policy_name=None,
        policy_focus=None,
        policy_variant=None,
        policy_lines=[],
        examples=[],
        prompt_template=prompt,
    )


def get_workflow_lab_baseline_bundle(
    request: WorkflowLabBaselineBundleRequest,
) -> WorkflowLabBaselineBundle:
    plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
    if getattr(plan, "topology_mode", "unknown") != "learning":
        raise ValueError("workflow_lab v1 only supports learning topology")

    plan.few_shot_mode = request.few_shot_mode
    sentences = _sample_sentences(request.sample_sentences)

    vocabulary = build_vocabulary_bundle(plan, sentences=sentences)
    grammar = build_grammar_bundle(plan, sentences=sentences)
    translation = build_translation_bundle(plan, sentences=sentences)

    return WorkflowLabBaselineBundle(
        reading_goal=request.reading_goal,
        reading_variant=request.reading_variant,
        prompt_version=get_prompt_version(),
        prompt_profile=plan.prompt_profile,
        topology_mode="learning",
        few_shot_mode=request.few_shot_mode,
        agents={
            "vocabulary": _learning_layer(
                agent_name="vocabulary",
                policy_focus=plan.policy.vocabulary_focus,
                bundle=vocabulary,
                sentences=sentences,
                reading_variant=request.reading_variant,
            ),
            "grammar": _learning_layer(
                agent_name="grammar",
                policy_focus=plan.policy.grammar_focus,
                bundle=grammar,
                sentences=sentences,
                reading_variant=request.reading_variant,
            ),
            "translation": _learning_layer(
                agent_name="translation",
                policy_focus=plan.policy.translation_focus,
                bundle=translation,
                sentences=sentences,
                reading_variant=request.reading_variant,
            ),
            "repair": _repair_layer(sentences),
        },
    )
