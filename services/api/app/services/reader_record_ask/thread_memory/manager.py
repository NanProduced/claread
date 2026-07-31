"""Deep orchestration module for Ask Claread thread-memory preparation."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from app.config.settings import Settings
from app.services.reader_record_ask.execution_config import CompactorBudgetConfig
from app.services.reader_record_ask.model_view_budget import (
    RESERVE_RECENT_HISTORY,
    ModelViewRenderer,
    RenderedModelView,
)
from app.services.reader_record_ask.thread_memory.allowlist import (
    build_allowlist,
    build_host_bindings,
    compute_watermark,
    validate_snapshot,
)
from app.services.reader_record_ask.thread_memory.compactor import (
    CompactorRunOutcome,
    run_thread_memory_compactor,
)
from app.services.reader_record_ask.thread_memory.emergency import (
    emergency_compact,
)
from app.services.reader_record_ask.thread_memory.preparation import (
    prepare_snapshot_for_model,
)
from app.services.reader_record_ask.thread_memory.recent_history import (
    partition_recent_history,
)
from app.services.reader_record_ask.thread_memory.repository import (
    ThreadMemoryRepository,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    ThreadMemorySnapshot,
)

CompactionStatus = Literal[
    "not_needed",
    "completed",
    "fallback",
    "failed",
]
CompactionEventKind = Literal[
    "started",
    "completed",
    "fallback",
    "failed",
]
CompactorCallable = Callable[..., Awaitable[CompactorRunOutcome]]

_RECENT_PAIRS = 20


@dataclass(frozen=True, slots=True)
class ThreadMemoryLifecycleEvent:
    """Privacy-safe lifecycle event; never carries transcript/model content."""

    kind: CompactionEventKind
    detail_code: str | None
    attempt_count: int
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class PreparedThreadMemoryContext:
    """One atomic memory view ready for prompt assembly."""

    snapshot: ThreadMemorySnapshot | None
    recent_messages: tuple[dict[str, Any], ...]
    recent_history_view: RenderedModelView | None
    compaction_status: CompactionStatus
    detail_code: str | None
    attempt_count: int
    persisted: bool


def _now_iso_utc() -> str:
    return datetime.now(UTC).isoformat()


def _turn_groups(
    messages: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role == "user":
            current = [message]
            groups.append(current)
        elif role == "assistant" and current is not None:
            current.append(message)
    return groups


def _flatten(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [message for group in groups for message in group]


def _runs_for_messages(
    runs: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    message_ids = {
        str(message.get("id") or message.get("message_id") or "")
        for message in messages
    }
    return [
        run
        for run in runs
        if isinstance(run, dict)
        and str(run.get("message_id") or "") in message_ids
    ]


def _matching_episode_prefix(
    snapshot: ThreadMemorySnapshot | None,
    *,
    aged_groups: list[list[dict[str, Any]]],
) -> tuple[list[Episode], int] | None:
    if snapshot is None:
        return [], 0
    episodes = sorted(
        snapshot.episodes,
        key=lambda episode: (
            episode.turn_range.start,
            episode.turn_range.end,
            episode.episode_id,
        ),
    )
    expected_start = 1
    for episode in episodes:
        start = episode.turn_range.start
        end = episode.turn_range.end
        if (
            start != expected_start
            or end < start
            or end > len(aged_groups)
        ):
            return None
        segment = _flatten(aged_groups[start - 1 : end])
        if (
            not episode.compaction_input_watermark
            or episode.compaction_input_watermark
            != compute_watermark(segment)
        ):
            return None
        expected_start = end + 1
    return episodes, expected_start - 1


def _emergency_episode(
    *,
    messages: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    turn_range: tuple[int, int],
    host_bindings: dict[str, Any],
) -> Episode:
    episode = emergency_compact(
        messages,
        runs,
        turn_range=turn_range,
        host_bindings=host_bindings,
    )
    return episode.model_copy(
        update={
            "compaction_input_watermark": compute_watermark(messages),
        }
    )


class ThreadMemoryManager:
    """Own canonical read, compaction, CAS persistence, fence, and recent view."""

    def __init__(
        self,
        *,
        repository: ThreadMemoryRepository,
        renderer: ModelViewRenderer,
        compactor: CompactorCallable = run_thread_memory_compactor,
        event_sink: Callable[[ThreadMemoryLifecycleEvent], None] | None = None,
        settings: Settings | None = None,
        compactor_budget: CompactorBudgetConfig | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._repository = repository
        self._renderer = renderer
        self._compactor = compactor
        self._event_sink = event_sink
        self._settings = settings
        self._compactor_budget = compactor_budget or CompactorBudgetConfig()
        self._clock = monotonic_clock or time.perf_counter

    def _emit(
        self,
        kind: CompactionEventKind,
        *,
        detail_code: str | None,
        attempt_count: int,
        started_at: float,
    ) -> None:
        if self._event_sink is None:
            return
        elapsed_ms = max(0, int((self._clock() - started_at) * 1000))
        try:
            self._event_sink(
                ThreadMemoryLifecycleEvent(
                    kind=kind,
                    detail_code=detail_code,
                    attempt_count=attempt_count,
                    elapsed_ms=elapsed_ms,
                )
            )
        except Exception:  # noqa: BLE001 - observation cannot break Ask
            return

    async def prepare_context(
        self,
        *,
        thread_id: UUID,
        fence_context: dict[str, Any],
    ) -> PreparedThreadMemoryContext:
        """Prepare one atomic thread-memory view before the answer model runs."""

        view = await self._repository.load_canonical_memory_view(
            thread_id=thread_id
        )
        if view is None:
            return PreparedThreadMemoryContext(
                snapshot=None,
                recent_messages=(),
                recent_history_view=None,
                compaction_status="failed",
                detail_code="canonical_view_unavailable",
                attempt_count=0,
                persisted=False,
            )

        canonical_messages = list(view.canonical_messages)
        ok_turn_runs = list(view.ok_turn_runs)
        partition = partition_recent_history(
            canonical_messages=canonical_messages,
            renderer=self._renderer,
            budget_chars=RESERVE_RECENT_HISTORY,
            recent_pairs=_RECENT_PAIRS,
        )
        aged_messages = list(partition.aged_messages)
        if not aged_messages:
            return PreparedThreadMemoryContext(
                snapshot=None,
                recent_messages=partition.recent_messages,
                recent_history_view=partition.rendered_view,
                compaction_status="not_needed",
                detail_code=None,
                attempt_count=0,
                persisted=False,
            )
        if not getattr(view, "storage_available", True):
            return PreparedThreadMemoryContext(
                snapshot=None,
                recent_messages=partition.recent_messages,
                recent_history_view=partition.rendered_view,
                compaction_status="failed",
                detail_code="storage_unavailable",
                attempt_count=0,
                persisted=False,
            )

        all_groups = _turn_groups(canonical_messages)
        aged_groups = _turn_groups(aged_messages)
        host_bindings = build_host_bindings(ok_turn_runs)
        prefix = _matching_episode_prefix(
            view.snapshot,
            aged_groups=aged_groups,
        )
        if (
            prefix is None
            or view.snapshot is None
            or view.snapshot.thread_id != str(thread_id)
        ):
            episodes: list[Episode] = []
            covered_turns = 0
        else:
            episodes, covered_turns = prefix

        status: CompactionStatus = "not_needed"
        detail_code: str | None = None
        attempt_count = 0
        started_at = self._clock()
        if covered_turns < len(aged_groups):
            segment_groups = aged_groups[covered_turns:]
            segment_messages = _flatten(segment_groups)
            segment_runs = _runs_for_messages(
                ok_turn_runs,
                segment_messages,
            )
            turn_range = (covered_turns + 1, len(aged_groups))
            self._emit(
                "started",
                detail_code=None,
                attempt_count=0,
                started_at=started_at,
            )
            outcome = await self._compactor(
                canonical_messages=segment_messages,
                ok_turn_runs=segment_runs,
                turn_range=turn_range,
                host_bindings=host_bindings,
                settings=self._settings,
                budget=self._compactor_budget,
            )
            attempt_count = outcome.attempt_count
            if outcome.episode is not None and outcome.detail_code == "ok":
                episodes.append(outcome.episode)
                status = "completed"
                detail_code = "ok"
            else:
                fallback = _emergency_episode(
                    messages=segment_messages,
                    runs=segment_runs,
                    turn_range=turn_range,
                    host_bindings=host_bindings,
                )
                episodes.append(fallback)
                status = "fallback"
                detail_code = outcome.detail_code

        current_watermark = compute_watermark(canonical_messages)
        now = _now_iso_utc()
        previous = view.snapshot
        candidate = ThreadMemorySnapshot(
            version="thread_memory_v1",
            watermark=current_watermark,
            thread_id=str(thread_id),
            created_at=previous.created_at if previous is not None else now,
            last_compacted_at=(
                now
                if status in {"completed", "fallback"}
                else (
                    previous.last_compacted_at
                    if previous is not None
                    else None
                )
            ),
            last_compaction_stats=(
                {
                    "aged_turn_count": len(aged_groups),
                    "recent_turn_count": len(all_groups) - len(aged_groups),
                    "episode_count": len(episodes),
                }
                if status in {"completed", "fallback"}
                else (
                    previous.last_compaction_stats
                    if previous is not None
                    else None
                )
            ),
            episodes=episodes,
        )

        allowlist = build_allowlist(canonical_messages, ok_turn_runs)
        validated, metrics = validate_snapshot(
            candidate,
            host_bindings,
            allowlist,
            None,
        )
        if metrics.get("rejected"):
            self._emit(
                "failed",
                detail_code="snapshot_rejected",
                attempt_count=attempt_count,
                started_at=started_at,
            )
            return PreparedThreadMemoryContext(
                snapshot=None,
                recent_messages=partition.recent_messages,
                recent_history_view=partition.rendered_view,
                compaction_status="failed",
                detail_code="snapshot_rejected",
                attempt_count=attempt_count,
                persisted=False,
            )

        should_write = (
            previous is None
            or previous.watermark != validated.watermark
            or previous.episodes != validated.episodes
        )
        if should_write:
            try:
                write = await self._repository.upsert_thread_memory_snapshot(
                    thread_id=thread_id,
                    snapshot=validated,
                    version=view.snapshot_version,
                )
            except Exception:  # noqa: BLE001 - optional memory fails soft
                self._emit(
                    "failed",
                    detail_code="storage_write_failed",
                    attempt_count=attempt_count,
                    started_at=started_at,
                )
                return PreparedThreadMemoryContext(
                    snapshot=None,
                    recent_messages=partition.recent_messages,
                    recent_history_view=partition.rendered_view,
                    compaction_status="failed",
                    detail_code="storage_write_failed",
                    attempt_count=attempt_count,
                    persisted=False,
                )
            if not write.applied:
                self._emit(
                    "failed",
                    detail_code="cas_conflict",
                    attempt_count=attempt_count,
                    started_at=started_at,
                )
                return PreparedThreadMemoryContext(
                    snapshot=None,
                    recent_messages=partition.recent_messages,
                    recent_history_view=partition.rendered_view,
                    compaction_status="failed",
                    detail_code="cas_conflict",
                    attempt_count=attempt_count,
                    persisted=False,
                )

        try:
            prepared_snapshot, _fence_metrics = await prepare_snapshot_for_model(
                validated,
                host_bindings=host_bindings,
                allowlist=allowlist,
                fence_context=fence_context,
            )
        except Exception:  # noqa: BLE001 - fence failure skips memory
            if status in {"completed", "fallback"}:
                self._emit(
                    "failed",
                    detail_code="fence_failed",
                    attempt_count=attempt_count,
                    started_at=started_at,
                )
            return PreparedThreadMemoryContext(
                snapshot=None,
                recent_messages=partition.recent_messages,
                recent_history_view=partition.rendered_view,
                compaction_status="failed",
                detail_code="fence_failed",
                attempt_count=attempt_count,
                persisted=should_write,
            )

        if status in {"completed", "fallback"}:
            self._emit(
                status,
                detail_code=detail_code,
                attempt_count=attempt_count,
                started_at=started_at,
            )
        return PreparedThreadMemoryContext(
            snapshot=prepared_snapshot,
            recent_messages=partition.recent_messages,
            recent_history_view=partition.rendered_view,
            compaction_status=status,
            detail_code=detail_code,
            attempt_count=attempt_count,
            persisted=should_write,
        )


__all__ = [
    "PreparedThreadMemoryContext",
    "ThreadMemoryLifecycleEvent",
    "ThreadMemoryManager",
]
