"""Tests for Example Lab AI generation."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import Settings
from app.eval_adapter import example_lab
from app.eval_adapter.schemas import ExampleLabGenerateRagFieldsRequest, ExampleLabGenerateRagFieldsResult
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


# ---------------------------------------------------------------------------
# generate_rag_fields tests
# ---------------------------------------------------------------------------

async def test_no_model_profile_raises_value_error() -> None:
    """Without model_profile, generate_rag_fields should raise ValueError."""
    with pytest.raises(ValueError, match="model_profile is required"):
        await example_lab.generate_rag_fields(
            sentence_text="The quick brown fox jumps over the lazy dog.",
            output_fragment={"type": "grammar_note", "label": "非限制性定语从句", "note_zh": "讲解"},
            reading_variant="gaokao",
            model_profile=None,
        )


async def test_llm_path_returns_new_contract_fields() -> None:
    """LLM path should return grammar_tags, retrieval_text, derived_by and debug metadata."""
    settings = _settings_with_profile()
    fake = AsyncMock()
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["restrictive_relative_clause"],
            "retrieval_text": "variant: gaokao\noutput_type: grammar_note\ngrammar_tags: restrictive_relative_clause\nlabel: 限制性定语从句\nsource_sentence: The cat that chased the mouse ran away.\nexplanation: that引导定语从句",
            "rationale": "含限定性 that 从句修饰 cat",
        },
        raw_text=json.dumps({"grammar_tags": ["restrictive_relative_clause"]}),
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
            output_fragment={"type": "grammar_note", "label": "限制性定语从句", "note_zh": "that引导定语从句修饰cat"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    assert result["generated_by"] == "llm"
    assert result["grammar_tags"] == ["restrictive_relative_clause"]
    assert "retrieval_text" in result
    assert result["derived_by"].startswith("llm:")
    # Old fields must NOT be present
    assert "teaching_goal" not in result
    assert "structure_signals" not in result
    assert result["confidence"] in ("high", "medium", "low")
    assert result["model_name"] == "primary-model"
    assert result["profile_name"] == "primary"
    assert result["usage"]["total_tokens"] == 150
    assert "限定性" in result["reasoning"]
    assert result["fallback_reason"] == ""
    call_kwargs = fake.call_args.kwargs
    assert "rule_hints" not in call_kwargs["user_prompt"]
    assert call_kwargs["max_tokens"] == 512
    assert call_kwargs["temperature"] == 0.0
    assert fake.call_count == 1


async def test_llm_tags_are_normalized_via_open_vocabulary() -> None:
    """LLM output tags should be normalized: alias merge, reject generic, snake_case."""
    settings = _settings_with_profile()
    fake = AsyncMock()
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["Relative Clause", "defining_relative_clause", "general", "passive-voice"],
            "retrieval_text": "variant: gaokao\noutput_type: grammar_note",
            "rationale": "test",
        },
        raw_text="{}",
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
            output_fragment={"type": "grammar_note", "label": "限制性定语从句", "note_zh": "讲解"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    # "Relative Clause" → "relative_clause" (generic tag, NOT aliased to restrictive)
    # "defining_relative_clause" → alias merge → "restrictive_relative_clause"
    # "general" → rejected
    # "passive-voice" → "passive_voice"
    tags = result["grammar_tags"]
    assert "relative_clause" in tags
    assert "restrictive_relative_clause" in tags
    assert "passive_voice" in tags
    assert "general" not in tags
    assert "Relative Clause" not in tags
    # No duplicates
    assert len(tags) == len(set(tags))


async def test_rationale_is_truncated_to_300_chars() -> None:
    """Rationale must be capped so a chatty LLM cannot blow up the result payload."""
    settings = _settings_with_profile()
    fake = AsyncMock()
    long_rationale = "x" * 1000
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["restrictive_relative_clause"],
            "retrieval_text": "variant: gaokao\noutput_type: grammar_note",
            "rationale": long_rationale,
        },
        raw_text=json.dumps({"grammar_tags": ["restrictive_relative_clause"]}),
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
            output_fragment={"type": "grammar_note", "label": "限制性定语从句", "note_zh": "讲解"},
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
            output_fragment={"type": "grammar_note", "label": "让步状语从句", "note_zh": "讲解"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    assert result["generated_by"] == "llm_fallback"
    assert result["grammar_tags"]
    assert result["derived_by"] == "rule_engine"
    assert "retrieval_text" in result
    # Old fields must NOT be present
    assert "teaching_goal" not in result
    assert "structure_signals" not in result
    assert result["model_name"] == ""
    assert result["usage"] is None
    assert "bad gateway" in result["fallback_reason"]


async def test_llm_profile_not_configured_falls_back_to_rule() -> None:
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

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
            output_fragment={"type": "grammar_note", "label": "主谓一致", "note_zh": "讲解"},
            reading_variant="gaokao",
            model_profile="missing-profile",
        )

    assert result["generated_by"] == "llm_fallback"
    assert fake.call_count == 1


async def test_fallback_retrieval_text_uses_note_zh_for_grammar_note() -> None:
    """Rule fallback should use note_zh as explanation for grammar_note."""
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

    fake = AsyncMock(side_effect=StructuredCompletionError("fail"))

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="The cat that chased the mouse ran away.",
            output_fragment={"type": "grammar_note", "label": "限制性定语从句", "note_zh": "that引导定语从句修饰cat"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    rt = result["retrieval_text"]
    assert "explanation: that引导定语从句修饰cat" in rt
    assert "variant: gaokao" in rt
    assert "output_type: grammar_note" in rt


async def test_fallback_retrieval_text_uses_analysis_zh_for_sentence_analysis() -> None:
    """Rule fallback should use analysis_zh as explanation for sentence_analysis."""
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

    fake = AsyncMock(side_effect=StructuredCompletionError("fail"))

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="The research conducted by scientists was groundbreaking.",
            output_fragment={"type": "sentence_analysis", "label": "过去分词后置定语", "analysis_zh": "主干是The research was groundbreaking"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    rt = result["retrieval_text"]
    assert "explanation: 主干是The research was groundbreaking" in rt
    assert "output_type: sentence_analysis" in rt


async def test_llm_malformed_retrieval_text_is_rebuilt() -> None:
    """If LLM returns retrieval_text in wrong format (e.g. key=value), it should be rebuilt."""
    settings = _settings_with_profile()
    fake = AsyncMock()
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["inversion"],
            # Wrong format: using = instead of :
            "retrieval_text": "variant=gaokao\noutput_type=grammar_note\ngrammar_tags=inversion",
            "rationale": "test",
        },
        raw_text="{}",
        model_name="primary-model",
        profile_name="primary",
        base_url="https://example.invalid/v1",
        usage=None,
    )

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="Not only did the policy raise costs.",
            output_fragment={"type": "grammar_note", "label": "倒装结构", "note_zh": "not only前置触发部分倒装"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    rt = result["retrieval_text"]
    # Must be in canonical colon-separated format
    assert "variant: gaokao" in rt
    assert "output_type: grammar_note" in rt
    assert "explanation: not only前置触发部分倒装" in rt
    # The old = format must NOT be present
    assert "variant=" not in rt


async def test_llm_missing_required_key_in_retrieval_text_is_rebuilt() -> None:
    """If LLM returns retrieval_text missing a required key, it should be rebuilt."""
    settings = _settings_with_profile()
    fake = AsyncMock()
    fake.return_value = StructuredCompletionResult(
        parsed={
            "grammar_tags": ["inversion"],
            # Missing 'explanation' key
            "retrieval_text": "variant: gaokao\noutput_type: grammar_note\ngrammar_tags: inversion\nlabel: 倒装\nsource_sentence: test",
            "rationale": "test",
        },
        raw_text="{}",
        model_name="primary-model",
        profile_name="primary",
        base_url="https://example.invalid/v1",
        usage=None,
    )

    with patch("app.config.settings.get_settings", lambda: settings), patch(
        "app.llm.structured_completion.run_structured_completion", fake
    ):
        result = await example_lab.generate_rag_fields(
            sentence_text="Not only did the policy raise costs.",
            output_fragment={"type": "grammar_note", "label": "倒装结构", "note_zh": "讲解"},
            reading_variant="gaokao",
            model_profile="primary",
        )

    rt = result["retrieval_text"]
    # Must have been rebuilt with all required keys including explanation
    assert "explanation:" in rt


# ---------------------------------------------------------------------------
# normalize_grammar_tags tests
# ---------------------------------------------------------------------------

def test_normalize_grammar_tags_basic() -> None:
    assert example_lab.normalize_grammar_tags(["inversion", "passive_voice"]) == ["inversion", "passive_voice"]


def test_normalize_grammar_tags_alias_merge() -> None:
    result = example_lab.normalize_grammar_tags(["defining_relative_clause"])
    assert result == ["restrictive_relative_clause"]


def test_normalize_grammar_tags_relative_clause_stays_generic() -> None:
    """relative_clause should NOT be aliased to restrictive_relative_clause."""
    result = example_lab.normalize_grammar_tags(["relative_clause"])
    assert result == ["relative_clause"]


def test_normalize_grammar_tags_reject_generic() -> None:
    result = example_lab.normalize_grammar_tags(["general", "complex", "inversion"])
    assert result == ["inversion"]


def test_normalize_grammar_tags_snake_case_conversion() -> None:
    result = example_lab.normalize_grammar_tags(["Passive Voice", "past-participle"])
    assert "passive_voice" in result
    assert "past_participle" in result


def test_normalize_grammar_tags_dedup() -> None:
    result = example_lab.normalize_grammar_tags(["inversion", "inversion", "inversion"])
    assert result == ["inversion"]


def test_normalize_grammar_tags_empty() -> None:
    assert example_lab.normalize_grammar_tags([]) == []


def test_rule_extract_generic_dingyu_maps_to_relative_clause() -> None:
    """Rule engine: generic '定语从句' should map to relative_clause, not restrictive_relative_clause."""
    tags = example_lab._rule_extract_grammar_tags("定语从句", "grammar_note")
    assert "relative_clause" in tags
    assert "restrictive_relative_clause" not in tags


def test_rule_extract_specific_xianzhixing_maps_to_restrictive() -> None:
    """Rule engine: specific '限制性定语从句' should map to restrictive_relative_clause."""
    tags = example_lab._rule_extract_grammar_tags("限制性定语从句", "grammar_note")
    assert "restrictive_relative_clause" in tags


# ---------------------------------------------------------------------------
# _validate_retrieval_text tests
# ---------------------------------------------------------------------------

def test_validate_retrieval_text_accepts_canonical_format() -> None:
    text = "variant: gaokao\noutput_type: grammar_note\ngrammar_tags: inversion\nlabel: 倒装\nsource_sentence: test\nexplanation: 讲解"
    assert example_lab._validate_retrieval_text(text) is True


def test_validate_retrieval_text_rejects_equals_format() -> None:
    text = "variant=gaokao\noutput_type=grammar_note\ngrammar_tags=inversion\nlabel=倒装\nsource_sentence=test\nexplanation=讲解"
    assert example_lab._validate_retrieval_text(text) is False


def test_validate_retrieval_text_rejects_missing_key() -> None:
    text = "variant: gaokao\noutput_type: grammar_note\ngrammar_tags: inversion"
    assert example_lab._validate_retrieval_text(text) is False


def test_validate_retrieval_text_rejects_empty() -> None:
    assert example_lab._validate_retrieval_text("") is False


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

def test_schema_request_validates_grammar_note_fragment() -> None:
    """grammar_note output_fragment must have label and note_zh."""
    with pytest.raises(ValueError, match="note_zh"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "grammar_note", "label": "test"},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_sentence_analysis_fragment() -> None:
    """sentence_analysis output_fragment must have label and analysis_zh."""
    with pytest.raises(ValueError, match="analysis_zh"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "sentence_analysis", "label": "test"},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_fragment_type_required() -> None:
    """output_fragment must have a type field."""
    with pytest.raises(ValueError, match="type"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"label": "test"},
            reading_variant="intermediate_reading",
        )


def test_schema_request_accepts_valid_grammar_note() -> None:
    req = ExampleLabGenerateRagFieldsRequest(
        sentence_text="test",
        output_fragment={"type": "grammar_note", "label": "test", "note_zh": "讲解"},
        reading_variant="intermediate_reading",
    )
    assert req.output_fragment["type"] == "grammar_note"


def test_schema_request_accepts_valid_sentence_analysis() -> None:
    req = ExampleLabGenerateRagFieldsRequest(
        sentence_text="test",
        output_fragment={"type": "sentence_analysis", "label": "test", "analysis_zh": "分析"},
        reading_variant="intermediate_reading",
    )
    assert req.output_fragment["type"] == "sentence_analysis"


def test_schema_request_accepts_empty_fragment() -> None:
    """Empty output_fragment is allowed (will be populated later)."""
    req = ExampleLabGenerateRagFieldsRequest(
        sentence_text="test",
        output_fragment={},
        reading_variant="intermediate_reading",
    )
    assert req.output_fragment == {}


def test_schema_request_rejects_missing_reading_variant() -> None:
    """reading_variant is a hard boundary; missing field must 422 (Pydantic)."""
    with pytest.raises(Exception, match="reading_variant"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "grammar_note", "label": "test", "note_zh": "讲解"},
        )


def test_schema_request_rejects_empty_reading_variant() -> None:
    """Empty string reading_variant must also fail (no default fallback)."""
    with pytest.raises(Exception, match="reading_variant"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "grammar_note", "label": "test", "note_zh": "讲解"},
            reading_variant="",
        )


def test_schema_result_no_old_fields() -> None:
    """ExampleLabGenerateRagFieldsResult must not have teaching_goal or structure_signals."""
    result = ExampleLabGenerateRagFieldsResult()
    assert not hasattr(result, "teaching_goal")
    assert not hasattr(result, "structure_signals")
    assert not hasattr(result, "retrieval_version")
    assert result.derived_by == ""


def test_schema_result_rejects_old_fields() -> None:
    """Trying to construct with old fields should fail due to extra='forbid'."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ExampleLabGenerateRagFieldsResult(
            grammar_tags=["inversion"],
            teaching_goal="balanced",
        )


