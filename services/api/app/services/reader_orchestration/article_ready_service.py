from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.schemas.reader_orchestration import ReaderPlateSnapshot

from .base_builder import LowImpactReadingBaseBuildInput, build_low_impact_reading_base
from .repository import ReaderOrchestrationRepository
from .snapshot import build_reader_plate_snapshot


@dataclass(frozen=True, slots=True)
class PlainTextArticleReadySubmitRequest:
    user_id: UUID
    plain_text: str
    title: str | None = None
    language: str | None = None
    source_metadata: dict[str, Any] | None = None
    client_record_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArticleReadyPersistenceResult:
    record_id: UUID
    original_input_id: UUID
    base_id: UUID
    article_ready_event_id: UUID
    article_ready_sequence: int
    snapshot: ReaderPlateSnapshot


class ArticleReadyPersistenceService:
    def __init__(
        self,
        *,
        repository: ReaderOrchestrationRepository | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._pool = pool or self._repository.get_pool()

    async def submit_plain_text(
        self,
        request: PlainTextArticleReadySubmitRequest,
    ) -> ArticleReadyPersistenceResult:
        title = _resolve_title(request.title, request.plain_text)
        language = (request.language or "en").strip() or "en"
        now = datetime.now(UTC)

        record_id = uuid4()
        original_input_id = uuid4()
        base_id = uuid4()
        article_ready_event_id = uuid4()

        build_result = build_low_impact_reading_base(
            LowImpactReadingBaseBuildInput(
                reading_record_id=str(record_id),
                base_id=str(base_id),
                source_text=request.plain_text,
                title=title,
                language=language,
            )
        )

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._repository.insert_reading_record(
                    conn,
                    record_id=record_id,
                    user_id=request.user_id,
                    client_record_id=request.client_record_id,
                    title=title,
                    language=language,
                    created_at=now,
                )
                await self._repository.insert_original_input(
                    conn,
                    original_input_id=original_input_id,
                    record_id=record_id,
                    user_id=request.user_id,
                    source_text=request.plain_text,
                    source_metadata=dict(request.source_metadata or {}),
                    created_at=now,
                )
                await self._repository.insert_reading_base(
                    conn,
                    base_id=base_id,
                    build_result=build_result,
                    created_at=now,
                )
                await self._repository.insert_reading_units(
                    conn,
                    record_id=record_id,
                    base_id=base_id,
                    units=build_result.units,
                )
                await self._repository.insert_anchor_segments(
                    conn,
                    record_id=record_id,
                    base_id=base_id,
                    anchor_segments=build_result.anchor_segments,
                )
                await self._repository.set_active_base_and_mark_article_ready(
                    conn,
                    record_id=record_id,
                    base_id=base_id,
                    expected_generation=1,
                    updated_at=now,
                )
                await self._repository.ensure_event_sequence_row(
                    conn,
                    record_id=record_id,
                    updated_at=now,
                )
                article_ready_sequence = await self._repository.allocate_event_sequence(
                    conn,
                    record_id=record_id,
                )
                await self._repository.insert_reader_event(
                    conn,
                    event_id=article_ready_event_id,
                    record_id=record_id,
                    sequence=article_ready_sequence,
                    event_type="article_ready",
                    payload_json={
                        "record_id": str(record_id),
                        "base_id": str(base_id),
                        "generation": 1,
                        "readiness_state": "article_ready",
                        "product_state": "readable_enhancing",
                    },
                    created_at=now,
                )

        snapshot = await self.load_snapshot(
            record_id=record_id,
            user_id=request.user_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
        return ArticleReadyPersistenceResult(
            record_id=record_id,
            original_input_id=original_input_id,
            base_id=base_id,
            article_ready_event_id=article_ready_event_id,
            article_ready_sequence=article_ready_sequence,
            snapshot=snapshot,
        )

    async def load_snapshot(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        expected_base_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> ReaderPlateSnapshot:
        async with self._pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                facts = await self._repository.load_snapshot_facts(
                    conn,
                    record_id=record_id,
                    user_id=user_id,
                    expected_base_id=expected_base_id,
                    expected_generation=expected_generation,
                )

        return build_reader_plate_snapshot(
            facts.build_result,
            snapshot_taken_at=facts.snapshot_taken_at,
            last_event_sequence=facts.last_event_sequence,
            enhancement_layers=facts.enhancement_layers,
            parsed_decisions=facts.parsed_decisions,
        )


def _resolve_title(title: str | None, plain_text: str) -> str:
    if title is not None and title.strip():
        return title.strip()

    first_non_empty_line = next(
        (line.strip() for line in plain_text.splitlines() if line.strip()),
        "",
    )
    if first_non_empty_line:
        return first_non_empty_line[:120].rstrip()
    return "Untitled Reading"
