"""BudgetedUsageModel — test-only provider request/usage instrumentation.

Requirement: BudgetedUsageModel 深模块.

Prior to this module, the harness's ``_extract_usage()`` defensive
``getattr`` walk usually returned ``None`` because the agent's draft
object did not expose ``.usage()`` the way raw pydantic-ai ``RunResult``
does. As a result:

- Request counts were recorded as 0 in artifacts.
- Token counts were recorded as 0.
- The "max_requests" / "max_tokens" caps were never actually enforced
  — they existed only on paper.

This module fixes that by wrapping the resolved :class:`Model` instance
with a :class:`BudgetedUsageModel` that:

- Increments ``executed_requests`` BEFORE each ``request()`` /
  ``request_stream()`` call to the wrapped provider model.
- Raises :class:`BudgetExhaustedError` BEFORE the call when the cap is
  hit (so no provider request is made past the cap).
- Aggregates ``input_tokens`` / ``output_tokens`` from each
  :class:`ModelResponse.usage` after the call.
- Exposes ``executed_requests`` / ``executed_tokens`` /
  ``request_cap`` / ``token_cap`` for the harness to write into the
  artifact's ``agent_usage`` field.
- Does NOT log request body, ``reasoning_content``, API key, or any
  other sensitive payload — only counts and aggregate token numbers.

The wrapper extends pydantic-ai's :class:`WrapperModel` so it inherits
all delegation (``model_name``, ``provider``, ``profile``, etc.) and
only overrides the two request entrypoints.

When the wrapped model's response does not carry usage (some test
models omit it), ``executed_requests`` is still incremented — the
spec requires "usage 缺失时请求数仍正确".
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from pydantic_ai.models.wrapper import WrapperModel

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from typing import Any

    from pydantic_ai._run_context import RunContext
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models import ModelRequestParameters, ModelSettings, StreamedResponse
    from pydantic_ai.settings import ModelSettings as _ModelSettings  # noqa: F401


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class BudgetExhaustedError(RuntimeError):
    """Raised by :class:`BudgetedUsageModel` when a cap is hit.

    The harness catches this, records a :class:`BudgetStopResult` on
    the :class:`PhasePlanner`, and stops the run loop. The artifact
    for the in-flight (rejected) request is NOT written — only the
    already-completed requests are recorded.

    The exception message is intentionally generic (no payload, no
    API key, no request body). It carries only:
    - which cap was hit (``request_cap`` or ``token_cap``)
    - the executed counts at the moment of rejection
    """

    def __init__(
        self,
        *,
        cap_kind: str,
        executed_requests: int,
        executed_tokens: int | None,
        request_cap: int | None,
        token_cap: int | None,
    ) -> None:
        self.cap_kind = cap_kind
        self.executed_requests = executed_requests
        self.executed_tokens = executed_tokens
        self.request_cap = request_cap
        self.token_cap = token_cap
        super().__init__(
            f"budget_exhausted: cap_kind={cap_kind} "
            f"executed_requests={executed_requests} "
            f"executed_tokens={executed_tokens or 0} "
            f"request_cap={request_cap} token_cap={token_cap}"
        )


# ---------------------------------------------------------------------------
# BudgetedUsageModel
# ---------------------------------------------------------------------------


class BudgetedUsageModel(WrapperModel):
    """Test-only wrapper that enforces real request/token caps.

    Construction::

        BudgetedUsageModel(wrapped=resolved_model, max_requests=30, max_tokens=10000)

    The harness wraps the resolved provider model with this class
    BEFORE handing it to ``agent.override(model=...)`` (or to the
    agent constructor). Every ``request()`` / ``request_stream()``
    call then goes through the cap check + counter increment.

    The wrapper does NOT change the model's output or the tool loop
    semantics — it only counts and enforces. Multi-turn tool call
    loops naturally call ``request()`` once per turn, so a 5-turn
    tool loop increments ``executed_requests`` by 5.
    """

    def __init__(
        self,
        wrapped: Any,
        *,
        max_requests: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        super().__init__(wrapped=wrapped)
        if max_requests is not None and max_requests < 1:
            raise ValueError(
                f"max_requests must be >= 1 or None, got {max_requests}"
            )
        if max_tokens is not None and max_tokens < 1:
            raise ValueError(
                f"max_tokens must be >= 1 or None, got {max_tokens}"
            )
        self._request_cap = max_requests
        self._token_cap = max_tokens
        self._executed_requests = 0
        self._executed_input_tokens = 0
        self._executed_output_tokens = 0

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def request_cap(self) -> int | None:
        return self._request_cap

    @property
    def token_cap(self) -> int | None:
        return self._token_cap

    @property
    def executed_requests(self) -> int:
        """Number of provider requests actually made (not rejected)."""
        return self._executed_requests

    @property
    def executed_input_tokens(self) -> int:
        return self._executed_input_tokens

    @property
    def executed_output_tokens(self) -> int:
        return self._executed_output_tokens

    @property
    def executed_tokens(self) -> int:
        """Sum of input + output tokens across all executed requests.

        Returns 0 when the wrapped model's responses do not carry usage
        (some test models). The spec requires ``executed_requests`` to
        still be correct in that case.
        """
        return self._executed_input_tokens + self._executed_output_tokens

    # ------------------------------------------------------------------
    # Cap enforcement (called before each request)
    # ------------------------------------------------------------------

    def _check_caps_before_request(self) -> None:
        """Raise :class:`BudgetExhaustedError` if a cap would be exceeded.

        Request cap: checked against ``executed_requests`` (the count of
        already-completed requests). If the next request would exceed
        ``request_cap``, raise BEFORE the provider is called.

        Token cap: checked against ``executed_tokens`` (the aggregate so
        far). If the next request would exceed ``token_cap``, raise.
        Token cap is best-effort — we cannot know how many tokens the
        next request will consume until it completes, so we can only
        block based on the running total.
        """
        if self._request_cap is not None:
            if self._executed_requests + 1 > self._request_cap:
                raise BudgetExhaustedError(
                    cap_kind="request_cap",
                    executed_requests=self._executed_requests,
                    executed_tokens=self.executed_tokens,
                    request_cap=self._request_cap,
                    token_cap=self._token_cap,
                )
        if self._token_cap is not None:
            # Best-effort: block when we are already at or over the cap.
            # The spec accepts this as a soft boundary for tokens.
            if self.executed_tokens >= self._token_cap:
                raise BudgetExhaustedError(
                    cap_kind="token_cap",
                    executed_requests=self._executed_requests,
                    executed_tokens=self.executed_tokens,
                    request_cap=self._request_cap,
                    token_cap=self._token_cap,
                )

    def _record_response_usage(self, response: ModelResponse) -> None:
        """Aggregate usage from a completed ``ModelResponse``.

        Defensive: some test models return ``RequestUsage(input=0, output=0)``
        or omit usage entirely. We still increment ``executed_requests``
        (already done before the call) — token aggregation is best-effort.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        self._executed_input_tokens += int(input_tokens)
        self._executed_output_tokens += int(output_tokens)

    # ------------------------------------------------------------------
    # Override request() and request_stream()
    # ------------------------------------------------------------------

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        # Cap check BEFORE the provider is called. The spec requires:
        # "达到 request cap 时在发出请求前拒绝".
        self._check_caps_before_request()

        # Increment request count BEFORE the call so a multi-turn loop
        # that raises mid-call still counts the attempt. The spec
        # requires "每次真实 provider model request 前递增 request count".
        self._executed_requests += 1

        # Delegate to the wrapped model. We do NOT log messages,
        # reasoning_content, or any payload — only counts.
        response = await self.wrapped.request(
            messages, model_settings, model_request_parameters
        )

        # Aggregate usage (best-effort).
        self._record_response_usage(response)
        return response

    @asynccontextmanager
    async def request_stream(  # type: ignore[override]
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        # Same cap check + increment as request(). The streaming path
        # is instrumented for completeness; the harness primarily uses
        # request() today.
        self._check_caps_before_request()
        self._executed_requests += 1

        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response_stream:
            # Note: usage from a streamed response is accumulated as
            # chunks arrive; the final usage is available on the
            # StreamedResponse after iteration completes. We do NOT
            # iterate here — the agent runtime does. Token aggregation
            # for streaming is best-effort and may under-count if the
            # stream is interrupted. The spec accepts this as a soft
            # boundary for tokens; request count is still correct.
            yield response_stream

        # After the stream completes, attempt to read final usage.
        final_usage = getattr(response_stream, "usage", None)
        if final_usage is not None:
            input_tokens = getattr(final_usage, "input_tokens", 0) or 0
            output_tokens = getattr(final_usage, "output_tokens", 0) or 0
            self._executed_input_tokens += int(input_tokens)
            self._executed_output_tokens += int(output_tokens)
