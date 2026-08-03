from __future__ import annotations

import logging

from app.config.settings import Settings
from app.llm.router import resolve_model_config
from app.llm.routes import ModelRoute
from app.llm.types import ModelSelection, ResolvedModelConfig

logger = logging.getLogger(__name__)


def build_model_metadata(
    model_config: ResolvedModelConfig | None,
) -> dict[str, str | None]:
    if model_config is None:
        return {
            "model_route": None,
            "model_profile": None,
            "model_provider": None,
            "model_name": None,
        }
    return {
        "model_route": model_config.route,
        "model_profile": model_config.profile_name,
        "model_provider": model_config.provider,
        "model_name": model_config.model_name,
    }


def resolve_model_metadata(
    settings: Settings,
    route: ModelRoute,
    selection: ModelSelection | None = None,
) -> dict[str, str | None]:
    """
    Best-effort route metadata lookup for audit fields.

    Audit enrichment must not become a hard dependency of the business path.
    """
    try:
        return build_model_metadata(resolve_model_config(settings, route, selection))
    except Exception as exc:
        logger.warning(
            "Failed to resolve model metadata for route=%s: %s",
            route,
            exc,
        )
        return build_model_metadata(None)
