from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.eval_adapter.schemas import ArticleAnalysisEvalRequest, WorkflowLabBaselineBundleRequest
from app.eval_adapter.workflow_lab import get_workflow_lab_baseline_bundle
from app.services.analysis.prompting.example_strategy import (
    get_grammar_example_strategy,
    get_translation_example_strategy,
)
from app.services.analysis.prompting.prompt_loader import (
    load_agent_instructions,
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


def test_instruction_override_falls_back_outside_scope() -> None:
    override = PromptRuntimeOverride(
        variant_id="instruction-variant",
        instructions={
            "grammar": "Variant grammar instructions.",
            "repair": "Variant repair instructions.",
        },
    )

    baseline = load_agent_instructions("grammar")
    with prompt_runtime_override(override):
        assert load_agent_instructions("grammar") == "Variant grammar instructions."
        assert load_agent_instructions("repair") == "Variant repair instructions."

    assert load_agent_instructions("grammar") == baseline


def test_grammar_instructions_define_source_evidence_span_contract() -> None:
    instructions = load_agent_instructions("grammar")

    assert "anchor_quotes" in instructions
    assert "必须逐字复制原句中的连续可见片段" in instructions
    assert "不得用 ... 或省略号代替原文中间内容" in instructions
    assert "自然短片段" not in instructions


def test_vocabulary_instructions_define_phrase_title_and_explicit_spans_contract() -> None:
    instructions = load_agent_instructions("vocabulary")

    assert "phrase_gloss.label 是短语卡片标题 / lookup_text / 教学短语名" in instructions
    assert "默认每条 phrase_gloss 都提供 anchor_quotes" in instructions
    assert "兼作旧式锚点" not in instructions


def test_workflow_lab_baseline_bundle_returns_learning_prompt_layers() -> None:
    bundle = get_workflow_lab_baseline_bundle(
        WorkflowLabBaselineBundleRequest(
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
        )
    )

    assert bundle.schema_version == "workflow-prompt-bundle-v1"
    assert bundle.topology_mode == "learning"
    assert set(bundle.agents) == {"vocabulary", "grammar", "translation", "repair"}
    assert bundle.agents["grammar"].instructions
    assert bundle.agents["grammar"].policy_lines
    assert bundle.agents["repair"].policy_lines == []


def test_workflow_lab_baseline_bundle_rejects_academic() -> None:
    with pytest.raises(ValueError, match="learning topology"):
        WorkflowLabBaselineBundleRequest(
            reading_goal="academic",
            reading_variant="academic_general",
        )


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


def test_vocabulary_phrase_examples_use_explicit_source_spans() -> None:
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
            if example.get("example_type") != "phrase":
                continue
            payload = json.loads(example["output_fragment"])
            if payload.get("type") != "phrase_gloss":
                continue
            sentence_text = example["sentence_text"]
            anchor_quotes = payload.get("anchor_quotes")
            assert anchor_quotes, f"{variant} phrase_gloss example missing anchor_quotes"
            assert 1 <= len(anchor_quotes) <= 4
            for quote in anchor_quotes:
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
            assert isinstance(payload, dict), f"{variant} output_fragment must decode to an object"


def test_grammar_examples_output_fragments_are_valid_json() -> None:
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
            payload = json.loads(example["output_fragment"])
            assert isinstance(payload, dict), f"{variant} output_fragment must decode to an object"


def test_grammar_policy_lines_focus_on_teaching_strategy_not_anchor_shape() -> None:
    policy_lines = load_policy_lines("grammar", "explicit_exam", "gaokao")
    joined = " ".join(policy_lines)

    assert "锚点" not in joined
    assert "span" not in joined


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
