"""Strategy bundle builder for V3 workflow.

统一的 strategy bundle 构建器。
设计原则：
- 为所有 agent 提供同一套配置来源
- 策略层解耦，不写死在 node 内
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.internal.execution_plan import GoalExecutionPlan
from app.services.analysis.prompting.example_strategy import (
    ExampleStrategy,
    get_grammar_example_strategy,
    get_grammar_example_strategy_async,
    get_translation_example_strategy,
    get_vocabulary_example_strategy,
)
from app.services.analysis.prompting.prompt_strategy import (
    PromptStrategy,
    build_grammar_prompt_strategy,
    build_translation_prompt_strategy,
    build_vocabulary_prompt_strategy,
)


@dataclass
class StrategyBundle:
    """策略Bundle，包含 prompt 和 example 策略。"""

    prompt_strategy: PromptStrategy
    example_strategy: ExampleStrategy
    rag_debug: dict | None = None


def build_vocabulary_bundle(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> StrategyBundle:
    """构建 vocabulary agent 的 strategy bundle。"""
    return StrategyBundle(
        prompt_strategy=build_vocabulary_prompt_strategy(plan),
        example_strategy=get_vocabulary_example_strategy(plan, sentences=sentences),
    )


def build_grammar_bundle(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> StrategyBundle:
    """构建 grammar agent 的 strategy bundle（同步版本）。"""
    return StrategyBundle(
        prompt_strategy=build_grammar_prompt_strategy(plan),
        example_strategy=get_grammar_example_strategy(plan, sentences=sentences),
    )


async def build_grammar_bundle_async(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> StrategyBundle:
    """构建 grammar agent 的 strategy bundle（异步版本，支持 RAG）。"""
    from app.config.settings import get_settings
    from app.services.analysis.prompting.rag.grammar_rag_service import (
        build_rag_debug_info,
        query_grammar_rag,
    )

    settings = get_settings()
    rag_debug = None
    example_strategy = await get_grammar_example_strategy_async(
        plan, sentences=sentences
    )

    if settings.grammar_rag_enabled and sentences:
        gn_result = await query_grammar_rag(
            variant=plan.variant_id,
            sentences=sentences,
            output_type="grammar_note",
        )
        sa_result = await query_grammar_rag(
            variant=plan.variant_id,
            sentences=sentences,
            output_type="sentence_analysis",
        )
        rag_debug = {
            "grammar_note": build_rag_debug_info(gn_result),
            "sentence_analysis": build_rag_debug_info(sa_result),
        }

    return StrategyBundle(
        prompt_strategy=build_grammar_prompt_strategy(plan),
        example_strategy=example_strategy,
        rag_debug=rag_debug,
    )


def build_translation_bundle(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> StrategyBundle:
    """构建 translation agent 的 strategy bundle。"""
    return StrategyBundle(
        prompt_strategy=build_translation_prompt_strategy(plan),
        example_strategy=get_translation_example_strategy(plan, sentences=sentences),
    )