def test_schema_request_validates_spans_must_be_array() -> None:
    """grammar_note spans must be an array if present."""
    with pytest.raises(ValueError, match="spans.*array"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "grammar_note", "label": "test", "note_zh": "讲解", "spans": "not-array"},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_chunks_must_be_array() -> None:
    """sentence_analysis chunks must be an array if present."""
    with pytest.raises(ValueError, match="chunks.*array"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "sentence_analysis", "label": "test", "analysis_zh": "分析", "chunks": "not-array"},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_spans_element_must_have_text() -> None:
    """grammar_note spans elements must have a string 'text' field."""
    with pytest.raises(ValueError, match="spans\\[0\\].*text"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "grammar_note", "label": "test", "note_zh": "讲解", "spans": [{}]},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_spans_element_text_must_be_string() -> None:
    """grammar_note spans elements 'text' must be a string."""
    with pytest.raises(ValueError, match="spans\\[0\\].*text"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "grammar_note", "label": "test", "note_zh": "讲解", "spans": [{"text": 123}]},
            reading_variant="intermediate_reading",
        )


def test_schema_request_accepts_valid_spans() -> None:
    """grammar_note with valid spans should pass."""
    req = ExampleLabGenerateRagFieldsRequest(
        sentence_text="test",
        output_fragment={
            "type": "grammar_note",
            "label": "test",
            "note_zh": "讲解",
            "spans": [{"text": "Not only"}, {"text": "did"}],
        },
        reading_variant="intermediate_reading",
    )
    assert len(req.output_fragment["spans"]) == 2


