"""Tests for BudgetedUsageModel — test-only provider request/usage
instrumentation.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement: BudgetedUsageModel 深模块（P0-8）.

These tests require ``pydantic_ai`` (only available in the services/api
venv). When run from the evals project (which does not depend on
pydantic_ai), the entire module is skipped via ``pytest.importorskip``.
The deep module under test (``budgeted_model.py``) lives in
``evals/claread_eval/reader_record_ask/`` per spec — it is only
imported when pydantic_ai is available.

Covers:
- Request count increments on each ``request()`` call.
- Request cap is enforced BEFORE the wrapped model is called (so no
  provider request is made past the cap).
- Token aggregation from ``ModelResponse.usage``.
- Usage-missing case still increments request count correctly.
- Multi-turn tool call loop increments multiple times (one per turn).
- ``BudgetExhaustedError`` carries only safe metadata (no payload).
"""

from __future__ import annotations

import pytest

# Skip entire module when pydantic_ai is not installed (evals project).
pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402
from pydantic_ai.usage import RequestUsage  # noqa: E402

from claread_eval.reader_record_ask.budgeted_model import (  # noqa: E402
    BudgetedUsageModel,
    BudgetExhaustedError,
)

# ---------------------------------------------------------------------------
# Helpers — build ModelResponse with controllable usage
# ---------------------------------------------------------------------------


def _make_response(
    *,
    text: str = "ok",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=text)],
        usage=RequestUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _make_function_model(
    *,
    responses: list[ModelResponse] | None = None,
    side_effect=None,
) -> FunctionModel:
    """Build a FunctionModel that returns canned responses in order.

    Note: ``FunctionModel`` auto-estimates usage when the returned
    response has no usage values (see ``pydantic_ai/models/function.py``
    line 156-157). For tests that require a "no usage" response, use
    :class:`_BareFakeModel` instead — it returns the response exactly
    as provided without auto-estimation.
    """
    if responses is None:
        responses = [_make_response()]

    call_count = {"n": 0}

    def _fn(messages: list[ModelMessage], info) -> ModelResponse:  # noqa: ANN001
        if side_effect is not None:
            side_effect(messages, info)
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        return responses[idx]

    return FunctionModel(_fn, model_name="test-fake")


class _BareFakeModel(Model):
    """Minimal fake Model that returns the configured response as-is.

    Unlike :class:`FunctionModel`, this does NOT auto-estimate usage
    when the response has no usage values — making it suitable for
    testing the "usage missing" case.
    """

    def __init__(self, response: ModelResponse) -> None:
        super().__init__()
        self._response = response
        self.call_count = 0

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.call_count += 1
        return self._response

    @property
    def model_name(self) -> str:
        return "test-bare"

    @property
    def system(self) -> str:
        return "bare"


