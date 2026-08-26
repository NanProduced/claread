"""Daily Reader Workflow - LangGraph StateGraph (teaching contract v2).

P-5B topology (replaces the v1 highlight / paragraph_notes / takeaways /
quality_review / refinement chain — the v1 nodes are removed from the run
path, never coexisting with the v2 gates):

  light_normalize
  → blueprint
  → language_support
  → translation
  → semantic_review
  → (FAIL verdict only) refinement   # at most one refinement per article
  → daily_projection

The five defense lines are wired to the shared stdlib-only teaching
package (``app/services/daily_reader/teaching/``) — no copied
implementations:

1. DTO hard boundary — pydantic stage DTOs with in-call output retries
   (``app/schemas/internal/daily_lesson_v2.py``); counts / UnitId /
   required fields / title contract fail as output-validation errors.
2. Deterministic contract checks feed the semantic-review input and are
   replayed fail-closed after refinement
   (``teaching.prototype.validate_teaching_contract`` /
   ``derive_translation_unit_ids``).
3. Post-patch DTO re-check + patch rejection + pre-image restore + FAIL +
   batch continue (P-4I semantics) in ``refinement_node``.
4. Stop/rejection diagnostics — every fail-closed stop records
   ``abort_reason`` + ``abort_diagnostics`` for the pipeline usage event.
5. Usage conservation + budget gate — per-stage usage ledgers aggregated
   by ``_aggregate_usage`` and the frozen per-article caps derived from
   the evals P-4E batch budget (80/393216/20 for the 4-case frozen batch
   → 20/98304/5 per article).

Abort semantics: a fail-closed article stops with ``abort=True`` and is
NOT stored (v1 precedent); the pipeline batch continues with the next
candidate.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable
from pydantic import ValidationError
from pydantic_ai.usage import RunUsage

from app.agents.daily_teaching_agents import (
    DailyBlueprintAgentDeps,
    DailyLanguageSupportAgentDeps,
    DailySemanticReviewAgentDeps,
    DailyTeachingRefinementAgentDeps,
    DailyTranslationAgentDeps,
    build_daily_blueprint_prompt,
    build_daily_language_support_prompt,
    build_daily_semantic_review_prompt,
    build_daily_teaching_refinement_prompt,
    build_daily_translation_prompt,
    get_daily_blueprint_agent,
    get_daily_language_support_agent,
    get_daily_semantic_review_agent,
    get_daily_teaching_refinement_agent,
    get_daily_translation_agent,
)
from app.config.settings import get_settings
from app.llm.agent_runner import extract_run_usage, run_agent_with_route
from app.llm.router import resolve_model_config
from app.llm.routes import (
    DAILY_READER_MODEL_PRESET,
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_REVIEW,
    MODEL_ROUTE_DAILY_TRANSLATION,
    ModelRoute,
)
from app.llm.types import ModelSelection
from app.observability.workflow_tracing import build_llm_trace_metadata, build_usage_metadata
from app.schemas.internal.daily_lesson_v2 import BlueprintDraft, LanguageSupportDraft
from app.services.daily_reader.extraction import (
    clean_extracted_article,
    detect_transcript_markers,
)
from app.services.daily_reader.teaching.gates import run_hard_gates
from app.services.daily_reader.teaching.prototype import (
    build_refinement_evidence,
    derive_translation_unit_ids,
    make_review_evidence,
    validate_teaching_contract,
)
from app.services.daily_reader.teaching.refinement_addressing import (
    collect_fields_to_fix,
    preapply_patch_violations,
)
from app.services.daily_reader.teaching.schema import validate_artifact

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "daily_reader"
WORKFLOW_VERSION = "3.0.0"
DAILY_MODEL_SELECTION = ModelSelection(preset=DAILY_READER_MODEL_PRESET)

# Defense line 5: frozen per-article budget, derived from the evals P-4E
# batch caps (model_requests 80 / output_tokens 393216 / logical calls 20
# for the 4-case frozen batch → per article 20 / 98304 / 5).
TEACHING_V2_MODEL_REQUESTS_MAX = 20
TEACHING_V2_OUTPUT_TOKENS_MAX = 98304
TEACHING_V2_LOGICAL_STAGES_MAX = 5

STAGE_USAGE_KEYS = (
    "blueprint_usage",
    "language_support_usage",
    "translation_usage",
    "semantic_review_usage",
    "refinement_usage",
)

MAX_PARAGRAPH_CHARS = 900
MAX_PARAGRAPH_SENTENCES = 8
READING_UNIT_TARGET_CHARS = 520
READING_UNIT_MIN_CHARS = 260
SECTION_HEADING_MAX_CHARS = 80


class DailyReaderState(TypedDict, total=False):
    original_text: str
    title: str
    subtitle: str
    source: str
    source_url: str
    cover_image_url: str | None
    tags: list[str]
    difficulty: str
    read_time_minutes: int
    pipeline_source: str
    pipeline_meta: dict

    reading_units: list[dict]
    lesson_blueprint: dict | None
    language_support: dict | None
    learning_package: dict | None
    derived_translation_unit_ids: list[str]
    teaching_contract_issues: list[dict] | None
    semantic_review_result: dict | None
    refinement_result: dict | None
    lesson_v2: dict | None
    body_json: dict | None

    abort: bool
    abort_reason: str | None
    abort_diagnostics: dict | None

    blueprint_usage: dict[str, object]
    language_support_usage: dict[str, object]
    translation_usage: dict[str, object]
    semantic_review_usage: dict[str, object]
    refinement_usage: dict[str, object]

    usage_summary: dict | None


def _metadata_from_owned_usage(run_usage: RunUsage) -> dict[str, object] | None:
    if int(getattr(run_usage, "requests", 0) or 0) <= 0:
        return None
    return build_usage_metadata(run_usage)


def _aggregate_usage(state: DailyReaderState) -> dict[str, Any]:
    per_agent: dict[str, dict[str, object]] = {}
    for key in STAGE_USAGE_KEYS:
        usage = state.get(key)
        if usage and isinstance(usage, dict):
            per_agent[key.replace("_usage", "")] = usage

    if not per_agent:
        return {
            "available": False,
            "per_agent": {},
            "aggregate": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "model_requests": 0,
                "tool_calls": 0,
            },
        }

    def _sum(field: str) -> int:
        return sum(int(u.get(field, 0) or 0) for u in per_agent.values())

    return {
        "available": True,
        "per_agent": per_agent,
        "aggregate": {
            "input_tokens": _sum("input_tokens"),
            "output_tokens": _sum("output_tokens"),
            "total_tokens": _sum("total_tokens"),
            "model_requests": _sum("model_requests"),
            "tool_calls": _sum("tool_calls"),
        },
    }


def _stage_budget_exceeded(state: DailyReaderState) -> bool:
    """Defense line 5: hard budget gate over the per-article caps."""
    model_requests = 0
    output_tokens = 0
    for key in STAGE_USAGE_KEYS:
        usage = state.get(key)
        if usage and isinstance(usage, dict):
            model_requests += int(usage.get("model_requests", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
    return (
        model_requests > TEACHING_V2_MODEL_REQUESTS_MAX
        or output_tokens > TEACHING_V2_OUTPUT_TOKENS_MAX
    )


def _abort(reason: str, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Defense line 4: every fail-closed stop carries reason + diagnostics."""
    return {
        "abort": True,
        "abort_reason": reason,
        "abort_diagnostics": diagnostics or {},
    }