def test_schema_request_validates_chunks_element_must_have_text() -> None:
    """sentence_analysis chunks elements must have a string 'text' field."""
    with pytest.raises(ValueError, match="chunks\\[0\\].*text"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "sentence_analysis", "label": "test", "analysis_zh": "分析", "chunks": [{"order": 1, "label": "主干"}]},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_chunks_element_must_have_order() -> None:
    """sentence_analysis chunks elements must have an integer 'order' field."""
    with pytest.raises(ValueError, match="chunks\\[0\\].*order"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "sentence_analysis", "label": "test", "analysis_zh": "分析", "chunks": [{"text": "The research", "label": "主干"}]},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_chunks_element_must_have_label() -> None:
    """sentence_analysis chunks elements must have a string 'label' field."""
    with pytest.raises(ValueError, match="chunks\\[0\\].*label"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "sentence_analysis", "label": "test", "analysis_zh": "分析", "chunks": [{"text": "The research", "order": 1}]},
            reading_variant="intermediate_reading",
        )


def test_schema_request_validates_chunks_element_order_must_be_int() -> None:
    """sentence_analysis chunks elements 'order' must be an integer."""
    with pytest.raises(ValueError, match="chunks\\[0\\].*order"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "sentence_analysis", "label": "test", "analysis_zh": "分析", "chunks": [{"text": "The research", "order": "1", "label": "主干"}]},
            reading_variant="intermediate_reading",
        )


