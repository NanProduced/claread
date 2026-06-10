"""Tests for stream_checkpoint module."""

from uuid import uuid4

from app.services.reader_ask import agent_runner as agent_runner_svc
from app.services.reader_ask import stream_checkpoint

# ---------------------------------------------------------------------------
# TestBuildCheckpoint
# ---------------------------------------------------------------------------


class TestTerminalReasoningStatus:
    def test_completed_when_reasoning_started(self) -> None:
        assert stream_checkpoint.terminal_reasoning_status(True) == "completed"

    def test_none_when_no_reasoning(self) -> None:
        assert stream_checkpoint.terminal_reasoning_status(False) is None

    def test_completed_even_without_emitted_content(self) -> None:
        """When reasoning.started fired but no delta arrived, the run still
        counts as having started reasoning.  The terminal status must be
        "completed" (not None or "streaming") so that the frontend can leave
        the streaming state."""
        assert stream_checkpoint.terminal_reasoning_status(True) == "completed"


class TestCurrentReasoningStatus:
    def test_streaming_when_reasoning_is_active(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime(
            reasoning_started=True,
            reasoning_active=True,
        )
        assert stream_checkpoint.current_reasoning_status(runtime) == "streaming"

    def test_completed_when_reasoning_already_finished(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime(
            reasoning_started=True,
            reasoning_active=False,
        )
        assert stream_checkpoint.current_reasoning_status(runtime) == "completed"

    def test_none_when_reasoning_never_started(self) -> None:
        runtime = agent_runner_svc.AgentStreamRuntime(
            reasoning_started=False,
            reasoning_active=False,
        )
        assert stream_checkpoint.current_reasoning_status(runtime) is None


class TestMaybeFlushTurnRunStreamCheckpoint:
    def _make_checkpoint(
        self,
        *,
        min_flush_interval_s: float = 0.8,
        min_content_chars: int = 48,
        min_reasoning_chars: int = 48,
    ) -> stream_checkpoint.TurnRunStreamCheckpoint:
        updates: list[dict] = []
        self._updates = updates

        async def fake_update_turn_run(**kwargs: object) -> None:
            updates.append(kwargs)

        checkpoint = stream_checkpoint.TurnRunStreamCheckpoint(
            turn_run_id=uuid4(),
            build_output_json=lambda content_md, reasoning_md, reasoning_status: {
                "content_md": content_md,
                "reasoning_md": reasoning_md,
                "reasoning_status": reasoning_status,
            },
            update_turn_run_cb=fake_update_turn_run,
            min_flush_interval_s=min_flush_interval_s,
            min_content_chars=min_content_chars,
            min_reasoning_chars=min_reasoning_chars,
        )
        return checkpoint

    async def test_no_flush_when_content_and_reasoning_unchanged_and_not_forced(self) -> None:
        """Content/reasoning unchanged and not forced → no flush."""
        checkpoint = self._make_checkpoint()
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="初始正文",
            emitted_reasoning="初始思路",
            reasoning_started=True,
            reasoning_active=True,
        )
        # First flush (initial, last_flushed_at == 0)
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(self._updates) == 1

        # Same content, not forced → no flush
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(self._updates) == 1

    async def test_flush_when_content_grows_beyond_threshold(self) -> None:
        """Content growth beyond min_content_chars triggers flush."""
        checkpoint = self._make_checkpoint(min_content_chars=10, min_flush_interval_s=999.0)
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="初始",
            reasoning_started=False,
        )
        # Initial flush
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(self._updates) == 1

        # Grow content beyond threshold
        runtime.emitted_text = "初始" + "新增内容超过十个字符阈值"
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(self._updates) == 2

    async def test_flush_when_reasoning_grows_beyond_threshold(self) -> None:
        """Reasoning growth beyond min_reasoning_chars triggers flush."""
        checkpoint = self._make_checkpoint(min_reasoning_chars=10, min_flush_interval_s=999.0)
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="正文",
            emitted_reasoning="初始思路",
            reasoning_started=True,
            reasoning_active=True,
        )
        # Initial flush
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(self._updates) == 1

        # Grow reasoning beyond threshold
        runtime.emitted_reasoning = "初始思路" + "新增推理内容超过阈值"
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(self._updates) == 2

    async def test_force_flush_with_reasoning_started_even_if_empty(self) -> None:
        """force=True + reasoning_started → flush even when content is empty."""
        checkpoint = self._make_checkpoint(
            min_content_chars=999,
            min_reasoning_chars=999,
            min_flush_interval_s=999.0,
        )
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="",
            emitted_reasoning="",
            reasoning_started=True,
            reasoning_active=True,
        )
        # Not forced, empty → no flush
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime, force=False,
        )
        assert len(self._updates) == 0

        # Forced with reasoning_started → flush
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime, force=True,
        )
        assert len(self._updates) == 1
        assert self._updates[0]["user_visible_output_json"]["reasoning_status"] == "streaming"

    async def test_flush_updates_last_flushed_lengths(self) -> None:
        """After flush, last_flushed_content_len and last_flushed_reasoning_len are updated."""
        checkpoint = self._make_checkpoint()
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="已生成正文。",
            emitted_reasoning="先判断句子主干。",
            reasoning_started=True,
            reasoning_active=True,
        )

        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )

        assert checkpoint.last_flushed_content_len == len("已生成正文。")
        assert checkpoint.last_flushed_reasoning_len == len("先判断句子主干。")

    async def test_flush_persists_partial_reasoning_and_body(self) -> None:
        """Integration: flush persists partial content and reasoning."""
        turn_run_id = uuid4()
        updates: list[dict] = []

        async def fake_update_turn_run(**kwargs: object) -> None:
            updates.append(kwargs)

        checkpoint = stream_checkpoint.TurnRunStreamCheckpoint(
            turn_run_id=turn_run_id,
            build_output_json=lambda content_md, reasoning_md, reasoning_status: {
                "content_md": content_md,
                "reasoning_md": reasoning_md,
                "reasoning_status": reasoning_status,
            },
            update_turn_run_cb=fake_update_turn_run,
        )
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="已生成正文。",
            emitted_reasoning="先判断句子主干。",
            reasoning_started=True,
            reasoning_active=True,
        )

        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )

        assert len(updates) == 1
        assert updates[0]["turn_run_id"] == turn_run_id
        assert updates[0]["status"] == "streaming"
        assert updates[0]["user_visible_output_json"] == {
            "content_md": "已生成正文。",
            "reasoning_md": "先判断句子主干。",
            "reasoning_status": "streaming",
        }

    async def test_flush_is_throttled_until_forced(self) -> None:
        """Throttle: small delta doesn't flush, force does."""
        turn_run_id = uuid4()
        updates: list[dict] = []

        async def fake_update_turn_run(**kwargs: object) -> None:
            updates.append(kwargs)

        checkpoint = stream_checkpoint.TurnRunStreamCheckpoint(
            turn_run_id=turn_run_id,
            build_output_json=lambda content_md, reasoning_md, reasoning_status: {
                "content_md": content_md,
                "reasoning_md": reasoning_md,
                "reasoning_status": reasoning_status,
            },
            update_turn_run_cb=fake_update_turn_run,
            min_flush_interval_s=999.0,
            min_content_chars=999,
            min_reasoning_chars=999,
        )
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="第一段正文",
            emitted_reasoning="第一段思路",
            reasoning_started=True,
            reasoning_active=True,
        )

        # Initial flush (last_flushed_at == 0)
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(updates) == 1

        # Small delta — throttled
        runtime.emitted_text = "第一段正文，新增很短"
        runtime.emitted_reasoning = "第一段思路，新增很短"
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )
        assert len(updates) == 1  # still 1

        # Force flush
        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime, force=True,
        )
        assert len(updates) == 2
        assert updates[1]["user_visible_output_json"]["content_md"] == "第一段正文，新增很短"
        assert updates[1]["user_visible_output_json"]["reasoning_md"] == "第一段思路，新增很短"

    async def test_flush_marks_reasoning_completed_after_thinking_phase_ends(self) -> None:
        checkpoint = self._make_checkpoint()
        runtime = agent_runner_svc.AgentStreamRuntime(
            emitted_text="正文继续输出",
            emitted_reasoning="思路已经完整",
            reasoning_started=True,
            reasoning_active=False,
        )

        await stream_checkpoint.maybe_flush_turn_run_stream_checkpoint(
            checkpoint=checkpoint, runtime=runtime,
        )

        assert len(self._updates) == 1
        assert self._updates[0]["user_visible_output_json"]["reasoning_status"] == "completed"


class TestMakeCheckpointFlush:
    async def test_returns_none_when_checkpoint_is_none(self) -> None:
        result = stream_checkpoint.make_checkpoint_flush(None)
        assert result is None

    async def test_returns_callable_when_checkpoint_provided(self) -> None:
        async def fake_update(**kwargs: object) -> None:
            pass

        checkpoint = stream_checkpoint.TurnRunStreamCheckpoint(
            turn_run_id=uuid4(),
            build_output_json=lambda c, r, s: {},
            update_turn_run_cb=fake_update,
        )
        result = stream_checkpoint.make_checkpoint_flush(checkpoint)
        assert result is not None
        assert callable(result)