def _build_daily_llm_metadata(
    state: DailyReaderState,
    *,
    node_name: str,
    route: ModelRoute,
    extra: dict[str, Any] | None = None,
) -> dict[str, object]:
    try:
        model_config = resolve_model_config(get_settings(), route, DAILY_MODEL_SELECTION)
    except Exception as exc:
        logger.warning("daily model metadata resolution failed for route=%s: %s", route, exc)
        model_config = None
    return build_llm_trace_metadata(
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        request_id=state.get("source_url", "") or state.get("title", ""),
        source_type=state.get("pipeline_source", "pipeline"),
        reading_goal="daily_reading",
        reading_variant="standard",
        profile_id="daily_reader",
        model_name=model_config.model_name if model_config else "unconfigured",
        model_provider=model_config.provider if model_config else "unconfigured",
        extra={
            "node": node_name,
            "model_profile": model_config.profile_name if model_config else "unconfigured",
            "article_title": state.get("title", "")[:80],
            **(extra or {}),
        },
    )


def _span_result(result: Any) -> dict[str, Any]:
    usage = extract_run_usage(result)
    return {
        "output": result.output if hasattr(result, "output") else result,
        "usage_metadata": usage,
    }


@traceable(name="daily_blueprint_llm_call", run_type="llm")
async def _run_blueprint_llm_span(
    *,
    deps: DailyBlueprintAgentDeps,
    prompt: str,
    metadata: dict[str, object],
    run_usage: RunUsage | None = None,
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_blueprint_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_ANALYSIS,
        model_selection=DAILY_MODEL_SELECTION,
        run_usage=run_usage,
    )
    return _span_result(result)


@traceable(name="daily_language_support_llm_call", run_type="llm")
async def _run_language_support_llm_span(
    *,
    deps: DailyLanguageSupportAgentDeps,
    prompt: str,
    metadata: dict[str, object],
    run_usage: RunUsage | None = None,
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_language_support_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_ANNOTATION,
        model_selection=DAILY_MODEL_SELECTION,
        run_usage=run_usage,
    )
    return _span_result(result)


@traceable(name="daily_translation_llm_call", run_type="llm")
async def _run_translation_llm_span(
    *,
    deps: DailyTranslationAgentDeps,
    prompt: str,
    metadata: dict[str, object],
    run_usage: RunUsage | None = None,
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_translation_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_TRANSLATION,
        model_selection=DAILY_MODEL_SELECTION,
        run_usage=run_usage,
    )
    return _span_result(result)


@traceable(name="daily_semantic_review_llm_call", run_type="llm")
async def _run_semantic_review_llm_span(
    *,
    deps: DailySemanticReviewAgentDeps,
    prompt: str,
    metadata: dict[str, object],
    run_usage: RunUsage | None = None,
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_semantic_review_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_REVIEW,
        model_selection=DAILY_MODEL_SELECTION,
        run_usage=run_usage,
    )
    return _span_result(result)


@traceable(name="daily_teaching_refinement_llm_call", run_type="llm")
async def _run_teaching_refinement_llm_span(
    *,
    deps: DailyTeachingRefinementAgentDeps,
    prompt: str,
    metadata: dict[str, object],
    run_usage: RunUsage | None = None,
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_teaching_refinement_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_REVIEW,
        model_selection=DAILY_MODEL_SELECTION,
        run_usage=run_usage,
    )
    return _span_result(result)


