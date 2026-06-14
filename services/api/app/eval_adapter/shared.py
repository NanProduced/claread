from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any
from uuid import uuid4

from app.config.settings import Settings, get_settings
from app.eval_adapter.schemas import (
    LLMConfigSnapshot,
    ModelIdentity,
    ModelProfileSummary,
    PromptIdentity,
    RequestSnapshot,
    StructuredOutputRuntime,
    StructuredOutputSnapshot,
)
from app.llm.provider_factory import _resolve_openai_profile
from app.llm.registry import build_model_registry
from app.llm.router import ModelSelectionError, resolve_model_config
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection
from app.observability import disabled_tracing
from app.services.analysis.prompting.prompt_loader import get_prompt_version

logger = logging.getLogger(__name__)


def source_text_hash(text: str) -> str:
    return sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def request_id(request: Any) -> str:
    if request.request_id:
        return request.request_id
    if request.run_id and request.case_id:
        return f"eval:{request.run_id}:{request.case_id}"
    return f"eval:{uuid4()}"


def request_snapshot(request: Any, *, request_id_value: str) -> RequestSnapshot:
    return RequestSnapshot(
        case_id=request.case_id,
        run_id=request.run_id,
        request_id=request_id_value,
        source_text_hash=source_text_hash(request.text),
        source_char_count=len(request.text),
        reading_goal=request.reading_goal,
        reading_variant=request.reading_variant,
        source_type=request.source_type,
        extended=request.extended,
        rag_mode=request.rag_mode,
        trace_scope=request.trace_scope,
        repair_mode=getattr(request, "repair_mode", "patch"),
        repair_enabled=getattr(request, "repair_enabled", True),
    )


