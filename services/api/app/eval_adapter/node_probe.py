from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import Any

from app.agents.grammar_agent import GrammarAgentDeps, build_grammar_prompt
from app.agents.translation_agent import TranslationAgentDeps, build_translation_prompt
from app.agents.vocabulary_agent import VocabularyAgentDeps, build_vocabulary_prompt
from app.config.settings import get_settings
from app.eval_adapter.schemas import (
    ArticleAnalysisNodeProbeRequest,
    ArticleAnalysisNodeProbeResult,
    EvalError,
    ModelIdentity,
    SchemaIdentity,
    WorkflowIdentity,
)
from app.eval_adapter.shared import (
    build_llm_config_snapshot,
    build_llm_config_snapshot_safe,
    rag_override,
    trace_scope,
)
from app.eval_adapter.shared import (
    model_identity as build_model_identity,
)
from app.eval_adapter.shared import (
    prompt_identity as build_prompt_identity,
)
from app.eval_adapter.shared import (
    request_id as build_request_id,
)
from app.eval_adapter.shared import (
    request_snapshot as build_request_snapshot,
)
from app.llm.agent_runner import extract_run_usage
from app.llm.router import ModelSelectionError, validate_model_selection
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection
from app.services.analysis.debug_snapshots import (
    build_preprocess_summary,
    build_runtime_summary,
    build_trace_refs,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)
from app.services.prompting.runtime_context import (
    grammar_rag_enabled_override,
    prompt_runtime_override,
)
from app.services.prompting.strategy_builder import (
    StrategyBundle,
    build_grammar_bundle_async,
    build_translation_bundle,
    build_vocabulary_bundle,
)
from app.services.analysis.runtime.runners import (
    run_grammar_agent,
    run_translation_agent,
    run_vocabulary_agent,
)

NODE_PROBE_WORKFLOW_NAME = "article_analysis.node_probe"
NODE_PROBE_WORKFLOW_VERSION = "1.0.0"


def _workflow_identity(topology_mode: str) -> WorkflowIdentity:
    return WorkflowIdentity(
        workflow_name=NODE_PROBE_WORKFLOW_NAME,
        workflow_version=NODE_PROBE_WORKFLOW_VERSION,
        topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
    )


def _schema_identity(topology_mode: str) -> SchemaIdentity:
    return SchemaIdentity(
        schema_version="article-analysis-node-probe-v1",
        topology_mode=topology_mode if topology_mode in {"learning", "academic"} else "unknown",
    )


