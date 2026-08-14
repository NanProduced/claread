# task-history: (renamed from test_reader_semantic_outline_t58a_registration.py)
"""Semantic outline route / settings / prompt / capability registration.

Default-off registration only. No real adapter, no provider calls.
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.llm.registry import build_model_registry
from app.llm.router import build_model_for_route
from app.llm.routes import (
    ALL_MODEL_ROUTES,
    MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
)
from app.services.ai_usage import CAPABILITY_READER_SEMANTIC_OUTLINE
from app.services.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)
from app.services.reader_orchestration.job_bootstrap import (
    default_semantic_outline_request_eligibility,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    SemanticOutlineWorkerService,
    UnconfiguredSemanticOutlineGenerator,
)

pytestmark = [
    pytest.mark.chain_reader_orchestration,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

SEMANTIC_OUTLINE_PROMPT_AGENT = "reader_semantic_outline"


def test_route_in_all_model_routes() -> None:
    assert MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE == "reader_layer_semantic_outline"
    assert MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE in ALL_MODEL_ROUTES


def test_settings_defaults_are_closed() -> None:
    settings = Settings()
    assert settings.reader_semantic_outline_model_profile == ""
    assert settings.semantic_outline_generation_enabled is False


def test_route_defaults_only_when_profile_set() -> None:
    closed = build_model_registry(
        Settings(
            annotation_model_profile="annotation",
            reader_semantic_outline_model_profile="",
        )
    )
    opened = build_model_registry(
        Settings(
            annotation_model_profile="annotation",
            reader_semantic_outline_model_profile="outline_profile",
        )
    )
    assert MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE not in closed.route_defaults
    assert (
        opened.route_defaults[MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE]
        == "outline_profile"
    )


def test_build_model_fail_closed_without_profile() -> None:
    """Empty outline profile must not register a route default; isolated
    Settings (no default_profile / no profiles JSON) yields no model.
    """
    settings = Settings(
        default_model_profile="",
        annotation_model_profile="",
        reader_semantic_outline_model_profile="",
        model_profiles_json="",
        model_presets_json="",
    )
    registry = build_model_registry(settings)
    assert MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE not in registry.route_defaults
    model, config = build_model_for_route(
        settings, MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
    )
    assert model is None
    assert config is None


def test_capability_constant() -> None:
    assert CAPABILITY_READER_SEMANTIC_OUTLINE == "reader_semantic_outline"


def test_prompt_loads_versioned_full_contract() -> None:
    """Load via prompt-loader seam; assert contract markers, not version string order."""
    version = get_prompt_version()
    assert version and version != "unknown"

    text = load_agent_instructions(SEMANTIC_OUTLINE_PROMPT_AGENT)
    assert text.strip()
    # Must not be an empty stub
    assert len(text) > 200

    lower = text.lower()
    # Bounded-only input contract
    assert "preview" in lower or "有界" in text
    assert "unit_id" in lower or "units" in lower
    # Candidate-only output
    assert "candidate_ref" in lower
    assert "candidates" in lower
    # Forbid durable / revision ids (named as forbidden in prompt)
    assert "node_id" in lower
    assert "outline_revision" in lower
    assert "禁止" in text or "严禁" in text
    assert "parent_node_id" in lower

    # Output format disambiguation: retain exact negative requirements rather
    # than merely matching isolated keywords that could describe an allowed
    # Markdown fence or prose response.
    assert "只输出 raw JSON" in text
    assert "禁止 Markdown code fence" in text
    assert "禁止解释、前后缀文字及任何额外散文" in text

def test_default_worker_still_unconfigured() -> None:
    worker = SemanticOutlineWorkerService(pool=None)
    assert isinstance(worker._generator, UnconfiguredSemanticOutlineGenerator)


def test_default_eligibility_still_false() -> None:
    class _S:
        readiness_state = "article_ready"

    assert default_semantic_outline_request_eligibility(_S()) is False  # type: ignore[arg-type]


def test_generation_enabled_field_is_not_live_kill_switch() -> None:
    """Document contract: settings field exists but does not wire it.

    Presence of semantic_outline_generation_enabled=False must not be confused
    with bootstrap/executor dual-check kill-switch (d).
    """
    settings = Settings(semantic_outline_generation_enabled=False)
    assert settings.semantic_outline_generation_enabled is False
    # No bootstrap import side-effect: eligibility default remains false regardless.
    class _S:
        readiness_state = "article_ready"

    assert default_semantic_outline_request_eligibility(_S()) is False  # type: ignore[arg-type]
