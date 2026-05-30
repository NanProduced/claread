from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.eval_adapter.schemas import ArticleAnalysisEvalRequest
from app.services.analysis.prompting.example_strategy import (
    get_grammar_example_strategy,
    get_translation_example_strategy,
)
from app.services.analysis.prompting.prompt_loader import (
    load_examples,
    load_policy_lines,
)
from app.services.analysis.prompting.runtime_context import (
    PromptRuntimeOverride,
    get_prompt_runtime_override,
    prompt_runtime_override,
)


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        variant_id="intermediate_reading",
        few_shot_mode="baseline",
    )


def test_prompt_runtime_override_does_not_leak() -> None:
    override = PromptRuntimeOverride(
        variant_id="variant-a",
        few_shot_mode="off",
    )

    assert get_prompt_runtime_override() is None
    with prompt_runtime_override(override):
        assert get_prompt_runtime_override() == override
    assert get_prompt_runtime_override() is None


def test_policy_override_falls_back_outside_scope() -> None:
    override = PromptRuntimeOverride(
        variant_id="variant-a",
        policies={
            "grammar": {
                "balanced": {
                    "default": ["variant policy line"],
                }
            }
        },
    )

    baseline = load_policy_lines("grammar", "balanced", "intermediate_reading")
    with prompt_runtime_override(override):
        overridden = load_policy_lines("grammar", "balanced", "intermediate_reading")

    assert overridden == ["variant policy line"]
    assert load_policy_lines("grammar", "balanced", "intermediate_reading") == baseline


def test_few_shot_mode_off_returns_empty_examples() -> None:
    override = PromptRuntimeOverride(
        variant_id="no-few-shot",
        few_shot_mode="off",
    )

    with prompt_runtime_override(override):
        strategy = get_grammar_example_strategy(_plan())

    assert strategy.selection_mode == "off"
    assert strategy.examples == []


def test_load_examples_stays_baseline_while_strategy_controls_few_shot_mode() -> None:
    override = PromptRuntimeOverride(
        variant_id="no-few-shot",
        few_shot_mode="off",
    )
    baseline = load_examples("grammar", "intermediate_reading")

    with prompt_runtime_override(override):
        examples = load_examples("grammar", "intermediate_reading")

    assert examples == baseline


def test_few_shot_mode_variant_uses_manifest_examples_without_baseline_fallback() -> None:
    override = PromptRuntimeOverride(
        variant_id="variant-examples",
        few_shot_mode="variant",
        examples={
            "grammar": {
                "intermediate_reading": [
                    {
                        "example_type": "grammar",
                        "sentence_text": "Variant sentence.",
                        "output_fragment": '{"type":"grammar_note"}',
                    }
                ]
            }
        },
    )

    with prompt_runtime_override(override):
        missing_variant_strategy = get_translation_example_strategy(_plan())
        strategy = get_grammar_example_strategy(_plan())

    assert missing_variant_strategy.selection_mode == "variant"
    assert missing_variant_strategy.examples == []
    assert strategy.selection_mode == "variant"
    assert len(strategy.examples) == 1
    assert strategy.examples[0].sentence_text == "Variant sentence."


def test_prompt_override_requires_rag_mode_off() -> None:
    with pytest.raises(ValueError, match="rag_mode='off'"):
        ArticleAnalysisEvalRequest(
            text="Sentence one.",
            rag_mode="settings",
            prompt_override={
                "variant_id": "variant-a",
                "few_shot_mode": "off",
            },
        )
