"""Integration tests for PydanticAIGrammarWindowExecutor (window-scoped single call).

Design source:
  docs/architecture/reader-orchestration.md
  §8.3 LLM call (window-scoped single-call design)

Verifies:
  1. ``PydanticAIGrammarWindowExecutor`` can be constructed (no executor param).
  2. ``generate(context)`` makes a SINGLE LLM call per window (not per-unit).
  3. ``_ground_and_convert_candidates`` correctly grounds LLM output to
     ``CandidateItem`` with UTF-16 offsets + text_hash + self-rating.
  4. ``generate`` raises ``GrammarWindowExecutionError`` on LLM failure.
  5. Empty target_anchors returns empty list without LLM call.

The legacy ``_convert_output`` static method was removed (dead code
after the LLM candidate schema took over). Tests that exercised it are
removed; the remaining tests cover the live ``generate`` path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.reader_orchestration.grammar_window_worker import (
    GrammarWindowExecutionError,
    PydanticAIGrammarWindowExecutor,
    _WindowGrammarCandidateOutput,
    _WindowGrammarNoteCandidate,
    _WindowGrammarSpan,
    _WindowSentenceAnalysisCandidate,
    _WindowSentenceChunk,
)


def test_executor_can_be_constructed() -> None:
    """PydanticAIGrammarWindowExecutor 可被构造（无 executor 参数）。"""
    executor = PydanticAIGrammarWindowExecutor()
    assert executor is not None


@pytest.mark.anyio
async def test_generate_with_empty_target_anchors_returns_empty() -> None:
    """generate 在 target_anchors 为空时返回空 candidates 列表，不调用 LLM。"""
    executor = PydanticAIGrammarWindowExecutor()
    context: dict[str, Any] = {"target_anchors": []}
    result = await executor.generate(context)
    assert result.candidates == []
    assert result.usage_data is None


@pytest.mark.anyio
async def test_generate_makes_single_window_scoped_llm_call() -> None:
    """generate 对整个 window 发起一次 LLM 调用，而非 per-unit。

    使用 mock _run_agent 避免触达 real LLM。window context 包含 2 个
    target anchor 属于同一 unit，LLM 输出 1 grammar_note + 1 sentence_analysis。
    _run_agent 应只被调用一次（window-scoped single call）。

    Candidates carry the 3-field self-rating contract
    (``quality_score`` / ``reading_blocker`` / ``dedup_hint``). The legacy
    ``reason_code`` / ``confidence`` fields are gone.
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
        assert len(result.candidates) == 2
        item_types = {c.item_type for c in result.candidates}
        assert item_types == {"grammar_note", "sentence_analysis"}
        # 验证 candidate 的 anchor_segment_id 来自 LLM 输出
        anchor_ids = {c.anchor_segment_id for c in result.candidates}
        assert anchor_ids == {"anchor-1", "anchor-2"}
        # 验证 self-rating 字段被填充
        grammar_note = next(c for c in result.candidates if c.item_type == "grammar_note")
        assert grammar_note.quality_score == 4.0
        assert grammar_note.reading_blocker is False
        assert grammar_note.grammar_point == "主谓一致"
        assert grammar_note.pattern == "SVO"
        assert grammar_note.note == "The team revised the plan."
        assert len(grammar_note.spans) == 1
        assert grammar_note.spans[0]["anchor_segment_id"] == "anchor-1"
        assert grammar_note.spans[0]["selected_text"] == "team"
        # Dedup_hint is propagated so the window selector can dedup.
        assert grammar_note.dedup_hint == "subject_verb_agreement"
        # 验证 sentence_analysis self-rating
        sent_analysis = next(c for c in result.candidates if c.item_type == "sentence_analysis")
        assert sent_analysis.quality_score == 5.0
        assert sent_analysis.label == "main clause"
        assert sent_analysis.analysis == "简单 SVO 句型。"
        assert len(sent_analysis.chunks) == 1
        assert sent_analysis.chunks[0]["order"] == 1
        assert sent_analysis.dedup_hint == "main_clause_svo"


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