def light_normalize_node(state: DailyReaderState) -> dict:
    text = state.get("original_text", "")
    markers = detect_transcript_markers(text)
    if markers:
        # A-1: transcripts are not articles — reject before any LLM spend.
        meta = dict(state.get("pipeline_meta") or {})
        meta["rejection"] = {
            "code": "transcript_rejected",
            "reason": (
                "Transcript markers detected (HOST/BYLINE/SOUNDBITE); transcripts are not articles"
            ),
            "markers": markers,
        }
        logger.warning(
            "daily_reader: candidate rejected as transcript (markers=%s, title=%s)",
            markers,
            state.get("title", "")[:60],
        )
        return {
            **_abort(
                "transcript_rejected",
                {"markers": markers, "rejection": meta["rejection"]},
            ),
            "reading_units": [],
            "pipeline_meta": meta,
        }

    text = clean_extracted_article(text)
    title = state.get("title", "")
    raw_blocks = _split_into_raw_blocks(text)
    classified_blocks = _classify_raw_blocks(raw_blocks, title)
    reading_units_plan = _plan_reading_units(classified_blocks)
    # Teaching-contract unit ids: u\d{2,3} (mirrors teaching/schema.py
    # UNIT_ID_RE); every downstream anchor references these ids.
    reading_units = [
        {"id": f"u{i + 1:02d}", "text": unit["text"]} for i, unit in enumerate(reading_units_plan)
    ]
    return {"reading_units": reading_units}


def _is_aborted(state: DailyReaderState) -> bool:
    return bool(state.get("abort"))


def _selected_units_for_language_support(reading_units: list[dict], blueprint: dict) -> list[dict]:
    """Units referenced by the blueprint (selection ∩ evidence anchors).

    Production carries no gold dirty_fragments, so every unit is
    substantive (mirrors the P-4E generation-view selection rule).
    """
    referenced: list[str] = list(blueprint.get("selected_paragraph_ids") or [])
    for node in blueprint.get("structure_map") or []:
        referenced.extend(node.get("paragraph_ids") or [])
    for checkpoint in blueprint.get("comprehension_checkpoints") or []:
        referenced.extend(checkpoint.get("evidence_paragraph_ids") or [])
        referenced.extend(checkpoint.get("answer_evidence_paragraph_ids") or [])
    seen: set[str] = set()
    selected: list[dict] = []
    for unit in reading_units:
        uid = unit.get("id")
        if uid in referenced and uid not in seen:
            seen.add(uid)
            selected.append(unit)
    return selected


async def blueprint_node(state: DailyReaderState) -> dict:
    reading_units = state.get("reading_units", [])
    if not reading_units:
        return _abort("blueprint_no_reading_units")

    run_usage = RunUsage()
    try:
        deps = DailyBlueprintAgentDeps(
            article={
                "title": state.get("title", ""),
                "source": state.get("source", ""),
                "reading_units": reading_units,
            }
        )
        prompt = build_daily_blueprint_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="blueprint",
            route=MODEL_ROUTE_DAILY_ANALYSIS,
            extra={"unit_count": len(reading_units)},
        )
        result = await _run_blueprint_llm_span(
            deps=deps, prompt=prompt, metadata=metadata, run_usage=run_usage
        )
        blueprint = result.get("output")
        usage = result.get("usage_metadata")
        if not blueprint:
            raise RuntimeError("blueprint returned no output")

        updates: dict[str, Any] = {"lesson_blueprint": blueprint.model_dump()}
        if usage:
            updates["blueprint_usage"] = usage
            if _stage_budget_exceeded({**state, **updates}):
                updates.update(
                    _abort("teaching_v2_budget_exceeded", _budget_diagnostics(state, updates))
                )
        return updates
    except Exception as e:
        logger.error("blueprint_node failed: %s", e, exc_info=True)
        updates = _abort("blueprint_stage_failed", {"error": str(e)[:300]})
        usage = _metadata_from_owned_usage(run_usage)
        if usage:
            updates["blueprint_usage"] = usage
        return updates


def _budget_diagnostics(state: DailyReaderState, updates: dict[str, Any]) -> dict[str, Any]:
    aggregate = _aggregate_usage({**state, **updates})["aggregate"]
    return {
        "aggregate": aggregate,
        "caps": {
            "model_requests": TEACHING_V2_MODEL_REQUESTS_MAX,
            "output_tokens": TEACHING_V2_OUTPUT_TOKENS_MAX,
            "logical_stages": TEACHING_V2_LOGICAL_STAGES_MAX,
        },
    }


async def language_support_node(state: DailyReaderState) -> dict:
    blueprint = state.get("lesson_blueprint") or {}
    reading_units = state.get("reading_units", [])

    selected_units = _selected_units_for_language_support(reading_units, blueprint)
    if not selected_units:
        return _abort("language_support_selected_units_empty")

    run_usage = RunUsage()
    try:
        deps = DailyLanguageSupportAgentDeps(
            selected_units=selected_units,
            effective_difficulty=blueprint.get("effective_difficulty", ""),
        )
        prompt = build_daily_language_support_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="language_support",
            route=MODEL_ROUTE_DAILY_ANNOTATION,
            extra={"selected_unit_count": len(selected_units)},
        )
        result = await _run_language_support_llm_span(
            deps=deps, prompt=prompt, metadata=metadata, run_usage=run_usage
        )
        language_support = result.get("output")
        usage = result.get("usage_metadata")
        if not language_support:
            raise RuntimeError("language_support returned no output")

        # Fail-closed anchor identity (P-4E structural rule): every anchor
        # must resolve to a known reading-unit id.
        known_ids = {unit.get("id") for unit in reading_units}
        anchors = [t.paragraph_id for t in language_support.language_targets]
        anchors += [sm.paragraph_id for sm in language_support.sentence_maps]
        unknown = sorted({a for a in anchors if a not in known_ids})
        if unknown:
            updates = _abort(
                "language_support_anchor_unresolved",
                {"unknown_unit_ids": unknown[:5]},
            )
            if usage:
                updates["language_support_usage"] = usage
            return updates

        updates: dict[str, Any] = {"language_support": language_support.model_dump()}
        if usage:
            updates["language_support_usage"] = usage
            if _stage_budget_exceeded({**state, **updates}):
                updates.update(
                    _abort("teaching_v2_budget_exceeded", _budget_diagnostics(state, updates))
                )
        return updates
    except Exception as e:
        logger.error("language_support_node failed: %s", e, exc_info=True)
        updates = _abort("language_support_stage_failed", {"error": str(e)[:300]})
        usage = _metadata_from_owned_usage(run_usage)
        if usage:
            updates["language_support_usage"] = usage
        return updates


