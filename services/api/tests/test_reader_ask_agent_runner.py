"""Tests for reader_ask agent_runner: stream execution, replan, and outcome handling."""

from __future__ import annotations

import asyncio
import warnings
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai._warnings import PydanticAIDeprecationWarning
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.usage import RunUsage

from app.services.reader_ask import agent_runner as agent_runner_svc
from app.services.reader_ask import stream_events as stream_events_svc

# ---------------------------------------------------------------------------
# is_degenerate_answer
# ---------------------------------------------------------------------------

class TestIsDegenerateAnswer:
    def test_empty_string(self) -> None:
        assert agent_runner_svc.is_degenerate_answer("") is True

    def test_whitespace_only(self) -> None:
        assert agent_runner_svc.is_degenerate_answer("   \n  ") is True

    def test_short_valid_english(self) -> None:
        assert agent_runner_svc.is_degenerate_answer("Yes.") is False
        assert agent_runner_svc.is_degenerate_answer("OK.") is False

    def test_short_valid_cjk(self) -> None:
        assert agent_runner_svc.is_degenerate_answer("是的") is False
        assert agent_runner_svc.is_degenerate_answer("现在完成时") is False

    def test_refusal_english(self) -> None:
        assert agent_runner_svc.is_degenerate_answer("I cannot answer this question.") is True
        assert agent_runner_svc.is_degenerate_answer("As an AI, I'm unable to help.") is True

    def test_refusal_cjk(self) -> None:
        assert agent_runner_svc.is_degenerate_answer("我无法回答这个问题。") is True
        assert agent_runner_svc.is_degenerate_answer("没有足够的信息来回答。") is True

    def test_normal_answer(self) -> None:
        assert (
            agent_runner_svc.is_degenerate_answer(
                "This sentence uses the present perfect tense."
            )
            is False
        )


# ---------------------------------------------------------------------------
# build_replan_event
# ---------------------------------------------------------------------------

class TestBuildReplanEvent:
    def _make_planning_snapshot(
        self,
        *,
        clarification_mode: str = "none",
        clarification_only: bool = False,
    ) -> MagicMock:
        snap = MagicMock()
        snap.clarification_mode = clarification_mode
        snap.clarification_only = clarification_only
        return snap

    def test_degenerate_answer_triggers_replan(self) -> None:
        snap = self._make_planning_snapshot()
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=snap,
            assistant_message_id="msg-1",
        )
        assert result is not None
        event_name, event_payload = result
        assert event_name == stream_events_svc.EVENT_REPLAN_STARTED
        assert event_payload["message_id"] == "msg-1"
        assert event_payload["reason"] == "degenerate_answer"

    def test_valid_answer_no_replan(self) -> None:
        snap = self._make_planning_snapshot()
        result = agent_runner_svc.build_replan_event(
            final_content_md="This is a valid answer with enough content.",
            planning_snapshot=snap,
            assistant_message_id="msg-1",
        )
        assert result is None

    def test_no_planning_snapshot_no_replan(self) -> None:
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=None,
            assistant_message_id="msg-1",
        )
        assert result is None

    def test_clarification_mode_blocks_replan(self) -> None:
        snap = self._make_planning_snapshot(clarification_mode="required")
        result = agent_runner_svc.build_replan_event(
            final_content_md="",
            planning_snapshot=snap,
            assistant_message_id="msg-1",
        )
        assert result is None


# ---------------------------------------------------------------------------
# finish_reader_ask_agent_stream
# ---------------------------------------------------------------------------

