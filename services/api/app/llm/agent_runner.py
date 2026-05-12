from __future__ import annotations

from typing import Any

from app.config.settings import get_settings
from app.llm.router import build_model_for_route
from app.llm.routes import ModelRoute
from app.llm.types import ModelSelection, ResolvedModelConfig
from app.workflow.tracing import build_usage_metadata


async def run_agent_with_route(
    *,
    agent: Any,
    prompt: str,
    deps: Any,
    route: ModelRoute,
    model_selection: ModelSelection | None = None,
) -> Any:
    model, model_config = build_model_for_route(get_settings(), route, model_selection)
    if model is None:
        raise RuntimeError(f"model route is not configured: {route}")
    result = await agent.run(prompt, deps=deps, model=model)
    result._resolved_model_config = model_config
    return result


def extract_model_metadata(model_config: ResolvedModelConfig | None) -> dict[str, str]:
    if model_config is None:
        return {"model_name": "unknown", "profile_name": "unknown", "ls_provider": "unknown", "ls_model_name": "unknown"}
    return {
        "model_name": model_config.model_name,
        "profile_name": model_config.profile_name,
        "ls_provider": model_config.provider,
        "ls_model_name": model_config.model_name,
    }


def extract_run_usage(result: Any) -> dict[str, object] | None:
    usage_fn = getattr(result, "usage", None)
    if callable(usage_fn):
        usage = usage_fn()
        if usage is not None:
            return build_usage_metadata(usage)
    return None