async def translation_node(state: DailyReaderState) -> dict:
    blueprint = state.get("lesson_blueprint") or {}
    language_support = state.get("language_support") or {}
    reading_units = state.get("reading_units", [])
    unit_ids = [unit.get("id") for unit in reading_units]

    try:
        derived_targets = derive_translation_unit_ids(
            blueprint.get("effective_difficulty", ""),
            reading_units,
            # Production carries no gold: every unit is substantive.
            substantive_unit_ids=unit_ids,
            checkpoint_evidence_ids=[
                pid
                for checkpoint in blueprint.get("comprehension_checkpoints") or []
                for pid in checkpoint.get("evidence_paragraph_ids") or []
            ],
            language_target_paragraph_ids=[
                target.get("paragraph_id")
                for target in language_support.get("language_targets") or []
            ],
            sentence_map_paragraph_ids=[
                sm.get("paragraph_id") for sm in language_support.get("sentence_maps") or []
            ],
            high_difficulty_unit_ids=list(language_support.get("high_difficulty_unit_ids") or []),
        )
    except ValueError as e:
        return _abort("translation_targets_derivation_failed", {"error": str(e)[:300]})

    units_by_id = {unit.get("id"): unit for unit in reading_units}
    target_units = [units_by_id[uid] for uid in derived_targets]
    sentence_maps_payload = [
        {"paragraph_id": sm.get("paragraph_id"), "sentence": sm.get("sentence")}
        for sm in language_support.get("sentence_maps") or []
    ]

    run_usage = RunUsage()
    try:
        deps = DailyTranslationAgentDeps(
            target_units=target_units,
            sentence_maps=sentence_maps_payload,
            effective_difficulty=blueprint.get("effective_difficulty", ""),
        )
        prompt = build_daily_translation_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="translation",
            route=MODEL_ROUTE_DAILY_TRANSLATION,
            extra={"target_unit_count": len(target_units)},
        )
        result = await _run_translation_llm_span(
            deps=deps, prompt=prompt, metadata=metadata, run_usage=run_usage
        )
        translation = result.get("output")
        usage = result.get("usage_metadata")
        if not translation:
            raise RuntimeError("translation returned no output")

        returned_ids = [item.paragraph_id for item in translation.translations]
        duplicate_ids = sorted({pid for pid in returned_ids if returned_ids.count(pid) > 1})
        if duplicate_ids:
            updates = _abort(
                "translation_duplicate_targets", {"duplicate_unit_ids": duplicate_ids[:5]}
            )
            if usage:
                updates["translation_usage"] = usage
            return updates
        missing_ids = sorted(set(derived_targets) - set(returned_ids))
        extra_ids = sorted(set(returned_ids) - set(derived_targets))
        if missing_ids or extra_ids:
            updates = _abort(
                "translation_target_set_mismatch",
                {"missing_unit_ids": missing_ids[:5], "extra_unit_ids": extra_ids[:5]},
            )
            if usage:
                updates["translation_usage"] = usage
            return updates

        package: dict[str, Any] = {
            "comprehension_checkpoints": blueprint.get("comprehension_checkpoints") or [],
            "high_difficulty_unit_ids": list(
                language_support.get("high_difficulty_unit_ids") or []
            ),
            "language_targets": language_support.get("language_targets") or [],
            "sentence_maps": language_support.get("sentence_maps") or [],
            "transfer_task": blueprint.get("transfer_task") or {},
            "translations_by_paragraph_id": {
                item.paragraph_id: item.translation for item in translation.translations
            },
        }
        # Defense line 2: deterministic contract issues feed the semantic
        # review input (never silently swallowed).
        issues = validate_teaching_contract(blueprint, package, reading_units=reading_units)

        updates: dict[str, Any] = {
            "learning_package": package,
            "derived_translation_unit_ids": derived_targets,
            "teaching_contract_issues": issues,
        }
        if usage:
            updates["translation_usage"] = usage
            if _stage_budget_exceeded({**state, **updates}):
                updates.update(
                    _abort("teaching_v2_budget_exceeded", _budget_diagnostics(state, updates))
                )
        return updates
    except Exception as e:
        logger.error("translation_node failed: %s", e, exc_info=True)
        updates = _abort("translation_stage_failed", {"error": str(e)[:300]})
        usage = _metadata_from_owned_usage(run_usage)
        if usage:
            updates["translation_usage"] = usage
        return updates