def test_schema_request_accepts_valid_chunks() -> None:
    """sentence_analysis with valid chunks should pass."""
    req = ExampleLabGenerateRagFieldsRequest(
        sentence_text="test",
        output_fragment={
            "type": "sentence_analysis",
            "label": "test",
            "analysis_zh": "分析",
            "chunks": [{"text": "The research", "order": 1, "label": "主干"}],
        },
        reading_variant="intermediate_reading",
    )
    assert len(req.output_fragment["chunks"]) == 1


def test_schema_request_rejects_unknown_fragment_type() -> None:
    """output_fragment.type must be grammar_note or sentence_analysis; unknown types are rejected."""
    with pytest.raises(ValueError, match="must be 'grammar_note' or 'sentence_analysis'"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "translation", "label": "test"},
            reading_variant="intermediate_reading",
        )


def test_schema_request_rejects_vocab_highlight_type() -> None:
    """vocab_highlight is not a valid type for this endpoint."""
    with pytest.raises(ValueError, match="must be 'grammar_note' or 'sentence_analysis'"):
        ExampleLabGenerateRagFieldsRequest(
            sentence_text="test",
            output_fragment={"type": "vocab_highlight", "label": "test"},
            reading_variant="intermediate_reading",
        )


# ---------------------------------------------------------------------------
# Python / JS canonical-form alignment guards
# ---------------------------------------------------------------------------

