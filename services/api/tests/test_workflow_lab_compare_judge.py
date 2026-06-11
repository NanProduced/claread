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
                "providers": {
                    "primary-provider": {
                        "adapter": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "primary-key",
                    }
                },
                "models": {
                    "primary-judge": {
                        "provider": "primary-provider",
                        "model_name": "primary-judge",
                    }
                },
                "profiles": {
                    "primary": {
                        "model": "primary-judge",
                    }
                },
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
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
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
    assert result.input_tokens == 22
    assert result.output_tokens == 14
    assert result.total_tokens == 36
    assert result.latency_seconds is not None
    assert len(result.results) == 2
    first: WorkflowLabCompareJudgeCaseResult = result.results[0]
    assert first.case_id == "case-1"
    assert first.status == "succeeded"
    assert first.verdict == "candidate_preferred"
    assert first.preferred_side == "candidate"
    assert first.overall_score == 0.8
    assert first.summary == "候选更清晰。"
    assert first.reasons == ["结构更明确", "翻译更准确"]
    assert first.usage_summary == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
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
            usage={
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
            },
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


# ---------------------------------------------------------------------------
# Regression: WorkflowLabCompareJudgeSidePayload.warnings must be list[str]
# ---------------------------------------------------------------------------


def test_side_payload_rejects_object_warnings() -> None:
    """The schema is warnings: list[str]. The previous Directus -> API payload
    accidentally sent list[dict] (render_scene warnings with code/message),
    which pydantic rejected with
    "body.packets.0.baseline.warnings.0: Input should be a valid string".

    This test pins that contract: any object entry must be rejected so the
    Directus-side normalization in summarizeCompareJudgeSentenceOutput cannot
    silently regress.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkflowLabCompareJudgeSidePayload(
            sentence_text="x",
            warnings=[{"code": "SCHEMA_DRIFT", "message": "drift"}],
        )


def test_side_payload_accepts_string_warnings() -> None:
    payload = WorkflowLabCompareJudgeSidePayload(
        sentence_text="x",
        warnings=[
            "[SCHEMA_DRIFT] schema version drift sentence_id=s-1",
            "another warning",
        ],
    )
    assert payload.warnings == [
        "[SCHEMA_DRIFT] schema version drift sentence_id=s-1",
        "another warning",
    ]


async def test_run_workflow_lab_compare_judge_accepts_string_warnings_end_to_end() -> None:
    """End-to-end: a request whose baseline/candidate warnings are plain
    strings (the new Directus-side shape) must validate and execute cleanly.
    """
    settings = _settings_with_profile()
    packet = _build_packet("case-1")
    # Mutate the packet so warnings are plain strings, mirroring what the
    # Directus extension now sends after summarizeCompareJudgeSentenceOutput
    # normalizes render_scene.warnings.
    object.__setattr__(
        packet.baseline,
        "warnings",
        ["[SCHEMA_DRIFT] schema version drift sentence_id=sent-case-1"],
    )
    object.__setattr__(
        packet.candidate,
        "warnings",
        ["[TIMEOUT] agent timeout after 30s"],
    )
    request = _build_request([packet])
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "tie",
            "summary": "ok",
            "reasons": [],
            "overall_score": 0.5,
        }
    )

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert result.results[0].status == "succeeded"
    assert result.results[0].verdict == "tie"


async def test_run_workflow_lab_compare_judge_non_structured_exception_yields_partial_failure() -> None:
    """Regression: ``asyncio.gather(return_exceptions=True)`` must map any
    non-``StructuredCompletionError`` exception in one packet into the
    same case-error shape, and the other packets must still get judged.

    The previous code used default ``return_exceptions=False``, which
    cancelled every sibling coroutine and broke the
    ``WORKFLOW_COMPARE_JUDGE_PARTIAL_FAILURE`` contract for mid-flight
    exceptions.
    """
    settings = _settings_with_profile()

    class _SelectiveErrorFake:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def __call__(self, **kwargs: Any) -> Any:
            from app.llm.structured_completion import StructuredCompletionResult

            packet = kwargs.get("user_prompt") or ""
            # Distinguish which packet triggered the call by parsing the
            # user_prompt's case_id. The fake struct keeps this lightweight.
            # The contract: case-1 raises, case-2 / case-3 succeed.
            if '"case_id": "case-1"' in packet or '"case_id":"case-1"' in packet:
                self.calls.append("case-1")
                raise RuntimeError("simulated downstream fault (not a StructuredCompletionError)")

            self.calls.append(packet[:24])
            return StructuredCompletionResult(
                parsed={
                    "verdict": "tie",
                    "summary": "ok",
                    "reasons": [],
                    "overall_score": 0.5,
                },
                raw_text=json.dumps({"verdict": "tie"}),
                model_name="primary-judge",
                profile_name="primary",
                base_url="https://example.invalid/v1",
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            )

    request = _build_request(
        [_build_packet("case-1"), _build_packet("case-2"), _build_packet("case-3")]
    )
    fake = _SelectiveErrorFake()

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    # Every input packet must produce exactly one output entry.
    assert len(result.results) == 3
    assert [case.case_id for case in result.results] == ["case-1", "case-2", "case-3"]
    # case-1 failed with the packet-exception code, case-2 and case-3
    # succeeded — proving gather did not cancel siblings.
    failed = result.results[0]
    assert failed.status == "error"
    assert failed.verdict == "needs_review"
    assert failed.error is not None
    assert failed.error.code == "WORKFLOW_COMPARE_JUDGE_PACKET_EXCEPTION"
    assert "RuntimeError" in failed.error.message
    assert "simulated downstream fault" in failed.error.message

    succeeded = result.results[1:]
    assert all(case.status == "succeeded" for case in succeeded)
    assert all(case.error is None for case in succeeded)


async def test_run_workflow_lab_compare_judge_value_error_yields_partial_failure() -> None:
    """Same partial-failure guarantee for non-``StructuredCompletionError``,
    non-``RuntimeError`` exceptions (e.g. ``ValueError``)."""
    settings = _settings_with_profile()

    class _ValueErrorOnFirstFake:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def __call__(self, **kwargs: Any) -> Any:
            from app.llm.structured_completion import StructuredCompletionResult

            packet = kwargs.get("user_prompt") or ""
            if '"case_id": "case-1"' in packet or '"case_id":"case-1"' in packet:
                self.calls.append("case-1")
                raise ValueError("malformed upstream payload")

            return StructuredCompletionResult(
                parsed={"verdict": "tie", "summary": "ok", "reasons": [], "overall_score": 0.5},
                raw_text="{}",
                model_name="primary-judge",
                profile_name="primary",
                base_url="https://example.invalid/v1",
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            )

    request = _build_request([_build_packet("case-1"), _build_packet("case-2")])
    fake = _ValueErrorOnFirstFake()

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        result = await run_workflow_lab_compare_judge(request, settings=settings)

    assert len(result.results) == 2
    assert result.results[0].status == "error"
    assert result.results[0].error.code == "WORKFLOW_COMPARE_JUDGE_PACKET_EXCEPTION"
    assert "ValueError" in result.results[0].error.message
    assert result.results[1].status == "succeeded"


async def test_run_workflow_lab_compare_judge_compacts_heavy_prompt_payload() -> None:
    settings = _settings_with_profile()
    packet = _build_packet("case-heavy")
    object.__setattr__(
        packet.baseline,
        "translation",
        " ".join(["baseline-translation"] * 80),
    )
    object.__setattr__(
        packet.baseline,
        "inline_marks",
        [{
            "title": "cross one's path",
            "anchor": {
                "kind": "multi_text",
                "parts": [
                    {"anchor_text": "crosses"},
                    {"anchor_text": "path"},
                ],
            },
            "type": "phrase_gloss",
            "lookup_kind": "idiom",
            "extra": "偶然遇到",
        }] + [
            {
                "anchor": f"anchor-{idx}-" + ("x" * 60),
                "type": "grammar_note",
                "extra": "detail-" + ("y" * 60),
            }
            for idx in range(1, 8)
        ],
    )
    object.__setattr__(
        packet.baseline,
        "sentence_entries",
        [
            {
                "type": "sentence_analysis",
                "label": f"label-{idx}",
                "content": " ".join([f"entry-{idx}"] * 80),
                "chunks": [{"label": "chunk", "text": " ".join(["chunk"] * 40)} for _ in range(4)],
            }
            for idx in range(6)
        ],
    )
    object.__setattr__(
        packet.baseline,
        "warnings",
        [f"warning-{idx}-" + ("z" * 220) for idx in range(6)],
    )
    object.__setattr__(
        packet.baseline,
        "drop_log",
        [
            {"code": f"DROP_{idx}", "reason": " ".join(["drop"] * 50), "sentence_id": f"s-{idx}"}
            for idx in range(5)
        ],
    )
    request = _build_request([packet])
    fake = _FakeStructuredCompletion(
        parsed={
            "verdict": "tie",
            "summary": "ok",
            "reasons": [],
            "overall_score": 0.5,
        }
    )

    with patch(
        "app.eval_adapter.workflow_lab_compare_judge.run_structured_completion",
        fake,
    ):
        await run_workflow_lab_compare_judge(request, settings=settings)

    sent_prompt = json.loads(fake.calls[0]["user_prompt"])
    baseline = sent_prompt["packet"]["baseline"]
    assert len(baseline["inline_marks"]) == 6
    assert baseline["inline_marks"][0]["title"] == "cross one's path"
    assert baseline["inline_marks"][0]["anchor"] == "crosses / path"
    assert baseline["inline_marks"][0]["lookup_kind"] == "idiom"
    assert len(baseline["sentence_entries"]) == 4
    assert len(baseline["warnings"]) == 4
    assert len(baseline["drop_log"]) == 3
    assert len(baseline["translation"]) <= 240
    assert all(len(item["summary"]) <= 180 for item in baseline["sentence_entries"])
    assert fake.calls[0]["max_tokens"] == 320