async def semantic_review_node(state: DailyReaderState) -> dict:
    blueprint = state.get("lesson_blueprint") or {}
    package = state.get("learning_package") or {}

    run_usage = RunUsage()
    try:
        deps = DailySemanticReviewAgentDeps(
            original_text=state.get("original_text", ""),
            blueprint=blueprint,
            learning_package=package,
            deterministic_checks={
                "derived_translation_unit_ids": state.get("derived_translation_unit_ids") or [],
                "teaching_contract_issues": state.get("teaching_contract_issues") or [],
            },
        )
        prompt = build_daily_semantic_review_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="semantic_review",
            route=MODEL_ROUTE_DAILY_REVIEW,
            extra={
                "teaching_contract_issue_count": len(state.get("teaching_contract_issues") or []),
            },
        )
        result = await _run_semantic_review_llm_span(
            deps=deps, prompt=prompt, metadata=metadata, run_usage=run_usage
        )
        review = result.get("output")
        usage = result.get("usage_metadata")
        if not review:
            raise RuntimeError("semantic_review returned no output")

        try:
            evidence = make_review_evidence(**review.model_dump())
        except (TypeError, ValueError) as e:
            updates = _abort("semantic_review_evidence_invalid", {"error": str(e)[:300]})
            if usage:
                updates["semantic_review_usage"] = usage
            return updates

        updates: dict[str, Any] = {"semantic_review_result": evidence}
        if usage:
            updates["semantic_review_usage"] = usage
            if _stage_budget_exceeded({**state, **updates}):
                updates.update(
                    _abort("teaching_v2_budget_exceeded", _budget_diagnostics(state, updates))
                )
        return updates
    except Exception as e:
        logger.error("semantic_review_node failed: %s", e, exc_info=True)
        updates = _abort("semantic_review_stage_failed", {"error": str(e)[:300]})
        usage = _metadata_from_owned_usage(run_usage)
        if usage:
            updates["semantic_review_usage"] = usage
        return updates


