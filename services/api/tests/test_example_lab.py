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


async def test_no_model_profile_raises_value_error() -> None:
    """Without model_profile, generate_rag_fields should raise ValueError."""
    with pytest.raises(ValueError, match="model_profile is required"):
        await example_lab.generate_rag_fields(
            sentence_text="The quick brown fox jumps over the lazy dog.",
            output_fragment={"type": "grammar_note", "label": "非限制性定语从句"},
            reading_variant="gaokao",
            model_profile=None,
        )


async def test_llm_path_uses_shared_helper_and_returns_llm_label() -> None:
    settings = _settings_with_profile()
    fake = AsyncMock()
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["relative_clause"],
            "structure_signals": ["has_that_clause", "long_sentence"],
            "teaching_goal": "balanced",
            "retrieval_text": "raw-text",
            "rationale": "含限定性 that 从句修饰 cat，句子较长。",
        },
        raw_text=json.dumps({"grammar_tags": ["relative_clause"]}),
        model_name="primary-model",
        profile_name="primary",
        base_url="https://example.invalid/v1",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
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
    assert result["confidence"] in ("high", "medium", "low")
    assert result["model_name"] == "primary-model"
    assert result["profile_name"] == "primary"
    assert result["usage"]["total_tokens"] == 150
    # The short Chinese rationale should flow into the public "reasoning" field.
    assert "限定性" in result["reasoning"]
    # The new prompt should be shorter and not require a long CoT reasoning field.
    call_kwargs = fake.call_args.kwargs
    assert "rationale" not in call_kwargs["system_prompt"].lower() or "≤ 200" in call_kwargs["system_prompt"]
    assert "rule_hints" not in call_kwargs["user_prompt"]
    assert call_kwargs["max_tokens"] == 512
    assert call_kwargs["temperature"] == 0.0
    assert fake.call_count == 1


async def test_rationale_is_truncated_to_300_chars() -> None:
    """Rationale must be capped so a chatty LLM cannot blow up the result payload."""
    settings = _settings_with_profile()
    fake = AsyncMock()
    long_rationale = "x" * 1000
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["relative_clause"],
            "structure_signals": ["has_that_clause"],
            "teaching_goal": "balanced",
            "retrieval_text": "raw-text",
            "rationale": long_rationale,
        },
        raw_text=json.dumps({"grammar_tags": ["relative_clause"]}),
        model_name="primary-model",
        profile_name="primary",
        base_url="https://example.invalid/v1",
        usage=None,
    )

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="The cat that chased the mouse ran away.",
            output_fragment={"type": "grammar_note", "label": "限制性定语从句"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    assert len(result["reasoning"]) == 300


async def test_llm_failure_falls_back_to_rule_with_llm_fallback_label() -> None:
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

    fake = AsyncMock(side_effect=StructuredCompletionError("LLM HTTP 502: bad gateway"))

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="Whatever the cat brought.",
            output_fragment={"type": "grammar_note", "label": "让步状语从句"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    assert result["generated_by"] == "llm_fallback"
    assert result["grammar_tags"]
    assert result["model_name"] == ""
    assert result["usage"] is None


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

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
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
