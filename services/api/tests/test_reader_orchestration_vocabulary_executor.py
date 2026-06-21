from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.settings import Settings
from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length
from app.llm.registry import build_model_registry
from app.llm.routes import MODEL_ROUTE_READER_LAYER_VOCABULARY
from app.services.reader_orchestration import vocabulary_worker as vocabulary_worker_module
from app.services.reader_orchestration.vocabulary_worker import (
    PydanticAIVocabularyExecutor,
    VocabularyAnchorSegmentContext,
    VocabularyExecutionError,
    VocabularyJobContext,
)


class _StubAgentResult:
    def __init__(self, output: object) -> None:
        self.output = output


class _ExecutorUnderTest(PydanticAIVocabularyExecutor):
    def __init__(self, output: object) -> None:
        self._output = output
        super().__init__(
            settings=Settings(reader_vocabulary_model_profile="reader_vocabulary")
        )

    def _build_agent(self, *, model: object):  # type: ignore[override]
        return object()

    async def _run_agent(self, agent: object, prompt: str) -> _StubAgentResult:  # type: ignore[override]
        return _StubAgentResult(self._output)


def _build_context(
    *,
    source_text: str,
    anchor_segment_id: str = "s1",
) -> VocabularyJobContext:
    return VocabularyJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="vocabulary_unit_v1",
        source_language="en",
        source_text=source_text,
        text_hash=compute_text_range_hash(source_text),
        anchor_segments=(
            VocabularyAnchorSegmentContext(
                anchor_segment_id=anchor_segment_id,
                sentence_id=anchor_segment_id,
                segment_type="sentence",
                unit_start_utf16=0,
                unit_end_utf16=utf16_code_unit_length(source_text),
                text_hash=compute_text_range_hash(source_text),
                text=source_text,
            ),
        ),
    )


def _patch_stub_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vocabulary_worker_module,
        "build_model_for_route",
        lambda settings, route: (
            object(),
            SimpleNamespace(
                profile_name="reader-vocab-profile",
                provider="stub-provider",
                model_name="stub-model",
                api_key="",
            ),
        ),
    )
    monkeypatch.setattr(
        vocabulary_worker_module,
        "extract_run_usage",
        lambda result: {
            "aggregate": {
                "input_tokens": 10,
                "output_tokens": 8,
                "total_tokens": 18,
            }
        },
    )


@pytest.mark.anyio
async def test_real_executor_resolves_offsets_hashes_and_typed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(
        source_text="The results prompted the team to rethink their approach.",
    )
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "phrase_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted the team",
                    "phrase": "prompt sb to do sth",
                    "phrase_type": "phrasal_verb",
                    "gloss": "促使某人做某事",
                    "example": None,
                }
            ],
        }
    )

    result = await executor.generate(context)

    assert len(result.output.items) == 1
    item = result.output.items[0]
    assert item.item_type == "phrase_gloss"
    assert item.anchor.anchor_segment_id == "s1"
    assert item.anchor.start_offset == utf16_code_unit_length("The results ")
    assert item.anchor.end_offset == utf16_code_unit_length(
        "The results prompted the team"
    )
    assert item.anchor.text_hash == compute_text_range_hash("prompted the team")
    assert result.model_route == "reader_layer_vocabulary"
    assert result.model_profile == "reader-vocab-profile"
    assert result.model_provider == "stub-provider"
    assert result.model_name == "stub-model"
    assert result.diagnostics == {
        "candidate_item_count": 1,
        "resolved_item_count": 1,
        "skipped_item_count": 0,
        "skipped_items": [],
        "skipped_items_truncated_count": 0,
    }


@pytest.mark.anyio
async def test_real_executor_skips_ambiguous_and_missing_selected_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="We had had enough of the delays.")
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "selected_text": "had",
                    "headword": "had",
                    "brief_explanation": "表示拥有",
                    "reason": "common",
                },
                {
                    "item_type": "context_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "ghost phrase",
                    "display": "ghost phrase",
                    "gloss": "不存在",
                    "reason": "不应被定位",
                },
            ],
        }
    )

    result = await executor.generate(context)

    assert result.output.items == []
    assert result.diagnostics is not None
    assert result.diagnostics["resolved_item_count"] == 0
    assert result.diagnostics["skipped_item_count"] == 2
    assert result.diagnostics["skipped_items_truncated_count"] == 0
    assert {
        item["reason_code"] for item in result.diagnostics["skipped_items"]
    } == {"selected_text_ambiguous", "selected_text_not_found"}


@pytest.mark.anyio
async def test_real_executor_keeps_highest_priority_item_for_same_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(
        source_text="The results prompted the team to rethink their approach.",
    )
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted the team",
                    "headword": "prompted",
                    "brief_explanation": "促使",
                    "reason": "common",
                },
                {
                    "item_type": "phrase_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted the team",
                    "phrase": "prompt the team",
                    "phrase_type": "collocation",
                    "gloss": "促使团队行动",
                    "example": None,
                },
                {
                    "item_type": "context_gloss",
                    "anchor_segment_id": "s1",
                    "selected_text": "prompted the team",
                    "display": "prompt the team",
                    "gloss": "在这里强调触发后续反思",
                    "reason": "依赖当前语境",
                },
            ],
        }
    )

    result = await executor.generate(context)

    assert len(result.output.items) == 1
    assert result.output.items[0].item_type == "context_gloss"
    assert result.diagnostics is not None
    assert result.diagnostics["skipped_item_count"] == 2
    assert {
        item["reason_code"] for item in result.diagnostics["skipped_items"]
    } == {"span_conflict_higher_priority_kept"}


@pytest.mark.anyio
async def test_real_executor_invalid_model_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="The results prompted the team forward.")
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "headword": "prompted",
                }
            ],
        }
    )

    with pytest.raises(VocabularyExecutionError, match="invalid structured output") as exc_info:
        await executor.generate(context)

    assert exc_info.value.failure_class == "validation"
    assert exc_info.value.failure_code == "model_output_invalid"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_real_executor_rejects_too_many_candidate_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="The results prompted the team forward.")
    executor = _ExecutorUnderTest(
        {
            "schema_version": 1,
            "items": [
                {
                    "item_type": "vocab_highlight",
                    "anchor_segment_id": "s1",
                    "selected_text": f"item-{index}",
                    "headword": f"item{index}",
                    "brief_explanation": "解释",
                    "reason": "common",
                }
                for index in range(6)
            ],
        }
    )

    with pytest.raises(VocabularyExecutionError, match="invalid structured output") as exc_info:
        await executor.generate(context)

    assert exc_info.value.failure_class == "validation"
    assert exc_info.value.failure_code == "model_output_invalid"


@pytest.mark.anyio
async def test_real_executor_accepts_empty_valid_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub_route(monkeypatch)
    context = _build_context(source_text="The sentence is too basic to annotate.")
    executor = _ExecutorUnderTest({"schema_version": 1, "items": []})

    result = await executor.generate(context)

    assert result.output.items == []
    assert result.diagnostics == {
        "candidate_item_count": 0,
        "resolved_item_count": 0,
        "skipped_item_count": 0,
        "skipped_items": [],
        "skipped_items_truncated_count": 0,
    }


def test_reader_vocabulary_route_uses_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        reader_vocabulary_model_profile="reader_vocabulary",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_LAYER_VOCABULARY] == (
        "reader_vocabulary"
    )


def test_reader_vocabulary_route_requires_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        reader_vocabulary_model_profile="",
    )

    registry = build_model_registry(settings)

    assert MODEL_ROUTE_READER_LAYER_VOCABULARY not in registry.route_defaults