def _apply_patch(container: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        current = container.get(key)
        if key == "translations_by_paragraph_id" and isinstance(value, dict):
            # The corrected translation map is a whole-field replacement so the
            # model can also remove entries; every other field merges.
            container[key] = dict(value)
        elif isinstance(current, dict) and isinstance(value, dict):
            container[key] = {**current, **dict(value)}
        else:
            container[key] = value


async def refinement_node(state: DailyReaderState) -> dict:
    blueprint = state.get("lesson_blueprint") or {}
    package = state.get("learning_package") or {}
    review = state.get("semantic_review_result") or {}
    reading_units = state.get("reading_units", [])

    run_usage = RunUsage()
    try:
        # Finite field-addressing rule (P-4E) plus frozen derivation-input
        # pre-check (P-5D-R3): shared with the evals runner.
        fields_to_fix, addressing_error, addressing_field = collect_fields_to_fix(
            review.get("issues", []), package, blueprint
        )
        if addressing_error == "refinement_field_unknown":
            updates = _abort("refinement_field_unknown", {"field": addressing_field})
            usage = _metadata_from_owned_usage(run_usage)
            if usage:
                updates["refinement_usage"] = usage
            return updates
        if addressing_error == "frozen_derivation_field":
            updates = _abort("frozen_derivation_field", {"field": addressing_field})
            usage = _metadata_from_owned_usage(run_usage)
            if usage:
                updates["refinement_usage"] = usage
            return updates
        if not fields_to_fix:
            return _abort("refinement_fields_empty")

        evidence_context = {
            "failed_contracts": [
                result["contract"]
                for result in review.get("contract_results", [])
                if not result["passed"]
            ]
        }
        deps = DailyTeachingRefinementAgentDeps(
            review_before_refinement=review,
            fields_to_fix=fields_to_fix,
            evidence_context=evidence_context,
        )
        prompt = build_daily_teaching_refinement_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="refinement",
            route=MODEL_ROUTE_DAILY_REVIEW,
            extra={"fields_to_fix": sorted(fields_to_fix)},
        )
        result = await _run_teaching_refinement_llm_span(
            deps=deps, prompt=prompt, metadata=metadata, run_usage=run_usage
        )
        refinement = result.get("output")
        usage = result.get("usage_metadata")
        if not refinement:
            raise RuntimeError("refinement returned no output")
        patch = refinement.refinement_patch

        # Defense line 3 (P-4I): patch violations do not stop the batch — the
        # patch is rejected, both containers are restored to their serialized
        # pre-patch image, the refinement call's usage stays booked, and the
        # article lands fail-closed with a FAIL after-review.
        violations = preapply_patch_violations(patch, package, blueprint, fields_to_fix)
        if not violations:
            pre_blueprint = json.loads(json.dumps(blueprint))
            pre_package = json.loads(json.dumps(package))
            for key, value in patch.items():
                if key in package:
                    _apply_patch(package, {key: value})
                elif key in blueprint:
                    _apply_patch(blueprint, {key: value})
            try:
                BlueprintDraft.model_validate(blueprint)
            except ValidationError as exc:
                err = exc.errors()[0]
                violations.append(
                    {
                        "container": "blueprint",
                        "error_type": err.get("type"),
                        "loc": [str(part) for part in err.get("loc", [])],
                    }
                )
            try:
                LanguageSupportDraft.model_validate(
                    {
                        "high_difficulty_unit_ids": package["high_difficulty_unit_ids"],
                        "language_targets": package["language_targets"],
                        "sentence_maps": package["sentence_maps"],
                    }
                )
            except ValidationError as exc:
                err = exc.errors()[0]
                violations.append(
                    {
                        "container": "learning_package",
                        "error_type": err.get("type"),
                        "loc": [str(part) for part in err.get("loc", [])],
                    }
                )
            invalid_translations = sorted(
                pid
                for pid, value in package["translations_by_paragraph_id"].items()
                if not isinstance(value, str) or not value.strip()
            )
            if invalid_translations:
                violations.append(
                    {
                        "container": "learning_package",
                        "error_type": "invalid_translation_value",
                        "loc": invalid_translations[:5],
                    }
                )
            if violations:
                # Restore from the serialized pre-image: byte-faithful,
                # never a reconstruction.
                blueprint.clear()
                blueprint.update(pre_blueprint)
                package.clear()
                package.update(pre_package)
        patch_rejected = bool(violations)
        if patch_rejected and set(patch) - set(fields_to_fix):
            # The canonical refinement-evidence contract only describes a
            # patch inside fields_to_fix. A patch that touched anything
            # else cannot produce directed evidence: fail closed without
            # an after-review (the article is not stored either way).
            updates: dict[str, Any] = _abort(
                "teaching_v2_after_review_fail",
                {
                    "patch_rejected": True,
                    "violations": violations,
                    "restored_fields": sorted(patch),
                },
            )
            if usage:
                updates["refinement_usage"] = usage
            return updates

        # Defense line 2 replay: deterministic contract + translation-target
        # derivation re-run on the (possibly patched) containers; a replay
        # contradiction downgrades the rechecks fail-closed.
        replay_issues = validate_teaching_contract(blueprint, package, reading_units=reading_units)
        try:
            replay_targets = derive_translation_unit_ids(
                blueprint.get("effective_difficulty", ""),
                reading_units,
                substantive_unit_ids=[unit.get("id") for unit in reading_units],
                checkpoint_evidence_ids=[
                    pid
                    for checkpoint in blueprint.get("comprehension_checkpoints") or []
                    for pid in checkpoint.get("evidence_paragraph_ids") or []
                ],
                language_target_paragraph_ids=[
                    target.get("paragraph_id") for target in package.get("language_targets") or []
                ],
                sentence_map_paragraph_ids=[
                    sm.get("paragraph_id") for sm in package.get("sentence_maps") or []
                ],
                high_difficulty_unit_ids=list(package.get("high_difficulty_unit_ids") or []),
            )
        except ValueError as e:
            updates = _abort("deterministic_replay_failed", {"error": str(e)[:300]})
            if usage:
                updates["refinement_usage"] = usage
            return updates
        current_ids = set(package.get("translations_by_paragraph_id") or {})
        non_string_keys = sorted(k for k in current_ids if not isinstance(k, str))
        missing_ids = sorted(set(replay_targets) - current_ids)
        extra_ids = sorted(current_ids - set(replay_targets))
        replay_passed = (
            not replay_issues and not non_string_keys and not missing_ids and not extra_ids
        )

        effective_rechecks = [item.model_dump() for item in refinement.rechecked_contract_results]
        effective_remaining = [item.model_dump() for item in refinement.remaining_issues]
        if patch_rejected:
            # The directed rechecks describe a patch that was rejected
            # host-side; they are discarded and replaced with fail-closed
            # rejection evidence per failed contract.
            rejection_note = "; ".join(
                f"{v['container']}:{v['error_type']}:{','.join(str(p) for p in v['loc'])}"
                for v in violations
            )
            failed_contracts = [
                result["contract"]
                for result in review.get("contract_results", [])
                if not result["passed"]
            ]
            effective_rechecks = [
                {
                    "contract": contract,
                    "passed": False,
                    "rationale": (
                        f"refinement patch rejected ({rejection_note}); directed fix not applied"
                    ),
                }
                for contract in failed_contracts
            ]
            effective_remaining = [
                {
                    "contract": contract,
                    "field": "refinement_patch",
                    "problem": f"refinement patch rejected ({rejection_note})",
                }
                for contract in failed_contracts
            ]
        elif not replay_passed:
            replay_detail = (
                f"missing={missing_ids[:5]},extra={extra_ids[:5]},"
                f"non_string={non_string_keys[:5]},issues={len(replay_issues)}"
            )
            effective_rechecks = [
                {
                    **dict(result),
                    "passed": False,
                    "rationale": (
                        f"host deterministic replay failed ({replay_detail}); "
                        f"{result.get('rationale', '')}"
                    ),
                }
                for result in effective_rechecks
            ]
            effective_remaining = [
                {
                    "contract": result["contract"],
                    "field": str(result["contract"]),
                    "problem": f"host deterministic replay failed ({replay_detail})",
                }
                for result in effective_rechecks
            ]
        try:
            refinement_evidence = build_refinement_evidence(
                review_before_refinement=review,
                fields_to_fix=fields_to_fix,
                refinement_patch=json.loads(json.dumps(patch)),
                rechecked_contract_results=effective_rechecks,
                remaining_issues=effective_remaining,
                hard_gate_replay={"all_passed": replay_passed},
                prior_refinement_count=0,
            )
        except (TypeError, ValueError) as e:
            updates = _abort(
                "refinement_evidence_invalid",
                {"error_type": type(e).__name__, "message": str(e)[:300]},
            )
            if usage:
                updates["refinement_usage"] = usage
            return updates
        if patch_rejected:
            refinement_evidence["rejection"] = {
                "reason": "patch_violation",
                "violations": violations,
                "restored_fields": sorted(patch),
            }

        updates: dict[str, Any] = {
            "lesson_blueprint": blueprint,
            "learning_package": package,
            "refinement_result": refinement_evidence,
        }
        if usage:
            updates["refinement_usage"] = usage
            if _stage_budget_exceeded({**state, **updates}):
                updates.update(
                    _abort("teaching_v2_budget_exceeded", _budget_diagnostics(state, updates))
                )
                return updates

        after_review = refinement_evidence["review_after_refinement"]
        if after_review["verdict"] == "FAIL":
            # Fail-closed: the lesson failed quality and is not stored; the
            # pipeline batch continues with the next candidate (P-4I
            # quality_fail_continue semantics, mapped to production abort).
            updates.update(
                _abort(
                    "teaching_v2_after_review_fail",
                    {
                        "patch_rejected": patch_rejected,
                        "replay_passed": replay_passed,
                        "remaining_issues": list(after_review.get("remaining_issues") or [])[:5],
                    },
                )
            )
        return updates
    except Exception as e:
        logger.error("refinement_node failed: %s", e, exc_info=True)
        updates = _abort("refinement_stage_failed", {"error": str(e)[:300]})
        usage = _metadata_from_owned_usage(run_usage)
        if usage:
            updates["refinement_usage"] = usage
        return updates


