"""Integration tests for PydanticAIGrammarWindowExecutor (window-scoped single call).

Design source:
  docs/initiatives/reader-agentic-orchestration/analysis-window-zplus-design.md
  §8.3 LLM call (window-scoped single-call design)

Verifies:
  1. ``PydanticAIGrammarWindowExecutor`` can be constructed (no executor param).
  2. ``generate(context)`` makes a SINGLE LLM call per window (not per-unit).
  3. ``_ground_and_convert_candidates`` correctly grounds LLM output to
     ``CandidateItem`` with UTF-16 offsets + text_hash + self-rating.
  4. ``generate`` raises ``GrammarWindowExecutionError`` on LLM failure.
  5. ``_convert_output`` backward-compat static method still works.
  6. Empty target_anchors returns empty list without LLM call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
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
    GrammarWindowExecutionError,
    PydanticAIGrammarWindowExecutor,
    _WindowGrammarCandidateOutput,
    _WindowGrammarNoteCandidate,
    _WindowGrammarSpan,
    _WindowSentenceAnalysisCandidate,
    _WindowSentenceChunk,
)


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
    """PydanticAIGrammarWindowExecutor 可被构造（无 executor 参数）。"""
    executor = PydanticAIGrammarWindowExecutor()
    assert executor is not None


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
    """generate 在 target_anchors 为空时返回空列表，不调用 LLM。"""
    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {"target_anchors": []}
    result = await executor.generate(context)
    assert result == []


@pytest.mark.anyio
async def test_generate_makes_single_window_scoped_llm_call() -> None:
    """generate 对整个 window 发起一次 LLM 调用，而非 per-unit。

    使用 mock _run_agent 避免触达 real LLM。window context 包含 2 个
    target anchor 属于同一 unit，LLM 输出 1 grammar_note + 1 sentence_analysis。
    _run_agent 应只被调用一次（window-scoped single call）。
    """
    # 准备 fake LLM output
    fake_output = _WindowGrammarCandidateOutput(
        grammar_notes=[
            _WindowGrammarNoteCandidate(
                anchor_segment_id="anchor-1",
                spans=[
                    _WindowGrammarSpan(
                        anchor_segment_id="anchor-1",
                        selected_text="team",
                    )
                ],
                grammar_point="主谓一致",
                pattern="SVO",
                note="The team revised the plan.",
                quality_score=4,
                reading_blocker=False,
                reason_code="grammar_pattern",
                confidence=0.9,
                dedup_hint="subject_verb_agreement",
            ),
        ],
        sentence_analyses=[
            _WindowSentenceAnalysisCandidate(
                anchor_segment_id="anchor-2",
                selected_text="The team revised the plan.",
                label="main clause",
                analysis="简单 SVO 句型。",
                chunks=[
                    _WindowSentenceChunk(
                        order=1,
                        label="clause",
                        text="The team revised the plan.",
                    ),
                ],
                quality_score=5,
                reading_blocker=False,
                reason_code="long_sentence",
                confidence=0.95,
                dedup_hint="main_clause_svo",
            ),
        ],
    )

    # Fake result from agent.run
    fake_result = MagicMock()
    fake_result.output = fake_output

    executor = PydanticAIGrammarWindowExecutor()

    # Mock LLM-related calls to avoid real model configuration
    with patch(
        "app.services.reader_orchestration.grammar_window_worker.get_settings"
    ) as mock_settings, patch(
        "app.services.reader_orchestration.grammar_window_worker.build_model_for_route"
    ) as mock_build_model, patch(
        "app.services.reader_orchestration.grammar_window_worker.assert_real_llm_allowed"
    ), patch(
        "app.services.reader_orchestration.grammar_window_worker.extract_run_usage",
        return_value={"aggregate": {"input_tokens": 100, "output_tokens": 50}},
    ), patch.object(
        executor, "_build_window_agent", return_value=MagicMock()
    ), patch.object(
        executor, "_run_agent", new_callable=AsyncMock, return_value=fake_result
    ) as mock_run_agent:
        mock_settings.return_value.reader_grammar_bundle_model_profile = "test-profile"
        mock_build_model.return_value = (MagicMock(), MagicMock())

        context: dict[str, Any] = {
            "job_id": UUID(int=1),
            "base_id": UUID(int=2),
            "reading_record_id": UUID(int=3),
            "window_budget": {
                "max_grammar_notes": 4,
                "max_sentence_analyses": 3,
            },
            "target_anchors": [
                {
                    "anchor_segment_id": "anchor-1",
                    "unit_id": "unit-1",
                    "unit_order_index": 0,
                    "base_start_utf16": 0,
                    "base_end_utf16": 4,
                    "unit_base_start_utf16": 0,
                    "unit_base_end_utf16": 30,
                    "source_text": "team",
                },
                {
                    "anchor_segment_id": "anchor-2",
                    "unit_id": "unit-1",
                    "unit_order_index": 0,
                    "base_start_utf16": 0,
                    "base_end_utf16": 26,
                    "unit_base_start_utf16": 0,
                    "unit_base_end_utf16": 30,
                    "source_text": "The team revised the plan.",
                },
            ],
        }

        result = await executor.generate(context)

        # SINGLE LLM call (window-scoped, not per-unit)
        assert mock_run_agent.call_count == 1
        # 返回 2 个 candidate（1 grammar_note + 1 sentence_analysis）
        assert len(result) == 2
        item_types = {c.item_type for c in result}
        assert item_types == {"grammar_note", "sentence_analysis"}
        # 验证 candidate 的 anchor_segment_id 来自 LLM 输出
        anchor_ids = {c.anchor_segment_id for c in result}
        assert anchor_ids == {"anchor-1", "anchor-2"}
        # 验证 self-rating 字段被填充
        grammar_note = next(c for c in result if c.item_type == "grammar_note")
        assert grammar_note.quality_score == 4.0
        assert grammar_note.reading_blocker is False
        assert grammar_note.grammar_point == "主谓一致"
        assert grammar_note.pattern == "SVO"
        assert grammar_note.note == "The team revised the plan."
        assert len(grammar_note.spans) == 1
        assert grammar_note.spans[0]["anchor_segment_id"] == "anchor-1"
        assert grammar_note.spans[0]["selected_text"] == "team"
        # 验证 sentence_analysis self-rating
        sent_analysis = next(c for c in result if c.item_type == "sentence_analysis")
        assert sent_analysis.quality_score == 5.0
        assert sent_analysis.label == "main clause"
        assert sent_analysis.analysis == "简单 SVO 句型。"
        assert len(sent_analysis.chunks) == 1
        assert sent_analysis.chunks[0]["order"] == 1


@pytest.mark.anyio
async def test_generate_raises_on_llm_failure() -> None:
    """LLM 失败时 generate raises GrammarWindowExecutionError（不吞掉）。"""
    executor = PydanticAIGrammarWindowExecutor()

    with patch(
        "app.services.reader_orchestration.grammar_window_worker.get_settings"
    ) as mock_settings, patch(
        "app.services.reader_orchestration.grammar_window_worker.build_model_for_route"
    ) as mock_build_model, patch(
        "app.services.reader_orchestration.grammar_window_worker.assert_real_llm_allowed"
    ), patch.object(
        executor, "_build_window_agent", return_value=MagicMock()
    ), patch.object(
        executor, "_run_agent", new_callable=AsyncMock,
        side_effect=RuntimeError("LLM timeout"),
    ):
        mock_settings.return_value.reader_grammar_bundle_model_profile = "test-profile"
        mock_build_model.return_value = (MagicMock(), MagicMock())

        context: dict[str, Any] = {
            "target_anchors": [
                {
                    "anchor_segment_id": "anchor-1",
                    "unit_id": "unit-1",
                    "unit_order_index": 0,
                    "base_start_utf16": 0,
                    "base_end_utf16": 4,
                    "unit_base_start_utf16": 0,
                    "unit_base_end_utf16": 30,
                    "source_text": "team",
                },
            ],
        }

        with pytest.raises(
            GrammarWindowExecutionError,
            match="window grammar agent execution failed",
        ):
            await executor.generate(context)
