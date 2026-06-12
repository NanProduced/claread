"""Tests for reader_ask agent_runner: stream execution, replan, and outcome handling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)

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

        mock_agent = MagicMock()
        mock_agent.run_stream = MagicMock(return_value=mock_stream_ctx)

        mock_usage = MagicMock(request_tokens=10, response_tokens=20)
        mock_stream_ctx.usage = MagicMock(return_value=mock_usage)

        mock_deps = MagicMock()
        mock_deps.event_queue = event_queue

        with patch.object(agent_runner_svc, "build_reader_ask_prompt", return_value="prompt"):
            with patch.object(
                agent_runner_svc,
                "build_usage_metadata",
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
                "build_usage_metadata",
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