class TestFinishReaderAskAgentStream:
    def test_normal_completion(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.content_parts = ["Hello ", "world"]
        runtime.usage_summary = {"total_tokens": 100}

        outcome, interrupted_event = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )
        assert outcome.content_md == "Hello world"
        assert outcome.usage_summary == {"total_tokens": 100}
        assert outcome.interrupted is False
        assert interrupted_event is None

    def test_interrupted_with_partial_content(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.content_parts = ["Partial "]
        runtime.producer_error = RuntimeError("timeout")

        outcome, interrupted_event = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )
        assert outcome.interrupted is True
        assert outcome.content_md == "Partial"
        assert interrupted_event is not None
        event_name, event_payload = interrupted_event
        assert event_name == stream_events_svc.EVENT_MESSAGE_INTERRUPTED
        assert event_payload["message_id"] == "msg-1"
        assert event_payload["content_md"] == "Partial"
        assert event_payload["can_retry"] is True

    def test_interrupted_no_content_raises(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.producer_error = RuntimeError("fatal")

        with pytest.raises(RuntimeError, match="fatal"):
            agent_runner_svc.finish_reader_ask_agent_stream(
                runtime=runtime,
                assistant_message_id="msg-1",
            )


# ---------------------------------------------------------------------------
# stream_reader_ask_events
# ---------------------------------------------------------------------------

class TestStreamReaderAskEvents:
    @pytest.mark.asyncio
    async def test_yields_queued_events(self) -> None:
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        producer_done = asyncio.Event()

        await event_queue.put(("message.delta", {"message_id": "m1", "delta": "hi"}))
        producer_done.set()

        events = []
        async for event_name, event_payload in agent_runner_svc.stream_reader_ask_events(
            event_queue=event_queue,
            producer_done=producer_done,
        ):
            events.append((event_name, event_payload))

        assert len(events) == 1
        assert events[0][0] == "message.delta"
        assert events[0][1]["delta"] == "hi"


# ---------------------------------------------------------------------------
# start_reader_ask_agent_stream — reasoning + message delta event enqueuing
# ---------------------------------------------------------------------------

class TestStartReaderAskAgentStream:
    @pytest.mark.asyncio
    async def test_reasoning_and_message_delta_events_enqueued(self) -> None:
        """Verify snapshot fallback still emits reasoning/message events."""
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        # Build a mock agent that yields responses with thinking and text
        mock_response_1 = MagicMock()
        mock_response_1.thinking = "Let me think"
        mock_response_1.text = "Hello"

        mock_response_2 = MagicMock()
        mock_response_2.thinking = "Let me think more"
        mock_response_2.text = "Hello world"

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.stream_responses = MagicMock(
            return_value=self._async_iter([(mock_response_1, False), (mock_response_2, True)])
        )
        mock_stream_ctx._marked_completed = AsyncMock()
        mock_stream_ctx.get_output = AsyncMock(return_value="Hello world")

        mock_agent = MagicMock()
        mock_agent.run_stream = MagicMock(return_value=mock_stream_ctx)

        mock_usage = MagicMock(request_tokens=10, response_tokens=20)
        mock_stream_ctx.usage = MagicMock(return_value=mock_usage)

        mock_deps = MagicMock()
        mock_deps.event_queue = event_queue

        with patch.object(agent_runner_svc, "build_reader_ask_prompt", return_value="prompt"):
            with patch.object(
                agent_runner_svc,
                "extract_run_usage",
                return_value={"total_tokens": 30},
            ):
                task, runtime = agent_runner_svc.start_reader_ask_agent_stream(
                    agent=mock_agent,
                    deps=mock_deps,
                    model=MagicMock(),
                    route_settings=MagicMock(
                        extra_body={"enable_thinking": True},
                        extra_headers=None,
                        max_tokens=1000,
                        temperature=0.7,
                        top_p=1.0,
                        timeout=60,
                        parallel_tool_calls=True,
                        seed=None,
                        presence_penalty=None,
                        frequency_penalty=None,
                        stop_sequences=None,
                    ),
                    assistant_message_id="msg-1",
                    model_config=None,
                )

                # Wait for the stream to complete
                await task

        # Collect all events
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        event_names = [e[0] for e in events]
        assert stream_events_svc.EVENT_REASONING_STARTED in event_names
        assert stream_events_svc.EVENT_REASONING_DELTA in event_names
        assert stream_events_svc.EVENT_REASONING_COMPLETED in event_names
        assert stream_events_svc.EVENT_MESSAGE_DELTA in event_names

        # Verify reasoning delta content
        reasoning_deltas = [
            e[1]["delta"] for e in events if e[0] == stream_events_svc.EVENT_REASONING_DELTA
        ]
        assert "Let me think" in reasoning_deltas[0]

        # Verify message delta content
        message_deltas = [
            e[1]["delta"] for e in events if e[0] == stream_events_svc.EVENT_MESSAGE_DELTA
        ]
        assert "Hello" in message_deltas[0]

    @pytest.mark.asyncio
    async def test_raw_stream_events_complete_reasoning_before_answer_delta(self) -> None:
        """Raw part events should close reasoning before answer text starts."""
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        raw_events = [
            PartStartEvent(index=0, part=ThinkingPart(content="先看题干")),
            PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="，再判断语境")),
            PartEndEvent(
                index=0,
                part=ThinkingPart(content="先看题干，再判断语境"),
                next_part_kind="text",
            ),
            PartStartEvent(index=1, part=TextPart(content="这是")),
            PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="答案")),
        ]

        fake_result = SimpleNamespace(
            _stream_response=self._async_iter(raw_events),
            response=MagicMock(),
            usage=MagicMock(return_value=MagicMock(request_tokens=10, response_tokens=20)),
            _marked_completed=AsyncMock(),
        )

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=fake_result)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_agent = MagicMock()
        mock_agent.run_stream = MagicMock(return_value=mock_stream_ctx)

        mock_deps = MagicMock()
        mock_deps.event_queue = event_queue

        with patch.object(agent_runner_svc, "build_reader_ask_prompt", return_value="prompt"):
            with patch.object(
                agent_runner_svc,
                "extract_run_usage",
                return_value={"total_tokens": 30},
            ):
                task, runtime = agent_runner_svc.start_reader_ask_agent_stream(
                    agent=mock_agent,
                    deps=mock_deps,
                    model=MagicMock(),
                    route_settings=MagicMock(
                        extra_body={"enable_thinking": True},
                        extra_headers=None,
                        max_tokens=1000,
                        temperature=0.7,
                        top_p=1.0,
                        timeout=60,
                        parallel_tool_calls=True,
                        seed=None,
                        presence_penalty=None,
                        frequency_penalty=None,
                        stop_sequences=None,
                    ),
                    assistant_message_id="msg-1",
                    model_config=None,
                )

                await task

        fake_result._marked_completed.assert_awaited_once_with(fake_result.response)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        event_names = [event_name for event_name, _payload in events]
        first_reasoning_completed = event_names.index(stream_events_svc.EVENT_REASONING_COMPLETED)
        first_message_delta = event_names.index(stream_events_svc.EVENT_MESSAGE_DELTA)
        assert first_reasoning_completed < first_message_delta
        assert runtime.emitted_reasoning == "先看题干，再判断语境"
        assert runtime.emitted_text == "这是答案"
        assert runtime.reasoning_active is False

    @staticmethod
    def _async_iter(items: list[Any]):
        """Create an async iterator from a list."""
        async def _gen():
            for item in items:
                yield item
        return _gen()


