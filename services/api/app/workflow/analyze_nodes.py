"""Workflow Nodes for article_analysis.

节点设计：
1. prepare_input - 输入清洗、分段分句
2. derive_user_config - 用户配置推导
3. parallel_agents - 词汇、结构、翻译并行标注
4. normalize_and_ground - 确定性归一化
5. repair_agent - 可选修复
6. project_render_scene - 前端协议投影
7. assemble_result - 结果收敛
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from time import perf_counter
from typing import Any

from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from app.agents.grammar_agent import GrammarAgentDeps
from app.agents.repair_agent import RepairPatchDeps
from app.agents.translation_agent import TranslationAgentDeps
from app.agents.vocabulary_agent import VocabularyAgentDeps
from app.config.settings import get_settings
from app.llm.agent_runner import extract_run_usage
from app.llm.router import resolve_model_config
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.runtime import get_model_selection
from app.llm.types import ModelSelection
from app.schemas.analysis import AnalyzeRequestMeta, ArticleStructure, RenderSceneModel, Warning
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.schemas.internal.normalized import NormalizedAnnotationResult
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.postprocess.draft_validators import validate_all_drafts
from app.services.analysis.postprocess.normalize_and_ground import normalize_and_ground
from app.services.analysis.postprocess.projection import (
    project_normalized_to_render_scene,
)
from app.services.analysis.postprocess.repair_items import (
    apply_repair_patches_to_normalized_result,
    build_repair_patch_request_with_stats,
)
from app.services.analysis.postprocess.repair_policy import (
    repair_worthy_drop_count,
    should_trigger_patch_repair,
)
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.prompting.strategy_builder import (
    build_grammar_bundle_async,
    build_translation_bundle,
    build_vocabulary_bundle,
)
from app.services.analysis.runtime.runners import (
    run_grammar_agent,
    run_translation_agent,
    run_vocabulary_agent,
)
from app.workflow.analyze_state import AnalyzeState
from app.workflow.tracing import build_llm_trace_metadata

logger = logging.getLogger(__name__)
WORKFLOW_NAME = "article_analysis"
WORKFLOW_VERSION = "3.0.0"
MAX_ANNOTATION_ATTEMPTS = 3

# 触发 repair 的条件
ANCHOR_FAILURE_THRESHOLD = 0.35
REPAIR_PATCH_MAX_TARGETS = 8


def _repair_enabled(config: RunnableConfig | None) -> bool:
    """决定是否启用 repair。

    优先级：config["configurable"]["repair_enabled"] > env CLAREAD_WORKFLOW_REPAIR_ENABLED。
    默认启用。显式设置为 "false" / "0" / "no" 时关闭。
    """
    # 1. 显式 config 优先
    if config is not None:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if configurable and isinstance(configurable, dict):
            val = configurable.get("repair_enabled")
            if val is not None:
                if isinstance(val, bool):
                    return val
                if str(val).lower() in ("false", "0", "no"):
                    return False
                return True

    # 2. 环境变量
    env_val = os.environ.get("CLAREAD_WORKFLOW_REPAIR_ENABLED")
    if env_val is not None:
        return env_val.lower() not in ("false", "0", "no")

    # 3. 默认启用
    return True


def _annotation_count_by_type(annotations: list[Any]) -> dict[str, int]:
    counts = Counter(getattr(a, "type", str(type(a).__name__)) for a in annotations)
    return dict(sorted(counts.items()))


ANCHOR_DROP_REASONS = frozenset({
    "anchor_not_substring", "anchor_invalid", "resolve_failed",
    "sentence_id_not_found", "schematic_anchor_not_groundable",
})


def _anchor_drop_summary(drop_log: list[Any]) -> dict[str, Any]:
    """按 (annotation_type, drop_reason) 汇总 anchor 相关 drop。"""
    anchor_drops = [
        e for e in drop_log
        if getattr(e, "drop_reason", "") in ANCHOR_DROP_REASONS
    ]
    by_type_and_reason = Counter(
        (getattr(e, "annotation_type", ""), getattr(e, "drop_reason", ""))
        for e in anchor_drops
    )
    return {
        "total_anchor_drops": len(anchor_drops),
        "by_annotation_type_and_reason": [
            {"annotation_type": at, "drop_reason": dr, "count": cnt}
            for (at, dr), cnt in by_type_and_reason.most_common()
        ],
    }


def _merge_timings(state: AnalyzeState, new_timings: dict[str, float]) -> dict[str, float]:
    """合并节点计时到 state。"""
    merged = dict(state.get("node_timings", {}) or {})
    merged.update(new_timings)
    return merged


def _model_selection(config: RunnableConfig | None) -> ModelSelection | None:
    return get_model_selection(config)


def _aggregate_usage_summary(
    usages: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    per_agent = {name: usage for name, usage in usages.items() if usage}
    if not per_agent:
        return {
            "available": False,
            "per_agent": {},
            "aggregate": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            "note": "workflow 当前未从 agent 结果中提取到 usage。",
        }

    def _sum_token(field: str) -> int:
        return sum(int(usage.get(field, 0) or 0) for usage in per_agent.values())

    return {
        "available": True,
        "per_agent": per_agent,
        "aggregate": {
            "input_tokens": _sum_token("input_tokens"),
            "output_tokens": _sum_token("output_tokens"),
            "total_tokens": _sum_token("total_tokens"),
        },
    }


def _build_learning_rag_debug(grammar_bundle: Any) -> dict[str, Any] | None:
    if not getattr(grammar_bundle, "rag_debug", None):
        return None
    return {
        "agents": {
            "grammar": grammar_bundle.rag_debug,
        }
    }


def _empty_result(
    *,
    request_id: str,
    payload: Any,
    profile_id: str,
) -> RenderSceneModel:
    return RenderSceneModel(
        request=AnalyzeRequestMeta(
            request_id=request_id,
            source_type=payload.source_type,
            reading_goal=payload.reading_goal,
            reading_variant=payload.reading_variant,
            profile_id=profile_id,
        ),
        article=ArticleStructure(
            source_type=payload.source_type,
            source_text=payload.text,
            render_text="",
            paragraphs=[],
            sentences=[],
        ),
        translations=[],
        inline_marks=[],
        sentence_entries=[],
        warnings=[],
    )


def _build_agent_trace_metadata(
    state: AnalyzeState,
    node_name: str,
    model_selection: ModelSelection | None = None,
) -> dict[str, object]:
    payload = state["payload"]
    plan = state["goal_execution_plan"]
    model_config = resolve_model_config(
        get_settings(), MODEL_ROUTE_ANNOTATION_GENERATION, model_selection
    )
    return build_llm_trace_metadata(
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        request_id=payload.request_id or "",
        source_type=payload.source_type,
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id=plan.prompt_profile,
        model_name=model_config.model_name if model_config else "unconfigured",
        model_provider=model_config.provider if model_config else "unconfigured",
        extra={
            "node": node_name,
            "model_profile": model_config.profile_name if model_config else "unconfigured",
            "sentence_count": len(state["prepared_input"].sentences),
        },
    )


@traceable(name="vocabulary_llm_call", run_type="llm")
async def _run_vocabulary_llm_span(
    *,
    deps: VocabularyAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_vocabulary_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {
        "output": result.output if hasattr(result, "output") else result,
        "usage_metadata": usage,
    }


@traceable(name="grammar_llm_call", run_type="llm")
async def _run_grammar_llm_span(
    *,
    deps: GrammarAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_grammar_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {
        "output": result.output if hasattr(result, "output") else result,
        "usage_metadata": usage,
    }


@traceable(name="translation_llm_call", run_type="llm")
async def _run_translation_llm_span(
    *,
    deps: TranslationAgentDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    result = await run_translation_agent(deps, model_selection=model_selection)
    usage = extract_run_usage(result)
    return {
        "output": result.output if hasattr(result, "output") else result,
        "usage_metadata": usage,
    }


# -------------------------------------------------------------------
# Node implementations
# -------------------------------------------------------------------


async def prepare_input_node(state: AnalyzeState) -> AnalyzeState:
    t0 = perf_counter()
    payload = state["payload"]
    prepared_input = prepare_input(payload.text)
    warnings: list[Any] = []

    if not prepared_input.render_text.strip():
        return {
            "prepared_input": prepared_input,
            "warnings": warnings,
            "render_scene": _empty_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id="unresolved",
            ),
            "node_timings": _merge_timings(state, {"prepare_input": perf_counter() - t0}),
        }

    FAIL_WARN = {"code_like", "other"}
    PROCEED_WARN = {"article_mixed", "structured_doc", "html_like"}

    if prepared_input.text_type in FAIL_WARN:
        warnings.append(
            Warning(
                code="UNSUPPORTED_TEXT_TYPE",
                level="warning",
                message=f"文本类型为 {prepared_input.text_type}，可能影响标注质量。",
            )
        )
    elif prepared_input.text_type in PROCEED_WARN:
        warnings.append(
            Warning(
                code="TEXT_TYPE_NEEDS_CARE",
                level="info",
                message=f"文本类型为 {prepared_input.text_type}，将继续分析但需注意质量。",
            )
        )

    if prepared_input.english_ratio < 0.45 or not prepared_input.sentences:
        warnings.append(
            Warning(
                code="LOW_ENGLISH_RATIO",
                level="warning",
                message=f"英文占比 {prepared_input.english_ratio:.0%} 低于阈值，或无有效句子。",
            )
        )

    if prepared_input.noise_ratio >= 0.55:
        warnings.append(
            Warning(
                code="HIGH_NOISE_RATIO",
                level="warning",
                message="输入中存在较多噪音内容，结果可能需要结合原文查看。",
            )
        )

    return {
        "prepared_input": prepared_input,
        "warnings": warnings,
        "node_timings": _merge_timings(
            state, {"prepare_input": perf_counter() - t0}
        ),
    }


async def derive_user_config_node(
    state: AnalyzeState, config: RunnableConfig
) -> AnalyzeState:
    t0 = perf_counter()
    # Always resolve repair_enabled from config/env, even if plan already exists,
    # so the conditional edge can read it from state.
    repair_enabled = _repair_enabled(config)
    existing_plan = state.get("goal_execution_plan")
    if existing_plan is not None:
        return {
            "repair_enabled": repair_enabled,
            "node_timings": _merge_timings(
                state, {"derive_user_config": perf_counter() - t0}
            ),
        }

    payload = state["payload"]
    plan = build_goal_execution_plan(
        payload.reading_goal, payload.reading_variant
    )
    return {
        "goal_execution_plan": plan,
        "repair_enabled": repair_enabled,
        "node_timings": _merge_timings(
            state, {"derive_user_config": perf_counter() - t0}
        ),
    }


async def _run_parallel_agents(
    state: AnalyzeState,
    model_selection: ModelSelection | None,
) -> dict[str, Any]:
    """并行运行三个 agent。"""
    prepared_input = state["prepared_input"]
    plan = state["goal_execution_plan"]

    sentences_data = [
        {"sentence_id": s.sentence_id, "text": s.text}
        for s in prepared_input.sentences
    ]

    vocab_bundle = build_vocabulary_bundle(plan)
    grammar_bundle = await build_grammar_bundle_async(plan, sentences=sentences_data)
    translation_bundle = build_translation_bundle(plan)

    logger.info(
        "Grammar strategy: mode=%s, examples=%d, rag_debug=%s",
        grammar_bundle.example_strategy.selection_mode,
        len(grammar_bundle.example_strategy.examples),
        "yes" if grammar_bundle.rag_debug else "no",
    )

    vocab_deps = VocabularyAgentDeps(
        sentences=sentences_data,
        prompt_strategy=vocab_bundle.prompt_strategy,
        examples=vocab_bundle.example_strategy.examples,
    )
    grammar_deps = GrammarAgentDeps(
        sentences=sentences_data,
        prompt_strategy=grammar_bundle.prompt_strategy,
        examples=grammar_bundle.example_strategy.examples,
    )
    translation_deps = TranslationAgentDeps(
        sentences=sentences_data,
        prompt_strategy=translation_bundle.prompt_strategy,
        examples=translation_bundle.example_strategy.examples,
    )

    vocab_meta = _build_agent_trace_metadata(state, "vocabulary_agent", model_selection)
    grammar_meta = _build_agent_trace_metadata(state, "grammar_agent", model_selection)
    translation_meta = _build_agent_trace_metadata(state, "translation_agent", model_selection)

    # 各 agent 独立计时：在各自 coroutine 内 perf_counter
    # 即使 agent 抛异常也记录耗时，方便定位 timeout/失败瓶颈
    async def _timed(coro: Any) -> tuple[Any, float]:
        t0 = perf_counter()
        try:
            result = await coro
        except Exception as exc:
            elapsed = perf_counter() - t0
            return exc, elapsed
        elapsed = perf_counter() - t0
        return result, elapsed

    vocab_task = _timed(
        _run_vocabulary_llm_span(
            deps=vocab_deps, metadata=vocab_meta,
            model_selection=model_selection,
        )
    )
    grammar_task = _timed(
        _run_grammar_llm_span(
            deps=grammar_deps, metadata=grammar_meta,
            model_selection=model_selection,
        )
    )
    translation_task = _timed(
        _run_translation_llm_span(
            deps=translation_deps, metadata=translation_meta,
            model_selection=model_selection,
        )
    )

    gather_t0 = perf_counter()
    results = await asyncio.gather(
        vocab_task, grammar_task, translation_task,
        return_exceptions=True,
    )
    _ = perf_counter() - gather_t0  # wall time for parallel_agents recorded separately

    # 从 _timed 返回值中提取 agent 结果和独立耗时
    # _timed 始终返回 (result_or_exc, elapsed)，不再需要 gather 捕获异常
    agent_timings: dict[str, float] = {}
    raw_results: list[Any] = []
    for i, key in enumerate(
        ("vocabulary_agent", "grammar_agent", "translation_agent")
    ):
        agent_result, agent_elapsed = results[i]
        raw_results.append(agent_result)
        agent_timings[key] = agent_elapsed

    vocab_result = raw_results[0] if not isinstance(raw_results[0], Exception) else None
    grammar_result = raw_results[1] if not isinstance(raw_results[1], Exception) else None
    translation_result = raw_results[2] if not isinstance(raw_results[2], Exception) else None

    errors: list[Warning] = []
    if isinstance(raw_results[0], Exception):
        logger.error("vocabulary_agent 调用失败", exc_info=raw_results[0])
        errors.append(Warning(
            code="VOCABULARY_AGENT_FAILED", level="error",
            message=f"vocabulary agent 调用失败: {raw_results[0]}",
        ))
    if isinstance(raw_results[1], Exception):
        logger.error("grammar_agent 调用失败", exc_info=raw_results[1])
        errors.append(Warning(
            code="GRAMMAR_AGENT_FAILED", level="error",
            message=f"grammar agent 调用失败: {raw_results[1]}",
        ))
    if isinstance(raw_results[2], Exception):
        logger.error("translation_agent 调用失败", exc_info=raw_results[2])
        errors.append(Warning(
            code="TRANSLATION_AGENT_FAILED", level="error",
            message=f"translation agent 调用失败: {raw_results[2]}",
        ))

    vocabulary_output = vocab_result.get("output") if vocab_result else None
    grammar_output = grammar_result.get("output") if grammar_result else None
    translation_output = translation_result.get("output") if translation_result else None
    vocabulary_usage = (
        vocab_result.get("usage_metadata") or vocab_result.get("usage")
        if vocab_result else None
    )
    grammar_usage = (
        grammar_result.get("usage_metadata") or grammar_result.get("usage")
        if grammar_result else None
    )
    translation_usage = (
        translation_result.get("usage_metadata") or translation_result.get("usage")
        if translation_result else None
    )
    usage_summary = _aggregate_usage_summary({
        "vocabulary": vocabulary_usage,
        "grammar": grammar_usage,
        "translation": translation_usage,
    })

    return {
        "vocabulary_draft": vocabulary_output,
        "grammar_draft": grammar_output,
        "translation_draft": translation_output,
        "vocabulary_usage": vocabulary_usage,
        "grammar_usage": grammar_usage,
        "translation_usage": translation_usage,
        "usage_summary": usage_summary,
        "rag_debug": _build_learning_rag_debug(grammar_bundle),
        "agent_errors": errors,
        "agent_timings": agent_timings,
    }


async def parallel_agents_node(state: AnalyzeState, config: RunnableConfig) -> AnalyzeState:
    """Parallel agents node."""
    t0 = perf_counter()
    model_selection = _model_selection(config)
    result = await _run_parallel_agents(state, model_selection)
    errors = result.get("agent_errors", [])
    parallel_elapsed = perf_counter() - t0

    # 合并 agent 子耗时和 parallel_agents 总耗时
    agent_timings = result.get("agent_timings", {})
    timings = _merge_timings(state, {"parallel_agents": parallel_elapsed, **agent_timings})

    return {
        "vocabulary_draft": result.get("vocabulary_draft"),
        "grammar_draft": result.get("grammar_draft"),
        "translation_draft": result.get("translation_draft"),
        "vocabulary_usage": result.get("vocabulary_usage"),
        "grammar_usage": result.get("grammar_usage"),
        "translation_usage": result.get("translation_usage"),
        "usage_summary": result.get("usage_summary"),
        "rag_debug": result.get("rag_debug"),
        "warnings": [*state.get("warnings", []), *errors],
        "node_timings": timings,
    }


async def normalize_and_ground_node(state: AnalyzeState) -> AnalyzeState:
    """Normalize and ground node。"""
    t0 = perf_counter()
    prepared_input = state["prepared_input"]
    vocabulary_draft = state.get("vocabulary_draft")
    grammar_draft = state.get("grammar_draft")
    translation_draft = state.get("translation_draft")

    partial_warnings: list[Warning] = []
    if vocabulary_draft is None:
        vocabulary_draft = VocabularyDraft()
        partial_warnings.append(Warning(
            code="VOCABULARY_AGENT_FAILED", level="error",
            message="vocabulary agent 未返回有效结果，词汇标注已降级为空",
        ))
    if grammar_draft is None:
        grammar_draft = GrammarDraft()
        partial_warnings.append(Warning(
            code="GRAMMAR_AGENT_FAILED", level="error",
            message="grammar agent 未返回有效结果，语法标注已降级为空",
        ))
    if translation_draft is None:
        translation_draft = TranslationDraft(
            title="（翻译不可用）", sentence_translations=[]
        )
        partial_warnings.append(Warning(
            code="TRANSLATION_AGENT_FAILED", level="error",
            message="translation agent 未返回有效结果，翻译已降级为空",
        ))

    # Draft 统计：按 annotation type 分组
    all_draft_annotations: list[Any] = []
    all_draft_annotations.extend(vocabulary_draft.vocab_highlights)
    all_draft_annotations.extend(vocabulary_draft.phrase_glosses)
    all_draft_annotations.extend(vocabulary_draft.context_glosses)
    all_draft_annotations.extend(grammar_draft.grammar_notes)
    all_draft_annotations.extend(grammar_draft.sentence_analyses)
    draft_counts = _annotation_count_by_type(all_draft_annotations)

    sentences = [
        PreparedSentence.model_validate(s)
        if not isinstance(s, PreparedSentence) else s
        for s in prepared_input.sentences
    ]
    validation_warnings = validate_all_drafts(
        vocabulary_draft, grammar_draft, translation_draft, sentences
    )
    draft_warnings = [
        Warning(code="DRAFT_VALIDATION", level="warning", message=msg)
        for msg in validation_warnings
    ]

    normalized_result = normalize_and_ground(
        vocabulary_draft=vocabulary_draft,
        grammar_draft=grammar_draft,
        translation_draft=translation_draft,
        sentences=sentences,
        policy=state["goal_execution_plan"].policy,
    )

    # Normalized 统计：按 annotation type 分组
    normalized_counts = _annotation_count_by_type(normalized_result.annotations)

    # Anchor 相关 drop 汇总
    anchor_drop = _anchor_drop_summary(normalized_result.drop_log)

    # Drop 统计：按 annotation_type / drop_reason / drop_stage 分组
    drop_by_type = Counter(getattr(e, "annotation_type", "") for e in normalized_result.drop_log)
    drop_by_reason = Counter(getattr(e, "drop_reason", "") for e in normalized_result.drop_log)
    drop_by_stage = Counter(getattr(e, "drop_stage", "") for e in normalized_result.drop_log)

    annotation_stats: dict[str, Any] = {
        "draft_counts": draft_counts,
        "normalized_counts": normalized_counts,
        "drop_counts_by_type": dict(sorted(drop_by_type.items())),
        "drop_counts_by_reason": dict(sorted(drop_by_reason.items())),
        "drop_counts_by_stage": dict(sorted(drop_by_stage.items())),
        "anchor_drop_summary": anchor_drop,
        "canonical_stats": normalized_result.canonical_stats,
    }

    # Repair decision stats：即使不进入 repair_agent_node 也能观测
    # canonical/patch 口径：使用 normalized_annotations + combined drops
    pre_repair_count = len(normalized_result.normalized_annotations)
    combined_drops = (
        list(normalized_result.drop_log or [])
        + list(normalized_result.canonical_drop_log or [])
    )
    repair_worthy_drops = repair_worthy_drop_count(combined_drops)
    total_annotations = pre_repair_count + repair_worthy_drops
    failure_ratio = (
        repair_worthy_drops / total_annotations
        if total_annotations > 0 else 0.0
    )
    will_repair = should_trigger_patch_repair(
        normalized_result, threshold=ANCHOR_FAILURE_THRESHOLD
    )

    repair_decision_stats: dict[str, Any] = {
        "repair_triggered": False,
        "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
        "trigger_reason": (
            f"failure_ratio={failure_ratio:.2f}"
            f" > threshold={ANCHOR_FAILURE_THRESHOLD}"
            if will_repair else None
        ),
        "pre_repair_annotation_count": pre_repair_count,
        "post_repair_annotation_count": None,
        "repair_elapsed_s": None,
        "repair_succeeded": None,
        "repair_disabled": not state.get("repair_enabled", True),
        "patch_failure_ratio": round(failure_ratio, 4),
        "canonical_repair_worthy_drop_count": repair_worthy_drops,
    }

    return {
        "normalized_result": normalized_result,
        "drop_log": normalized_result.drop_log,
        "canonical_drop_log": normalized_result.canonical_drop_log,
        "warnings": [*state.get("warnings", []), *partial_warnings, *draft_warnings],
        "node_timings": _merge_timings(
            state, {"normalize_and_ground": perf_counter() - t0}
        ),
        "annotation_stats": annotation_stats,
        "repair_stats": repair_decision_stats,
    }


async def repair_agent_node(state: AnalyzeState, config: RunnableConfig) -> AnalyzeState:
    """Repair agent node（条件触发）。

    只使用 item-level patch repair。
    可通过 config["configurable"]["repair_enabled"]=false 或
    env CLAREAD_WORKFLOW_REPAIR_ENABLED=false 显式关闭。
    """
    t0 = perf_counter()
    normalized_result = state.get("normalized_result")

    repair_enabled = state.get("repair_enabled")
    if repair_enabled is None:
        repair_enabled = _repair_enabled(config)

    # ── Disabled guard ──────────────────────────────────────────────
    if not repair_enabled:
        repair_stats: dict[str, Any] = {
            "repair_triggered": False,
            "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
            "trigger_reason": None,
            "pre_repair_annotation_count": None,
            "post_repair_annotation_count": None,
            "repair_elapsed_s": None,
            "repair_succeeded": None,
            "repair_disabled": True,
        }
        return {
            "repair_request": None,
            "node_timings": _merge_timings(state, {"repair_agent": perf_counter() - t0}),
            "repair_stats": repair_stats,
        }

    # ── Trigger guard (patch policy) ────────────────────────────────
    will_repair = should_trigger_patch_repair(
        normalized_result, threshold=ANCHOR_FAILURE_THRESHOLD
    )

    if not will_repair:
        repair_stats = {
            "repair_triggered": False,
            "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
            "trigger_reason": None,
            "pre_repair_annotation_count": None,
            "post_repair_annotation_count": None,
            "repair_elapsed_s": None,
            "repair_succeeded": None,
            "repair_disabled": False,
        }
        return {
            "repair_request": None,
            "node_timings": _merge_timings(state, {"repair_agent": perf_counter() - t0}),
            "repair_stats": repair_stats,
        }

    # Compute canonical failure ratio for trigger_reason
    if normalized_result is not None:
        combined_drops = list(normalized_result.drop_log or [])
        combined_drops.extend(normalized_result.canonical_drop_log or [])
        patch_drop_count = repair_worthy_drop_count(combined_drops)
        patch_annotation_count = len(normalized_result.normalized_annotations)
        patch_total = patch_annotation_count + patch_drop_count
        failure_ratio = (
            patch_drop_count / patch_total if patch_total > 0 else 0.0
        )
    else:
        failure_ratio = 0.0

    trigger_reason = (
        f"failure_ratio={failure_ratio:.2f}"
        f" > threshold={ANCHOR_FAILURE_THRESHOLD}"
    )

    prepared_input = state["prepared_input"]
    vocabulary_draft = state.get("vocabulary_draft")
    grammar_draft = state.get("grammar_draft")
    translation_draft = state.get("translation_draft")

    if vocabulary_draft is None or grammar_draft is None or translation_draft is None:
        repair_stats = {
            "repair_triggered": True,
            "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
            "trigger_reason": trigger_reason,
            "pre_repair_annotation_count": None,
            "post_repair_annotation_count": None,
            "repair_elapsed_s": None,
            "repair_succeeded": None,
            "repair_disabled": False,
        }
        return {
            "repair_request": None,
            "node_timings": _merge_timings(state, {"repair_agent": perf_counter() - t0}),
            "repair_stats": repair_stats,
        }

    return await _repair_patch_mode(
        state, config, t0, normalized_result,
        trigger_reason, prepared_input,
        vocabulary_draft, grammar_draft, translation_draft,
    )


async def _repair_patch_mode(
    state: AnalyzeState,
    config: RunnableConfig,
    t0: float,
    normalized_result: NormalizedAnnotationResult,
    trigger_reason: str,
    prepared_input: Any,
    vocabulary_draft: VocabularyDraft,
    grammar_draft: GrammarDraft,
    translation_draft: TranslationDraft,
) -> AnalyzeState:
    """Item-level patch repair 逻辑。"""
    # patch mode 使用 canonical 口径（normalized_annotations），
    # 因为 patch repair 修的就是 normalized_annotations
    pre_repair_count = len(normalized_result.normalized_annotations)
    sentences = [
        PreparedSentence.model_validate(s)
        if not isinstance(s, PreparedSentence) else s
        for s in prepared_input.sentences
    ]

    # 1. Build patch request with stats
    build_result = build_repair_patch_request_with_stats(
        drop_log=normalized_result.drop_log or [],
        sentences=sentences,
        vocabulary_draft=vocabulary_draft,
        grammar_draft=grammar_draft,
        translation_draft=translation_draft,
        canonical_drop_log=normalized_result.canonical_drop_log or [],
        max_targets=REPAIR_PATCH_MAX_TARGETS,
    )

    patch_base_stats: dict[str, Any] = {
        "patch_target_count": None,
        "patch_repair_worthy_count": None,
        "patch_missing_sentence_count": None,
        "patch_selected_target_count": None,
        "patch_patched_count": None,
        "patch_delete_count": None,
        "patch_invalid_patch_count": None,
        "patch_postprocess_drop_count": None,
        "patch_no_targets": False,
    }

    if build_result.request is None:
        # No valid targets (all missing sentences or no repair-worthy drops)
        patch_base_stats.update({
            "patch_no_targets": True,
            "patch_repair_worthy_count": build_result.stats.repair_worthy_count,
            "patch_missing_sentence_count": build_result.stats.missing_sentence_count,
            "patch_selected_target_count": build_result.stats.selected_target_count,
        })
        repair_stats = {
            "repair_triggered": True,
            "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
            "trigger_reason": trigger_reason,
            "pre_repair_annotation_count": pre_repair_count,
            "post_repair_annotation_count": pre_repair_count,
            "repair_elapsed_s": None,
            "repair_succeeded": False,
            **patch_base_stats,
        }
        return {
            "repair_request": None,
            "warnings": [
                *state.get("warnings", []),
                Warning(
                    code="REPAIR_PATCH_NO_TARGETS", level="warning",
                    message="patch repair: 无可用修复 target，跳过 LLM 调用",
                ),
            ],
            "node_timings": _merge_timings(state, {"repair_agent": perf_counter() - t0}),
            "repair_stats": repair_stats,
        }

    # Record build stats
    patch_base_stats.update({
        "patch_target_count": len(build_result.request.targets),
        "patch_repair_worthy_count": build_result.stats.repair_worthy_count,
        "patch_missing_sentence_count": build_result.stats.missing_sentence_count,
        "patch_selected_target_count": build_result.stats.selected_target_count,
    })

    patch_request = build_result.request

    # 2. Call patch repair LLM
    patch_deps = RepairPatchDeps(patch_request=patch_request)
    patch_meta = _build_agent_trace_metadata(
        state, "repair_patch_agent", _model_selection(config)
    )
    patch_meta["extra"] = {
        **(patch_meta.get("extra") or {}),
        "target_count": len(patch_request.targets),
    }

    repair_model_selection = _model_selection(config)
    try:
        repair_inner_t0 = perf_counter()
        patch_llm_result = await _run_repair_patch_llm_span(
            deps=patch_deps,
            metadata=patch_meta,
            model_selection=repair_model_selection,
        )
        repair_elapsed = perf_counter() - repair_inner_t0
        patch_result = patch_llm_result.get("output")
        repair_usage = patch_llm_result.get("usage_metadata")

        # 3. Merge patches into normalized result
        annotation_density = (
            state["goal_execution_plan"].policy.annotation_density
        )
        merge = apply_repair_patches_to_normalized_result(
            result=normalized_result,
            patch_result=patch_result,
            patch_request=patch_request,
            sentences=sentences,
            annotation_density=annotation_density,
        )

        repaired_result = merge.result
        merge_stats = merge.stats

        # Record merge stats
        patch_base_stats.update({
            "patch_patched_count": merge_stats.patched_count,
            "patch_delete_count": merge_stats.delete_count,
            "patch_invalid_patch_count": merge_stats.invalid_patch_count,
            "patch_postprocess_drop_count": merge_stats.postprocess_drop_count,
        })

        usage_summary = _aggregate_usage_summary({
            "vocabulary": state.get("vocabulary_usage"),
            "grammar": state.get("grammar_usage"),
            "translation": state.get("translation_usage"),
            "repair": repair_usage,
        })

        post_repair_count = len(repaired_result.normalized_annotations)
        repair_stats = {
            "repair_triggered": True,
            "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
            "trigger_reason": trigger_reason,
            "pre_repair_annotation_count": pre_repair_count,
            "post_repair_annotation_count": post_repair_count,
            "repair_elapsed_s": repair_elapsed,
            "repair_succeeded": True,
            **patch_base_stats,
        }

        # Update annotation_stats
        annotation_stats = dict(state.get("annotation_stats") or {})
        annotation_stats.update({
            "canonical_stats": repaired_result.canonical_stats,
        })

        return {
            "repair_request": {
                "target_count": len(patch_request.targets),
                "repaired": True,
            },
            "normalized_result": repaired_result,
            "drop_log": repaired_result.drop_log,
            "canonical_drop_log": repaired_result.canonical_drop_log,
            "annotation_stats": annotation_stats,
            "repair_usage": repair_usage,
            "usage_summary": usage_summary,
            "node_timings": _merge_timings(state, {"repair_agent": perf_counter() - t0}),
            "repair_stats": repair_stats,
        }
    except Exception:
        logger.exception("repair_patch_agent 调用失败")
        usage_summary = _aggregate_usage_summary({
            "vocabulary": state.get("vocabulary_usage"),
            "grammar": state.get("grammar_usage"),
            "translation": state.get("translation_usage"),
        })
        repair_stats = {
            "repair_triggered": True,
            "trigger_threshold": ANCHOR_FAILURE_THRESHOLD,
            "trigger_reason": trigger_reason,
            "pre_repair_annotation_count": pre_repair_count,
            "post_repair_annotation_count": None,
            "repair_elapsed_s": None,
            "repair_succeeded": False,
            **patch_base_stats,
        }
        return {
            "repair_request": {
                "repaired": False,
            },
            "warnings": [
                *state.get("warnings", []),
                Warning(
                    code="REPAIR_PATCH_AGENT_FAILED", level="warning",
                    message="patch repair agent 调用失败，继续使用归一化结果",
                ),
            ],
            "usage_summary": usage_summary,
            "node_timings": _merge_timings(state, {"repair_agent": perf_counter() - t0}),
            "repair_stats": repair_stats,
        }


@traceable(name="repair_patch_llm_call", run_type="llm")
async def _run_repair_patch_llm_span(
    *,
    deps: RepairPatchDeps,
    metadata: dict[str, object],
    model_selection: ModelSelection | None = None,
) -> dict[str, Any]:
    from app.agents.repair_agent import build_repair_patch_prompt, get_repair_patch_agent
    from app.llm.agent_runner import run_agent_with_route

    result = await run_agent_with_route(
        agent=get_repair_patch_agent(),
        prompt=build_repair_patch_prompt(deps),
        deps=deps,
        route=MODEL_ROUTE_ANNOTATION_GENERATION,
        model_selection=model_selection,
    )
    usage = extract_run_usage(result)
    return {
        "output": result.output if hasattr(result, "output") else result,
        "usage_metadata": usage,
    }


async def project_render_scene_node(state: AnalyzeState) -> AnalyzeState:
    """Project to render scene node。"""
    t0 = perf_counter()
    payload = state["payload"]
    prepared_input = state["prepared_input"]
    normalized_result = state.get("normalized_result")
    plan = state.get("goal_execution_plan")

    if normalized_result is None:
        return {
            "render_scene": _empty_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id=(
                    plan.prompt_profile if plan else "unresolved"
                ),
            ),
            "node_timings": _merge_timings(
                state, {"project_render_scene": perf_counter() - t0}
            ),
        }

    projection_outcome = project_normalized_to_render_scene(
        normalized_result=normalized_result,
        prepared_input=prepared_input,
        source_type=payload.source_type,
        reading_goal=payload.reading_goal,
        reading_variant=payload.reading_variant,
        profile_id=plan.prompt_profile if plan else "unknown",
        request_id=payload.request_id or "",
    )

    return {
        "render_scene": projection_outcome.result,
        "warnings": [
            *state.get("warnings", []),
            *[Warning(**w) for w in projection_outcome.warnings],
        ],
        "node_timings": _merge_timings(
            state, {"project_render_scene": perf_counter() - t0}
        ),
    }


async def assemble_result_node(state: AnalyzeState) -> AnalyzeState:
    """Assemble result node。"""
    t0 = perf_counter()
    render_scene = state.get("render_scene")

    if render_scene is None:
        payload = state["payload"]
        plan = state.get("goal_execution_plan")
        return {
            "render_scene": _empty_result(
                request_id=payload.request_id or "",
                payload=payload,
                profile_id=(
                    plan.prompt_profile if plan else "unresolved"
                ),
            ),
            "node_timings": _merge_timings(
                state, {"assemble_result": perf_counter() - t0}
            ),
        }

    existing_warnings = state.get("warnings", [])
    if existing_warnings and hasattr(render_scene, "warnings"):
        seen_keys = {(w.code, w.sentence_id) for w in render_scene.warnings}
        for w in existing_warnings:
            if (w.code, w.sentence_id) not in seen_keys:
                render_scene.warnings.append(w)
                seen_keys.add((w.code, w.sentence_id))

    heavy_failure_codes = {
        "VOCABULARY_AGENT_FAILED", "GRAMMAR_AGENT_FAILED",
        "TRANSLATION_AGENT_FAILED", "NORMALIZE_AND_GROUND_FAILED",
    }
    has_heavy_failure = any(
        w.code in heavy_failure_codes for w in render_scene.warnings
    )
    has_no_entries = (
        len(render_scene.sentence_entries) == 0
        and len(render_scene.inline_marks) == 0
    )
    informational_codes = {
        "LOW_ENGLISH_RATIO", "HIGH_NOISE_RATIO",
        "UNSUPPORTED_TEXT_TYPE", "DRAFT_VALIDATION",
    }
    has_informational_only = (
        len(render_scene.warnings) > 0
        and all(
            w.code in informational_codes for w in render_scene.warnings
        )
    )

    if has_heavy_failure and has_no_entries:
        render_scene.user_facing_state = "degraded_heavy"
    elif len(render_scene.warnings) > 0 and not has_informational_only:
        render_scene.user_facing_state = "degraded_light"
    else:
        render_scene.user_facing_state = "normal"

    return {
        "render_scene": render_scene,
        "node_timings": _merge_timings(
            state, {"assemble_result": perf_counter() - t0}
        ),
    }