def prompt_snapshot_hash(request: Any, *, prompt_version: str | None = None) -> str | None:
    if request.prompt_override is None:
        return None
    if request.prompt_override.prompt_snapshot_hash:
        return request.prompt_override.prompt_snapshot_hash
    version = prompt_version or get_prompt_version()
    payload = {
        "prompt_version": version,
        "prompt_override": request.prompt_override.model_dump(
            mode="json",
            exclude={"prompt_snapshot_hash"},
            exclude_none=True,
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def prompt_identity(request: Any, *, prompt_version: str | None = None) -> PromptIdentity:
    version = prompt_version or get_prompt_version()
    return PromptIdentity(
        prompt_version=version,
        prompt_variant_id=(
            request.prompt_variant_id
            or (request.prompt_override.variant_id if request.prompt_override else None)
        ),
        prompt_snapshot_hash=prompt_snapshot_hash(request, prompt_version=version),
    )


def safe_model_selection_payload(selection: ModelSelection | None) -> dict[str, Any]:
    if selection is None:
        return {}
    return selection.model_dump(mode="json", exclude_none=True)


def model_settings_payload(model_settings: Any) -> dict[str, Any]:
    if model_settings is None:
        return {}
    payload = model_settings.model_dump(mode="json", exclude_none=True)
    payload.pop("extra_headers", None)
    return payload


def model_identity(
    selection: ModelSelection | None,
    *,
    settings: Settings | None = None,
) -> ModelIdentity | None:
    config = resolve_model_config(
        settings or get_settings(),
        MODEL_ROUTE_ANNOTATION_GENERATION,
        selection,
    )
    if config is None:
        return None
    return ModelIdentity(
        route=config.route,
        profile_name=config.profile_name,
        provider=config.provider,
        model_name=config.model_name,
        fallback_profiles=list(config.fallback_profiles),
        model_settings=model_settings_payload(config.model_settings),
    )


def _resolve_parallel_tool_calls(config: Any) -> bool | None:
    """Resolve parallel_tool_calls from model_settings."""
    ms = config.model_settings
    if ms is None:
        return None
    return getattr(ms, "parallel_tool_calls", None)


def _resolve_thinking_enabled(config: Any) -> bool:
    """Resolve thinking_enabled from model_settings.extra_body."""
    ms = config.model_settings
    if ms is None:
        return False
    return ms.thinking_enabled()


def _build_structured_output_runtime(
    config: Any,
    resolved_profile: Any,
) -> list[StructuredOutputRuntime]:
    """Build per-agent structured output runtime entries.

    Currently the workflow uses a single model profile for all three agents
    (vocabulary, grammar, translation), so we produce one entry per agent
    with the same resolved config.  Observed fields are None because the
    snapshot is built before the actual run; they are filled in later by
    the eval adapter after the run completes.
    """
    resolved_mode = (
        resolved_profile.default_structured_output_mode
        if resolved_profile
        else "tool"
    )
    supports_tcr = (
        resolved_profile.openai_supports_tool_choice_required
        if resolved_profile
        else True
    )
    supports_json_object = (
        resolved_profile.supports_json_object_output
        if resolved_profile
        else False
    )

    # tool mode: required or auto depending on provider support
    # prompted / native mode: no tool_choice is sent → None
    if resolved_mode == "tool":
        inferred_tool_choice = "required" if supports_tcr else "auto"
    else:
        inferred_tool_choice = None

    if resolved_mode == "native":
        inferred_response_format = "json_schema"
    elif resolved_mode == "prompted" and supports_json_object:
        inferred_response_format = "json_object"
    else:
        inferred_response_format = None

    parallel_tc = _resolve_parallel_tool_calls(config)
    thinking = _resolve_thinking_enabled(config)

    agent_names = ["vocabulary", "grammar", "translation"]
    entries: list[StructuredOutputRuntime] = []
    for agent_name in agent_names:
        entries.append(
            StructuredOutputRuntime(
                agent_name=agent_name,
                profile_name=config.profile_name,
                provider=config.provider,
                model_name=config.model_name,
                resolved_default_structured_output_mode=resolved_mode,
                resolved_openai_supports_tool_choice_required=supports_tcr,
                inferred_expected_tool_choice=inferred_tool_choice,
                inferred_expected_response_format=inferred_response_format,
                resolved_parallel_tool_calls=parallel_tc,
                resolved_thinking_enabled=thinking,
                observed_usage=None,
                observed_retry_count=None,
                observed_request_count=None,
            )
        )
    return entries


def enrich_structured_output_runtime(
    snapshot: LLMConfigSnapshot,
    runtime_summary: dict[str, Any] | None,
) -> LLMConfigSnapshot:
    """Fill observed fields in structured_output_runtime from runtime_summary.

    ``runtime_summary`` is the dict produced by ``build_runtime_summary()``,
    whose ``per_agent`` key maps agent names (e.g. "vocabulary") to usage
    dicts with ``input_tokens`` / ``output_tokens`` / ``total_tokens``.

    Returns the same snapshot object with observed fields mutated in-place.
    """
    per_agent = (runtime_summary or {}).get("per_agent") or {}
    for entry in snapshot.structured_output_runtime:
        agent_data = per_agent.get(entry.agent_name)
        if agent_data and isinstance(agent_data, dict):
            entry.observed_usage = {
                "input_tokens": agent_data.get("input_tokens"),
                "output_tokens": agent_data.get("output_tokens"),
                "total_tokens": agent_data.get("total_tokens"),
            }
    return snapshot


def build_llm_config_snapshot(
    selection: ModelSelection | None,
    *,
    settings: Settings | None = None,
) -> LLMConfigSnapshot | None:
    config = resolve_model_config(
        settings or get_settings(),
        MODEL_ROUTE_ANNOTATION_GENERATION,
        selection,
    )
    if config is None:
        return None

    # Use the resolved OpenAIModelProfile (which merges config + hint +
    # PydanticAI defaults) rather than the raw OpenAIProfileConfig dict,
    # so that inferred fields reflect actual runtime behavior.
    resolved_profile = _resolve_openai_profile(config)
    openai_profile = config.openai_profile
    openai_profile_dict = (
        openai_profile.model_dump(exclude_none=True) if openai_profile else {}
    )

    # Read from resolved profile (falls back to PydanticAI defaults)
    supports_tool_choice_required = (
        resolved_profile.openai_supports_tool_choice_required
        if resolved_profile
        else True
    )
    default_mode = (
        resolved_profile.default_structured_output_mode
        if resolved_profile
        else "tool"
    )
    supports_json_schema = (
        resolved_profile.supports_json_schema_output
        if resolved_profile
        else False
    )
    supports_json_object = (
        resolved_profile.supports_json_object_output
        if resolved_profile
        else False
    )
    # tool mode: required or auto depending on provider support
    # prompted / native mode: no tool_choice is sent → None
    if default_mode == "tool":
        expected_tool_choice = "required" if supports_tool_choice_required else "auto"
    else:
        expected_tool_choice = None
    # Infer expected_response_format from PydanticAI's actual request behavior:
    #   tool mode → no response_format, uses tools + tool_choice → None
    #   native mode → response_format json_schema → "json_schema"
    #   prompted + json_object → response_format json_object → "json_object"
    #   prompted without json_object → no response_format → None
    if default_mode == "native":
        expected_response_format = "json_schema"
    elif default_mode == "prompted" and supports_json_object:
        expected_response_format = "json_object"
    else:
        expected_response_format = None

    return LLMConfigSnapshot(
        profile_name=config.profile_name,
        provider=config.provider,
        adapter=config.adapter,
        model_name=config.model_name,
        fallback_profiles=list(config.fallback_profiles),
        model_settings=model_settings_payload(config.model_settings),
        openai_profile=openai_profile_dict,
        structured_output=StructuredOutputSnapshot(
            default_structured_output_mode=default_mode,
            supports_json_schema_output=supports_json_schema,
            supports_json_object_output=supports_json_object,
            openai_supports_tool_choice_required=supports_tool_choice_required,
            expected_tool_choice=expected_tool_choice,
            expected_response_format=expected_response_format,
        ),
        parallel_tool_calls=_resolve_parallel_tool_calls(config),
        thinking_enabled=_resolve_thinking_enabled(config),
        structured_output_runtime=_build_structured_output_runtime(
            config, resolved_profile
        ),
    )


def build_llm_config_snapshot_safe(
    selection: ModelSelection | None,
    *,
    settings: Settings | None = None,
) -> LLMConfigSnapshot | None:
    """Safe wrapper that returns None instead of raising on invalid selection."""
    try:
        return build_llm_config_snapshot(selection, settings=settings)
    except ModelSelectionError:
        return None


def list_model_profile_summaries(
    *,
    settings: Settings | None = None,
) -> list[ModelProfileSummary]:
    """List model profile summaries for eval/ops tooling.

    This is a **resolve-only** catalog: it checks that each profile can be
    resolved via ``resolve_model_config``, but does NOT verify that the
    resolved config can be built into a live model (``build_model_instance``).
    Profiles that fail resolution (e.g. reference an unknown provider) are
    silently skipped.  This is intentional — the summary is a static catalog,
    not a usability promise.
    """
    effective_settings = settings or get_settings()
    registry = build_model_registry(effective_settings)
    annotation_default = registry.route_defaults.get(MODEL_ROUTE_ANNOTATION_GENERATION)
    default_profile = registry.default_profile
    summaries: list[ModelProfileSummary] = []
    for profile_name, profile in registry.profiles.items():
        if not profile.is_configured():
            continue
        try:
            resolved = resolve_model_config(
                effective_settings,
                MODEL_ROUTE_ANNOTATION_GENERATION,
                ModelSelection(default_profile=profile_name),
            )
        except ModelSelectionError as exc:
            logger.warning(
                "Skipping model profile %r in summary: %s",
                profile_name,
                exc,
            )
            continue
        summaries.append(
            ModelProfileSummary(
                profile_name=profile_name,
                provider=resolved.provider if resolved is not None else "",
                model_name=resolved.model_name if resolved is not None else "",
                annotation_route_default=profile_name == annotation_default,
                default_profile=profile_name == default_profile,
            )
        )
    return sorted(
        summaries,
        key=lambda item: (
            not item.annotation_route_default,
            not item.default_profile,
            item.profile_name.lower(),
        ),
    )


def rag_override(request: Any) -> bool | None:
    if request.rag_mode == "settings":
        return None
    if request.rag_mode in {"off", "baseline"}:
        return False
    return True


@contextmanager
def trace_scope(request: Any) -> Iterator[None]:
    """Apply eval-only LangSmith tracing scope for the wrapped block.

    Policy (see ``docs/operations/langsmith.md``):

    * ``trace_scope == "off"`` (the default for every eval-center request):
      disable LangSmith tracing for the wrapped call only, via the SDK's
      ``ContextVar``-based switch. **No process-global env mutation.**
    * ``trace_scope == "inherit"``: no wrapping. Whatever global tracing
      state (``LANGSMITH_TRACING``) is active applies. Use this when an
      operator deliberately wants the eval run to appear in the main
      ``LANGSMITH_PROJECT`` for debugging.

    Anything else (e.g. the historical ``"isolated"`` value, which used to
    mutate ``os.environ["LANGSMITH_PROJECT"]`` and was unsafe under
    concurrency) is rejected at the schema layer and treated as
    ``"inherit"`` here as a defensive default.

    ``request.trace_project`` is intentionally ignored — per-call project
    switching is no longer a supported capability. Operators who need a
    dedicated eval project should configure a separate process /
    deployment with its own ``LANGSMITH_PROJECT``.
    """

    scope = getattr(request, "trace_scope", "off")
    if scope == "off":
        with disabled_tracing():
            yield
        return
    # "inherit" or any unexpected value: do nothing, inherit global tracing.
    yield