def daily_projection_node(state: DailyReaderState) -> dict:
    blueprint = state.get("lesson_blueprint") or {}
    package = state.get("learning_package") or {}
    reading_units = state.get("reading_units", [])
    review = state.get("semantic_review_result")
    refinement = state.get("refinement_result")
    usage_summary = _aggregate_usage(state)

    artifact: dict[str, Any] = {
        # Production row identity pre-storage: the source URL (stable per
        # article, unique within a discovery batch).
        "case_id": state.get("source_url", ""),
        "lesson_blueprint": blueprint,
        "learning_package": package,
        "source_assets": {"source_caption": ""},
        "run_meta": {
            "outcome": "cleaned_publish",
            "refinement_count": 1 if refinement else 0,
            "usage": usage_summary.get("aggregate") if usage_summary.get("available") else None,
            "review": review,
            "refinement": refinement,
        },
    }

    case = {
        "input": {
            "reading_units": reading_units,
            "source_caption": "",
        }
    }

    # Evaluation Lane (defense line 2, final fail-closed): shape validation
    # + the shared gold-free hard-gate registry run exactly once, on the
    # final artifact. A failure aborts — the article is not stored.
    schema_errors = validate_artifact(case, artifact)
    if schema_errors:
        return {
            **_abort(
                "teaching_v2_artifact_schema_violation",
                {"schema_errors": schema_errors[:5]},
            ),
            "usage_summary": usage_summary,
        }

    gates = run_hard_gates(case, artifact)
    if not gates["all_passed"]:
        failed_gates = sorted(
            name for name, result in gates["gates"].items() if result["passed"] is False
        )
        return {
            **_abort(
                "teaching_v2_hard_gates_failed",
                {
                    "failed_gates": failed_gates,
                    "passed_count": gates["passed_count"],
                    "scored_count": gates["scored_count"],
                },
            ),
            "usage_summary": usage_summary,
        }
    artifact["run_meta"]["hard_gates"] = {
        "all_passed": gates["all_passed"],
        "passed_count": gates["passed_count"],
        "scored_count": gates["scored_count"],
    }

    body_json = {
        "paragraphs": [{"id": unit.get("id"), "text": unit.get("text")} for unit in reading_units]
    }

    logger.info(
        "daily_projection_node: units=%d, checkpoints=%d, language_targets=%d, "
        "translations=%d, refinement_count=%d, gates=%d/%d",
        len(reading_units),
        len(package.get("comprehension_checkpoints") or []),
        len(package.get("language_targets") or []),
        len(package.get("translations_by_paragraph_id") or {}),
        artifact["run_meta"]["refinement_count"],
        gates["passed_count"],
        gates["scored_count"],
    )

    return {
        "lesson_v2": artifact,
        "body_json": body_json,
        "usage_summary": usage_summary,
    }


def _semantic_review_route(state: DailyReaderState) -> str:
    if state.get("abort", False):
        return "abort"
    review = state.get("semantic_review_result")
    if isinstance(review, dict) and review.get("verdict") == "FAIL":
        return "refine"
    return "project"


