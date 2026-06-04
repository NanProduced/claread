"""Tests for Example Lab AI generation."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import Settings
from app.eval_adapter import example_lab
from app.llm.structured_completion import StructuredCompletionResult


def _settings_with_profile(profile: str = "primary", *, base_url: str = "https://example.invalid/v1", api_key: str = "primary-key") -> Settings:
    return Settings(
        default_model_profile=profile,
        model_profiles_json=json.dumps(
            {
                profile: {
                    "provider": "openai_compatible",
                    "model_name": "primary-model",
                    "base_url": base_url,
                    "api_key": api_key,
                }
            }
        ),
    )


async def test_rule_only_path_returns_rule_label() -> None:
    result = await example_lab.generate_rag_fields(
        sentence_text="The quick brown fox jumps over the lazy dog.",
        output_fragment={"type": "grammar_note", "label": "非限制性定语从句"},
        reading_variant="gaokao",
        model_profile=None,
    )

    assert result["generated_by"] == "rule"
    assert "nonrestrictive_relative_clause" in result["grammar_tags"]


async def test_llm_path_uses_shared_helper_and_returns_llm_label() -> None:
    settings = _settings_with_profile()
    fake = AsyncMock()
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["relative_clause"],
            "structure_signals": ["has_wh_clause", "long_sentence"],
            "teaching_goal": "balanced",
            "retrieval_text": "raw-text",
        },
        raw_text=json.dumps({"grammar_tags": ["relative_clause"]}),
        model_name="primary-model",
        profile_name="primary",
        base_url="https://example.invalid/v1",
    )

    with patch.object(example_lab, "get_settings", lambda: settings), patch.object(
        example_lab, "run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="The cat that chased the mouse ran away.",
            output_fragment={"type": "grammar_note", "label": "限制性定语从句"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    assert result["generated_by"] == "llm"
    assert result["grammar_tags"] == ["relative_clause"]
    assert result["teaching_goal"] == "balanced"
    assert fake.call_count == 1


async def test_llm_failure_falls_back_to_rule_with_llm_fallback_label() -> None:
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

    fake = AsyncMock(side_effect=StructuredCompletionError("LLM HTTP 502: bad gateway"))

    with patch.object(example_lab, "get_settings", lambda: settings), patch.object(
        example_lab, "run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="Whatever the cat brought.",
            output_fragment={"type": "grammar_note", "label": "让步状语从句"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    assert result["generated_by"] == "llm_fallback"
    # Rule-based extraction should have run and produced a label.
    assert result["grammar_tags"]


async def test_llm_profile_not_configured_falls_back_to_rule() -> None:
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

    # Mimic the real helper: a missing profile triggers a
    # StructuredCompletionError, not a successful return.
    fake = AsyncMock(
        side_effect=StructuredCompletionError(
            "Model profile is not configured: Unknown model profile for "
            "annotation_generation: missing-profile"
        )
    )

    with patch.object(example_lab, "get_settings", lambda: settings), patch.object(
        example_lab, "run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="The student studied hard.",
            output_fragment={"type": "grammar_note", "label": "主谓一致"},
            reading_variant="gaokao",
            model_profile="missing-profile",
        )

    # No model_profile is configured for the route, so helper raises and rule
    # fallback kicks in.
    assert result["generated_by"] == "llm_fallback"
    assert fake.call_count == 1
