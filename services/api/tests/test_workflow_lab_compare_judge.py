"""Tests for the API-side Workflow Lab compare LLM judge."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config.settings import Settings
from app.eval_adapter.schemas import (
    WorkflowLabCompareJudgeCaseResult,
    WorkflowLabCompareJudgePacket,
    WorkflowLabCompareJudgeRequest,
    WorkflowLabCompareJudgeSidePayload,
)
from app.eval_adapter.workflow_lab_compare_judge import (
    run_workflow_lab_compare_judge,
)


def _settings_with_profile() -> Settings:
    return Settings(
        default_model_profile="primary",
        model_profiles_json=json.dumps(
            {
                "primary": {
                    "provider": "openai_compatible",
                    "model_name": "primary-judge",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "primary-key",
                }
            }
        ),
    )


def _build_packet(case_id: str = "case-1") -> WorkflowLabCompareJudgePacket:
    return WorkflowLabCompareJudgePacket(
        compare_id="cmp-1",
        case_id=case_id,
        sentence_id=f"sent-{case_id}",
        sentence_text="The quick brown fox.",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        baseline=WorkflowLabCompareJudgeSidePayload(
            sentence_id=f"sent-{case_id}",
            sentence_text="The quick brown fox.",
            translation="敏捷的棕色狐狸。",
            inline_marks=[],
            sentence_entries=[],
        ),
        candidate=WorkflowLabCompareJudgeSidePayload(
            sentence_id=f"sent-{case_id}",
            sentence_text="The quick brown fox.",
            translation="敏捷的棕色狐狸。",
            inline_marks=[],
            sentence_entries=[],
        ),
    )


def _build_request(
    packets: list[WorkflowLabCompareJudgePacket],
    *,
    judge_model_profile: str = "primary",
) -> WorkflowLabCompareJudgeRequest:
    return WorkflowLabCompareJudgeRequest(
        judge_run_id="judge-run-1",
        compare_id="cmp-1",
        rubric_id="rubric-1",
        rubric_version="v1",
        judge_model_profile=judge_model_profile,
        packets=packets,
    )


class _FakeStructuredCompletion:
    def __init__(self, *, parsed: dict[str, Any], error: Exception | None = None) -> None:
        self.parsed = parsed
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        from app.llm.structured_completion import StructuredCompletionResult

        return StructuredCompletionResult(
            parsed=self.parsed,
            raw_text=json.dumps(self.parsed),
            model_name="primary-judge",
            profile_name="primary",
            base_url="https://example.invalid/v1",
        )


def test_request_requires_at_least_minimum_fields() -> None:
    with pytest.raises(Exception):
        WorkflowLabCompareJudgeRequest(
            judge_run_id="",
            compare_id="",
            rubric_id="",
            judge_model_profile="",
            packets=[],
        )


async def test_run_workflow_lab_compare_judge_returns_per_case_results() -> None:
    settings = _settings_with_profile()
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "candidate_preferred",
            "summary": "候选更清晰。",
            "reasons": ["结构更明确", "翻译更准确"],
            "overall_score": 0.8,
        }
    )
    request = _build_request([_build_packet("case-1"), _build_packet("case-2")])

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert result.judge_run_id == "judge-run-1"
    assert result.compare_id == "cmp-1"
    assert result.rubric_id == "rubric-1"
    assert result.judge_model_profile == "primary"
    assert result.model_name == "primary-judge"
    assert result.profile_name == "primary"
    assert len(result.results) == 2
    first: WorkflowLabCompareJudgeCaseResult = result.results[0]
    assert first.case_id == "case-1"
    assert first.status == "succeeded"
    assert first.verdict == "candidate_preferred"
    assert first.preferred_side == "candidate"
    assert first.overall_score == 0.8
    assert first.summary == "候选更清晰。"
    assert first.reasons == ["结构更明确", "翻译更准确"]
    assert first.error is None
    assert len(fake.calls) == 2


async def test_run_workflow_lab_compare_judge_clamps_score() -> None:
    settings = _settings_with_profile()
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "baseline_preferred",
            "summary": "基线更好。",
            "reasons": ["reason"],
            "overall_score": 5.0,
        }
    )
    request = _build_request([_build_packet()])

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    case = result.results[0]
    assert case.overall_score == 1.0
    assert case.preferred_side == "baseline"
    assert case.verdict == "baseline_preferred"


async def test_run_workflow_lab_compare_judge_handles_invalid_score() -> None:
    settings = _settings_with_profile()
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "tie",
            "summary": "平局。",
            "reasons": [],
            "overall_score": "not-a-number",
        }
    )
    request = _build_request([_build_packet()])

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    case = result.results[0]
    assert case.overall_score == 0.5
    assert case.preferred_side is None
    assert case.verdict == "tie"


async def test_run_workflow_lab_compare_judge_falls_back_to_needs_review_on_unknown_verdict() -> None:
    settings = _settings_with_profile()
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "some_unknown_value",
            "summary": "无法判断。",
            "reasons": [],
            "overall_score": 0.5,
        }
    )
    request = _build_request([_build_packet()])

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    case = result.results[0]
    assert case.verdict == "needs_review"
    assert case.preferred_side is None
    assert case.overall_score == 0.5


async def test_run_workflow_lab_compare_judge_reports_unconfigured_profile() -> None:
    settings = _settings_with_profile()
    request = _build_request([_build_packet()], judge_model_profile="does-not-exist")

    result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert len(result.results) == 1
    case = result.results[0]
    assert case.status == "error"
    assert case.verdict == "needs_review"
    assert case.overall_score is None
    assert case.error is not None
    assert case.error.code == "WORKFLOW_COMPARE_JUDGE_LLM_NOT_CONFIGURED"


async def test_run_workflow_lab_compare_judge_surfaces_llm_errors() -> None:
    settings = _settings_with_profile()
    from app.llm.structured_completion import StructuredCompletionError

    fake = _FakeStructuredCompletion(
        parsed={},
        error=StructuredCompletionError("LLM HTTP 502: bad gateway"),
    )
    request = _build_request([_build_packet()])

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    case = result.results[0]
    assert case.status == "error"
    assert case.verdict == "needs_review"
    assert case.preferred_side is None
    assert case.overall_score is None
    assert case.error is not None
    assert case.error.code == "WORKFLOW_COMPARE_JUDGE_LLM_ERROR"
    assert "LLM HTTP 502" in case.error.message


async def test_run_workflow_lab_compare_judge_preserves_packet_order() -> None:
    settings = _settings_with_profile()
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "candidate_preferred",
            "summary": "ok",
            "reasons": [],
            "overall_score": 0.7,
        }
    )
    packets = [_build_packet(f"case-{i}") for i in range(5)]
    request = _build_request(packets)

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert [case.case_id for case in result.results] == [f"case-{i}" for i in range(5)]


class _SlowFakeStructuredCompletion:
    """Fake that sleeps before returning, used to exercise timeout paths."""

    def __init__(self, *, delay: float, parsed: dict[str, Any] | None = None) -> None:
        self.delay = delay
        self.parsed = parsed or {
            "verdict": "candidate_preferred",
            "summary": "ok",
            "reasons": [],
            "overall_score": 0.7,
        }
        self.active = 0
        self.peak = 0
        self.start_lock = asyncio.Lock()

    async def __call__(self, **kwargs: Any) -> Any:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        from app.llm.structured_completion import StructuredCompletionResult

        return StructuredCompletionResult(
            parsed=self.parsed,
            raw_text=json.dumps(self.parsed),
            model_name="primary-judge",
            profile_name="primary",
            base_url="https://example.invalid/v1",
        )


async def test_run_workflow_lab_compare_judge_short_circuits_when_total_timeout_exhausted() -> None:
    settings = _settings_with_profile()
    fake = _SlowFakeStructuredCompletion(delay=0.5)
    packets = [_build_packet(f"case-{i}") for i in range(6)]
    request = _build_request(packets)
    # Each case costs 0.5s; total budget of 0.6s only fits the first case.
    request.total_timeout_seconds = 0.6
    request.concurrency = 1

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert len(result.results) == 6
    # The first case should have been judged; the rest are short-circuited.
    first = result.results[0]
    assert first.status == "succeeded"
    assert first.verdict == "candidate_preferred"
    short_circuited = [case for case in result.results[1:] if case.status == "error"]
    assert short_circuited, "expected at least one short-circuited case"
    for case in short_circuited:
        assert case.error is not None
        assert case.error.code == "WORKFLOW_COMPARE_JUDGE_TOTAL_TIMEOUT"
    # Order preserved.
    assert [case.case_id for case in result.results] == [f"case-{i}" for i in range(6)]


async def test_run_workflow_lab_compare_judge_runs_with_bounded_concurrency() -> None:
    settings = _settings_with_profile()
    fake = _SlowFakeStructuredCompletion(delay=0.2)
    packets = [_build_packet(f"case-{i}") for i in range(4)]
    request = _build_request(packets)
    request.concurrency = 3
    request.total_timeout_seconds = 5.0

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert len(result.results) == 4
    assert all(case.status == "succeeded" for case in result.results)
    # Concurrency 3 → peak should be 3, not 4. Serial default would be 1.
    assert fake.peak <= 3
    assert fake.peak >= 2  # sanity: actually ran in parallel


async def test_run_workflow_lab_compare_judge_clamps_concurrency_to_max() -> None:
    settings = _settings_with_profile()
    request = _build_request([_build_packet()])
    request.concurrency = 999  # beyond MAX_CONCURRENCY=8

    fake = _FakeStructuredCompletion(
        parsed={"verdict": "tie", "summary": "ok", "reasons": [], "overall_score": 0.5}
    )

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        # Should not raise even with bogus concurrency.
        result = await run_workflow_lab_compare_judge(request, settings=settings)
    assert result.results[0].status == "succeeded"
