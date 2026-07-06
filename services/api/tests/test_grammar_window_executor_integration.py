"""Integration tests for PydanticAIGrammarWindowExecutor (P1-3).

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §8.3 LLM call (executor adapter)

Verifies:
  1. ``PydanticAIGrammarWindowExecutor`` can be constructed.
  2. ``generate(context)`` signature matches ``GrammarWindowExecutorProtocol``.
  3. ``_convert_output`` correctly maps ``GrammarBundleOutput`` items to
     ``CandidateItem`` with proper ``item_type`` / ``anchor_segment_id`` /
     ``semantic_dedup_key`` / ``pattern_key``.
  4. ``generate`` delegates to the legacy ``PydanticAIGrammarBundleExecutor``
     per unit and collects candidates (uses a mock legacy executor, not a
     real LLM call).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.reader_orchestration import (
    GrammarBundleOutput,
    GrammarNoteItem,
    ReaderTextRangeAnchor,
    SentenceAnalysisChunk,
    SentenceAnalysisItem,
)
from app.services.reader_orchestration.grammar_window_worker import (
    PydanticAIGrammarWindowExecutor,
)
from app.services.reader_orchestration.grammar_worker import GrammarExecutionResult


def _make_anchor(
    *,
    anchor_segment_id: str = "anchor-1",
    unit_id: str = "unit-1",
    selected_text: str = "team",
    start_offset: int = 0,
) -> ReaderTextRangeAnchor:
    from app.contracts.annotation import compute_text_range_hash, utf16_code_unit_length

    end_offset = start_offset + utf16_code_unit_length(selected_text)
    return ReaderTextRangeAnchor(
        base_id="base-1",
        unit_id=unit_id,
        anchor_segment_id=anchor_segment_id,
        sentence_id=anchor_segment_id,
        segment_type="sentence",
        start_offset=start_offset,
        end_offset=end_offset,
        selected_text=selected_text,
        text_hash=compute_text_range_hash(selected_text),
    )


def _make_grammar_note(
    *,
    anchor_segment_id: str = "anchor-1",
    grammar_point: str = "subject-verb agreement",
    pattern: str | None = "SVO",
    note: str = "The team revised the plan.",
    selected_text: str = "team",
) -> GrammarNoteItem:
    return GrammarNoteItem(
        spans=[_make_anchor(
            anchor_segment_id=anchor_segment_id,
            selected_text=selected_text,
        )],
        grammar_point=grammar_point,
        pattern=pattern,
        note=note,
    )


def _make_sentence_analysis(
    *,
    anchor_segment_id: str = "anchor-1",
    label: str = "main clause",
    analysis: str = "Simple SVO clause.",
    selected_text: str = "The team revised the plan.",
) -> SentenceAnalysisItem:
    return SentenceAnalysisItem(
        anchor=_make_anchor(
            anchor_segment_id=anchor_segment_id,
            selected_text=selected_text,
        ),
        label=label,
        analysis=analysis,
        chunks=[
            SentenceAnalysisChunk(order=1, label="clause", text=selected_text),
        ],
    )


def test_executor_can_be_constructed() -> None:
    """PydanticAIGrammarWindowExecutor 可被构造。"""
    executor = PydanticAIGrammarWindowExecutor()
    assert executor is not None
    assert executor._executor is not None


def test_convert_output_maps_grammar_notes() -> None:
    """_convert_output 将 GrammarNoteItem 映射为 grammar_note CandidateItem。"""
    note = _make_grammar_note(
        anchor_segment_id="anchor-1",
        grammar_point="subject-verb agreement",
        pattern="SVO",
        note="The team revised the plan.",
    )
    output = GrammarBundleOutput(grammar_notes=[note], sentence_analyses=[])

    candidates = PydanticAIGrammarWindowExecutor._convert_output(output)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.item_type == "grammar_note"
    assert candidate.anchor_segment_id == "anchor-1"
    assert len(candidate.spans) == 1
    assert candidate.spans[0]["anchor_segment_id"] == "anchor-1"
    assert candidate.pattern_key == "SVO"
    assert candidate.quality_score == 0.0
    assert candidate.reading_blocker is False
    # semantic_dedup_key 是 sha1(grammar_point|note) 的前 16 字符
    assert len(candidate.semantic_dedup_key) == 16


def test_convert_output_maps_sentence_analyses() -> None:
    """_convert_output 将 SentenceAnalysisItem 映射为 sentence_analysis CandidateItem。"""
    analysis = _make_sentence_analysis(
        anchor_segment_id="anchor-2",
        label="main clause",
        analysis="Simple SVO clause.",
    )
    output = GrammarBundleOutput(
        grammar_notes=[],
        sentence_analyses=[analysis],
    )

    candidates = PydanticAIGrammarWindowExecutor._convert_output(output)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.item_type == "sentence_analysis"
    assert candidate.anchor_segment_id == "anchor-2"
    assert len(candidate.spans) == 1
    assert candidate.spans[0]["anchor_segment_id"] == "anchor-2"
    assert candidate.pattern_key is None
    assert candidate.quality_score == 0.0
    assert candidate.reading_blocker is False
    assert len(candidate.semantic_dedup_key) == 16


def test_convert_output_dedup_key_is_deterministic() -> None:
    """相同 grammar_point + note 产生相同 semantic_dedup_key。"""
    note1 = _make_grammar_note(grammar_point="gp", note="n1")
    note2 = _make_grammar_note(grammar_point="gp", note="n1")
    output = GrammarBundleOutput(grammar_notes=[note1, note2], sentence_analyses=[])

    candidates = PydanticAIGrammarWindowExecutor._convert_output(output)
    assert len(candidates) == 2
    assert candidates[0].semantic_dedup_key == candidates[1].semantic_dedup_key


def test_convert_output_empty_input() -> None:
    """空 GrammarBundleOutput 产生空 candidate 列表。"""
    output = GrammarBundleOutput()
    candidates = PydanticAIGrammarWindowExecutor._convert_output(output)
    assert candidates == []


@pytest.mark.anyio
async def test_generate_with_empty_target_anchors_returns_empty() -> None:
    """generate 在 target_anchors 为空时返回空列表，不调用 legacy executor。"""
    mock_legacy = AsyncMock()
    executor = PydanticAIGrammarWindowExecutor(executor=mock_legacy)
    context: dict[str, Any] = {"target_anchors": []}
    result = await executor.generate(context)
    assert result == []
    mock_legacy.generate.assert_not_called()


@pytest.mark.anyio
async def test_generate_delegates_to_legacy_executor_per_unit() -> None:
    """generate 对 window 内每个 unit 调用 legacy executor，收集 candidates。

    使用 mock legacy executor 避免触达 real LLM。window context 包含 2 个
    anchor 属于同一 unit，因此 legacy executor 只被调用一次。
    """
    from app.services.reader_orchestration.grammar_worker import (
        GrammarExecutionResult,
        GrammarJobContext,
    )

    # 准备 mock legacy executor 的输出
    note = _make_grammar_note(
        anchor_segment_id="anchor-1",
        grammar_point="subject-verb agreement",
        pattern="SVO",
        note="The team revised the plan.",
    )
    analysis = _make_sentence_analysis(
        anchor_segment_id="anchor-2",
        label="main clause",
        analysis="Simple SVO clause.",
    )
    mock_output = GrammarBundleOutput(
        grammar_notes=[note],
        sentence_analyses=[analysis],
    )
    mock_result = GrammarExecutionResult(output=mock_output)
    mock_legacy = AsyncMock()
    mock_legacy.generate = AsyncMock(return_value=mock_result)

    executor = PydanticAIGrammarWindowExecutor(executor=mock_legacy)

    # 构造 window context（2 个 anchor 属于同一 unit-1）
    context: dict[str, Any] = {
        "job_id": UUID(int=1),
        "base_id": UUID(int=2),
        "reading_record_id": UUID(int=3),
        "target_anchors": [
            {
                "anchor_segment_id": "anchor-1",
                "unit_id": "unit-1",
                "unit_order_index": 0,
                "base_start_utf16": 0,
                "base_end_utf16": 10,
                "unit_base_start_utf16": 0,
                "unit_base_end_utf16": 20,
                "source_text": "team",
            },
            {
                "anchor_segment_id": "anchor-2",
                "unit_id": "unit-1",
                "unit_order_index": 0,
                "base_start_utf16": 11,
                "base_end_utf16": 20,
                "unit_base_start_utf16": 0,
                "unit_base_end_utf16": 20,
                "source_text": "revised",
            },
        ],
    }

    # _build_unit_context 需要 DB，直接 mock 它返回一个最小 GrammarJobContext
    async def _fake_build_unit_context(
        *,
        context: dict[str, Any],
        unit_id: str,
        unit_anchors: list[dict[str, Any]],
    ) -> GrammarJobContext | None:
        from app.services.reader_orchestration.grammar_worker import (
            GrammarAnchorSegmentContext,
        )
        return GrammarJobContext(
            job_id=UUID(int=1),
            run_id=UUID(int=1),
            reading_record_id=UUID(int=3),
            user_id=UUID(int=1),
            base_id=UUID(int=2),
            unit_id=unit_id,
            order_index=0,
            expected_generation=1,
            operation_fingerprint="",
            source_language="en",
            source_text="The team revised the plan.",
            text_hash="deadbeef",
            anchor_segments=(
                GrammarAnchorSegmentContext(
                    anchor_segment_id="anchor-1",
                    sentence_id="anchor-1",
                    segment_type="sentence",
                    unit_start_utf16=0,
                    unit_end_utf16=10,
                    text_hash="deadbeef",
                    text="team",
                ),
            ),
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            strategy_version="v1",
            strategy_hash="hash",
            layer_policy_hash="policy",
            grammar_prompt_lines=("- line",),
        )

    executor._build_unit_context = _fake_build_unit_context  # type: ignore[method-assign]

    result = await executor.generate(context)

    # legacy executor 被调用一次（2 个 anchor 属于同一 unit）
    assert mock_legacy.generate.call_count == 1
    # 返回 2 个 candidate（1 grammar_note + 1 sentence_analysis）
    assert len(result) == 2
    item_types = {c.item_type for c in result}
    assert item_types == {"grammar_note", "sentence_analysis"}
    # 验证 candidate 的 anchor_segment_id 来自 legacy output
    anchor_ids = {c.anchor_segment_id for c in result}
    assert anchor_ids == {"anchor-1", "anchor-2"}


@pytest.mark.anyio
async def test_generate_skips_unit_when_build_context_returns_none() -> None:
    """_build_unit_context 返回 None 时跳过该 unit，继续处理其他 unit。"""
    mock_legacy = AsyncMock()
    mock_legacy.generate = AsyncMock(
        return_value=GrammarExecutionResult(output=GrammarBundleOutput())
    )
    executor = PydanticAIGrammarWindowExecutor(executor=mock_legacy)

    # mock _build_unit_context 始终返回 None
    async def _always_none(**kwargs: Any) -> None:
        return None

    executor._build_unit_context = _always_none  # type: ignore[method-assign]

    context: dict[str, Any] = {
        "job_id": UUID(int=1),
        "base_id": UUID(int=2),
        "reading_record_id": UUID(int=3),
        "target_anchors": [
            {
                "anchor_segment_id": "anchor-1",
                "unit_id": "unit-1",
                "unit_order_index": 0,
                "base_start_utf16": 0,
                "base_end_utf16": 10,
                "unit_base_start_utf16": 0,
                "unit_base_end_utf16": 20,
                "source_text": "team",
            },
        ],
    }

    result = await executor.generate(context)
    assert result == []
    mock_legacy.generate.assert_not_called()
