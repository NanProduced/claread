"""T5.7 — semantic outline production readiness (controlled real path).

Covers: unconfigured default generator, permanent vs transient errors,
explicit eligibility + fake real-chain, bounded input.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    EnhancementJobBootstrapService,
    allow_semantic_outline_request_eligibility,
    default_semantic_outline_request_eligibility,
)
from app.services.reader_orchestration.semantic_outline_publisher import (
    SemanticOutlineCandidateNode,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    OUTLINE_MAX_ATTEMPTED_NODES,
    FakeSemanticOutlineGenerator,
    SemanticOutlineGenerationError,
    SemanticOutlineWorkerService,
    UnconfiguredSemanticOutlineGenerator,
    build_bounded_worker_input,
    clamp_candidates,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_0020_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0020_reader_semantic_outline_layer.sql"
).read_text(encoding="utf-8")
OUTLINE_SCHEMA_SQL = BASELINE_SQL + "\n" + MIGRATION_0020_SQL


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def outline_env() -> asyncpg.Pool:
    schema_name = f"test_reader_semantic_outline_t57_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(OUTLINE_SCHEMA_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            db_connection.DB_POOL = original_pool
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


def test_t57_default_eligibility_false() -> None:
    class _S:
        readiness_state = "article_ready"

    assert default_semantic_outline_request_eligibility(_S()) is False  # type: ignore[arg-type]
    assert allow_semantic_outline_request_eligibility(_S()) is True  # type: ignore[arg-type]


def test_t57_bounded_input_no_unbounded_fulltext() -> None:
    rows = [
        {
            "unit_id": f"u{i}",
            "order_index": i,
            "unit_type": "body",
            "unit_text": "WORD " * 200,
        }
        for i in range(1, 50)
    ]
    built = build_bounded_worker_input(
        base_id="b",
        generation=1,
        unit_rows=rows,
        max_unit_preview_chars=40,
        max_total_preview_chars=300,
    )
    assert built.total_preview_chars <= 300
    assert len(built.units) == 49
    assert all(len(u.preview) <= 40 for u in built.units)


def test_t57_clamp_candidates() -> None:
    nodes = tuple(
        SemanticOutlineCandidateNode(
            candidate_ref=f"c{i}",
            parent_candidate_ref=None,
            depth=1,
            title=f"t{i}",
            start_unit_id="u1",
            end_unit_id="u1",
        )
        for i in range(OUTLINE_MAX_ATTEMPTED_NODES + 3)
    )
    assert len(clamp_candidates(nodes)) == OUTLINE_MAX_ATTEMPTED_NODES


async def test_t57_default_generator_unconfigured_permanent_fail(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Unconfigured path."
    )
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=allow_semantic_outline_request_eligibility,
    )
    boot = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id, user_id=user_id
    )
    assert boot is not None
    run_id = boot.run_id

    worker = SemanticOutlineWorkerService(pool=outline_env)
    assert isinstance(worker._generator, UnconfiguredSemanticOutlineGenerator)
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-unconfigured",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "semantic_outline_generator_unconfigured"
    async with outline_env.acquire() as conn:
        job = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, failure_message, run_id
            FROM reader_jobs
            WHERE job_type = $1
            """,
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
        run = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, finished_at
            FROM reader_runs
            WHERE id = $1
            """,
            run_id,
        )
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            article.record_id,
        )
        events = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
              AND payload_json->>'layer_type' = 'semantic_outline'
            """,
            article.record_id,
        )
    assert job is not None and run is not None
    assert job["status"] == "failed_terminal"
    assert job["failure_class"] == "configuration"
    assert job["failure_code"] == "semantic_outline_generator_unconfigured"
    assert job["failure_message"]
    assert run["status"] == "failed_terminal"
    assert run["finished_at"] is not None
    assert run["failure_class"] == "configuration"
    assert run["failure_code"] == "semantic_outline_generator_unconfigured"
    assert int(layers) == 0
    assert int(events) == 0


