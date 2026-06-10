"""Tests for agent invocation helpers — resolve, replan, and stream lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.reader_ask_agent import ReaderAskAgentDeps, ReaderAskRuntimeState
from app.llm.types import RunModelSettings
from app.schemas.reader_ask import ReaderAskAnchorRef, ReaderAskCitation
from app.services.reader_ask.agent_invocation import (
    ReaderAskStreamCompleted,
    ReaderAskStreamSseEvent,
    build_reader_ask_planner_model_route,
    build_reader_ask_replan_model_route,
    build_reader_ask_replan_event,
    resolve_reader_ask_agent,
    run_reader_ask_replan,
    stream_reader_ask_agent_run,
)


def _anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="s1",
        selected_text="test sentence",
    )


def _citation() -> ReaderAskCitation:
    return ReaderAskCitation(
        citation_id="c1",
        kind="vocabulary",
        label="test",
    )


def _vocab_cite(item: dict) -> ReaderAskCitation:  # noqa: ARG001
    return _citation()


def _dict_cite(item: dict) -> ReaderAskCitation:  # noqa: ARG001
    return _citation()


def _dict_ai_cite(item: dict, q: str, eid: int) -> ReaderAskCitation:  # noqa: ARG001
    return _citation()


def _make_deps(**kwargs) -> ReaderAskAgentDeps:
    """Build a minimal ReaderAskAgentDeps for testing."""
    from app.services.reader_ask.agent_deps_factory import build_reader_ask_agent_deps

    return build_reader_ask_agent_deps(
        payload={"test": True},
        event_queue=asyncio.Queue(),
        state=ReaderAskRuntimeState(),
        query_seed="seed",
        task_mode="general",
        entry_action="ask_about_this",
        record_id="r1",
        record_title="Test Record",
        primary_anchor=None,
        get_record_context_fn=AsyncMock(),
        get_record_insights_fn=AsyncMock(),
        search_user_vocabulary_fn=AsyncMock(),
        lookup_dictionary_entry_fn=AsyncMock(),
        run_dictionary_ai_context_explain_fn=AsyncMock(),
        generate_sentence_annotation_fn=AsyncMock(),
        vocabulary_item_to_citation_fn=_vocab_cite,
        dictionary_item_to_citation_fn=_dict_cite,
        dictionary_ai_to_citation_fn=_dict_ai_cite,
        **kwargs,
    )


class TestResolveReaderAskAgent:
    """resolve_reader_ask_agent returns agent + model + model_config."""

    @patch("app.services.reader_ask.agent_invocation.build_model_for_route")
    @patch("app.services.reader_ask.agent_invocation.get_reader_ask_agent")
    def test_returns_resolved_triple(
        self,
        mock_get_agent: MagicMock,
        mock_build_model: MagicMock,
    ) -> None:
        fake_agent = MagicMock()
        fake_model = MagicMock()
        fake_config = MagicMock()
        mock_get_agent.return_value = fake_agent
        mock_build_model.return_value = (fake_model, fake_config)

        resolved = resolve_reader_ask_agent()

        assert resolved.agent is fake_agent
        assert resolved.model is fake_model
        assert resolved.model_config is fake_config

    @patch("app.services.reader_ask.agent_invocation.build_model_for_route")
    @patch("app.services.reader_ask.agent_invocation.get_reader_ask_agent")
    def test_raises_when_model_is_none(
        self,
        mock_get_agent: MagicMock,
        mock_build_model: MagicMock,
    ) -> None:
        mock_get_agent.return_value = MagicMock()
        mock_build_model.return_value = (None, None)

        with pytest.raises(RuntimeError, match="model route is not configured"):
            resolve_reader_ask_agent()


class TestRunReaderAskReplan:
    """run_reader_ask_replan delegates to agent.run with correct params."""

    @patch("app.services.reader_ask.agent_invocation.build_reader_ask_replan_model_route")
    @patch("app.services.reader_ask.agent_invocation.resolve_reader_ask_agent")
    async def test_passes_deps_model_and_settings(
        self,
        mock_resolve: AsyncMock,
        mock_replan_route: MagicMock,
    ) -> None:
        fake_agent = AsyncMock()
        fake_result = MagicMock()
        fake_result.output = "replanned answer"
        fake_agent.run.return_value = fake_result
        fake_model = MagicMock()

        mock_resolved = MagicMock()
        mock_resolved.agent = fake_agent
        mock_resolved.model = fake_model
        mock_resolve.return_value = mock_resolved
        mock_replan_route.return_value = (fake_model, MagicMock())

        deps = _make_deps()
        route_settings = RunModelSettings(
            max_tokens=1000,
            temperature=0.5,
            timeout=30,
        )

        result = await run_reader_ask_replan(
            replan_deps=deps,
            replan_max_output=800,
            route_settings=route_settings,
        )

        assert result == "replanned answer"
        fake_agent.run.assert_awaited_once()
        call_kwargs = fake_agent.run.call_args
        # Verify key arguments are passed through
        assert call_kwargs.kwargs["deps"] is deps
        assert call_kwargs.kwargs["model"] is fake_model
        assert call_kwargs.kwargs["model_settings"] is not None

    @patch("app.services.reader_ask.agent_invocation.resolve_reader_ask_agent")
    @patch("app.services.reader_ask.agent_invocation.build_reader_ask_replan_model_route")
    async def test_returns_empty_string_when_output_is_none(
        self,
        mock_replan_route: MagicMock,
        mock_resolve: AsyncMock,
    ) -> None:
        fake_agent = AsyncMock()
        fake_result = MagicMock()
        fake_result.output = None
        fake_agent.run.return_value = fake_result

        mock_resolved = MagicMock()
        mock_resolved.agent = fake_agent
        mock_resolved.model = MagicMock()
        mock_resolve.return_value = mock_resolved
        mock_replan_route.return_value = (MagicMock(), MagicMock())

        deps = _make_deps()
        route_settings = RunModelSettings(
            max_tokens=1000,
            temperature=0.5,
            timeout=30,
        )

        result = await run_reader_ask_replan(
            replan_deps=deps,
            replan_max_output=800,
            route_settings=route_settings,
        )

        assert result == ""

    @patch("app.services.reader_ask.agent_invocation.resolve_reader_ask_agent")
    @patch("app.services.reader_ask.agent_invocation.build_reader_ask_replan_model_route")
    async def test_caps_max_tokens_to_replan_max_output(
        self,
        mock_replan_route: MagicMock,
        mock_resolve: AsyncMock,
    ) -> None:
        fake_agent = AsyncMock()
        fake_result = MagicMock()
        fake_result.output = "answer"
        fake_agent.run.return_value = fake_result

        mock_resolved = MagicMock()
        mock_resolved.agent = fake_agent
        mock_resolved.model = MagicMock()
        mock_resolve.return_value = mock_resolved
        mock_replan_route.return_value = (MagicMock(), MagicMock())

        deps = _make_deps()
        # route_settings.max_tokens is larger than replan_max_output
        route_settings = RunModelSettings(
            max_tokens=2000,
            temperature=0.5,
            timeout=30,
        )

        await run_reader_ask_replan(
            replan_deps=deps,
            replan_max_output=800,
            route_settings=route_settings,
        )

        call_kwargs = fake_agent.run.call_args
        model_settings = call_kwargs.kwargs["model_settings"]
        # max_tokens should be capped at replan_max_output (800)
        assert model_settings["max_tokens"] == 800

    @patch("app.services.reader_ask.agent_invocation.resolve_reader_ask_agent")
    @patch("app.services.reader_ask.agent_invocation.build_reader_ask_replan_model_route")
    async def test_preserves_thinking_settings_when_capping_replan_tokens(
        self,
        mock_replan_route: MagicMock,
        mock_resolve: AsyncMock,
    ) -> None:
        fake_agent = AsyncMock()
        fake_result = MagicMock()
        fake_result.output = "answer"
        fake_agent.run.return_value = fake_result

        mock_resolved = MagicMock()
        mock_resolved.agent = fake_agent
        mock_resolved.model = MagicMock()
        mock_resolve.return_value = mock_resolved
        mock_replan_route.return_value = (MagicMock(), MagicMock())

        deps = _make_deps()
        route_settings = RunModelSettings(
            max_tokens=2000,
            temperature=0.5,
            timeout=30,
            extra_headers={"X-Test": "1"},
            extra_body={"enable_thinking": True},
        )

        await run_reader_ask_replan(
            replan_deps=deps,
            replan_max_output=800,
            route_settings=route_settings,
        )

        model_settings = fake_agent.run.call_args.kwargs["model_settings"]
        assert model_settings["max_tokens"] == 800
        assert model_settings["extra_headers"] == {"X-Test": "1"}
        assert model_settings["extra_body"] == {"enable_thinking": True}


class TestStreamReaderAskAgentRun:
    """stream_reader_ask_agent_run orchestrates the full stream lifecycle."""

    def _make_producer_task(self) -> asyncio.Task[None]:
        """Create a completed dummy producer task."""
        async def _noop() -> None:
            pass

        return asyncio.create_task(_noop())

    @patch("app.services.reader_ask.agent_invocation.agent_runner_svc")
    @patch("app.services.reader_ask.agent_invocation.stream_events_svc")
    async def test_encodes_stream_events_as_sse(
        self,
        mock_stream_events: MagicMock,
        mock_runner: MagicMock,
    ) -> None:
        """Lower-level events are encoded as SSE event steps."""
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        await event_queue.put(("message_delta", {"text": "hello"}))
        producer_done = asyncio.Event()
        producer_done.set()

        mock_runtime = MagicMock()
        mock_runtime.producer_done = producer_done

        mock_runner.start_reader_ask_agent_stream.return_value = (
            self._make_producer_task(),
            mock_runtime,
        )

        async def _fake_stream_events(**kwargs):
            while not event_queue.empty():
                yield await event_queue.get()

        mock_runner.stream_reader_ask_events.side_effect = _fake_stream_events

        mock_runner.finish_reader_ask_agent_stream.return_value = (
            MagicMock(content_md="hello", usage_summary=None, interrupted=False),
            None,
        )
        mock_stream_events.encode_sse.side_effect = lambda e, d: f"event: {e}\ndata: {d}\n\n"

        deps = _make_deps()
        items = []
        async for item in stream_reader_ask_agent_run(
            agent=MagicMock(),
            deps=deps,
            model=MagicMock(),
            route_settings=RunModelSettings(max_tokens=1000, temperature=0.5, timeout=30),
            assistant_message_id="msg1",
            base_url="",
        ):
            items.append(item)

        sse_events = [i for i in items if isinstance(i, ReaderAskStreamSseEvent)]
        assert len(sse_events) == 1
        assert "message_delta" in sse_events[0].encoded_sse

    @patch("app.services.reader_ask.agent_invocation.agent_runner_svc")
    @patch("app.services.reader_ask.agent_invocation.stream_events_svc")
    async def test_awaits_producer_task_in_finally(
        self,
        mock_stream_events: MagicMock,
        mock_runner: MagicMock,
    ) -> None:
        """producer_task is awaited in the finally block."""
        producer_done = asyncio.Event()
        producer_done.set()
        mock_runtime = MagicMock()
        mock_runtime.producer_done = producer_done

        producer_task = self._make_producer_task()
        mock_runner.start_reader_ask_agent_stream.return_value = (
            producer_task,
            mock_runtime,
        )

        async def _no_events(**kwargs):
            return
            yield  # noqa: PIE790

        mock_runner.stream_reader_ask_events.side_effect = _no_events
        mock_runner.finish_reader_ask_agent_stream.return_value = (
            MagicMock(content_md="", usage_summary=None, interrupted=False),
            None,
        )
        mock_stream_events.encode_sse.side_effect = lambda e, d: f"event: {e}\n\n"

        deps = _make_deps()
        async for _ in stream_reader_ask_agent_run(
            agent=MagicMock(),
            deps=deps,
            model=MagicMock(),
            route_settings=RunModelSettings(max_tokens=1000, temperature=0.5, timeout=30),
            assistant_message_id="msg1",
            base_url="",
        ):
            pass

        # producer_task was awaited (completed without error)
        assert producer_task.done()

    @patch("app.services.reader_ask.agent_invocation.agent_runner_svc")
    @patch("app.services.reader_ask.agent_invocation.stream_events_svc")
    async def test_interrupted_event_yields_sse_then_completed(
        self,
        mock_stream_events: MagicMock,
        mock_runner: MagicMock,
    ) -> None:
        """When finish returns an interrupted_event, SSE is yielded first, then completed."""
        producer_done = asyncio.Event()
        producer_done.set()
        mock_runtime = MagicMock()
        mock_runtime.producer_done = producer_done

        mock_runner.start_reader_ask_agent_stream.return_value = (
            self._make_producer_task(),
            mock_runtime,
        )

        async def _no_events(**kwargs):
            return
            yield  # noqa: PIE790

        mock_runner.stream_reader_ask_events.side_effect = _no_events

        mock_outcome = MagicMock(
            content_md="partial",
            usage_summary={"total_tokens": 10},
            interrupted=True,
        )
        interrupted_event = ("message_interrupted", {"detail": "timeout"})
        mock_runner.finish_reader_ask_agent_stream.return_value = (
            mock_outcome,
            interrupted_event,
        )
        mock_stream_events.encode_sse.side_effect = lambda e, d: f"event: {e}\n\n"

        deps = _make_deps()
        items = []
        async for item in stream_reader_ask_agent_run(
            agent=MagicMock(),
            deps=deps,
            model=MagicMock(),
            route_settings=RunModelSettings(max_tokens=1000, temperature=0.5, timeout=30),
            assistant_message_id="msg1",
            base_url="",
        ):
            items.append(item)

        # Should have: 1 interrupted SSE + 1 completed
        sse_events = [i for i in items if isinstance(i, ReaderAskStreamSseEvent)]
        completed = [i for i in items if isinstance(i, ReaderAskStreamCompleted)]
        assert len(sse_events) == 1
        assert "message_interrupted" in sse_events[0].encoded_sse
        assert len(completed) == 1
        # SSE event comes before completed
        assert items.index(sse_events[0]) < items.index(completed[0])

    @patch("app.services.reader_ask.agent_invocation.agent_runner_svc")
    @patch("app.services.reader_ask.agent_invocation.stream_events_svc")
    async def test_normal_completion_yields_completed_with_outcome(
        self,
        mock_stream_events: MagicMock,
        mock_runner: MagicMock,
    ) -> None:
        """Normal completion yields ReaderAskStreamCompleted with transparent outcome."""
        producer_done = asyncio.Event()
        producer_done.set()
        mock_runtime = MagicMock()
        mock_runtime.producer_done = producer_done

        mock_runner.start_reader_ask_agent_stream.return_value = (
            self._make_producer_task(),
            mock_runtime,
        )

        async def _no_events(**kwargs):
            return
            yield  # noqa: PIE790

        mock_runner.stream_reader_ask_events.side_effect = _no_events

        mock_outcome = MagicMock(
            content_md="Hello world",
            usage_summary={"total_tokens": 42},
            interrupted=False,
        )
        mock_runner.finish_reader_ask_agent_stream.return_value = (
            mock_outcome,
            None,
        )
        mock_stream_events.encode_sse.side_effect = lambda e, d: f"event: {e}\n\n"

        deps = _make_deps()
        items = []
        async for item in stream_reader_ask_agent_run(
            agent=MagicMock(),
            deps=deps,
            model=MagicMock(),
            route_settings=RunModelSettings(max_tokens=1000, temperature=0.5, timeout=30),
            assistant_message_id="msg1",
            base_url="",
        ):
            items.append(item)

        completed = [i for i in items if isinstance(i, ReaderAskStreamCompleted)]
        assert len(completed) == 1
        assert completed[0].outcome.content_md == "Hello world"
        assert completed[0].outcome.usage_summary == {"total_tokens": 42}
        assert completed[0].stream_runtime is mock_runtime


class TestBuildReaderAskReplanEvent:
    """build_reader_ask_replan_event delegates to agent_runner.build_replan_event."""

    @patch("app.services.reader_ask.agent_invocation.agent_runner_svc")
    def test_degenerate_answer_eligible_returns_event(
        self,
        mock_runner: MagicMock,
    ) -> None:
        """Degenerate answer + eligible planning snapshot returns replan.started event."""
        mock_runner.build_replan_event.return_value = (
            "replan.started",
            {"assistant_message_id": "msg1"},
        )

        result = build_reader_ask_replan_event(
            final_content_md="I don't know",
            planning_snapshot=MagicMock(),
            assistant_message_id="msg1",
        )

        assert result is not None
        assert result[0] == "replan.started"
        mock_runner.build_replan_event.assert_called_once_with(
            final_content_md="I don't know",
            planning_snapshot=mock_runner.build_replan_event.call_args.kwargs["planning_snapshot"],
            assistant_message_id="msg1",
        )

    @patch("app.services.reader_ask.agent_invocation.agent_runner_svc")
    def test_non_degenerate_answer_returns_none(
        self,
        mock_runner: MagicMock,
    ) -> None:
        """Non-degenerate answer returns None (no replan)."""
        mock_runner.build_replan_event.return_value = None

        result = build_reader_ask_replan_event(
            final_content_md="This is a detailed answer about the topic.",
            planning_snapshot=MagicMock(),
            assistant_message_id="msg1",
        )

        assert result is None


class TestBuildReaderAskPlannerModelRoute:
    """build_reader_ask_planner_model_route delegates to build_model_for_route."""

    @patch("app.services.reader_ask.agent_invocation.build_model_for_route")
    @patch("app.services.reader_ask.agent_invocation.get_settings")
    def test_uses_planner_route(
        self,
        mock_settings: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        """Delegates to build_model_for_route with MODEL_ROUTE_READER_ASK_PLANNER."""
        from app.llm.routes import MODEL_ROUTE_READER_ASK_PLANNER

        fake_model = MagicMock()
        fake_config = MagicMock()
        mock_build.return_value = (fake_model, fake_config)

        result = build_reader_ask_planner_model_route()

        mock_build.assert_called_once_with(
            mock_settings.return_value, MODEL_ROUTE_READER_ASK_PLANNER, None
        )
        assert result == (fake_model, fake_config)


class TestBuildReaderAskReplanModelRoute:
    @patch("app.services.reader_ask.agent_invocation.build_model_for_route")
    @patch("app.services.reader_ask.agent_invocation.get_settings")
    def test_uses_replan_route(
        self,
        mock_settings: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        from app.llm.routes import MODEL_ROUTE_READER_ASK_REPLAN

        fake_model = MagicMock()
        fake_config = MagicMock()
        mock_build.return_value = (fake_model, fake_config)

        result = build_reader_ask_replan_model_route()

        mock_build.assert_called_once_with(
            mock_settings.return_value, MODEL_ROUTE_READER_ASK_REPLAN, None
        )
        assert result == (fake_model, fake_config)
