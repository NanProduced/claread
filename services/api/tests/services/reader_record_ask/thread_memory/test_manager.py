from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.services.reader_record_ask.model_view_budget import ModelViewRenderer
from app.services.reader_record_ask.thread_memory.allowlist import compute_watermark
from app.services.reader_record_ask.thread_memory.compactor import (
    CompactorRunOutcome,
)
from app.services.reader_record_ask.thread_memory.emergency import (
    emergency_compact,
)
from app.services.reader_record_ask.thread_memory.manager import (
    ThreadMemoryManager,
)
from app.services.reader_record_ask.thread_memory.repository import (
    CanonicalMemoryView,
    SnapshotWriteResult,
)
from app.services.reader_record_ask.thread_memory.schema import (
    ThreadMemorySnapshot,
)

_THREAD_ID = UUID("11111111-1111-4111-8111-111111111111")


def _pairs(count: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        messages.extend(
            [
                {
                    "id": f"u{index}",
                    "role": "user",
                    "content_md": f"question-{index}",
                    "canonical_turn_run_id": None,
                    "answer_blocks": [],
                    "web_search_summary": None,
                },
                {
                    "id": f"a{index}",
                    "role": "assistant",
                    "content_md": f"answer-{index}",
                    "canonical_turn_run_id": f"r{index}",
                    "answer_blocks": [],
                    "web_search_summary": None,
                },
            ]
        )
    return messages


class _Repository:
    def __init__(self, view: CanonicalMemoryView) -> None:
        self.view = view
        self.loads = 0
        self.writes: list[tuple[ThreadMemorySnapshot, int]] = []

    async def load_canonical_memory_view(
        self,
        *,
        thread_id: UUID,
    ) -> CanonicalMemoryView:
        assert thread_id == _THREAD_ID
        self.loads += 1
        return self.view

    async def upsert_thread_memory_snapshot(
        self,
        *,
        thread_id: UUID,
        snapshot: ThreadMemorySnapshot,
        version: int,
    ) -> SnapshotWriteResult:
        assert thread_id == _THREAD_ID
        self.writes.append((snapshot, version))
        return SnapshotWriteResult(applied=True, version=version + 1)


def _view(
    messages: list[dict[str, Any]],
    *,
    snapshot: ThreadMemorySnapshot | None = None,
    version: int = 0,
    storage_available: bool = True,
) -> CanonicalMemoryView:
    return CanonicalMemoryView(
        snapshot=snapshot,
        snapshot_version=version,
        canonical_messages=tuple(messages),
        ok_turn_runs=(),
        storage_available=storage_available,
    )


def _episode_for_first_turn(messages: list[dict[str, Any]]):
    episode = emergency_compact(
        messages[:2],
        [],
        turn_range=(1, 1),
        host_bindings={},
    )
    return episode.model_copy(
        update={
            "compaction_input_watermark": compute_watermark(messages[:2]),
            "compaction_model": "deepseek-v4-flash",
            "compaction_method": "model",
        }
    )


@pytest.mark.asyncio
async def test_manager_compacts_aged_prefix_persists_and_keeps_recent_suffix() -> None:
    messages = _pairs(21)
    repo = _Repository(_view(messages))
    calls = 0

    async def compactor(**kwargs: Any) -> CompactorRunOutcome:
        nonlocal calls
        calls += 1
        assert kwargs["turn_range"] == (1, 1)
        return CompactorRunOutcome(
            episode=_episode_for_first_turn(messages),
            detail_code="ok",
            attempt_count=1,
        )

    events = []
    manager = ThreadMemoryManager(
        repository=repo,
        renderer=ModelViewRenderer(),
        compactor=compactor,
        event_sink=events.append,
    )
    prepared = await manager.prepare_context(
        thread_id=_THREAD_ID,
        fence_context={},
    )

    assert calls == 1
    assert prepared.snapshot is not None
    assert prepared.compaction_status == "completed"
    assert [message["id"] for message in prepared.recent_messages] == [
        item
        for index in range(2, 22)
        for item in (f"u{index}", f"a{index}")
    ]
    assert prepared.recent_history_view is not None
    assert len(repo.writes) == 1
    assert [event.kind for event in events] == ["started", "completed"]


@pytest.mark.asyncio
async def test_manager_uses_emergency_fallback_after_safe_model_failure() -> None:
    messages = _pairs(21)
    repo = _Repository(_view(messages))

    async def compactor(**kwargs: Any) -> CompactorRunOutcome:
        del kwargs
        return CompactorRunOutcome(
            episode=None,
            detail_code="timeout",
            attempt_count=2,
        )

    events = []
    prepared = await ThreadMemoryManager(
        repository=repo,
        renderer=ModelViewRenderer(),
        compactor=compactor,
        event_sink=events.append,
    ).prepare_context(thread_id=_THREAD_ID, fence_context={})

    assert prepared.snapshot is not None
    assert prepared.compaction_status == "fallback"
    assert prepared.detail_code == "timeout"
    assert prepared.snapshot.episodes[0].compaction_model == "none"
    assert [event.kind for event in events] == ["started", "fallback"]


@pytest.mark.asyncio
async def test_manager_reuses_matching_episode_when_only_recent_suffix_changed() -> None:
    old_messages = _pairs(21)
    episode = _episode_for_first_turn(old_messages)
    snapshot = ThreadMemorySnapshot(
        version="thread_memory_v1",
        watermark=compute_watermark(old_messages),
        thread_id=str(_THREAD_ID),
        created_at="2026-07-31T00:00:00+00:00",
        last_compacted_at="2026-07-31T00:00:00+00:00",
        last_compaction_stats=None,
        episodes=[episode],
    )
    new_messages = _pairs(21)
    new_messages[-2] = {
        **new_messages[-2],
        "content_md": "updated recent question",
    }
    repo = _Repository(_view(new_messages, snapshot=snapshot, version=3))
    calls = 0

    async def compactor(**kwargs: Any) -> CompactorRunOutcome:
        del kwargs
        nonlocal calls
        calls += 1
        raise AssertionError("matching aged episode must not be re-compacted")

    prepared = await ThreadMemoryManager(
        repository=repo,
        renderer=ModelViewRenderer(),
        compactor=compactor,
    ).prepare_context(thread_id=_THREAD_ID, fence_context={})

    assert calls == 0
    assert prepared.snapshot is not None
    assert prepared.compaction_status == "not_needed"
    assert prepared.snapshot.episodes == [episode]
    assert prepared.snapshot.watermark == compute_watermark(new_messages)
    assert len(repo.writes) == 1


@pytest.mark.asyncio
async def test_manager_does_not_pay_for_model_when_storage_table_is_missing() -> None:
    messages = _pairs(21)
    repo = _Repository(_view(messages, storage_available=False))
    calls = 0

    async def compactor(**kwargs: Any) -> CompactorRunOutcome:
        del kwargs
        nonlocal calls
        calls += 1
        raise AssertionError("compactor must not run without persistence")

    prepared = await ThreadMemoryManager(
        repository=repo,
        renderer=ModelViewRenderer(),
        compactor=compactor,
    ).prepare_context(thread_id=_THREAD_ID, fence_context={})

    assert calls == 0
    assert prepared.snapshot is None
    assert prepared.compaction_status == "failed"
    assert prepared.detail_code == "storage_unavailable"
    assert repo.writes == []


@pytest.mark.asyncio
async def test_manager_emits_only_failed_when_cas_rejects_completed_draft() -> None:
    repo = _Repository(_view(_pairs(21)))

    async def reject_write(**_kwargs: Any) -> SnapshotWriteResult:
        return SnapshotWriteResult(applied=False, version=0)

    repo.upsert_thread_memory_snapshot = reject_write  # type: ignore[method-assign]
    events = []

    async def compactor(**kwargs: Any) -> CompactorRunOutcome:
        return CompactorRunOutcome(
            episode=_episode_for_first_turn(kwargs["canonical_messages"]),
            detail_code="ok",
            attempt_count=1,
        )

    prepared = await ThreadMemoryManager(
        repository=repo,
        renderer=ModelViewRenderer(),
        compactor=compactor,
        event_sink=events.append,
    ).prepare_context(thread_id=_THREAD_ID, fence_context={})

    assert prepared.compaction_status == "failed"
    assert prepared.detail_code == "cas_conflict"
    assert [event.kind for event in events] == ["started", "failed"]