async def test_t57_permanent_error_no_retry(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Permanent."
    )
    boot = await EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=allow_semantic_outline_request_eligibility,
    ).bootstrap_semantic_outline_job(
        record_id=article.record_id, user_id=user_id
    )
    assert boot is not None

    class _Permanent:
        async def generate(self, context):
            raise SemanticOutlineGenerationError(
                "bad request",
                failure_class="configuration",
                failure_code="provider_4xx",
                retryable=False,
            )

    worker = SemanticOutlineWorkerService(
        pool=outline_env, generator=_Permanent()  # type: ignore[arg-type]
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-perm",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "provider_4xx"
    async with outline_env.acquire() as conn:
        job = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, failure_message
            FROM reader_jobs WHERE job_type = $1
            """,
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
        run = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, finished_at
            FROM reader_runs WHERE id = $1
            """,
            boot.run_id,
        )
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            article.record_id,
        )
        events = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'layer_published'
              AND payload_json->>'layer_type' = 'semantic_outline'
            """,
            article.record_id,
        )
    assert job is not None and run is not None
    assert job["status"] == "failed_terminal"
    assert job["failure_class"] == "configuration"
    assert job["failure_code"] == "provider_4xx"
    assert "bad request" in str(job["failure_message"])
    assert run["status"] == "failed_terminal"
    assert run["finished_at"] is not None
    assert run["failure_class"] == "configuration"
    assert run["failure_code"] == "provider_4xx"
    assert int(layers) == 0
    assert int(events) == 0


async def test_t57_transient_error_retry_later(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Transient."
    )
    boot = await EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=allow_semantic_outline_request_eligibility,
    ).bootstrap_semantic_outline_job(
        record_id=article.record_id, user_id=user_id
    )
    assert boot is not None

    class _Transient:
        async def generate(self, context):
            raise SemanticOutlineGenerationError(
                "timeout",
                failure_class="provider",
                failure_code="provider_timeout",
                retryable=True,
            )

    worker = SemanticOutlineWorkerService(
        pool=outline_env, generator=_Transient()  # type: ignore[arg-type]
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-trans",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "retry_later"
    assert result.error_code == "provider_timeout"
    async with outline_env.acquire() as conn:
        job = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, failure_message
            FROM reader_jobs WHERE job_type = $1
            """,
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
        run = await conn.fetchrow(
            """
            SELECT status, failure_class, failure_code, finished_at
            FROM reader_runs WHERE id = $1
            """,
            boot.run_id,
        )
    assert job is not None and run is not None
    assert job["status"] == "retry_later"
    assert job["failure_class"] == "provider"
    assert job["failure_code"] == "provider_timeout"
    assert "timeout" in str(job["failure_message"])
    # Run stays open for retry; must not be terminalized early.
    assert run["status"] == "running"
    assert run["finished_at"] is None


async def test_t57_explicit_eligibility_fake_chain_publishes(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="Section one.\n\nSection two with more text.",
    )
    boot = await EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=allow_semantic_outline_request_eligibility,
    ).bootstrap_semantic_outline_job(
        record_id=article.record_id, user_id=user_id
    )
    assert boot is not None
    async with outline_env.acquire() as conn:
        uid = await conn.fetchval(
            """
            SELECT unit_id FROM reading_units
            WHERE reading_record_id = $1
            ORDER BY order_index
            LIMIT 1
            """,
            article.record_id,
        )
    uid = str(uid)
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(
            (
                SemanticOutlineCandidateNode(
                    candidate_ref="c1",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Intro",
                    start_unit_id=uid,
                    end_unit_id=uid,
                ),
            )
        ),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-explicit",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    snapshot = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    assert snapshot.semantic_outline is not None
    assert snapshot.semantic_outline.status in {"ready", "partial"}
    assert snapshot.navigation.units


async def test_t57_default_eligibility_creates_no_outline_job(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="No outline by default."
    )
    result = await EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=default_semantic_outline_request_eligibility,
    ).bootstrap_semantic_outline_job(
        record_id=article.record_id, user_id=user_id
    )
    assert result is None
    async with outline_env.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    assert int(count) == 0