def _make_request_params() -> ModelRequestParameters:
    return ModelRequestParameters(
        function_tools=[],
        allow_text_output=True,
        output_tools=[],
        output_mode=None,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Construction & validation
# ---------------------------------------------------------------------------


async def test_construction_accepts_none_caps() -> None:
    inner = _make_function_model()
    wrapper = BudgetedUsageModel(inner)
    assert wrapper.request_cap is None
    assert wrapper.token_cap is None
    assert wrapper.executed_requests == 0
    assert wrapper.executed_tokens == 0


async def test_construction_rejects_invalid_max_requests() -> None:
    inner = _make_function_model()
    with pytest.raises(ValueError):
        BudgetedUsageModel(inner, max_requests=0)


async def test_construction_rejects_invalid_max_tokens() -> None:
    inner = _make_function_model()
    with pytest.raises(ValueError):
        BudgetedUsageModel(inner, max_tokens=0)


# ---------------------------------------------------------------------------
# Request count increments
# ---------------------------------------------------------------------------


async def test_request_count_increments() -> None:
    inner = _make_function_model(responses=[_make_response(), _make_response()])
    wrapper = BudgetedUsageModel(inner)
    assert wrapper.executed_requests == 0

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_requests == 1

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_requests == 2


async def test_request_count_increments_even_when_usage_missing() -> None:
    """Spec: "usage 缺失时请求数仍正确".

    A ModelResponse with ``usage=RequestUsage(input=0, output=0)`` is
    the typical "no usage info" case for some test models. The wrapper
    must still increment request count.

    Uses :class:`_BareFakeModel` instead of :class:`FunctionModel`
    because ``FunctionModel`` auto-estimates usage when the response
    has no usage values (see ``pydantic_ai/models/function.py`` line
    156-157), which would defeat the purpose of this test.
    """
    empty_usage_response = ModelResponse(
        parts=[TextPart(content="ok")],
        usage=RequestUsage(input_tokens=0, output_tokens=0),
    )
    inner = _BareFakeModel(empty_usage_response)
    wrapper = BudgetedUsageModel(inner)

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_requests == 1
    # Token totals stay at 0 (best-effort), but request count is correct.
    assert wrapper.executed_tokens == 0
    assert wrapper.executed_input_tokens == 0
    assert wrapper.executed_output_tokens == 0


# ---------------------------------------------------------------------------
# Request cap enforcement (BEFORE provider is called)
# ---------------------------------------------------------------------------


async def test_request_cap_blocks_before_provider_call() -> None:
    """Spec: "达到 request cap 时在发出请求前拒绝".

    The wrapper must raise BEFORE calling the wrapped model, so no
    provider request is made past the cap.
    """
    inner_calls = {"n": 0}

    def _track(messages, info):  # noqa: ANN001
        inner_calls["n"] += 1

    inner = _make_function_model(
        responses=[_make_response(), _make_response(), _make_response()],
        side_effect=_track,
    )
    wrapper = BudgetedUsageModel(inner, max_requests=2)

    await wrapper.request([], None, _make_request_params())
    await wrapper.request([], None, _make_request_params())
    assert inner_calls["n"] == 2

    # Third request must be blocked BEFORE the inner model is called.
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await wrapper.request([], None, _make_request_params())
    assert inner_calls["n"] == 2  # inner was NOT called a third time
    assert exc_info.value.cap_kind == "request_cap"
    assert exc_info.value.executed_requests == 2
    assert exc_info.value.request_cap == 2


async def test_request_cap_one_allows_exactly_one_request() -> None:
    inner = _make_function_model(responses=[_make_response()])
    wrapper = BudgetedUsageModel(inner, max_requests=1)

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_requests == 1

    with pytest.raises(BudgetExhaustedError):
        await wrapper.request([], None, _make_request_params())


# ---------------------------------------------------------------------------
# Token aggregation
# ---------------------------------------------------------------------------


async def test_token_aggregation_from_response_usage() -> None:
    inner = _make_function_model(
        responses=[
            _make_response(input_tokens=100, output_tokens=50),
            _make_response(input_tokens=200, output_tokens=80),
        ]
    )
    wrapper = BudgetedUsageModel(inner)

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_input_tokens == 100
    assert wrapper.executed_output_tokens == 50
    assert wrapper.executed_tokens == 150

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_input_tokens == 300
    assert wrapper.executed_output_tokens == 130
    assert wrapper.executed_tokens == 430


async def test_token_cap_blocks_after_threshold_reached() -> None:
    """Token cap is best-effort: blocks when aggregate is already at or
    over the cap, BEFORE the next request is made.
    """
    inner = _make_function_model(
        responses=[
            _make_response(input_tokens=100, output_tokens=50),  # total 150
            _make_response(input_tokens=100, output_tokens=50),  # would be 300
        ]
    )
    wrapper = BudgetedUsageModel(inner, max_tokens=200)

    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_tokens == 150

    # Second request: aggregate (150) < cap (200), so allowed.
    await wrapper.request([], None, _make_request_params())
    assert wrapper.executed_tokens == 300

    # Third request: aggregate (300) >= cap (200), blocked.
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await wrapper.request([], None, _make_request_params())
    assert exc_info.value.cap_kind == "token_cap"
    assert exc_info.value.token_cap == 200


# ---------------------------------------------------------------------------
# BudgetExhaustedError carries only safe metadata
# ---------------------------------------------------------------------------


async def test_budget_exhausted_error_does_not_leak_payload() -> None:
    """The exception message must NOT contain request body, API key, or
    any sensitive payload — only counts and cap values.
    """
    sensitive_messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="api_key=sk-secret-abc123")])
    ]
    inner = _make_function_model(responses=[_make_response()])
    wrapper = BudgetedUsageModel(inner, max_requests=1)

    await wrapper.request(sensitive_messages, None, _make_request_params())

    with pytest.raises(BudgetExhaustedError) as exc_info:
        await wrapper.request(sensitive_messages, None, _make_request_params())

    err_msg = str(exc_info.value)
    assert "sk-secret-abc123" not in err_msg
    assert "api_key" not in err_msg
    # Safe metadata IS present.
    assert "request_cap" in err_msg
    assert "executed_requests" in err_msg


# ---------------------------------------------------------------------------
# Wrapper does not change model output
# ---------------------------------------------------------------------------


async def test_wrapper_does_not_change_model_output() -> None:
    """Spec: "wrapper 不能改变模型输出或 tool loop 语义".

    The wrapper must return exactly what the wrapped model returned.
    """
    expected_response = _make_response(text="hello world")
    inner = _make_function_model(responses=[expected_response])
    wrapper = BudgetedUsageModel(inner)

    actual = await wrapper.request([], None, _make_request_params())
    assert actual is expected_response


# ---------------------------------------------------------------------------
# Multi-turn tool call simulation (each request() = one turn)
# ---------------------------------------------------------------------------


async def test_multi_turn_tool_loop_counts_each_request() -> None:
    """Spec: "多轮 tool call 计入多次请求".

    A multi-turn tool call loop calls ``request()`` once per turn. The
    wrapper must increment ``executed_requests`` by the number of turns.
    """
    # Simulate 4 turns: tool_call -> tool_return -> tool_call -> final text
    responses = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="search", args={"q": "test"})],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
        ),
        ModelResponse(
            parts=[ToolCallPart(tool_name="read_range", args={"start": 0})],
            usage=RequestUsage(input_tokens=20, output_tokens=8),
        ),
        ModelResponse(
            parts=[TextPart(content="final answer")],
            usage=RequestUsage(input_tokens=30, output_tokens=12),
        ),
    ]
    inner = _make_function_model(responses=responses)
    wrapper = BudgetedUsageModel(inner)

    for _ in range(3):
        await wrapper.request([], None, _make_request_params())

    assert wrapper.executed_requests == 3
    assert wrapper.executed_input_tokens == 60  # 10 + 20 + 30
    assert wrapper.executed_output_tokens == 25  # 5 + 8 + 12
