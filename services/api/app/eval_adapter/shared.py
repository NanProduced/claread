from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Any
from uuid import uuid4

from app.config.settings import Settings, get_settings
from app.eval_adapter.schemas import (
    ModelIdentity,
    ModelProfileSummary,
    PromptIdentity,
    RequestSnapshot,
)
from app.llm.registry import build_model_registry
from app.llm.router import resolve_model_config
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.types import ModelSelection
from app.observability import disabled_tracing
from app.services.analysis.prompting.prompt_loader import get_prompt_version


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


def list_model_profile_summaries(
    *,
    settings: Settings | None = None,
) -> list[ModelProfileSummary]:
    registry = build_model_registry(settings or get_settings())
    annotation_default = registry.route_defaults.get(MODEL_ROUTE_ANNOTATION_GENERATION)
    default_profile = registry.default_profile
    summaries = [
        ModelProfileSummary(
            profile_name=profile_name,
            provider=profile.provider,
            model_name=profile.model_name,
            annotation_route_default=profile_name == annotation_default,
            default_profile=profile_name == default_profile,
        )
        for profile_name, profile in registry.profiles.items()
        if profile.is_configured()
    ]
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
