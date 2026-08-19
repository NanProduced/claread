"""OBS-01B-C: index worker embedding usage accounting (ai_usage_events).

Real PostgreSQL per-test isolated schema; fake providers only (no real
DashScope/Zilliz). Covers the frozen contract:

- one idempotent usage event per attempted provider embedding invocation
  (``reader:rag_embedding:{job_id}:{attempt_count}:1``);
- provider success -> status=succeeded + index_publish_outcome lifecycle
  via metadata-only patch (published / publish_failed /
  abandoned_after_embedding / superseded_after_embedding / pending);
- provider failure -> status=failed, no index_publish_outcome, original
  retry_later / failed_terminal semantics untouched;
- usage persist/patch failures never affect business outcomes;
- noop / pre-provider failures / non-attempted providers -> zero events;
- attempt_count fail-closed before any provider call.

Harness (seed helpers, schema fixture, job fetchers) is reused from
``test_article_rag_index_worker`` which itself imports from other test
modules — same established pattern.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from app.services.ai_usage.service import (
    AIUsageEventCreate,
    record_invocation_keyed_usage_event,
)
from app.services.reader_orchestration.article_rag_index_worker import (
    ArticleRagEmbedding,
    ArticleRagEmbeddingBatchUsage,
    ArticleRagEmbeddingInvocationResult,
    ArticleRagEmbeddingUsageReport,
    ArticleRagIndexWorkerError,
    ArticleRagIndexWorkerService,
    FakeArticleRagVectorWriter,
)
from tests.test_article_rag_index_plan import (  # noqa: E402
    _RECORD_ID,
    _STABLE_DOC_ID,
    _USER_ID,
)

# Reuse the full worker harness from the existing suite.
from tests.test_article_rag_index_worker import (  # noqa: E402
    _LEASE_DURATION,
    _LEASE_OWNER,
    _RETRY_DELAY,
    _build_bootstrap_service,
    _build_worker_service,
    _fetch_job,
    _reset_job_to_queued,
    _seed_paragraph_environment,
)

pytest_plugins = ("tests.test_article_rag_index_worker",)
pytestmark = pytest.mark.anyio

_DIM = 1024
_MODEL = "text-embedding-v4"


# ---------------------------------------------------------------------------
# Fake index-capable provider
# ---------------------------------------------------------------------------


def _attempted_report(
    **overrides: Any,
) -> ArticleRagEmbeddingUsageReport:
    base: dict[str, Any] = dict(
        provider_call_attempted=True,
        provider_succeeded=True,
        usage_completeness="complete",
        input_tokens=123,
        output_tokens=0,
        total_tokens=123,
        batch_count=1,
        completed_batch_count=1,
        failed_batch_ordinal=None,
        batches=(
            ArticleRagEmbeddingBatchUsage(
                ordinal=1,
                request_id="req-1",
                input_count=1,
                total_tokens=123,
            ),
        ),
        batches_truncated_count=0,
        provider_name="dashscope",
        model_name=_MODEL,
    )
    base.update(overrides)
    return ArticleRagEmbeddingUsageReport(**base)


def _fake_embeddings(texts: list[str]) -> list[ArticleRagEmbedding]:
    out: list[ArticleRagEmbedding] = []
    for text in texts:
        text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        digest = hashlib.sha256((text_sha + "|" + _MODEL).encode()).digest()
        vector = tuple((digest[i % len(digest)] / 255.0) * 2.0 - 1.0 for i in range(_DIM))
        out.append(
            ArticleRagEmbedding(
                text_sha256=text_sha, model=_MODEL, vector=vector, dim=_DIM
            )
        )
    return out


class _UsageFakeProvider:
    """Index-capable fake: returns a typed report or raises a typed error."""

    def __init__(
        self,
        *,
        report: ArticleRagEmbeddingUsageReport | None = None,
        error: ArticleRagIndexWorkerError | None = None,
        embeddings_count_override: int | None = None,
        mutate_during_embedding=None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._report = report or _attempted_report()
        self._error = error
        self._count_override = embeddings_count_override
        self._mutate = mutate_during_embedding
        self._pool = pool
        self.call_count = 0
        self.last_texts: list[str] | None = None

    async def embed_texts_with_usage(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> ArticleRagEmbeddingInvocationResult:
        self.call_count += 1
        self.last_texts = list(texts)
        if self._mutate is not None and self._pool is not None:
            await self._mutate(self._pool)
        if self._error is not None:
            raise self._error
        embeddings = _fake_embeddings(texts)
        if self._count_override is not None:
            embeddings = embeddings[: self._count_override]
        return ArticleRagEmbeddingInvocationResult(
            embeddings=tuple(embeddings),
            usage_report=self._report,
        )


class _RetryableVectorWriter:
    def __init__(self) -> None:
        self.call_count = 0

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings,
        metadata,
    ):
        self.call_count += 1
        raise ArticleRagIndexWorkerError(
            "fake retryable vector write failure",
            retryable=True,
            failure_class="vector_write",
            failure_code="vector_write_failed",
        )


async def _bump_generation(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reading_records "
            "SET generation = generation + 1, active_base_id = NULL "
            "WHERE id = $1",
            _RECORD_ID,
        )


async def _bootstrap(pool: asyncpg.Pool):
    await _seed_paragraph_environment(pool)
    return await _build_bootstrap_service(pool).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
    )


async def _fetch_usage_events(
    pool: asyncpg.Pool,
) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, status, capability_code, usage_scope, billing_mode,"
        " user_id, reading_record_id, reader_run_id, reader_job_id,"
        " operation_fingerprint, model_route, model_provider, model_name,"
        " input_tokens, output_tokens, total_tokens, billed_points,"
        " error_code, error_message, invocation_key, workflow_name,"
        " workflow_version, metadata_json"
        " FROM ai_usage_events ORDER BY created_at"
    )


async def _run_worker(service: ArticleRagIndexWorkerService):
    return await service.process_next(
        lease_owner=_LEASE_OWNER,
        lease_duration=_LEASE_DURATION,
        retry_delay=_RETRY_DELAY,
    )


# ---------------------------------------------------------------------------
# 1. Full success
# ---------------------------------------------------------------------------


async def test_success_full_flow_one_event_published(worker_env) -> None:
    bootstrap_result = await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"
    assert provider.call_count == 1

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    job = await _fetch_job(worker_env, job_id=bootstrap_result.job_id)
    expected_key = f"reader:rag_embedding:{bootstrap_result.job_id}:{job['attempt_count']}:1"
    assert event["invocation_key"] == expected_key
    assert event["status"] == "succeeded"
    assert event["capability_code"] == "rag_embedding"
    assert event["usage_scope"] == "system_internal"
    assert event["billing_mode"] == "internal_only"
    assert event["billed_points"] == 0
    assert event["user_id"] == _USER_ID
    assert event["reading_record_id"] == _RECORD_ID
    assert event["reader_job_id"] == bootstrap_result.job_id
    assert event["reader_run_id"] == job["run_id"]
    assert event["operation_fingerprint"] == job["operation_fingerprint"]
    assert event["model_provider"] == "dashscope"
    assert event["model_name"] == _MODEL
    assert event["model_route"] == "rag_embedding"
    assert event["error_code"] is None
    assert event["error_message"] is None
    assert event["input_tokens"] == 123
    assert event["total_tokens"] == 123
    metadata = event["metadata_json"]
    assert metadata["index_publish_outcome"] == "published"
    assert metadata["stable_document_id"] == str(_STABLE_DOC_ID)
    assert metadata["index_run_id"] == str(bootstrap_result.index_run_id)
    assert metadata["attempt_ordinal"] == job["attempt_count"]
    assert metadata["usage_completeness"] == "complete"
    assert metadata["provider_call_attempted"] is True
    assert metadata["provider_succeeded"] is True
    assert metadata["batch_count"] == 1
    assert metadata["completed_batch_count"] == 1
    assert metadata["failed_batch_ordinal"] is None
    assert metadata["batches_truncated_count"] == 0
    assert metadata["batches"] == [
        {"ordinal": 1, "request_id": "req-1", "input_count": 1, "total_tokens": 123}
    ]


# ---------------------------------------------------------------------------
# 2/3. Provider failures carry partial / unavailable reports
# ---------------------------------------------------------------------------


async def test_partial_provider_failure_failed_event(worker_env) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider(
        error=ArticleRagIndexWorkerError(
            "fake partial embedding failure",
            retryable=True,
            failure_class="embedding",
            failure_code="embedding_backend_failed",
            embedding_usage_report=_attempted_report(
                provider_succeeded=False,
                usage_completeness="partial",
                input_tokens=100,
                total_tokens=100,
                batch_count=2,
                completed_batch_count=1,
                failed_batch_ordinal=2,
                batches=(
                    ArticleRagEmbeddingBatchUsage(
                        ordinal=1, request_id="req-1", input_count=10, total_tokens=100
                    ),
                ),
            ),
        )
    )
    service = _build_worker_service(worker_env, embedding_provider=provider)

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "retry_later"
    assert provider.call_count == 1

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "failed"
    assert event["error_code"] == "embedding_backend_failed"
    assert event["input_tokens"] == 100
    assert event["total_tokens"] == 100
    metadata = event["metadata_json"]
    assert metadata["usage_completeness"] == "partial"
    assert metadata["provider_succeeded"] is False
    assert metadata["failed_batch_ordinal"] == 2
    assert "index_publish_outcome" not in metadata
    # Original business semantics preserved: job retry_later.
    assert result.failure_code == "embedding_backend_failed"


async def test_first_batch_failure_unavailable_zero_tokens(worker_env) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider(
        error=ArticleRagIndexWorkerError(
            "fake first-batch failure",
            retryable=True,
            failure_class="embedding",
            failure_code="embedding_backend_failed",
            embedding_usage_report=_attempted_report(
                provider_succeeded=False,
                usage_completeness="unavailable",
                input_tokens=0,
                total_tokens=0,
                batch_count=1,
                completed_batch_count=0,
                failed_batch_ordinal=1,
                batches=(),
            ),
        )
    )
    service = _build_worker_service(worker_env, embedding_provider=provider)

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "retry_later"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "failed"
    assert event["input_tokens"] == 0
    assert event["total_tokens"] == 0
    assert event["metadata_json"]["usage_completeness"] == "unavailable"


# ---------------------------------------------------------------------------
# 4. Preflight / unconfigured / non-attempted -> zero events
# ---------------------------------------------------------------------------


async def test_unconfigured_provider_zero_events(worker_env) -> None:
    from app.services.reader_orchestration.article_rag_index_worker import (
        UnconfiguredArticleRagEmbeddingProvider,
    )

    await _bootstrap(worker_env)
    service = _build_worker_service(
        worker_env,
        embedding_provider=UnconfiguredArticleRagEmbeddingProvider(),
    )

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "failed_terminal"
    assert await _fetch_usage_events(worker_env) == []


async def test_non_attempted_fake_provider_zero_events(worker_env) -> None:
    # FakeArticleRagEmbeddingProvider reports attempted=False (no real
    # provider call) — the worker must skip the usage event entirely.
    from app.services.reader_orchestration.article_rag_index_worker import (
        FakeArticleRagEmbeddingProvider,
    )

    await _bootstrap(worker_env)
    provider = FakeArticleRagEmbeddingProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"
    assert provider.call_count == 1
    assert await _fetch_usage_events(worker_env) == []


async def test_pre_provider_contract_failure_zero_events(worker_env) -> None:
    # Collection mismatch fails closed BEFORE the provider call.
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = ArticleRagIndexWorkerService(
        pool=worker_env,
        embedding_provider=provider,
        vector_writer=FakeArticleRagVectorWriter(),
        default_vector_collection="wrong-collection",
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "failed_terminal"
    assert provider.call_count == 0
    assert await _fetch_usage_events(worker_env) == []


# ---------------------------------------------------------------------------
# 5. Provider success + local validation failure -> abandoned_after_embedding
# ---------------------------------------------------------------------------


async def test_worker_coverage_mismatch_abandoned_after_embedding(
    worker_env,
) -> None:
    await _bootstrap(worker_env)
    # Provider succeeds with report, but returns too few embeddings ->
    # worker coverage validation fails AFTER the usage event was written.
    provider = _UsageFakeProvider(embeddings_count_override=0)
    service = _build_worker_service(worker_env, embedding_provider=provider)

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.failure_code == "embedding_failed"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "succeeded"
    assert event["error_code"] is None
    assert event["metadata_json"]["index_publish_outcome"] == (
        "abandoned_after_embedding"
    )


async def test_adapter_style_error_with_succeeded_report_abandoned(
    worker_env,
) -> None:
    await _bootstrap(worker_env)
    # Adapter-level coverage error carrying a provider_succeeded=True
    # report (the error was raised by the adapter AFTER the provider
    # returned successfully).
    provider = _UsageFakeProvider(
        error=ArticleRagIndexWorkerError(
            "fake adapter coverage mismatch",
            retryable=False,
            failure_class="embedding_coverage",
            failure_code="embedding_coverage_mismatch",
            embedding_usage_report=_attempted_report(),
        )
    )
    service = _build_worker_service(worker_env, embedding_provider=provider)

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "failed_terminal"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "succeeded"
    assert event["input_tokens"] == 123
    assert event["metadata_json"]["index_publish_outcome"] == (
        "abandoned_after_embedding"
    )


# ---------------------------------------------------------------------------
# 6. Fence supersede after embedding
# ---------------------------------------------------------------------------


async def test_fence_supersede_after_embedding(worker_env) -> None:
    bootstrap_result = await _bootstrap(worker_env)
    provider = _UsageFakeProvider(
        mutate_during_embedding=_bump_generation,
        pool=worker_env,
    )
    service = _build_worker_service(worker_env, embedding_provider=provider)

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "superseded"
    assert provider.call_count == 1

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "succeeded"
    assert event["metadata_json"]["index_publish_outcome"] == (
        "superseded_after_embedding"
    )
    # Index run superseded — original state machine preserved.
    row = await worker_env.fetchrow(
        "SELECT status FROM reader_article_rag_index_runs WHERE id = $1",
        bootstrap_result.index_run_id,
    )
    assert row["status"] == "superseded"


# ---------------------------------------------------------------------------
# 7. Zilliz failures -> publish_failed, business semantics unchanged
# ---------------------------------------------------------------------------


async def test_zilliz_retryable_failure_publish_failed(worker_env) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    writer = _RetryableVectorWriter()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=writer,
    )

    result = await _run_worker(service)
    assert result is not None
    assert result.status == "retry_later"
    assert result.failure_code == "vector_write_failed"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "succeeded"
    assert event["metadata_json"]["index_publish_outcome"] == "publish_failed"


async def test_zilliz_count_mismatch_publish_failed(worker_env) -> None:
    from app.services.reader_orchestration.article_rag_index_worker import (
        ArticleRagVectorWriteResult,
    )

    await _bootstrap(worker_env)

    class _PartialWriter:
        def __init__(self) -> None:
            self.call_count = 0

        async def upsert_chunks(self, *, collection, chunks_with_embeddings, metadata):
            self.call_count += 1
            return ArticleRagVectorWriteResult(
                collection=collection,
                upserted_count=0,
                provider_metadata={"provider": "partial_fake"},
            )

    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=_PartialWriter(),
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "retry_later"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    assert events[0]["metadata_json"]["index_publish_outcome"] == "publish_failed"


# ---------------------------------------------------------------------------
# 8. Finalization exception -> exception propagates, event stays pending
# ---------------------------------------------------------------------------


async def test_finalization_exception_keeps_pending(worker_env) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    async def _boom(**kwargs: Any):
        raise RuntimeError("finalization DB failure")

    service._mark_indexed_and_succeed = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="finalization DB failure"):
        await _run_worker(service)

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "succeeded"
    assert event["metadata_json"]["index_publish_outcome"] == "pending"


# ---------------------------------------------------------------------------
# 9/10. Observability failures never affect business outcomes
# ---------------------------------------------------------------------------


async def test_recorder_persist_failed_business_still_succeeds(
    worker_env, monkeypatch,
) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    async def _persist_failed(*args: Any, **kwargs: Any):
        return None, "persist_failed"

    monkeypatch.setattr(
        "app.services.reader_orchestration.article_rag_index_worker"
        ".record_invocation_keyed_usage_event",
        _persist_failed,
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"
    assert provider.call_count == 1
    assert await _fetch_usage_events(worker_env) == []


async def test_recorder_raises_business_still_succeeds(
    worker_env, monkeypatch,
) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    async def _explodes(*args: Any, **kwargs: Any):
        raise RuntimeError("SECRET-RECORDER-ERROR-DO-NOT-LOG")

    monkeypatch.setattr(
        "app.services.reader_orchestration.article_rag_index_worker"
        ".record_invocation_keyed_usage_event",
        _explodes,
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"
    assert provider.call_count == 1
    assert await _fetch_usage_events(worker_env) == []


async def test_patch_failure_business_still_succeeds(
    worker_env, monkeypatch,
) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    async def _patch_fails(*args: Any, **kwargs: Any):
        raise RuntimeError("SECRET-PATCH-ERROR-DO-NOT-LOG")

    monkeypatch.setattr(
        "app.services.reader_orchestration.article_rag_index_worker"
        ".update_ai_usage_event_metadata",
        _patch_fails,
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"

    # Event persisted; outcome stays pending because the patch failed.
    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    assert events[0]["metadata_json"]["index_publish_outcome"] == "pending"


async def test_patch_false_business_still_succeeds(
    worker_env, monkeypatch, caplog,
) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    async def _patch_returns_false(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(
        "app.services.reader_orchestration.article_rag_index_worker"
        ".update_ai_usage_event_metadata",
        _patch_returns_false,
    )
    caplog.set_level(logging.ERROR)
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"
    assert provider.call_count == 1

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "succeeded"
    assert event["metadata_json"]["index_publish_outcome"] == "pending"

    patch_logs = [
        record
        for record in caplog.records
        if record.getMessage().startswith("rag_embedding_outcome_patch_failed ")
    ]
    assert len(patch_logs) == 1
    assert patch_logs[0].levelno == logging.ERROR
    log_message = patch_logs[0].getMessage()
    assert "operation=" in log_message
    assert f"invocation_key={event['invocation_key']}" in log_message
    assert f"event_id={event['id']}" in log_message
    assert "error_category=metadata_patch_returned_false" in log_message
    assert "SECRET-FALSE-ERROR-DO-NOT-LOG" not in log_message
    assert "Traceback" not in log_message
    assert patch_logs[0].exc_info is None
    assert patch_logs[0].exc_text is None


# ---------------------------------------------------------------------------
# 11. Indexed noop: zero provider calls, zero events
# ---------------------------------------------------------------------------


async def test_indexed_noop_no_extra_provider_call_or_event(worker_env) -> None:
    bootstrap_result = await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )

    first = await _run_worker(service)
    assert first is not None
    assert first.status == "succeeded"
    assert provider.call_count == 1
    assert len(await _fetch_usage_events(worker_env)) == 1

    # Simulate lease-expiry recovery: job back to queued, index_run stays
    # indexed -> second claim is the idempotent no-op path.
    await _reset_job_to_queued(worker_env, job_id=bootstrap_result.job_id)
    provider.call_count = 0
    second = await _run_worker(service)
    assert second is not None
    assert second.status == "succeeded"
    assert second.idempotent_noop is True
    assert provider.call_count == 0
    # No new usage event on the no-op path.
    assert len(await _fetch_usage_events(worker_env)) == 1


# ---------------------------------------------------------------------------
# 12. Replay / conflict dispositions
# ---------------------------------------------------------------------------


async def test_conflict_never_patches_existing_row(worker_env) -> None:
    bootstrap_result = await _bootstrap(worker_env)
    job = await _fetch_job(worker_env, job_id=bootstrap_result.job_id)
    # The queued job still has attempt_count=N; the NEXT claim bumps it
    # to N+1, so the pre-seeded conflict row must use the next claim
    # ordinal to collide with the worker's invocation key.
    invocation_key = (
        f"reader:rag_embedding:{bootstrap_result.job_id}:"
        f"{job['attempt_count'] + 1}:1"
    )

    # Pre-insert a DIFFERENT observation under the same invocation key.
    stale_event = AIUsageEventCreate(
        usage_scope="system_internal",
        capability_code="rag_embedding",
        billing_mode="internal_only",
        status="succeeded",
        user_id=_USER_ID,
        reading_record_id=_RECORD_ID,
        usage_data={"input_tokens": 999, "output_tokens": 0},
        model_route="rag_embedding",
        model_provider="dashscope",
        model_name="wrong-model",
        metadata_json={},
    )
    stale_id, stale_disposition = await record_invocation_keyed_usage_event(
        stale_event,
        invocation_key=invocation_key,
        observation_hash="0" * 64,
        pool=worker_env,
    )
    assert stale_disposition == "inserted"

    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    event = events[0]
    # The winner (stale) row is untouched: no outcome patch, no token
    # overwrite, no model overwrite.
    assert event["id"] == stale_id
    assert event["input_tokens"] == 999
    assert event["model_name"] == "wrong-model"
    assert "index_publish_outcome" not in event["metadata_json"]


async def test_replayed_disposition_still_patches(worker_env, monkeypatch) -> None:
    bootstrap_result = await _bootstrap(worker_env)
    job = await _fetch_job(worker_env, job_id=bootstrap_result.job_id)
    # Same real conflict identity as the conflict test: the next claim
    # uses attempt_count N+1.
    invocation_key = (
        f"reader:rag_embedding:{bootstrap_result.job_id}:"
        f"{job['attempt_count'] + 1}:1"
    )

    # Seed a REAL row to patch (id irrelevant — recorder is mocked).
    seed_event = AIUsageEventCreate(
        usage_scope="system_internal",
        capability_code="rag_embedding",
        billing_mode="internal_only",
        status="succeeded",
        user_id=_USER_ID,
        reading_record_id=_RECORD_ID,
        usage_data={"input_tokens": 1, "output_tokens": 0},
        model_route="rag_embedding",
        model_provider="dashscope",
        model_name=_MODEL,
        metadata_json={},
    )
    seed_id, _ = await record_invocation_keyed_usage_event(
        seed_event,
        invocation_key=invocation_key,
        observation_hash="f" * 64,
        pool=worker_env,
    )

    async def _replayed(event, *, invocation_key, observation_hash, pool=None):
        return seed_id, "replayed"

    monkeypatch.setattr(
        "app.services.reader_orchestration.article_rag_index_worker"
        ".record_invocation_keyed_usage_event",
        _replayed,
    )
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    assert events[0]["id"] == seed_id
    # Replayed rows ARE eligible for the outcome patch.
    assert events[0]["metadata_json"]["index_publish_outcome"] == "published"


# ---------------------------------------------------------------------------
# 13. Metadata safety
# ---------------------------------------------------------------------------


async def test_metadata_contains_no_raw_payload(worker_env) -> None:
    paragraph_text = "Indexable paragraph for happy path."
    await _seed_paragraph_environment(worker_env, paragraph_text=paragraph_text)
    await _build_bootstrap_service(worker_env).bootstrap_article_rag_index(
        reading_record_id=_RECORD_ID, user_id=_USER_ID,
    )
    provider = _UsageFakeProvider()
    service = _build_worker_service(
        worker_env, embedding_provider=provider, vector_writer=FakeArticleRagVectorWriter(),
    )
    result = await _run_worker(service)
    assert result is not None
    assert result.status == "succeeded"

    events = await _fetch_usage_events(worker_env)
    assert len(events) == 1
    metadata = events[0]["metadata_json"]
    serialized = str(metadata)
    # Precise sensitive fields/values only: source text, vector values,
    # input_chars, raw provider usage markers, SDK message/body, keys
    # and URIs. The plain word "embedding" is deliberately NOT forbidden
    # (safe schema/capability names legitimately contain it).
    for forbidden in (
        paragraph_text,
        "input_chars",
        "vector",
        "api_key",
        "message",
        "http",
    ):
        assert forbidden not in serialized
    batch = metadata["batches"][0]
    assert set(batch.keys()) == {"ordinal", "request_id", "input_count", "total_tokens"}


# ---------------------------------------------------------------------------
# 14. attempt_count invalid -> fail-closed before provider call
# ---------------------------------------------------------------------------


async def test_attempt_count_invalid_fail_closed(worker_env) -> None:
    await _bootstrap(worker_env)
    provider = _UsageFakeProvider()
    service = _build_worker_service(worker_env, embedding_provider=provider)

    from app.services.reader_orchestration.job_runtime import ClaimResult

    fake_claim = ClaimResult(
        job_id=UUID(int=1),
        run_id=UUID(int=2),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        base_id=None,
        job_type="article_rag_index_build",
        target_type="stable_document",
        target_key=str(_STABLE_DOC_ID),
        expected_generation=1,
        operation_fingerprint="article_rag_index_build_v1",
        attempt_count=0,
        lease_owner="test",
        lease_token=UUID(int=3),
        lease_expires_at=None,
    )
    # Frozen behaviour: a non-int / bool / <1 attempt_count never reaches
    # _load_job_context or the provider — a fixed safe RuntimeError is
    # raised instead (no new business failure code is frozen for it).
    with pytest.raises(RuntimeError, match="attempt_count"):
        await service._process_claimed_job(
            claim=fake_claim, retry_delay=_RETRY_DELAY,
        )
    assert provider.call_count == 0
    assert await _fetch_usage_events(worker_env) == []
