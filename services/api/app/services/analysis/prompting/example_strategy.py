"""Example strategy for V3 workflow.

负责 example selection。
设计原则：
- baseline = 最少 few-shot
- 后续可通过 RAG 注入 dynamic few-shot
- 不影响 baseline 稳定性
- 示例要体现 variant 的差异化方向
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.schemas.internal.execution_plan import GoalExecutionPlan
from app.services.analysis.prompting.prompt_loader import load_examples

logger = logging.getLogger(__name__)


@dataclass
class ExampleEntry:
    """Example 条目。"""
    example_type: Literal[
        "vocab", "phrase", "context", "grammar",
        "sentence_analysis", "translation",
    ]
    sentence_text: str
    output_fragment: str


@dataclass
class ExampleStrategy:
    """Example 策略。"""
    examples: list[ExampleEntry]
    selection_mode: Literal["baseline", "rag", "manual"] = "baseline"


def _load_baseline_examples(example_name: str, variant: str) -> list[ExampleEntry]:
    raw_entries = load_examples(example_name, variant)
    return [
        ExampleEntry(
            example_type=entry["example_type"],
            sentence_text=entry["sentence_text"],
            output_fragment=entry["output_fragment"],
        )
        for entry in raw_entries
    ]


def _resolve_rag_examples(
    example_name: str,
    variant: str,
    sentences: list[dict] | None = None,
) -> list[ExampleEntry]:
    if sentences is None:
        return []
    # TODO: 接入 grammar_rag_service，按 grammar-rag-design.md 实现
    # 当前返回空列表，RAG 未上线时自动回退到 baseline
    return []


async def _resolve_rag_examples_async(
    example_name: str,
    variant: str,
    sentences: list[dict] | None = None,
) -> list[ExampleEntry]:
    """异步版本：调用 grammar_rag_service 获取 RAG 示例。

    同时拉取 grammar_note（最多 2 条）和 sentence_analysis（最多 1 条），
    合并后返回。
    """
    if sentences is None:
        return []
    from app.services.analysis.prompting.rag.grammar_rag_service import (
        query_grammar_rag,
    )
    gn_result = await query_grammar_rag(
        variant=variant,
        sentences=sentences,
        output_type="grammar_note",
    )
    sa_result = await query_grammar_rag(
        variant=variant,
        sentences=sentences,
        output_type="sentence_analysis",
    )
    return gn_result.examples + sa_result.examples


def get_vocabulary_example_strategy(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> ExampleStrategy:
    """获取 vocabulary agent 的 example 策略。

    RAG-03: vocabulary 不走 RAG。即使 plan.few_shot_mode == "rag"，
    也直接回退到 baseline。
    """
    if plan.few_shot_mode == "rag":
        # vocabulary 不支持 RAG，始终回退 baseline
        return ExampleStrategy(
            examples=_load_baseline_examples("vocabulary", plan.variant_id),
            selection_mode="baseline",
        )
    if plan.few_shot_mode != "baseline":
        return ExampleStrategy(examples=[], selection_mode=plan.few_shot_mode)

    return ExampleStrategy(
        examples=_load_baseline_examples("vocabulary", plan.variant_id),
        selection_mode="baseline",
    )


def get_grammar_example_strategy(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> ExampleStrategy:
    """获取 grammar agent 的 example 策略（同步版本）。

    RAG 仅在 GRAMMAR_RAG_ENABLED=true 时激活。
    同步版本不调用 RAG，RAG 场景请使用 get_grammar_example_strategy_async。
    """
    if plan.few_shot_mode == "rag":
        from app.config.settings import get_settings

        settings = get_settings()
        if settings.grammar_rag_enabled:
            # 同步版本无法调用 async RAG，直接 fallback
            return ExampleStrategy(
                examples=_load_baseline_examples("grammar", plan.variant_id),
                selection_mode="rag_fallback",
            )
        return ExampleStrategy(
            examples=_load_baseline_examples("grammar", plan.variant_id),
            selection_mode="baseline",
        )
    if plan.few_shot_mode not in ("baseline", "rag"):
        return ExampleStrategy(examples=[], selection_mode=plan.few_shot_mode)

    return ExampleStrategy(
        examples=_load_baseline_examples("grammar", plan.variant_id),
        selection_mode="baseline",
    )


async def get_grammar_example_strategy_async(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> ExampleStrategy:
    """获取 grammar agent 的 example 策略（异步版本）。

    RAG 仅在 GRAMMAR_RAG_ENABLED=true 时激活。
    异步版本可调用 grammar_rag_service 获取 RAG 示例。
    当 GRAMMAR_RAG_ENABLED=true 时，即使 plan.few_shot_mode="baseline"，
    也会内部切换到 RAG 模式（不暴露给前端）。
    """
    from app.config.settings import get_settings

    settings = get_settings()
    if settings.grammar_rag_enabled:
        logger.info("Grammar RAG enabled, querying RAG for variant=%s", plan.variant_id)
        rag_examples = await _resolve_rag_examples_async(
            "grammar", plan.variant_id, sentences
        )
        if rag_examples:
            logger.info("Grammar RAG returned %d examples", len(rag_examples))
            return ExampleStrategy(examples=rag_examples, selection_mode="rag")
        logger.info("Grammar RAG returned 0 examples, falling back to baseline")
        return ExampleStrategy(
            examples=_load_baseline_examples("grammar", plan.variant_id),
            selection_mode="rag_fallback",
        )
    if plan.few_shot_mode not in ("baseline", "rag"):
        return ExampleStrategy(examples=[], selection_mode=plan.few_shot_mode)

    return ExampleStrategy(
        examples=_load_baseline_examples("grammar", plan.variant_id),
        selection_mode="baseline",
    )


def get_translation_example_strategy(
    plan: GoalExecutionPlan,
    sentences: list[dict] | None = None,
) -> ExampleStrategy:
    """获取 translation agent 的 example 策略。

    RAG-03: translation 不走 RAG。即使 plan.few_shot_mode == "rag"，
    也直接回退到 baseline。
    """
    if plan.few_shot_mode == "rag":
        # translation 不支持 RAG，始终回退 baseline
        return ExampleStrategy(
            examples=_load_baseline_examples("translation", plan.variant_id),
            selection_mode="baseline",
        )
    if plan.few_shot_mode != "baseline":
        return ExampleStrategy(examples=[], selection_mode=plan.few_shot_mode)

    return ExampleStrategy(
        examples=_load_baseline_examples("translation", plan.variant_id),
        selection_mode="baseline",
    )