def test_normalize_grammar_tags_collapses_repeated_underscores() -> None:
    """Repeated underscores in raw input must collapse to a single '_' before
    the alias merge step — matches the JS hook at
    ``hooks-bundle/src/index.js:normalizeGrammarTag``."""
    # `participle__adverbial` (double underscore) must collapse + alias to
    # `past_participle_adverbial` — same path the JS hook takes.
    result = example_lab.normalize_grammar_tags(["participle__adverbial"])
    assert result == ["past_participle_adverbial"]


def test_normalize_grammar_tags_mixed_separators_collapse() -> None:
    """Whitespace + hyphen + underscore in the same input collapse to a single '_'."""
    result = example_lab.normalize_grammar_tags([" passive - voice "])
    assert result == ["passive_voice"]


def test_normalize_grammar_tags_three_underscores_collapse() -> None:
    """Three or more underscores collapse to a single '_'."""
    result = example_lab.normalize_grammar_tags(["past___participle_adverbial"])
    assert result == ["past_participle_adverbial"]


def test_normalize_grammar_tags_python_matches_js_known_inputs() -> None:
    """Pin a table of inputs whose canonical form must match the JS hook.

    These are the inputs most likely to be emitted by an LLM or curator via
    the two write paths. If either side ever drifts, this test breaks.
    """
    pairs = {
        "participle_adverbial": "past_participle_adverbial",
        "participle__adverbial": "past_participle_adverbial",
        "defining_relative_clause": "restrictive_relative_clause",
        "non_defining_relative_clause": "nonrestrictive_relative_clause",
        "non-defining_relative_clause": "nonrestrictive_relative_clause",
        "fronting": "subject_clause_fronting",
        "Relative Clause": "relative_clause",
        "general": None,  # rejected (forbidden token)
        "complex": None,  # rejected
    }
    for raw, expected in pairs.items():
        result = example_lab.normalize_grammar_tags([raw])
        if expected is None:
            assert result == [], f"expected '{raw}' to be rejected, got {result}"
        else:
            assert result == [expected], (
                f"expected '{raw}' → '{expected}', got {result} — "
                "Python / JS canonical form drift"
            )