def _dump_model(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {"value": value}


def _example_summary(selection_mode: str, examples: list[Any]) -> dict[str, Any]:
    dumped_examples = [
        asdict(example) if hasattr(example, "__dataclass_fields__") else _dump_model(example)
        for example in examples
    ]
    return {
        "selection_mode": selection_mode,
        "example_count": len(examples),
        "examples": dumped_examples,
    }


def _runtime_summary(
    usage: dict[str, Any] | None,
    *,
    latency_ms: int,
    node_name: str,
) -> dict[str, Any]:
    summary = build_runtime_summary(
        {
            "available": bool(usage),
            "per_agent": {node_name: usage} if usage else {},
            "aggregate": usage or {},
        },
        latency_ms=latency_ms,
        billed_points=0,
    )
    summary.pop("billed_points", None)
    return summary


def _failure_result(
    request: ArticleAnalysisNodeProbeRequest,
    *,
    request_id: str,
    status: str,
    error: BaseException,
    latency_ms: int,
    model_identity: ModelIdentity | None = None,
    topology_mode: str = "unknown",
    llm_config_snapshot: dict[str, Any] | None = None,
) -> ArticleAnalysisNodeProbeResult:
    return ArticleAnalysisNodeProbeResult(
        status=status,
        error=EvalError(code=type(error).__name__, message=str(error)),
        request_snapshot=build_request_snapshot(request, request_id_value=request_id),
        workflow_identity=_workflow_identity(topology_mode),
        schema_identity=_schema_identity(topology_mode),
        prompt_identity=build_prompt_identity(request, prompt_version=get_prompt_version()),
        model_identity=model_identity,
        llm_config_snapshot=llm_config_snapshot,
        node_name=request.node_name,
        runtime_summary={"latency_ms": latency_ms},
        trace_refs=build_trace_refs(request_id=request_id),
    )


async def _build_bundle_and_deps(
    node_name: str,
    plan: Any,
    sentences_data: list[dict[str, Any]],
) -> tuple[StrategyBundle, Any, str]:
    if node_name == "vocabulary":
        bundle = build_vocabulary_bundle(plan, sentences=sentences_data)
        deps = VocabularyAgentDeps(
            sentences=sentences_data,
            prompt_strategy=bundle.prompt_strategy,
            examples=bundle.example_strategy.examples,
        )
        prompt_preview = build_vocabulary_prompt(deps)
    elif node_name == "translation":
        bundle = build_translation_bundle(plan, sentences=sentences_data)
        deps = TranslationAgentDeps(
            sentences=sentences_data,
            prompt_strategy=bundle.prompt_strategy,
            examples=bundle.example_strategy.examples,
        )
        prompt_preview = build_translation_prompt(deps)
    else:
        bundle = await build_grammar_bundle_async(plan, sentences=sentences_data)
        deps = GrammarAgentDeps(
            sentences=sentences_data,
            prompt_strategy=bundle.prompt_strategy,
            examples=bundle.example_strategy.examples,
        )
        prompt_preview = build_grammar_prompt(deps)
    return bundle, deps, prompt_preview


async def _run_agent(
    node_name: str,
    deps: Any,
    model_selection: ModelSelection | None,
    timeout_seconds: float | None,
) -> Any:
    if node_name == "vocabulary":
        runner = run_vocabulary_agent
    elif node_name == "translation":
        runner = run_translation_agent
    else:
        runner = run_grammar_agent

    if timeout_seconds is None:
        return await runner(deps, model_selection=model_selection)
    return await asyncio.wait_for(
        runner(deps, model_selection=model_selection),
        timeout=timeout_seconds,
    )


async def run_article_analysis_node_probe(
    request: ArticleAnalysisNodeProbeRequest,
) -> ArticleAnalysisNodeProbeResult:
    started_at = perf_counter()
    request_id = build_request_id(request)
    model_selection = request.model_selection
    model_identity: ModelIdentity | None = None
    topology_mode = "unknown"
    requires_live_model = not request.dry_run

    try:
        # Dry-run only builds prompt/deps/debug output and does not actually call
        # the LLM, so resolve-only validation is enough there. Real execution
        # paths require a buildable model.
        validate_model_selection(
            get_settings(),
            model_selection,
            (MODEL_ROUTE_ANNOTATION_GENERATION,),
            buildable=requires_live_model,
        )
        model_identity = build_model_identity(model_selection, settings=get_settings())
    except ModelSelectionError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        _snap = build_llm_config_snapshot_safe(
            model_selection, settings=get_settings(),
        )
        return _failure_result(
            request,
            request_id=request_id,
            status="failed",
            error=exc,
            latency_ms=latency_ms,
            llm_config_snapshot=_snap.model_dump(mode="json") if _snap else None,
        )

    _llm_snapshot = build_llm_config_snapshot(
        model_selection, settings=get_settings(),
    )

    try:
        plan = build_goal_execution_plan(request.reading_goal, request.reading_variant)
        topology_mode = getattr(plan, "topology_mode", "unknown")
        if topology_mode != "learning":
            raise ValueError(
                "node_probe v1 only supports learning topology; "
                "academic should use a dedicated academic lab/workflow"
            )

        with (
            prompt_runtime_override(request.prompt_override),
            grammar_rag_enabled_override(rag_override(request)),
            trace_scope(request),
        ):
            prepared_input = prepare_input(request.text)
            sentences_data = [
                {"sentence_id": sentence.sentence_id, "text": sentence.text}
                for sentence in prepared_input.sentences
            ]

            bundle, deps, prompt_preview = await _build_bundle_and_deps(
                request.node_name, plan, sentences_data,
            )

            node_output: dict[str, Any] | None = None
            usage: dict[str, Any] | None = None
            if not request.dry_run:
                result = await _run_agent(
                    request.node_name,
                    deps,
                    model_selection,
                    request.timeout_seconds,
                )
                output = result.output if hasattr(result, "output") else result
                node_output = _dump_model(output)
                usage = extract_run_usage(result)

    except TimeoutError as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return _failure_result(
            request,
            request_id=request_id,
            status="timeout",
            error=exc,
            latency_ms=latency_ms,
            model_identity=model_identity,
            topology_mode=topology_mode,
            llm_config_snapshot=(
                _llm_snapshot.model_dump(mode="json") if _llm_snapshot else None
            ),
        )
    except Exception as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        return _failure_result(
            request,
            request_id=request_id,
            status="failed",
            error=exc,
            latency_ms=latency_ms,
            model_identity=model_identity,
            topology_mode=topology_mode,
            llm_config_snapshot=(
                _llm_snapshot.model_dump(mode="json") if _llm_snapshot else None
            ),
        )

    latency_ms = int((perf_counter() - started_at) * 1000)
    result_state = {
        "prepared_input": prepared_input,
        "goal_execution_plan": plan,
    }
    return ArticleAnalysisNodeProbeResult(
        status="succeeded",
        request_snapshot=build_request_snapshot(request, request_id_value=request_id),
        workflow_identity=_workflow_identity(topology_mode),
        schema_identity=_schema_identity(topology_mode),
        prompt_identity=build_prompt_identity(request, prompt_version=get_prompt_version()),
        model_identity=model_identity,
        node_name=request.node_name,
        node_output=node_output,
        prompt_preview=prompt_preview,
        agent_instructions=load_agent_instructions(request.node_name),
        prepared_sentences=sentences_data,
        example_summary=_example_summary(
            bundle.example_strategy.selection_mode,
            bundle.example_strategy.examples,
        ),
        preprocess_summary=build_preprocess_summary(request.text, result_state),
        runtime_summary=_runtime_summary(usage, latency_ms=latency_ms, node_name=request.node_name),
        rag_debug=bundle.rag_debug,
        trace_refs=build_trace_refs(request_id=request_id),
        warnings=[],
        llm_config_snapshot=_llm_snapshot.model_dump(mode="json") if _llm_snapshot else None,
    )
