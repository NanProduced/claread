from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.prompting.example_strategy import (
    get_grammar_example_strategy,
    get_translation_example_strategy,
)
from app.services.prompting.prompt_loader import (
    load_agent_instructions,
    load_examples,
    load_policy_lines,
)
from app.services.prompting.runtime_context import (
    PromptRuntimeOverride,
    get_prompt_runtime_override,
    prompt_runtime_override,
)


def _plan(*, few_shot_mode: str = "baseline") -> SimpleNamespace:
    return SimpleNamespace(
        variant_id="intermediate_reading",
        few_shot_mode=few_shot_mode,
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
                    "intermediate_reading": ["variant policy line"],
                }
            }
        },
    )

    baseline = load_policy_lines("grammar", "balanced", "intermediate_reading")
    with prompt_runtime_override(override):
        overridden = load_policy_lines("grammar", "balanced", "intermediate_reading")

    assert overridden == ["variant policy line"]
    assert load_policy_lines("grammar", "balanced", "intermediate_reading") == baseline


def test_instruction_override_uses_current_reader_agents() -> None:
    override = PromptRuntimeOverride(
        variant_id="instruction-variant",
        instructions={
            "reader_layer_grammar_bundle": "Variant grammar instructions.",
            "daily_vocab": "Variant Daily Reader instructions.",
        },
    )

    baseline = load_agent_instructions("reader_layer_grammar_bundle")
    with prompt_runtime_override(override):
        assert (
            load_agent_instructions("reader_layer_grammar_bundle")
            == "Variant grammar instructions."
        )
        assert load_agent_instructions("daily_vocab") == "Variant Daily Reader instructions."

    assert load_agent_instructions("reader_layer_grammar_bundle") == baseline


def test_reader_grammar_instructions_declare_current_anchor_contract() -> None:
    instructions = load_agent_instructions("reader_layer_grammar_bundle")

    assert "anchor_segment_id" in instructions
    assert "selected_text" in instructions
    assert "Markdown" in instructions
    assert "raw HTML" in instructions


def test_reader_vocabulary_instructions_keep_phrase_boundary() -> None:
    instructions = load_agent_instructions("reader_layer_vocabulary")

    assert "phrase_gloss" in instructions
    assert "selected_text" in instructions
    assert "fixed_collocation" in instructions
    assert "phrasal_verb" in instructions


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


def test_grammar_examples_use_exact_visible_text_anchors() -> None:
    variants = [
        "beginner_reading",
        "intensive_reading",
        "gaokao",
        "cet",
        "kaoyan",
        "tem",
        "ielts_toefl",
    ]

    for variant in variants:
        for example in load_examples("grammar", variant):
            if example.get("example_type") != "grammar":
                continue
            payload = json.loads(example["output_fragment"])
            sentence_text = example["sentence_text"]
            for quote in payload.get("anchor_quotes", []):
                quote_text = quote["text"]
                assert "..." not in quote_text
                assert quote_text in sentence_text


def test_vocabulary_examples_output_fragments_are_valid_json() -> None:
    variants = [
        "beginner_reading",
        "default",
        "intensive_reading",
        "gaokao",
        "cet",
        "kaoyan",
        "tem",
        "ielts_toefl",
    ]

    for variant in variants:
        for example in load_examples("vocabulary", variant):
            payload = json.loads(example["output_fragment"])
            assert isinstance(payload, dict)


def test_few_shot_mode_variant_uses_neutral_manifest_examples() -> None:
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
