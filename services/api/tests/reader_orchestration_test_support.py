from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import asyncpg

from app.database.connection import init_connection
from app.schemas.reader_orchestration import ReaderAnalysisProgress
from app.services.reader_orchestration.analysis_section_plan import (
    ANALYSIS_SECTION_PLAN_VERSION,
)
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceResult,
    ArticleReadyPersistenceService,
    PlainTextArticleReadySubmitRequest,
)
from app.services.reader_orchestration.layer_publisher import (
    TranslationLayerPublisher,
    VocabularyLayerPublisher,
)
from tests.test_reader_orchestration_schema_baseline import (
    BASELINE_SQL as _BASELINE_SQL,
)
from tests.test_reader_orchestration_schema_baseline import (
    DATABASE_URL,
)

BASELINE_SQL = _BASELINE_SQL
_WORD_RE = re.compile(r"[A-Za-z]+")


def fixture_analysis_progress(**overrides: object) -> ReaderAnalysisProgress:
    payload: dict[str, object] = {
        "mode": "automatic",
        "plan_version": ANALYSIS_SECTION_PLAN_VERSION,
        "overall_status": "queued",
        "active_phase": "translation",
        "translation_status": "queued",
        "completed_section_count": 0,
        "total_section_count": 0,
        "active_section_id": None,
        "needs_user_action": False,
        "last_progress_at": None,
        "sections": [],
    }
    payload.update(overrides)
    return ReaderAnalysisProgress.model_validate(payload)


class CompatTranslationLayerPublisher:
    """Compatibility wrapper used by pipeline runner tests.

     short-article batch path: the translation batch worker calls
    ``publish_article_translation_batch`` on the layer publisher. The real
    ``TranslationLayerPublisher`` implements it, but tests that want to
    observe published outputs need a compat wrapper that records both
    per-unit and batch publish calls while delegating to the real publisher
    for fence / persistence.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
    ) -> None:
        self._publisher = TranslationLayerPublisher(pool=pool)
        self.published_outputs: list[Any] = []
        self.published_batch_outputs: list[tuple[str, Any]] = []

    async def publish_unit_translation(
        self,
        *,
        job_id,
        lease_token,
        output,
        quality_json: dict[str, Any] | None = None,
    ):
        self.published_outputs.append(output)
        return await self._publisher.publish_unit_translation(
            job_id=job_id,
            lease_token=lease_token,
            output=output,
            quality_json=quality_json,
        )

    async def publish_article_translation_batch(
        self,
        *,
        job_id,
        lease_token,
        outputs,
        quality_json: dict[str, Any] | None = None,
    ):
        for unit_id, output in outputs:
            self.published_batch_outputs.append((unit_id, output))
        return await self._publisher.publish_article_translation_batch(
            job_id=job_id,
            lease_token=lease_token,
            outputs=outputs,
            quality_json=quality_json,
        )


class CompatVocabularyLayerPublisher:
    """Compatibility wrapper for the vocabulary layer publisher.

    Mirrors :class:`CompatTranslationLayerPublisher` so tests can observe
    both per-unit and batch vocabulary publish calls. The real
    ``VocabularyLayerPublisher`` is used for fence / persistence.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
    ) -> None:
        self._publisher = VocabularyLayerPublisher(pool=pool)
        self.published_outputs: list[Any] = []
        self.published_batch_outputs: list[tuple[str, Any]] = []

    async def publish_unit_vocabulary(
        self,
        *,
        job_id,
        lease_token,
        output,
        quality_json: dict[str, Any] | None = None,
    ):
        self.published_outputs.append(output)
        return await self._publisher.publish_unit_vocabulary(
            job_id=job_id,
            lease_token=lease_token,
            output=output,
            quality_json=quality_json,
        )

    async def publish_article_vocabulary_batch(
        self,
        *,
        job_id,
        lease_token,
        outputs,
        quality_json: dict[str, Any] | None = None,
    ):
        for unit_id, output in outputs:
            self.published_batch_outputs.append((unit_id, output))
        return await self._publisher.publish_article_vocabulary_batch(
            job_id=job_id,
            lease_token=lease_token,
            outputs=outputs,
            quality_json=quality_json,
        )


def long_plain_text_fixture() -> str:
    text = (
        "Although the committee, which had spent six months reviewing export data, "
        "labor surveys, and municipal tax receipts that rarely lined up neatly, "
        "claimed that the recovery was broad enough to justify ending the emergency "
        "grant program, several shop owners warned that the headline numbers hid a "
        "more fragile street-level reality, because customers were still delaying "
        "purchases whenever wages, school fees, and transport costs rose in the same "
        "week. "
        "The chair, speaking in a tone that sounded patient even when the gallery "
        "grew restless, argued that the city could not keep funding every pilot "
        "forever, yet she also admitted that the report, which was drafted before "
        "the latest shipping slowdown and revised after three agencies disputed one "
        "another's forecasts, did not fully capture how quickly a small inventory "
        "mistake, a delayed permit, or an unexpected customs check could turn a "
        "promising quarter into a month of defensive bookkeeping. "
        "What made the hearing difficult for new members, many of whom had expected "
        "a simple choice between extending support and declaring success, was that "
        "the witnesses described a chain of causes rather than a single crisis: "
        "manufacturers were receiving orders, but not on predictable schedules; "
        "managers were hiring trainees, but only if senior staff agreed to mentor "
        "them; and families were willing to spend, but mainly after they had "
        "confirmed, sometimes twice, that rent, medicine, and exam expenses were "
        "already covered. "
        "By the time the final vote arrived, the proposal that survived was not the "
        "clean, decisive resolution the briefing memo had promised, but a narrower "
        "plan that preserved training subsidies for districts with rising vacancy "
        "rates, required monthly explanations whenever projected savings depended on "
        "one-off asset sales, and ordered a follow-up review so that officials, "
        "business groups, and neighborhood organizers could compare whether the "
        "apparent improvement reflected durable demand, delayed reporting, or a "
        "temporary calm created by firms quietly postponing the expenses they knew "
        "would return in autumn."
    )
    assert len(_WORD_RE.findall(text)) >= 250
    return text


async def make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


async def insert_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    assert isinstance(user_id, UUID)
    return user_id


async def submit_article_ready(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    plain_text: str = "First sentence.\n\nSecond paragraph for translation.",
    title: str = "Translation Slice",
    language: str = "en",
) -> ArticleReadyPersistenceResult:
    service = ArticleReadyPersistenceService(pool=pool)
    return await service.submit_plain_text(
        PlainTextArticleReadySubmitRequest(
            user_id=user_id,
            plain_text=plain_text,
            title=title,
            language=language,
        )
    )