# ---------------------------------------------------------------------------
# Authoritative final-content backfill
# ---------------------------------------------------------------------------


class TestAuthoritativeFinalContent:
    """``finish_reader_ask_agent_stream`` must use
    ``runtime.authoritative_output`` (captured from ``await
    result.get_output()``) as the authoritative source for
    ``outcome.content_md`` whenever the agent run produced a final output.
    The streamed ``runtime.content_parts`` is kept on
    ``runtime.emitted_text`` for the checkpoint writer and eval, but
    should NOT be the final payload when a mismatch is possible (e.g. lost
    first delta).
    """

    @staticmethod
    def _runtime_with_authoritative(text: str) -> agent_runner_svc.AgentStreamRuntime:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.authoritative_output = text
        runtime.producer_result = MagicMock()  # still set for backward compat
        return runtime

    def test_authoritative_used_when_streamed_is_empty(self) -> None:
        runtime = self._runtime_with_authoritative("hello world")
        runtime.content_parts = []

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == "hello world"
        assert outcome.interrupted is False
        assert runtime.emitted_text == ""

    def test_streamed_used_when_authoritative_is_none(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.content_parts = ["complete"]
        runtime.producer_result = None

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == "complete"
        assert outcome.interrupted is False

    def test_streamed_used_when_both_match(self) -> None:
        runtime = self._runtime_with_authoritative("hello world")
        runtime.content_parts = ["hello world"]

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == "hello world"

    def test_both_empty_returns_empty(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime()
        runtime.content_parts = []
        runtime.producer_result = None

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == ""

    def test_authoritative_used_when_first_delta_lost(self) -> None:
        # Simulate the first delta ("hel") being lost — content_parts has
        # "lo " and "world" but the model's true final output is "hello world".
        # ``emitted_text`` is set to what was actually streamed (the delta
        # path is the only thing that mutates it in the real stream).
        runtime = self._runtime_with_authoritative("hello world")
        runtime.content_parts = ["lo ", "world"]
        runtime.emitted_text = "lo world"

        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.content_md == "hello world"
        # emitted_text MUST preserve the streamed text so checkpoint writer
        # and eval can detect the loss and compare against the authoritative.
        assert runtime.emitted_text == "lo world"

    def test_interrupted_with_authoritative(self) -> None:
        runtime = self._runtime_with_authoritative("abcdef")
        runtime.content_parts = ["abc"]
        runtime.emitted_text = "abc"
        runtime.producer_error = RuntimeError("network blip")

        outcome, interrupted_event = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        assert outcome.interrupted is True
        assert outcome.content_md == "abcdef"
        assert interrupted_event is not None
        event_name, event_payload = interrupted_event
        assert event_name == stream_events_svc.EVENT_MESSAGE_INTERRUPTED
        assert event_payload["content_md"] == "abcdef"

    def test_emitted_text_preserves_streamed_text_when_authoritative_differs(self) -> None:
        runtime = self._runtime_with_authoritative("hello world")
        runtime.content_parts = ["lo ", "world"]
        runtime.emitted_text = "lo world"

        agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=runtime,
            assistant_message_id="msg-1",
        )

        # The streamed text must be preserved on emitted_text so the
        # checkpoint writer still records what was actually streamed to
        # the client (useful for eval/debug of lost-delta cases).
        assert runtime.emitted_text == "lo world"


# ---------------------------------------------------------------------------
# stream_responses branch completion
# ---------------------------------------------------------------------------


class TestStreamResponsesBranchCompletion:
    """Verify that the ``stream_responses`` (snapshot-based) branch also
    calls ``_mark_stream_result_completed`` and ``_replay_missed_reasoning``
    so that ``runtime.authoritative_output`` is populated for authoritative
    backfill.
    """

    @pytest.mark.asyncio
    async def test_stream_responses_branch_captures_authoritative_output(self) -> None:
        """When the stream_responses branch completes,
        ``runtime.authoritative_output`` must be set from
        ``await result.get_output()`` for ``_resolve_authoritative_final_text``."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.agents.reader_ask_agent import ReaderAskAgentDeps
        from app.llm.types import RunModelSettings

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        deps = MagicMock(spec=ReaderAskAgentDeps)
        deps.event_queue = event_queue

        # Create a mock response for the stream_responses path
        mock_response = MagicMock()
        mock_response.text = "Hello from snapshot"
        mock_response.thinking = None

        # Create a mock result that simulates the stream_responses path.
        # pydantic-ai 1.73+ StreamedRunResult exposes get_output(), not
        # an output property.  We set up get_output() to return the
        # authoritative text.
        mock_result = MagicMock(
            spec=["stream_responses", "usage", "response", "get_output", "_marked_completed"]
        )
        mock_result.get_output = AsyncMock(return_value="Hello from snapshot")
        mock_result._marked_completed = AsyncMock()
        # Ensure _stream_response is None to force the stream_responses branch
        mock_result.__dict__["_stream_response"] = None

        # Make stream_responses return an async iterator
        async def _stream_responses(**kwargs):
            yield (mock_response, True)

        mock_result.stream_responses = _stream_responses
        mock_usage = MagicMock(request_tokens=10, response_tokens=20)
        mock_result.usage = MagicMock(return_value=mock_usage)
        # Set up response with no ThinkingParts so _replay_missed_reasoning is safe
        mock_result.response = MagicMock()
        mock_result.response.parts = []

        # Mock the context manager
        class MockStreamContext:
            async def __aenter__(self):
                return mock_result
            async def __aexit__(self, *args):
                pass

        mock_agent = MagicMock()
        mock_agent.run_stream = MagicMock(return_value=MockStreamContext())

        with patch.object(agent_runner_svc, "build_reader_ask_prompt", return_value="prompt"):
            with patch.object(
                agent_runner_svc,
                "extract_run_usage",
                return_value={"total_tokens": 30},
            ):
                task, stream_runtime = agent_runner_svc.start_reader_ask_agent_stream(
                    agent=mock_agent,
                    deps=deps,
                    model=MagicMock(),
                    route_settings=RunModelSettings(max_tokens=100, temperature=0.5),
                    assistant_message_id="msg-test",
                )

                await task  # Wait for producer to finish

        assert stream_runtime.producer_result is not None
        # The authoritative output must be captured from get_output()
        assert stream_runtime.authoritative_output == "Hello from snapshot"

        # Verify authoritative backfill works
        outcome, _ = agent_runner_svc.finish_reader_ask_agent_stream(
            runtime=stream_runtime,
            assistant_message_id="msg-test",
        )
        assert outcome.content_md == "Hello from snapshot"

    @pytest.mark.asyncio
    async def test_stream_responses_branch_reads_property_usage_without_deprecation_warning(
        self,
    ) -> None:
        from app.agents.reader_ask_agent import ReaderAskAgentDeps
        from app.llm.types import RunModelSettings

        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        deps = MagicMock(spec=ReaderAskAgentDeps)
        deps.event_queue = event_queue

        mock_response = MagicMock()
        mock_response.text = "Hello from snapshot"
        mock_response.thinking = None

        mock_result = MagicMock(
            spec=["stream_responses", "usage", "response", "get_output", "_marked_completed"]
        )
        mock_result.get_output = AsyncMock(return_value="Hello from snapshot")
        mock_result._marked_completed = AsyncMock()
        mock_result.__dict__["_stream_response"] = None

        async def _stream_responses(**kwargs):
            yield (mock_response, True)

        mock_result.stream_responses = _stream_responses
        mock_result.usage = RunUsage(input_tokens=10, output_tokens=20)
        mock_result.response = MagicMock()
        mock_result.response.parts = []

        class MockStreamContext:
            async def __aenter__(self):
                return mock_result

            async def __aexit__(self, *args):
                pass

        mock_agent = MagicMock()
        mock_agent.run_stream = MagicMock(return_value=MockStreamContext())

        with patch.object(agent_runner_svc, "build_reader_ask_prompt", return_value="prompt"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                task, stream_runtime = agent_runner_svc.start_reader_ask_agent_stream(
                    agent=mock_agent,
                    deps=deps,
                    model=MagicMock(),
                    route_settings=RunModelSettings(max_tokens=100, temperature=0.5),
                    assistant_message_id="msg-test",
                )

                await task

        assert stream_runtime.usage_summary == {
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
        }
        assert not any(
            issubclass(warning.category, PydanticAIDeprecationWarning)
            and "usage" in str(warning.message)
            for warning in caught
        )


# ---------------------------------------------------------------------------
# _mark_stream_result_completed exception propagation
# ---------------------------------------------------------------------------


class TestMarkStreamResultCompletedExceptionPropagation:
    """Verify that ``_mark_stream_result_completed`` propagates real
    completion exceptions instead of swallowing them, so the outer
    ``runtime.producer_error`` path can record the failure.
    """

    @pytest.mark.asyncio
    async def test_mark_completed_exception_propagates(self) -> None:
        """When ``_marked_completed()`` raises a real error (e.g. ModelHTTPError),
        it must propagate to the caller rather than being silently swallowed."""
        exc = RuntimeError("model completion failed")

        result = MagicMock(spec=["_marked_completed", "response"])
        result._marked_completed = AsyncMock(side_effect=exc)
        result.response = MagicMock()
        runtime = agent_runner_svc.AgentStreamRuntime()

        with pytest.raises(RuntimeError, match="model completion failed"):
            await agent_runner_svc._mark_stream_result_completed(result, runtime)

    @pytest.mark.asyncio
    async def test_get_output_exception_propagates(self) -> None:
        """When ``get_output()`` raises a real error, it must propagate."""
        exc = RuntimeError("output retrieval failed")

        result = MagicMock(spec=["get_output", "response"])
        result.get_output = AsyncMock(side_effect=exc)
        runtime = agent_runner_svc.AgentStreamRuntime()

        with pytest.raises(RuntimeError, match="output retrieval failed"):
            await agent_runner_svc._mark_stream_result_completed(result, runtime)

    @pytest.mark.asyncio
    async def test_attribute_error_in_mark_completed_is_suppressed(self) -> None:
        """``AttributeError`` from ``_marked_completed()`` (e.g. signature
        mismatch) should be silently suppressed — it's not a real failure."""
        result = MagicMock()
        result._marked_completed = AsyncMock(side_effect=AttributeError("bad signature"))
        result.response = MagicMock()
        result.get_output = AsyncMock(return_value="output after attr error")
        runtime = agent_runner_svc.AgentStreamRuntime()

        # Should not raise
        await agent_runner_svc._mark_stream_result_completed(result, runtime)
        # get_output should still be called to capture authoritative output
        assert runtime.authoritative_output == "output after attr error"

    @pytest.mark.asyncio
    async def test_no_completion_methods_succeeds_silently(self) -> None:
        """When the result has no ``_marked_completed`` or ``get_output``,
        the function should succeed silently."""
        result = SimpleNamespace()
        runtime = agent_runner_svc.AgentStreamRuntime()

        await agent_runner_svc._mark_stream_result_completed(result, runtime)

    @pytest.mark.asyncio
    async def test_get_output_captures_authoritative_output(self) -> None:
        """When ``get_output()`` succeeds, the output must be saved to
        ``runtime.authoritative_output``."""
        result = MagicMock(spec=["get_output", "response"])
        result.get_output = AsyncMock(return_value="final answer text")
        runtime = agent_runner_svc.AgentStreamRuntime()

        await agent_runner_svc._mark_stream_result_completed(result, runtime)

        assert runtime.authoritative_output == "final answer text"

    @pytest.mark.asyncio
    async def test_get_output_strips_whitespace(self) -> None:
        """``runtime.authoritative_output`` should strip whitespace from
        the captured output."""
        result = MagicMock(spec=["get_output", "response"])
        result.get_output = AsyncMock(return_value="  hello world  ")
        runtime = agent_runner_svc.AgentStreamRuntime()

        await agent_runner_svc._mark_stream_result_completed(result, runtime)

        assert runtime.authoritative_output == "hello world"

    @pytest.mark.asyncio
    async def test_get_output_none_leaves_authoritative_none(self) -> None:
        """When ``get_output()`` returns None, ``runtime.authoritative_output``
        must remain None."""
        result = MagicMock(spec=["get_output", "response"])
        result.get_output = AsyncMock(return_value=None)
        runtime = agent_runner_svc.AgentStreamRuntime()

        await agent_runner_svc._mark_stream_result_completed(result, runtime)

        assert runtime.authoritative_output is None

    @pytest.mark.asyncio
    async def test_completion_exception_sets_producer_error(self) -> None:
        """When ``_mark_stream_result_completed`` raises during a real stream,
        the outer ``except`` clause must set ``runtime.producer_error``."""
        from app.agents.reader_ask_agent import ReaderAskAgentDeps
        from app.llm.types import RunModelSettings

        exc = RuntimeError("completion failed")
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        mock_result = MagicMock(spec=["_marked_completed", "response", "stream_responses", "usage"])
        mock_result.__dict__["_stream_response"] = None
        mock_result._marked_completed = AsyncMock(side_effect=exc)
        mock_result.response = MagicMock()

        async def _stream_responses(**kwargs):
            yield (MagicMock(text="partial", thinking=None), True)

        mock_result.stream_responses = _stream_responses
        mock_result.usage = MagicMock(return_value=MagicMock(request_tokens=10, response_tokens=20))

        class MockStreamContext:
            async def __aenter__(self):
                return mock_result
            async def __aexit__(self, *args):
                pass

        mock_agent = MagicMock()
        mock_agent.run_stream = MagicMock(return_value=MockStreamContext())

        deps = MagicMock(spec=ReaderAskAgentDeps)
        deps.event_queue = event_queue

        with patch.object(agent_runner_svc, "build_reader_ask_prompt", return_value="prompt"):
            with patch.object(
                agent_runner_svc,
                "extract_run_usage",
                return_value={"total_tokens": 30},
            ):
                task, runtime = agent_runner_svc.start_reader_ask_agent_stream(
                    agent=mock_agent,
                    deps=deps,
                    model=MagicMock(),
                    route_settings=RunModelSettings(max_tokens=100, temperature=0.5),
                    assistant_message_id="msg-err",
                )

                await task

        assert runtime.producer_error is not None
        assert "completion failed" in str(runtime.producer_error)
