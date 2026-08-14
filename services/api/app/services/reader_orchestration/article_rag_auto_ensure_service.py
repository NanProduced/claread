"""Article RAG Index Auto-Ensure Hook.

Fail-soft wrapper around :class:`ArticleRagIndexLifecycleService` that
ensures an Article RAG index build job exists when a reading record
reaches ``article_ready``.  It is designed to be called from the
``article_ready`` entry points (stable-ready input, candidate confirm,
artifact materialization stable path) **inside the caller's existing
transaction** so the index job commits atomically with the readiness
transition.

Design
------
This service NEVER:

  * calls embedding providers (DashScope / 百炼)
  * calls vector stores (Zilliz / Milvus)
  * writes chunk text / embedding vectors
  * raises exceptions to the caller — **all** failures are caught and
    returned as a typed :class:`ArticleRagAutoEnsureResult` so the
    main ``article_ready`` flow is never blocked
  * includes raw exception messages, tokens, URIs, chunk text, or
    SDK internals in the result — the ``reason_code`` is always a
    fixed, safe identifier

Config switch
-------------
``reader_article_rag_enabled`` (from :class:`Settings`) gates the
entire hook.  When disabled, ``ensure_in_transaction`` returns
immediately with ``status=disabled`` without touching the lifecycle
service or the database.

Transaction model
-----------------
The caller must hold an active transaction on ``conn``.  The lifecycle
service's ``ensure_article_rag_index_job_in_transaction`` will itself
fail-closed if ``conn`` is not in a transaction, but this wrapper
catches that and returns a fail-soft result instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.services.reader_orchestration.article_rag_index_lifecycle_service import (
    ArticleRagIndexEnsureResult,
    ArticleRagIndexLifecycleService,
)

if TYPE_CHECKING:
    import asyncpg

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Auto-ensure-specific status values (beyond the lifecycle service's own
# ENSURE_STATUS_* values which are forwarded transparently).
AUTO_ENSURE_STATUS_DISABLED = "disabled"
AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR = "fail_soft_error"

# Fixed reason codes — never derived from exception messages to avoid
# leaking tokens / URIs / chunk text / SDK internals.
REASON_RAG_DISABLED = "rag_disabled"
REASON_AUTO_ENSURE_UNEXPECTED_ERROR = "auto_ensure_unexpected_error"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAutoEnsureResult:
    """Typed result of the auto-ensure hook.

    ``status`` is the high-level outcome the caller can switch on.
    On the ``disabled`` / ``fail_soft_error`` paths, ``index_run_id``
    and ``job_id`` are always ``None``.

    On the success paths (``enqueued`` / ``idempotent_noop``) or any
    typed non-success path forwarded from the lifecycle service
    (``not_ready`` / ``no_active_base`` / ``generation_mismatch`` etc.),
    ``status`` and ``reason_code`` mirror the lifecycle service's values.
    """

    status: str
    reason_code: str
    index_run_id: UUID | None = None
    job_id: UUID | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArticleRagAutoEnsureService:
    """Fail-soft Article RAG index auto-ensure hook.

    Construct via :func:`build_default_auto_ensure_service` in production
    code, or directly with ``enabled=False`` / a fake
    :class:`ArticleRagIndexLifecycleService` in tests.
    """

    def __init__(
        self,
        *,
        lifecycle_service: ArticleRagIndexLifecycleService | None = None,
        enabled: bool = False,
    ) -> None:
        self._lifecycle_service = lifecycle_service or ArticleRagIndexLifecycleService()
        self._enabled = enabled

    async def ensure_in_transaction(
        self,
        conn: asyncpg.Connection,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        expected_generation: int,
        now: datetime | None = None,
    ) -> ArticleRagAutoEnsureResult:
        """Ensure an Article RAG index job exists, fail-soft.

        Returns a typed result.  Never raises — all exceptions are caught
        and translated to ``fail_soft_error``.
        """
        if not self._enabled:
            return ArticleRagAutoEnsureResult(
                status=AUTO_ENSURE_STATUS_DISABLED,
                reason_code=REASON_RAG_DISABLED,
            )

        try:
            result: ArticleRagIndexEnsureResult = (
                await self._lifecycle_service.ensure_article_rag_index_job_in_transaction(
                    conn,
                    reading_record_id=reading_record_id,
                    user_id=user_id,
                    expected_generation=expected_generation,
                    now=now,
                )
            )
        except Exception:
            # Swallow ALL exceptions — the main article_ready flow must
            # never be blocked by a RAG ensure failure.  The reason_code
            # is a fixed, safe identifier; raw exception messages, tokens,
            # URIs, chunk text, or SDK internals are NEVER included.
            return ArticleRagAutoEnsureResult(
                status=AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR,
                reason_code=REASON_AUTO_ENSURE_UNEXPECTED_ERROR,
            )

        return ArticleRagAutoEnsureResult(
            status=result.status,
            reason_code=result.reason_code,
            index_run_id=result.index_run_id,
            job_id=result.job_id,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_default_auto_ensure_service() -> ArticleRagAutoEnsureService:
    """Build an auto-ensure service from current Settings.

    Reads ``reader_article_rag_enabled`` from :func:`get_settings`.
    Does NOT connect to DashScope / Zilliz — those are only contacted
    by the index worker when it processes a job.
    """
    from app.config.settings import get_settings

    settings = get_settings()
    return ArticleRagAutoEnsureService(
        enabled=settings.reader_article_rag_enabled,
    )


__all__ = [
    "AUTO_ENSURE_STATUS_DISABLED",
    "AUTO_ENSURE_STATUS_FAIL_SOFT_ERROR",
    "ArticleRagAutoEnsureResult",
    "ArticleRagAutoEnsureService",
    "build_default_auto_ensure_service",
]
