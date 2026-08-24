"""Canonical Daily Reader Teaching-v2 prototype real-run harness (P-4E).

Thin orchestration layer over the frozen P-2/P-4D canonical contracts:

- fixed 4+1 stage topology resolved through the production model chain
  (``resolve_model_config`` -> ``build_model_instance`` -> ``model.profile``)
- Generation Lane (gold-free inputs only) vs Evaluation Lane
  (``validate_artifact`` + ``run_hard_gates`` after the final artifact)
- per-stage ``RunUsage`` ledgers with case/batch conservation and budget
  caps derived from the actual stage settings
- dual-flag real-run authorization plus exclusive attempt markers; a run
  directory is single-shot: never resumed, overwritten or cleaned.

Teaching business logic stays in ``claread_eval.daily_reader.teaching_v2``
(prototype/schema/gates). This module must not grow a second teaching
implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

EVALS_ROOT = Path(__file__).resolve().parents[1]
SERVICES_API_ROOT = EVALS_ROOT.parent / "services" / "api"
for _path_entry in (str(EVALS_ROOT), str(SERVICES_API_ROOT)):
    if _path_entry not in sys.path:
        sys.path.insert(0, _path_entry)

from app.config.settings import Settings  # noqa: E402
from app.llm.provider_factory import build_model_instance  # noqa: E402
from app.llm.router import resolve_model_config  # noqa: E402
from app.llm.routes import (  # noqa: E402
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_REVIEW,
    MODEL_ROUTE_DAILY_TRANSLATION,
    ModelRoute,
)
from app.llm.types import (  # noqa: E402
    ModelSelection,
    ResolvedModelConfig,
    RouteModelSelection,
    RunModelSettings,
)
from pydantic import BaseModel, ValidationError  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.models import Model  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402
from pydantic_graph import End  # noqa: E402

from claread_eval.daily_reader.teaching_v2.gates import run_hard_gates  # noqa: E402
from claread_eval.daily_reader.teaching_v2.prototype import (  # noqa: E402
    SEMANTIC_REVIEW_CONTRACTS,  # noqa: F401  (re-exported canonical authority)
    TRANSFER_CONTENT_REQUIREMENT_VALUES,
    TRANSFER_TASK_KIND_BY_ARTICLE_TYPE,
    build_blueprint_prompt,
    build_language_support_prompt,
    build_refinement_evidence,
    build_refinement_prompt,
    build_semantic_review_prompt,
    build_translation_prompt,
    derive_translation_unit_ids,
    make_review_evidence,
    validate_teaching_contract,
)
from claread_eval.daily_reader.teaching_v2.schema import (  # noqa: E402
    CHECKPOINT_SKILLS,
    DIFFICULTIES,
    TRANSFER_TASK_KINDS,
    substantive_unit_ids,
    validate_artifact,
)

PRESET_NAME = "daily_reader"
# Frozen P-4E stage contract: the DashScope-hosted Flash/Pro DeepSeek
# profiles retained by ops. The official api.deepseek.com endpoint is out
# of contract for P-4E/P-4F real runs.
TIER_PROFILE_NAMES = {
    "flash": "workflow-dashscope-deepseek-v4-flash-0731",
    "pro": "workflow-dashscope-deepseek-v4-pro-0813",
}
OUTPUT_RETRIES = 3
TEMPERATURE = 0.2
TIMEOUT_SECONDS = 120.0
FROZEN_CASE_IDS = (
    "bbc-bumble-001",
    "npr-europe-heat-010",
    "bbc-iphone-motion-sickness-006",
    "bbc-crypto-liberland-007",
)

ALLOWED_ROUTES: frozenset[str] = frozenset(
    {
        MODEL_ROUTE_DAILY_ANALYSIS,
        MODEL_ROUTE_DAILY_ANNOTATION,
        MODEL_ROUTE_DAILY_TRANSLATION,
        MODEL_ROUTE_DAILY_REVIEW,
    }
)
FORBIDDEN_ROUTES: frozenset[str] = frozenset({"daily_takeaways"})
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "gold",
        "expected_article_type",
        "expected_difficulty",
        "allowed_paragraph_ids",
        "required_paragraph_ids",
    }
)
# ponytail: word-boundary token scan over rendered prompts; if a frozen
# article ever legitimately contains one of these tokens, split payload
# lanes structurally instead of loosening this scan.
FORBIDDEN_PROMPT_TOKENS = FORBIDDEN_PAYLOAD_KEYS | {
    "expected_outcome",
    "expected_translation_coverage",
    "expected_transfer_kind",
    "human_review",
}
DASHSCOPE_HOST_MARKERS = ("aliyuncs.com",)
FORBIDDEN_HOST_MARKER = "api.deepseek.com"

USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "model_requests", "tool_calls")


class StructuralCaseError(RuntimeError):
    """Infrastructure/identity drift that must stop the whole batch."""


class BudgetBreached(RuntimeError):
    """Raised after usage posting when a derived batch cap is exceeded."""


class GenerationLeakError(ValueError):
    """Gold-shaped content detected on a Generation Lane boundary."""


class StageFailure(RuntimeError):
    """One stage failed after Agent dispatch; carries confirmed usage."""

    def __init__(self, stage: str, cause: BaseException, usage: RunUsage, elapsed_ms: int) -> None:
        self.stage = stage
        self.usage = usage
        self.elapsed_ms = elapsed_ms
        super().__init__(f"{stage} failed: {type(cause).__name__}")
        self.__cause__ = cause


class BatchStopped(RuntimeError):
    """Structural/infra failure that halts all subsequent cases."""

    def __init__(self, reason: str, partial: dict[str, Any] | None = None) -> None:
        self.partial = partial
        super().__init__(reason)


@dataclass(frozen=True)
class StageSpec:
    name: str
    route: ModelRoute
    tier: str
    max_tokens: int


STAGE_TOPOLOGY: tuple[StageSpec, ...] = (
    StageSpec("blueprint", MODEL_ROUTE_DAILY_ANALYSIS, "pro", 4096),
    StageSpec("language_support", MODEL_ROUTE_DAILY_ANNOTATION, "flash", 4096),
    StageSpec("translation", MODEL_ROUTE_DAILY_TRANSLATION, "flash", 8192),
    StageSpec("semantic_review", MODEL_ROUTE_DAILY_REVIEW, "pro", 4096),
    StageSpec("refinement", MODEL_ROUTE_DAILY_REVIEW, "pro", 4096),
)


def validate_topology(topology: Sequence[StageSpec] = STAGE_TOPOLOGY) -> None:
    names = [spec.name for spec in topology]
    expected = ["blueprint", "language_support", "translation", "semantic_review", "refinement"]
    if names != expected:
        raise ValueError(f"canonical topology drifted: {names}")
    for spec in topology:
        if spec.route not in ALLOWED_ROUTES:
            raise ValueError(
                f"forbidden route: {spec.route} "
                "(daily_takeaways/daily_cover are out of the P-4E contract)"
            )
        if spec.route in FORBIDDEN_ROUTES:
            raise ValueError(f"forbidden route: {spec.route}")
        if spec.tier not in TIER_PROFILE_NAMES:
            raise ValueError(f"unknown tier: {spec.tier}")
        if spec.max_tokens not in (4096, 8192):
            raise ValueError(f"max_tokens outside frozen caps: {spec.max_tokens}")


# ---------------------------------------------------------------------------
# Transport DTOs — single-stage structured-output shapes only.
# ---------------------------------------------------------------------------


class CheckpointDraft(BaseModel):
    skill: Literal[*CHECKPOINT_SKILLS]
    prompt: str
    prompt_subject: str
    reference_answer: str
    reference_answer_subject: str
    evidence_paragraph_ids: list[str]
    answer_evidence_paragraph_ids: list[str]


class TransferTaskDraft(BaseModel):
    task_kind: Literal[*TRANSFER_TASK_KINDS]
    content_requirement: Literal[*TRANSFER_CONTENT_REQUIREMENT_VALUES]
    required_language_target_expressions: list[str]
    prompt: str = ""
    scaffold: str = ""
    reference_points: list[str] = []


class StructureNodeDraft(BaseModel):
    label: str
    function: str
    paragraph_ids: list[str]


class BlueprintDraft(BaseModel):
    article_type: Literal[*TRANSFER_TASK_KIND_BY_ARTICLE_TYPE]
    effective_difficulty: Literal[*DIFFICULTIES]
    reading_mission: str
    reading_mission_stance: Literal["neutral"]
    learning_objectives: list[str]
    structure_map: list[StructureNodeDraft]
    selected_paragraph_ids: list[str]
    comprehension_checkpoints: list[CheckpointDraft]
    transfer_task: TransferTaskDraft


class LanguageTargetDraft(BaseModel):
    expression: str
    paragraph_id: str
    target_kind: str
    teaching_purpose: str
    meaning_zh: str = ""
    usage_note: str = ""
    reusable_pattern: str = ""


class SentenceMapDraft(BaseModel):
    sentence: str
    paragraph_id: str
    translation: str = ""
    complexity_kind: Literal["complex_syntax", "argument_structure"] | None = None
    teaching_purpose: str = ""


class LanguageSupportDraft(BaseModel):
    language_targets: list[LanguageTargetDraft]
    sentence_maps: list[SentenceMapDraft]
    high_difficulty_unit_ids: list[str]


class TranslationItemDraft(BaseModel):
    paragraph_id: str
    translation: str


class TranslationDraft(BaseModel):
    translations: list[TranslationItemDraft]


class ReviewIssueDraft(BaseModel):
    contract: str
    field: str
    problem: str


class ContractResultDraft(BaseModel):
    contract: str
    passed: bool
    rationale: str


class SemanticReviewDraft(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    issues: list[ReviewIssueDraft]
    remaining_issues: list[str]
    contract_results: list[ContractResultDraft]
    reviewed_at_stage: Literal["before_refinement"]
    refinement_requested: bool


class RefinementDraft(BaseModel):
    refinement_patch: dict[str, Any]
    rechecked_contract_results: list[ContractResultDraft]
    remaining_issues: list[ReviewIssueDraft]


STAGE_OUTPUT_TYPES: dict[str, type[BaseModel]] = {
    "blueprint": BlueprintDraft,
    "language_support": LanguageSupportDraft,
    "translation": TranslationDraft,
    "semantic_review": SemanticReviewDraft,
    "refinement": RefinementDraft,
}


# ---------------------------------------------------------------------------
# Anti-leakage guards
# ---------------------------------------------------------------------------


def assert_generation_safe_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_PAYLOAD_KEYS:
                raise GenerationLeakError(f"forbidden generation key: {key}")
            assert_generation_safe_payload(child)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for child in value:
            assert_generation_safe_payload(child)


def assert_prompt_clean(prompt: str) -> None:
    folded = prompt.casefold()
    for token in sorted(FORBIDDEN_PROMPT_TOKENS):
        if re.search(rf"\b{re.escape(token)}\b", folded):
            raise GenerationLeakError(f"forbidden prompt token: {token}")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generation_view(case: Mapping[str, Any]) -> dict[str, Any]:
    """Project a frozen case onto its gold-free Generation Lane view."""
    if not isinstance(case, Mapping) or not isinstance(case.get("input"), Mapping):
        raise StructuralCaseError("generation_view_invalid")
    case_id = case.get("case_id")
    inp = case["input"]
    if not isinstance(case_id, str) or not case_id.strip():
        raise StructuralCaseError("case_id_missing")
    units = []
    for unit in inp.get("reading_units") or []:
        if not isinstance(unit, Mapping) or not isinstance(unit.get("id"), str):
            raise StructuralCaseError("reading_units_invalid")
        units.append({"id": unit["id"], "text": unit.get("text", "")})
    substantive = substantive_unit_ids(dict(case))
    return {
        "case_id": case_id,
        "title": inp.get("title", ""),
        "source": inp.get("source", ""),
        "source_caption": inp.get("source_caption", ""),
        "original_text": inp.get("original_text", ""),
        "reading_units": units,
        "substantive_unit_ids": sorted(substantive),
    }


def _article_payload(view: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "title": view["title"],
        "source": view["source"],
        "reading_units": view["reading_units"],
    }
    assert_generation_safe_payload(payload)
    return payload


# ---------------------------------------------------------------------------
# Production-chain stage runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageRuntime:
    spec: StageSpec
    config: ResolvedModelConfig
    settings_payload: dict[str, Any] | None

    @property
    def profile_name(self) -> str:
        return self.config.profile_name


def build_eval_selection(
    settings: Settings,
    tier_profile_names: Mapping[str, str] = TIER_PROFILE_NAMES,
) -> ModelSelection:
    """Pin every canonical route to its frozen tier profile via request-level selection."""
    from app.llm.registry import build_model_registry

    registry = build_model_registry(settings)
    if PRESET_NAME not in registry.presets:
        raise StructuralCaseError(f"preset_missing:{PRESET_NAME}")
    validate_topology()
    routes: dict[ModelRoute, RouteModelSelection] = {}
    for spec in STAGE_TOPOLOGY:
        routes[spec.route] = RouteModelSelection(profile=tier_profile_names[spec.tier])
    return ModelSelection(preset=PRESET_NAME, routes=routes)


def assert_route_contract(config: ResolvedModelConfig, spec: StageSpec) -> None:
    problems: list[str] = []
    if config.fallback_profiles:
        problems.append("fallback_not_empty")
    if config.adapter != "openai_compatible":
        problems.append(f"adapter:{config.adapter}")
    host = (config.base_url or "").lower()
    if FORBIDDEN_HOST_MARKER in host:
        problems.append("official_deepseek_host")
    https_ok = host.startswith("https://")
    dashscope_ok = any(marker in host for marker in DASHSCOPE_HOST_MARKERS)
    if not (https_ok and dashscope_ok):
        problems.append("host_not_dashscope")
    if spec.tier not in (config.model_name or "").lower():
        problems.append("tier_model_mismatch")
    profile = config.openai_profile
    if profile is None or profile.default_structured_output_mode != "prompted":
        problems.append("structured_output_mode_not_prompted")
    if profile is None or profile.supports_json_object_output is not True:
        problems.append("json_object_unsupported")
    stage_settings = config.model_settings
    if stage_settings is None or stage_settings.max_tokens != spec.max_tokens:
        problems.append("max_tokens_drift")
    temperature = stage_settings.temperature if stage_settings else None
    if temperature is None or abs(temperature - TEMPERATURE) > 1e-9:
        problems.append("temperature_drift")
    if stage_settings is None or stage_settings.timeout != TIMEOUT_SECONDS:
        problems.append("timeout_drift")
    extra_body = (stage_settings.extra_body if stage_settings else None) or {}
    thinking_off = extra_body.get("enable_thinking") is False and not (
        stage_settings.thinking_enabled() if stage_settings else False
    )
    if not thinking_off:
        problems.append("thinking_not_disabled")
    if problems:
        raise StructuralCaseError(f"route_contract_drift:{spec.name}:" + ",".join(problems))


def resolve_stage_runtime(
    settings: Settings,
    selection: ModelSelection,
    spec: StageSpec,
) -> StageRuntime:
    config = resolve_model_config(settings, spec.route, selection)
    if config is None:
        raise StructuralCaseError(f"route_unresolved:{spec.route}")
    stage_override = RunModelSettings(
        max_tokens=spec.max_tokens,
        temperature=TEMPERATURE,
        timeout=TIMEOUT_SECONDS,
        extra_body={"enable_thinking": False},
    )
    merged = (config.model_settings or RunModelSettings()).merged_with(stage_override)
    config = config.model_copy(update={"model_settings": merged}, deep=True)
    assert_route_contract(config, spec)
    return StageRuntime(spec=spec, config=config, settings_payload=merged.to_pydantic_ai())


def production_transport(config: ResolvedModelConfig) -> Model:
    model = build_model_instance(config)
    if model is None or isinstance(model, str):
        raise StructuralCaseError(f"model_build_failed:{config.model_name}")
    client = getattr(model, "client", None)
    if client is None or not hasattr(client, "max_retries"):
        raise StructuralCaseError("BLOCKED_PROVIDER_CLIENT_API")
    # Contract: zero SDK-level retries; outer retries stay 0 too.
    client.max_retries = 0
    if client.max_retries != 0:
        raise StructuralCaseError("sdk_retries_reset_failed")
    return model


def gold_identity_mismatch(case: Mapping[str, Any], artifact: Mapping[str, Any]) -> bool:
    """Direct comparison of generated identity fields vs Gold — no error text matching."""
    gold = case.get("gold") or {}
    blueprint = (artifact.get("lesson_blueprint") or {}) if isinstance(artifact, Mapping) else {}
    return blueprint.get("article_type") != gold.get("article_type") or blueprint.get(
        "effective_difficulty"
    ) != gold.get("expected_difficulty")


def artifact_structural_errors(case: Mapping[str, Any], artifact: Mapping[str, Any]) -> list[str]:
    """validate_artifact with the two gold-identity fields shadowed to gold.

    The transport DTOs already guarantee legal enums and presence for these
    fields, so any residual difference vs gold is a quality mismatch, not a
    structural defect. Everything else validate_artifact reports here is
    structural.
    """
    import copy

    gold = case.get("gold") or {}
    shadow = copy.deepcopy(dict(artifact))
    blueprint = shadow.get("lesson_blueprint")
    if isinstance(blueprint, dict):
        if "article_type" in gold:
            blueprint["article_type"] = gold["article_type"]
        if "expected_difficulty" in gold:
            blueprint["effective_difficulty"] = gold["expected_difficulty"]
    return validate_artifact(dict(case), shadow)


# ---------------------------------------------------------------------------
# Agent drive + ledgers
# ---------------------------------------------------------------------------


def usage_metadata(usage: RunUsage) -> dict[str, int]:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "model_requests": int(getattr(usage, "requests", 0) or 0),
        "tool_calls": int(getattr(usage, "tool_calls", 0) or 0),
    }


async def _drive_agent(
    agent: Agent[Any], prompt: str, settings_payload: dict[str, Any] | None
) -> tuple[Any, RunUsage, int]:
    started = time.perf_counter()
    agent_run: Any = None
    try:
        async with agent.iter(prompt, model_settings=settings_payload) as agent_run:
            node = agent_run.next_node
            while not isinstance(node, End):
                if agent_run.result is not None:
                    break
                node = await agent_run.next(node)
        result = agent_run.result
        usage = agent_run.usage
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        return result, usage, elapsed_ms
    except Exception as exc:
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        confirmed = RunUsage()
        try:
            if agent_run is not None:
                confirmed = agent_run.usage
        except Exception:
            confirmed = RunUsage()
        raise StageFailure("stage", exc, confirmed, elapsed_ms) from exc


def run_stage(runtime: StageRuntime, prompt: str, transport: Any) -> dict[str, Any]:
    """Gate the rendered prompt, then dispatch exactly one logical call."""
    assert_prompt_clean(prompt)
    agent: Agent[Any] = Agent(
        transport(runtime.config),
        name=f"daily_reader_teaching_v2_{runtime.spec.name}",
        output_type=STAGE_OUTPUT_TYPES[runtime.spec.name],
        retries=OUTPUT_RETRIES,
    )
    try:
        result, usage, elapsed_ms = asyncio.run(
            _drive_agent(agent, prompt, runtime.settings_payload)
        )
    except StageFailure as failure:
        return {
            "stage": runtime.spec.name,
            "route": runtime.spec.route,
            "tier": runtime.spec.tier,
            "profile": runtime.profile_name,
            "model_name": runtime.config.model_name,
            "outcome": f"error:{type(failure.__cause__).__name__}",
            "latency_ms": failure.elapsed_ms,
            "prompt_sha256": sha256_hex(prompt),
            "usage": usage_metadata(failure.usage),
        }
    return {
        "stage": runtime.spec.name,
        "route": runtime.spec.route,
        "tier": runtime.spec.tier,
        "profile": runtime.profile_name,
        "model_name": runtime.config.model_name,
        "outcome": "ok",
        "latency_ms": elapsed_ms,
        "prompt_sha256": sha256_hex(prompt),
        "usage": usage_metadata(usage),
        "output": result.output.model_dump(),
    }


# ---------------------------------------------------------------------------
# Case orchestration
# ---------------------------------------------------------------------------


def derive_budget(
    *,
    case_count: int,
    topology: Sequence[StageSpec] = STAGE_TOPOLOGY,
    output_retries: int = OUTPUT_RETRIES,
) -> dict[str, int]:
    requests_per_call = 1 + output_retries
    logical_calls_max = case_count * len(topology)
    return {
        "workflow_runs_max": case_count,
        "logical_calls_max": logical_calls_max,
        "model_requests_max": logical_calls_max * requests_per_call,
        "http_attempts_max": logical_calls_max * requests_per_call,
        "output_tokens_max": case_count * requests_per_call * sum(s.max_tokens for s in topology),
        "outer_retries": 0,
        "sdk_retries": 0,
        "judge_calls": 0,
        "db_calls": 0,
        "redis_calls": 0,
        "fastapi_calls": 0,
    }


def _sum_usage(entries: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {key: sum(entry["usage"][key] for entry in entries) for key in USAGE_KEYS}


def _exclusive_write(path: Path, text: str) -> None:
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(handle, "wb") as stream:
        stream.write(text.encode("utf-8"))


def _assert_known_anchor_ids(view: Mapping[str, Any], ids: Sequence[str]) -> None:
    known = {unit["id"] for unit in view["reading_units"]}
    unknown = sorted({unit_id for unit_id in ids if unit_id} - known)
    if unknown:
        raise StructuralCaseError(f"anchor_identity_unresolved:{unknown[:5]}")


def _selected_units_for_language_support(
    view: Mapping[str, Any], blueprint: Mapping[str, Any]
) -> list[dict[str, Any]]:
    referenced: list[str] = list(blueprint["selected_paragraph_ids"])
    for node in blueprint["structure_map"]:
        referenced.extend(node["paragraph_ids"])
    for checkpoint in blueprint["comprehension_checkpoints"]:
        referenced.extend(checkpoint["evidence_paragraph_ids"])
        referenced.extend(checkpoint["answer_evidence_paragraph_ids"])
    substantive = set(view["substantive_unit_ids"])
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for unit in view["reading_units"]:
        if unit["id"] in referenced and unit["id"] in substantive and unit["id"] not in seen:
            seen.add(unit["id"])
            selected.append(unit)
    if not selected:
        raise StructuralCaseError("language_support_selected_units_empty")
    return selected


def _apply_patch(container: dict[str, Any], patch: Mapping[str, Any]) -> None:
    for key, value in patch.items():
        current = container.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            container[key] = {**current, **dict(value)}
        else:
            container[key] = value


def run_case(
    case: Mapping[str, Any],
    settings: Settings,
    selection: ModelSelection,
    transport: Any,
    out_dir: Path,
    budget_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = generation_view(case)
    case_dir = Path(out_dir) / view["case_id"]
    case_dir.mkdir(parents=True, exist_ok=False)
    _exclusive_write(
        case_dir / "attempt.marker.json",
        json.dumps({"case_id": view["case_id"], "pid": os.getpid()}, sort_keys=True),
    )

    ledger: list[dict[str, Any]] = []

    def stop(reason: str) -> NoReturn:
        aggregate = _sum_usage(ledger)
        raise BatchStopped(
            f"{view['case_id']}:{reason}",
            partial={
                "case_id": view["case_id"],
                "outcome": f"stopped:{reason}",
                "stop_reason": f"{view['case_id']}:{reason}",
                "stage_ledger": ledger,
                "usage": {
                    "stages": {entry["stage"]: entry["usage"] for entry in ledger},
                    "aggregate": aggregate,
                },
                "artifact": None,
            },
        )

    def post_stage(entry: dict[str, Any]) -> dict[str, Any] | None:
        compact = {key: value for key, value in entry.items() if key != "output"}
        ledger.append(compact)
        if budget_state is not None:
            caps = budget_state["caps"]
            totals = budget_state["totals"]
            for key in ("model_requests", "output_tokens"):
                totals[key] += int(compact["usage"].get(key, 0) or 0)
            if totals["model_requests"] > caps.get("model_requests_max", float("inf")) or (
                totals["output_tokens"] > caps.get("output_tokens_max", float("inf"))
            ):
                # Exact outcome string per contract; details live in the
                # persisted batch report and the raised error.
                stop("budget_exceeded")
        return entry.get("output")

    def dispatch(spec: StageSpec, prompt: str) -> dict[str, Any] | None:
        runtime = resolve_stage_runtime(settings, selection, spec)
        output = post_stage(run_stage(runtime, prompt, transport))
        if output is None:
            stop(f"{spec.name}:{ledger[-1]['outcome']}")
        return output

    try:
        blueprint_spec, ls_spec, tr_spec, review_spec, refine_spec = STAGE_TOPOLOGY

        blueprint_prompt = build_blueprint_prompt(_article_payload(view))
        blueprint = dispatch(blueprint_spec, blueprint_prompt)
        assert blueprint is not None

        ls_prompt = build_language_support_prompt(
            _selected_units_for_language_support(view, blueprint),
            blueprint["effective_difficulty"],
        )
        language_support = dispatch(ls_spec, ls_prompt)
        assert language_support is not None
        _assert_known_anchor_ids(
            view,
            [target["paragraph_id"] for target in language_support["language_targets"]]
            + [sm["paragraph_id"] for sm in language_support["sentence_maps"]],
        )

        derived_targets = derive_translation_unit_ids(
            blueprint["effective_difficulty"],
            view["reading_units"],
            substantive_unit_ids=view["substantive_unit_ids"],
            checkpoint_evidence_ids=[
                pid
                for checkpoint in blueprint["comprehension_checkpoints"]
                for pid in checkpoint["evidence_paragraph_ids"]
            ],
            language_target_paragraph_ids=[
                target["paragraph_id"] for target in language_support["language_targets"]
            ],
            sentence_map_paragraph_ids=[
                sm["paragraph_id"] for sm in language_support["sentence_maps"]
            ],
            high_difficulty_unit_ids=list(language_support["high_difficulty_unit_ids"]),
        )
        units_by_id = {unit["id"]: unit for unit in view["reading_units"]}
        tr_prompt = build_translation_prompt(
            [units_by_id[unit_id] for unit_id in derived_targets],
            [
                {"paragraph_id": sm["paragraph_id"], "sentence": sm["sentence"]}
                for sm in language_support["sentence_maps"]
            ],
            blueprint["effective_difficulty"],
        )
        translation = dispatch(tr_spec, tr_prompt)
        assert translation is not None
        returned_ids = [item["paragraph_id"] for item in translation["translations"]]
        duplicate_ids = sorted({pid for pid in returned_ids if returned_ids.count(pid) > 1})
        if duplicate_ids:
            stop(f"translation_duplicate_targets:{duplicate_ids[:5]}")
        missing_ids = sorted(set(derived_targets) - set(returned_ids))
        extra_ids = sorted(set(returned_ids) - set(derived_targets))
        if missing_ids or extra_ids:
            stop(f"translation_target_set_mismatch:missing={missing_ids[:5]},extra={extra_ids[:5]}")

        blueprint_obj = blueprint
        package_obj: dict[str, Any] = {
            "comprehension_checkpoints": blueprint["comprehension_checkpoints"],
            "high_difficulty_unit_ids": list(language_support["high_difficulty_unit_ids"]),
            "language_targets": language_support["language_targets"],
            "sentence_maps": language_support["sentence_maps"],
            "transfer_task": blueprint["transfer_task"],
            "translations_by_paragraph_id": {
                item["paragraph_id"]: item["translation"] for item in translation["translations"]
            },
        }
        deterministic_issues = validate_teaching_contract(blueprint_obj, package_obj)

        review_prompt = build_semantic_review_prompt(
            view["original_text"],
            blueprint_obj,
            package_obj,
            {
                "derived_translation_unit_ids": derived_targets,
                "teaching_contract_issues": deterministic_issues,
            },
        )
        review_output = dispatch(review_spec, review_prompt)
        assert review_output is not None
        try:
            review_evidence = make_review_evidence(**review_output)
        except (TypeError, ValueError) as exc:
            stop(f"review_evidence_invalid:{exc}")

        refinement_evidence: dict[str, Any] | None = None
        refinement_count = 0
        fields_to_fix: dict[str, Any] = {}
        patch: dict[str, Any] = {}
        refine_output: dict[str, Any] | None = None
        if review_evidence["verdict"] == "FAIL":
            for issue in review_evidence["issues"]:
                top_field = str(issue.get("field", "")).split(".")[0]
                if top_field in package_obj:
                    fields_to_fix.setdefault(
                        top_field, json.loads(json.dumps(package_obj[top_field]))
                    )
                elif top_field in blueprint_obj:
                    fields_to_fix.setdefault(
                        top_field, json.loads(json.dumps(blueprint_obj[top_field]))
                    )
                else:
                    stop(f"refinement_field_unknown:{top_field}")
            evidence_context = {
                "failed_contracts": [
                    result["contract"]
                    for result in review_evidence["contract_results"]
                    if not result["passed"]
                ]
            }
            refine_prompt = build_refinement_prompt(
                review_evidence, fields_to_fix, evidence_context
            )
            refine_output = dispatch(refine_spec, refine_prompt)
            assert refine_output is not None
            patch = refine_output["refinement_patch"]
            for key, value in patch.items():
                if key in package_obj:
                    _apply_patch(package_obj, {key: value})
                elif key in blueprint_obj:
                    _apply_patch(blueprint_obj, {key: value})
                else:
                    stop(f"refinement_patch_target_unknown:{key}")
            refinement_count = 1

        if refinement_count:
            # Non-gold deterministic replay only — gold hard gates never feed
            # build_refinement_evidence.
            replay_issues = validate_teaching_contract(blueprint_obj, package_obj)
            try:
                replay_targets = derive_translation_unit_ids(
                    blueprint_obj["effective_difficulty"],
                    view["reading_units"],
                    substantive_unit_ids=view["substantive_unit_ids"],
                    checkpoint_evidence_ids=[
                        pid
                        for checkpoint in blueprint_obj["comprehension_checkpoints"]
                        for pid in checkpoint["evidence_paragraph_ids"]
                    ],
                    language_target_paragraph_ids=[
                        target["paragraph_id"] for target in package_obj["language_targets"]
                    ],
                    sentence_map_paragraph_ids=[
                        sm["paragraph_id"] for sm in package_obj["sentence_maps"]
                    ],
                    high_difficulty_unit_ids=list(package_obj["high_difficulty_unit_ids"]),
                )
            except (KeyError, ValueError) as exc:
                stop(f"deterministic_replay_failed:{exc}")
            replay_passed = not replay_issues and set(replay_targets) == set(returned_ids)
            try:
                refinement_evidence = build_refinement_evidence(
                    review_before_refinement=review_evidence,
                    fields_to_fix=fields_to_fix,
                    refinement_patch=json.loads(json.dumps(patch)),
                    rechecked_contract_results=refine_output["rechecked_contract_results"],
                    remaining_issues=refine_output["remaining_issues"],
                    hard_gate_replay={"all_passed": replay_passed},
                    prior_refinement_count=0,
                )
            except (TypeError, ValueError) as exc:
                stop(f"refinement_evidence_invalid:{exc}")

        artifact: dict[str, Any] = {
            "case_id": view["case_id"],
            "lesson_blueprint": blueprint_obj,
            "learning_package": package_obj,
            "source_assets": {"source_caption": view["source_caption"]},
            "run_meta": {
                "outcome": "cleaned_publish",
                "refinement_count": refinement_count,
            },
        }
        aggregate = _sum_usage(ledger)
        artifact["usage"] = aggregate
        artifact["run_meta"]["usage"] = aggregate

        schema_errors = validate_artifact(dict(case), artifact)
        identity_mismatch = gold_identity_mismatch(case, artifact)
        structural_errors = artifact_structural_errors(case, artifact)
        if structural_errors:
            stop(f"artifact_schema_violation:{structural_errors[:3]}")

        # Evaluation Lane: gold hard gates run exactly once, on the final artifact,
        # and their results are recorded here — never fed back into model prompts.
        gates = run_hard_gates(dict(case), artifact)

        _exclusive_write(
            case_dir / "review-evidence.json",
            json.dumps(
                {"review": review_evidence, "refinement": refinement_evidence},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
        _exclusive_write(
            case_dir / "artifact.json",
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
        )
        _exclusive_write(
            case_dir / "stage-ledger.json",
            json.dumps({"stages": ledger, "aggregate": aggregate}, ensure_ascii=False, indent=2),
        )

        after_fail = (
            refinement_evidence is not None
            and refinement_evidence["review_after_refinement"]["verdict"] == "FAIL"
        )
        quality_fail = identity_mismatch or not gates["all_passed"] or after_fail
        return {
            "case_id": view["case_id"],
            "outcome": "quality_fail_continue" if quality_fail else "completed",
            "stop_reason": None,
            "stage_ledger": ledger,
            "usage": {
                "stages": {entry["stage"]: entry["usage"] for entry in ledger},
                "aggregate": aggregate,
            },
            "schema_errors": schema_errors,
            "gates": {
                "all_passed": gates["all_passed"],
                "passed_count": gates["passed_count"],
                "scored_count": gates["scored_count"],
            },
            "artifact": artifact,
        }
    except StructuralCaseError as exc:
        stop(f"structural:{exc}")
    except (
        ValidationError,
        GenerationLeakError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        stop(f"unexpected:{type(exc).__name__}:{exc}")


def run_batch(
    cases: Sequence[Mapping[str, Any]],
    settings: Settings,
    selection: ModelSelection,
    transport: Any,
    out_dir: Path,
    *,
    budget: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    _exclusive_write(
        out_dir / "batch-attempt.marker.json",
        json.dumps(
            {
                "case_ids": [case.get("case_id") for case in cases],
                "pid": os.getpid(),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            sort_keys=True,
        ),
    )
    effective_budget = budget if budget is not None else derive_budget(case_count=len(cases))
    totals = dict.fromkeys(USAGE_KEYS, 0)
    reports: list[dict[str, Any]] = []
    stop_reason: str | None = None
    budget_stop: str | None = None
    # Per-stage posting state shared with run_case; the breach fires as soon as
    # a stage's usage is posted, not when the case completes.
    budget_state = {
        "caps": effective_budget,
        "totals": {"model_requests": 0, "output_tokens": 0},
    }
    for case in cases:
        if stop_reason is not None:
            reports.append(
                {
                    "case_id": case.get("case_id"),
                    "outcome": "skipped_by_stop",
                    "stop_reason": stop_reason,
                    "stage_ledger": [],
                    "usage": {"stages": {}, "aggregate": {}},
                }
            )
            continue
        try:
            report = run_case(
                case, settings, selection, transport, out_dir, budget_state=budget_state
            )
        except BatchStopped as exc:
            stop_reason = str(exc)
            if "budget_exceeded" in stop_reason:
                budget_stop = stop_reason
            if exc.partial is not None:
                report = exc.partial
            else:
                report = {
                    "case_id": case.get("case_id"),
                    "outcome": f"stopped:{exc}",
                    "stop_reason": str(exc),
                    "stage_ledger": [],
                    "usage": {"stages": {}, "aggregate": {}},
                }
        reports.append(report)
        for key in USAGE_KEYS:
            totals[key] += report["usage"]["aggregate"].get(key, 0)
    if budget_stop is not None:
        persisted = {
            "cases": reports,
            "aggregate": totals,
            "budget": dict(effective_budget),
            "stop_reason": budget_stop,
        }
        _exclusive_write(
            Path(out_dir) / "batch-report.json",
            json.dumps(persisted, ensure_ascii=False, indent=2, sort_keys=True),
        )
        raise BudgetBreached(
            f"budget_exceeded:model_requests={totals['model_requests']}/"
            f"{effective_budget.get('model_requests_max')}"
            f" output_tokens={totals['output_tokens']}/{effective_budget.get('output_tokens_max')}"
            f" report={Path(out_dir) / 'batch-report.json'}"
        )
    return {
        "cases": reports,
        "aggregate": totals,
        "budget": dict(effective_budget),
        "out_dir": str(out_dir),
    }


# ---------------------------------------------------------------------------
# CLI — dual-flag authorized real runs only
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daily Reader Teaching v2 canonical prototype real-run harness (P-4E)"
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="brand-new output directory (single attempt)",
    )
    parser.add_argument(
        "--dataset-dir",
        default=str(EVALS_ROOT / "datasets" / "daily-reader-teaching-v2"),
    )
    parser.add_argument("--case", action="append", default=[], help="case id (repeatable)")
    parser.add_argument("--model-profiles-json", default="")
    parser.add_argument("--model-presets-json", default="")
    parser.add_argument("--real-run", action="store_true", help="authorization flag 1/2")
    parser.add_argument(
        "--enable-real-provider-calls", action="store_true", help="authorization flag 2/2"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not (args.real_run and args.enable_real_provider_calls):
        print(
            "REFUSED: real runs require both --real-run and --enable-real-provider-calls",
            file=sys.stderr,
        )
        return 2
    dataset_dir = Path(args.dataset_dir)
    case_ids = args.case or list(FROZEN_CASE_IDS)
    cases = []
    for case_id in case_ids:
        path = dataset_dir / "cases" / f"{case_id}.json"
        if not path.is_file():
            print(f"missing frozen case file: {path}", file=sys.stderr)
            return 1
        cases.append(json.loads(path.read_text(encoding="utf-8")))
    settings_kwargs: dict[str, str] = {}
    if args.model_profiles_json:
        settings_kwargs["model_profiles_json"] = args.model_profiles_json
    if args.model_presets_json:
        settings_kwargs["model_presets_json"] = args.model_presets_json
    try:
        settings = Settings(**settings_kwargs)
        selection = build_eval_selection(settings)
        report = run_batch(cases, settings, selection, production_transport, Path(args.out_dir))
    except FileExistsError as exc:
        print(f"REFUSED: attempt/output already exists: {exc}", file=sys.stderr)
        return 3
    except BudgetBreached as exc:
        print(f"BUDGET STOPPED: {exc}", file=sys.stderr)
        return 4
    except BatchStopped as exc:
        print(f"BATCH STOPPED: {exc}", file=sys.stderr)
        return 5
    except StructuralCaseError as exc:
        print(f"CONFIG STOPPED: {exc}", file=sys.stderr)
        return 6
    print(json.dumps(report["aggregate"], sort_keys=True), "->", args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