def build_daily_reader_graph() -> Any:
    graph = StateGraph(DailyReaderState)

    graph.add_node("light_normalize", light_normalize_node)
    graph.add_node("blueprint", blueprint_node)
    graph.add_node("language_support", language_support_node)
    graph.add_node("translation", translation_node)
    graph.add_node("semantic_review", semantic_review_node)
    graph.add_node("refinement", refinement_node)
    graph.add_node("daily_projection", daily_projection_node)

    graph.add_edge(START, "light_normalize")
    graph.add_conditional_edges(
        "light_normalize",
        _is_aborted,
        {True: END, False: "blueprint"},
    )
    graph.add_conditional_edges(
        "blueprint",
        _is_aborted,
        {True: END, False: "language_support"},
    )
    graph.add_conditional_edges(
        "language_support",
        _is_aborted,
        {True: END, False: "translation"},
    )
    graph.add_conditional_edges(
        "translation",
        _is_aborted,
        {True: END, False: "semantic_review"},
    )
    graph.add_conditional_edges(
        "semantic_review",
        _semantic_review_route,
        {"abort": END, "refine": "refinement", "project": "daily_projection"},
    )
    graph.add_conditional_edges(
        "refinement",
        _is_aborted,
        {True: END, False: "daily_projection"},
    )
    graph.add_edge("daily_projection", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Reading-unit planning (light_normalize machinery, unchanged by v2)
# ---------------------------------------------------------------------------


def _split_into_raw_blocks(text: str) -> list[dict]:
    sections = re.split(r"\n\s*\n", text)
    blocks: list[dict] = []
    block_idx = 0
    section_idx = 0
    section_block_counts: dict[int, int] = {}

    for section in sections:
        section_stripped = section.strip()
        if not section_stripped:
            section_idx += 1
            continue

        if "\n" in section_stripped:
            lines = section_stripped.split("\n")
        elif len(section_stripped) > MAX_PARAGRAPH_CHARS:
            lines = re.split(r"(?<=[.!?])\s+", section_stripped)
        else:
            lines = [section_stripped]

        count = 0
        for line in lines:
            cleaned = _clean_paragraph(line)
            if cleaned:
                blocks.append(
                    {
                        "block_id": f"b_{block_idx}",
                        "text": cleaned,
                        "section_idx": section_idx,
                    }
                )
                block_idx += 1
                count += 1
        section_block_counts[section_idx] = count
        section_idx += 1

    for block in blocks:
        block["is_solo_in_section"] = section_block_counts.get(block["section_idx"], 0) == 1

    return blocks


def _classify_raw_blocks(blocks: list[dict], title: str) -> list[dict]:
    title_clean = title.strip().lower() if title else ""
    for idx, block in enumerate(blocks):
        text = block["text"].strip()
        if title_clean and text.lower() == title_clean:
            block["role"] = "title_duplicate"
        elif _is_section_heading_candidate(text, block.get("is_solo_in_section", False), idx):
            block["role"] = "section_heading"
        else:
            block["role"] = "content"
    return blocks


def _is_section_heading_candidate(
    text: str,
    is_solo_in_section: bool,
    block_index: int,
) -> bool:
    if block_index == 0:
        return False
    if len(text) >= SECTION_HEADING_MAX_CHARS:
        return False
    if text and text[-1] in ".!?;:":
        return False
    if not is_solo_in_section:
        return False
    return True


def _plan_reading_units(classified_blocks: list[dict]) -> list[dict]:
    filtered = [b for b in classified_blocks if b["role"] != "title_duplicate"]
    if not filtered:
        return []

    preliminary_groups: list[list[dict]] = []
    current_group: list[dict] = []

    for block in filtered:
        if block["role"] == "section_heading" and current_group:
            preliminary_groups.append(current_group)
            current_group = []
        elif block["role"] == "content":
            current_group.append(block)
    if current_group:
        preliminary_groups.append(current_group)

    merged_groups = _merge_short_groups(preliminary_groups)

    reading_units: list[dict] = []
    for group in merged_groups:
        if not group:
            continue
        units = _merge_content_blocks_into_units(group)
        reading_units.extend(units)

    return reading_units


def _merge_short_groups(groups: list[list[dict]]) -> list[list[dict]]:
    if not groups:
        return groups

    result = list(groups)
    if len(result) > 1:
        first_chars = sum(len(b["text"]) for b in result[0])
        if first_chars < READING_UNIT_MIN_CHARS:
            result[1] = result[0] + result[1]
            result.pop(0)

    merged = [result[0]] if result else []
    for i in range(1, len(result)):
        prev = merged[-1]
        curr = result[i]
        total_chars = sum(len(b["text"]) for b in curr)
        if total_chars < READING_UNIT_MIN_CHARS and prev:
            prev.extend(curr)
        else:
            merged.append(curr)
    return merged


def _merge_content_blocks_into_units(blocks: list[dict]) -> list[dict]:
    if not blocks:
        return []

    units: list[dict] = []
    current_texts: list[str] = []
    current_len = 0

    for block in blocks:
        text = block["text"]
        would_len = current_len + len(text) + (1 if current_texts else 0)

        if current_texts and would_len > READING_UNIT_TARGET_CHARS:
            merged = " ".join(current_texts)
            if len(merged) > MAX_PARAGRAPH_CHARS:
                for chunk in _split_long_paragraph(merged):
                    units.append({"text": chunk})
            else:
                units.append({"text": merged})
            current_texts = [text]
            current_len = len(text)
        else:
            current_texts.append(text)
            current_len = would_len

    if current_texts:
        merged = " ".join(current_texts)
        if len(merged) > MAX_PARAGRAPH_CHARS:
            for chunk in _split_long_paragraph(merged):
                units.append({"text": chunk})
        else:
            units.append({"text": merged})

    return _merge_short_units(units)


def _merge_short_units(units: list[dict]) -> list[dict]:
    if not units:
        return units

    result = [dict(units[0])]
    for i in range(1, len(units)):
        prev = result[-1]
        curr = units[i]
        if len(prev["text"]) < READING_UNIT_MIN_CHARS:
            merged_text = prev["text"] + " " + curr["text"]
            if len(merged_text) <= MAX_PARAGRAPH_CHARS:
                prev["text"] = merged_text
            else:
                result.append(dict(curr))
        elif len(curr["text"]) < READING_UNIT_MIN_CHARS and i == len(units) - 1:
            merged_text = prev["text"] + " " + curr["text"]
            if len(merged_text) <= MAX_PARAGRAPH_CHARS:
                prev["text"] = merged_text
            else:
                result.append(dict(curr))
        else:
            result.append(dict(curr))

    return result


def _split_into_paragraphs(text: str) -> list[str]:
    raw_blocks = _split_into_raw_blocks(text)
    classified_blocks = _classify_raw_blocks(raw_blocks, title="")
    reading_units = _plan_reading_units(classified_blocks)
    return [unit["text"] for unit in reading_units]


def _split_long_paragraph(text: str) -> list[str]:
    if len(text) <= MAX_PARAGRAPH_CHARS:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 1:
        return [
            text[i : i + MAX_PARAGRAPH_CHARS].strip()
            for i in range(0, len(text), MAX_PARAGRAPH_CHARS)
            if text[i : i + MAX_PARAGRAPH_CHARS].strip()
        ]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        would_len = current_len + len(sentence) + (1 if current else 0)
        if current and (
            would_len > MAX_PARAGRAPH_CHARS
            or (
                len(current) >= MAX_PARAGRAPH_SENTENCES
                and current_len >= int(MAX_PARAGRAPH_CHARS * 0.55)
            )
        ):
            chunks.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len = would_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def _clean_paragraph(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text
