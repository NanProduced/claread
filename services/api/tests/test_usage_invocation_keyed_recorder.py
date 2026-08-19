"""OBS-01B-A: status-agnostic invocation-keyed usage recorder primitives.

Covers the two generic seams extracted from the OBS-01A failed-usage
recorder, WITHOUT any Article RAG wiring:

- ``record_invocation_keyed_usage_event``: status-agnostic helper that
  merges the observation metadata itself, resolves the pool, owns its
  transaction (advisory xact lock -> SELECT FOR UPDATE -> hash compare ->
  insert/replay/conflict), and never raises to the business caller.
- ``update_ai_usage_event_metadata``: metadata-only patch that must not
  touch status / error fields / token columns / invocation_key.

All PostgreSQL tests use the per-test isolated schema pattern from
``test_agent_run_usage_snapshot.py`` (fresh schema + baseline DDL,
dropped on teardown). No real provider is ever called.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from app.database.json_compat import jsonb_param
from app.services.ai_usage.execution_diagnostics import current_execution
from app.services.ai_usage.service import (
    AIUsageEventCreate,
    compute_usage_invocation_observation_hash,
    record_invocation_keyed_usage_event,
    update_ai_usage_event_metadata,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Per-test isolated PostgreSQL schema (same pattern as OBS-01A suite)
# ---------------------------------------------------------------------------

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = re.sub(
    r"^\s*SET search_path = public, pg_catalog;\s*$",
    "",
    (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(encoding="utf-8"),
    flags=re.MULTILINE,
)


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://claread:claread_dev@127.0.0.1:5432/claread",
    )


@pytest.fixture
async def usage_pool() -> AsyncIterator[asyncpg.Pool]:
    schema_name = f"test_usage_keyed_recorder_{uuid4().hex}"
    try:
        admin = await asyncpg.connect(_database_url())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable for keyed recorder tests: {exc}")

    pool: asyncpg.Pool | None = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        pool = await asyncpg.create_pool(
            _database_url(),
            min_size=1,
            max_size=4,
            init=init_connection,
            server_settings={"search_path": f'"{schema_name}", public'},
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_USAGE = object()


def _event(
    *,
    status: str = "succeeded",
    usage_data: Any = _DEFAULT_USAGE,
    metadata_json: dict[str, Any] | None = None,
    input_tokens: int = 60,
) -> AIUsageEventCreate:
    if usage_data is _DEFAULT_USAGE:
        usage_data = {"input_tokens": input_tokens, "output_tokens": 0}
    return AIUsageEventCreate(
        usage_scope="system_internal",
        capability_code="rag_embedding",
        billing_mode="internal_only",
        status=status,
        usage_data=usage_data,
        model_route="rag_embedding",
        model_provider="dashscope",
        model_name="text-embedding-v4",
        metadata_json=dict(metadata_json or {}),
    )


def _observation_hash(
    event: AIUsageEventCreate,
    *,
    invocation_key: str,
    snapshot_fragment: dict[str, Any] | None = None,
) -> str:
    return compute_usage_invocation_observation_hash(
        invocation_key=invocation_key,
        event=event,
        snapshot_fragment=snapshot_fragment,
        execution_id=None,
        agent_run_id=None,
        attempt_ordinal=1,
    )


class _BarrierAcquire:
    """Async-context-manager proxy that waits on a barrier before entering."""

    def __init__(self, inner: Any, barrier: asyncio.Barrier) -> None:
        self._inner = inner
        self._barrier = barrier

    async def __aenter__(self) -> Any:
        await self._barrier.wait()
        return await self._inner.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._inner.__aexit__(*exc_info)


class _BarrierPool:
    """Pool wrapper forcing both callers past acquire() before either txn starts."""

    def __init__(self, pool: asyncpg.Pool, barrier: asyncio.Barrier) -> None:
        self._pool = pool
        self._barrier = barrier

    def acquire(self) -> _BarrierAcquire:
        return _BarrierAcquire(self._pool.acquire(), self._barrier)


class _BrokenPool:
    """Pool stub whose acquire() always raises (persist failure simulation)."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("simulated pool failure")
        self.acquire_calls = 0

    def acquire(self) -> Any:
        self.acquire_calls += 1
        raise self._exc


# High-visibility sentinel: if any persist-failure log path copies exception
# payload (message / traceback / args), this string MUST NOT appear in logs.
_SENTINEL_SECRET = "SECRET-DB-URI-DO-NOT-LOG"


class _SentinelPoolError(RuntimeError):
    """Pool failure whose message carries the do-not-log sentinel."""


# ---------------------------------------------------------------------------
# Generic recorder: status-agnostic persistence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recorder_persists_succeeded_event(
    usage_pool: asyncpg.Pool,
) -> None:
    # No execution correlation is active — the generic recorder must not
    # depend on ExecutionCorrelation / ContextVar state.
    assert current_execution() is None

    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event(status="succeeded")
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert disposition == "inserted"
    assert isinstance(event_id, UUID)

    row = await usage_pool.fetchrow(
        "SELECT status, capability_code, input_tokens, output_tokens,"
        " total_tokens, invocation_key, metadata_json"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert row["status"] == "succeeded"
    assert row["capability_code"] == "rag_embedding"
    assert row["input_tokens"] == 60
    assert row["output_tokens"] == 0
    assert row["total_tokens"] == 60
    assert row["invocation_key"] == invocation_key
    observation = row["metadata_json"]["usage_invocation_observation"]
    assert observation["schema_version"] == 1
    assert observation["sha256"] == observation_hash


@pytest.mark.anyio
async def test_recorder_persists_failed_event(
    usage_pool: asyncpg.Pool,
) -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event(status="failed", usage_data=None)
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert disposition == "inserted"

    row = await usage_pool.fetchrow(
        "SELECT status, input_tokens, output_tokens, total_tokens"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert row["status"] == "failed"
    # Zero-usage failed invocation: tokens stay zero (never fabricated).
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["total_tokens"] == 0


@pytest.mark.anyio
async def test_recorder_replay_same_observation_returns_same_row(
    usage_pool: asyncpg.Pool,
) -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    first_id, first_disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert first_disposition == "inserted"

    replay_id, replay_disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert replay_disposition == "replayed"
    assert replay_id == first_id

    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_recorder_conflict_different_observation_keeps_first_row(
    usage_pool: asyncpg.Pool,
) -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    first_event = _event(usage_data={"input_tokens": 60, "output_tokens": 0})
    first_hash = _observation_hash(first_event, invocation_key=invocation_key)
    conflicting_event = _event(usage_data={"input_tokens": 999, "output_tokens": 0})
    conflicting_hash = _observation_hash(conflicting_event, invocation_key=invocation_key)

    first_id, first_disposition = await record_invocation_keyed_usage_event(
        first_event,
        invocation_key=invocation_key,
        observation_hash=first_hash,
        pool=usage_pool,
    )
    assert first_disposition == "inserted"

    conflict_id, conflict_disposition = await record_invocation_keyed_usage_event(
        conflicting_event,
        invocation_key=invocation_key,
        observation_hash=conflicting_hash,
        pool=usage_pool,
    )
    assert conflict_disposition == "conflict"
    assert conflict_id == first_id

    row = await usage_pool.fetchrow(
        "SELECT input_tokens, metadata_json FROM ai_usage_events WHERE id = $1",
        first_id,
    )
    # Old row untouched.
    assert row["input_tokens"] == 60
    assert row["metadata_json"]["usage_invocation_observation"]["sha256"] == (first_hash)
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


async def _insert_row_with_metadata(
    pool: asyncpg.Pool,
    *,
    invocation_key: str,
    metadata_json: dict[str, Any],
) -> UUID:
    return await pool.fetchval(
        """
        INSERT INTO ai_usage_events (
            usage_scope, capability_code, billing_mode, status,
            invocation_key, metadata_json
        )
        VALUES ('system_internal', 'rag_embedding', 'internal_only',
                'succeeded', $1, $2::jsonb)
        RETURNING id
        """,
        invocation_key,
        jsonb_param(metadata_json),
    )


@pytest.mark.anyio
async def test_recorder_existing_row_without_observation_metadata_is_conflict(
    usage_pool: asyncpg.Pool,
) -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    existing_id = await _insert_row_with_metadata(
        usage_pool,
        invocation_key=invocation_key,
        metadata_json={"some_existing": "value"},
    )

    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)
    result_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert disposition == "conflict"
    assert result_id == existing_id

    row = await usage_pool.fetchrow(
        "SELECT metadata_json FROM ai_usage_events WHERE id = $1",
        existing_id,
    )
    # Row must stay untouched (no observation metadata injected).
    assert row["metadata_json"] == {"some_existing": "value"}
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_recorder_existing_row_with_invalid_observation_is_conflict(
    usage_pool: asyncpg.Pool,
) -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    # Observation metadata exists but has no usable sha256.
    existing_id = await _insert_row_with_metadata(
        usage_pool,
        invocation_key=invocation_key,
        metadata_json={"usage_invocation_observation": {"schema_version": 1}},
    )

    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)
    result_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert disposition == "conflict"
    assert result_id == existing_id

    row = await usage_pool.fetchrow(
        "SELECT metadata_json FROM ai_usage_events WHERE id = $1",
        existing_id,
    )
    assert row["metadata_json"]["usage_invocation_observation"] == {"schema_version": 1}
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_recorder_concurrent_same_observation_exactly_one_row(
    usage_pool: asyncpg.Pool,
) -> None:
    barrier = asyncio.Barrier(2)
    pool = _BarrierPool(usage_pool, barrier)
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    results = await asyncio.gather(
        record_invocation_keyed_usage_event(
            event,
            invocation_key=invocation_key,
            observation_hash=observation_hash,
            pool=pool,
        ),
        record_invocation_keyed_usage_event(
            event,
            invocation_key=invocation_key,
            observation_hash=observation_hash,
            pool=pool,
        ),
    )

    dispositions = sorted(d for _, d in results)
    assert dispositions == ["inserted", "replayed"]
    assert results[0][0] == results[1][0]
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_recorder_concurrent_different_observation_keeps_first_row(
    usage_pool: asyncpg.Pool,
) -> None:
    barrier = asyncio.Barrier(2)
    pool = _BarrierPool(usage_pool, barrier)
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    first_event = _event(usage_data={"input_tokens": 60, "output_tokens": 0})
    first_hash = _observation_hash(first_event, invocation_key=invocation_key)
    conflicting_event = _event(usage_data={"input_tokens": 999, "output_tokens": 0})
    conflicting_hash = _observation_hash(conflicting_event, invocation_key=invocation_key)

    results = await asyncio.gather(
        record_invocation_keyed_usage_event(
            first_event,
            invocation_key=invocation_key,
            observation_hash=first_hash,
            pool=pool,
        ),
        record_invocation_keyed_usage_event(
            conflicting_event,
            invocation_key=invocation_key,
            observation_hash=conflicting_hash,
            pool=pool,
        ),
    )

    dispositions = sorted(d for _, d in results)
    assert dispositions == ["conflict", "inserted"]
    inserted_id = next(i for i, d in results if d == "inserted")
    conflict_id = next(i for i, d in results if d == "conflict")
    assert conflict_id == inserted_id
    row = await usage_pool.fetchrow(
        "SELECT input_tokens FROM ai_usage_events WHERE id = $1", inserted_id
    )
    # Which observation wins the race is nondeterministic; the invariant is
    # that the FIRST inserted row survives untouched (60 or 999, not a mix).
    assert row["input_tokens"] in {60, 999}
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_recorder_pool_unavailable_returns_persist_failed(
    monkeypatch,
) -> None:
    from app.services.ai_usage import service as usage_service

    monkeypatch.setattr(usage_service.db_connection, "DB_POOL", None)
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=None,
    )
    assert event_id is None
    assert disposition == "persist_failed"


@pytest.mark.anyio
async def test_recorder_db_error_returns_persist_failed() -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    # A broken pool must never raise to the business caller.
    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=_BrokenPool(),  # type: ignore[arg-type]
    )
    assert event_id is None
    assert disposition == "persist_failed"


@pytest.mark.anyio
async def test_recorder_does_not_mutate_caller_event(
    usage_pool: asyncpg.Pool,
) -> None:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    caller_metadata: dict[str, Any] = {
        "keep": "me",
        "usage_invocation_observation": {
            "schema_version": 1,
            "sha256": "forged-by-caller",
        },
    }
    event = _event(metadata_json=caller_metadata)
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    # Deep copy taken BEFORE the helper call: after the call the event's
    # metadata must be completely identical (no in-place merge, no key
    # addition, no reordering side effects on values).
    metadata_before = copy.deepcopy(event.metadata_json)

    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=usage_pool,
    )
    assert disposition == "inserted"

    # The event object and the caller's metadata dict are both unmutated
    # (no observation merge in place), and the caller-forged sha256 must
    # NOT survive: the recorder's own merge is authoritative in the row.
    assert event.metadata_json == metadata_before
    assert caller_metadata == {
        "keep": "me",
        "usage_invocation_observation": {
            "schema_version": 1,
            "sha256": "forged-by-caller",
        },
    }
    row = await usage_pool.fetchrow(
        "SELECT metadata_json FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    stored = row["metadata_json"]
    assert stored["keep"] == "me"
    assert stored["usage_invocation_observation"]["sha256"] == (observation_hash)


@pytest.mark.anyio
async def test_recorder_uses_db_pool_fallback(
    usage_pool: asyncpg.Pool,
    monkeypatch,
) -> None:
    from app.services.ai_usage import service as usage_service

    monkeypatch.setattr(usage_service.db_connection, "DB_POOL", usage_pool)
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=None,
    )
    assert disposition == "inserted"
    assert isinstance(event_id, UUID)


# ---------------------------------------------------------------------------
# Metadata-only patch
# ---------------------------------------------------------------------------


async def _recorded_event(
    pool: asyncpg.Pool,
) -> tuple[UUID, str]:
    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event(status="succeeded")
    observation_hash = _observation_hash(event, invocation_key=invocation_key)
    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=pool,
    )
    assert disposition == "inserted"
    assert event_id is not None
    return event_id, invocation_key


@pytest.mark.anyio
async def test_metadata_patch_preserves_all_other_columns(
    usage_pool: asyncpg.Pool,
) -> None:
    event_id, invocation_key = await _recorded_event(usage_pool)

    before = await usage_pool.fetchrow(
        "SELECT status, error_code, error_message, input_tokens,"
        " output_tokens, total_tokens, invocation_key, metadata_json"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )

    patched = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch={"index_publish_outcome": "published"},
        pool=usage_pool,
    )
    assert patched is True

    after = await usage_pool.fetchrow(
        "SELECT status, error_code, error_message, input_tokens,"
        " output_tokens, total_tokens, invocation_key, metadata_json"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    # Metadata-only: every non-metadata column stays identical.
    assert after["status"] == before["status"] == "succeeded"
    assert after["error_code"] == before["error_code"]
    assert after["error_message"] == before["error_message"]
    assert after["input_tokens"] == before["input_tokens"] == 60
    assert after["output_tokens"] == before["output_tokens"]
    assert after["total_tokens"] == before["total_tokens"]
    assert after["invocation_key"] == before["invocation_key"] == (invocation_key)
    # Original metadata keys preserved; new key merged.
    after_meta = after["metadata_json"]
    for key, value in before["metadata_json"].items():
        assert after_meta[key] == value
    assert after_meta["index_publish_outcome"] == "published"


@pytest.mark.anyio
async def test_metadata_patch_replay_is_stable(
    usage_pool: asyncpg.Pool,
) -> None:
    event_id, _ = await _recorded_event(usage_pool)

    first = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch={"index_publish_outcome": "published"},
        pool=usage_pool,
    )
    second = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch={"index_publish_outcome": "published"},
        pool=usage_pool,
    )
    assert first is True
    assert second is True

    metadata = await usage_pool.fetchval(
        "SELECT metadata_json FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    assert metadata["index_publish_outcome"] == "published"
    count = await usage_pool.fetchval("SELECT count(*) FROM ai_usage_events")
    assert count == 1


@pytest.mark.anyio
async def test_metadata_patch_missing_event_returns_false(
    usage_pool: asyncpg.Pool,
) -> None:
    patched = await update_ai_usage_event_metadata(
        uuid4(),
        metadata_patch={"index_publish_outcome": "published"},
        pool=usage_pool,
    )
    assert patched is False


@pytest.mark.anyio
async def test_metadata_patch_pool_unavailable_returns_false(
    monkeypatch,
) -> None:
    from app.services.ai_usage import service as usage_service

    monkeypatch.setattr(usage_service.db_connection, "DB_POOL", None)
    patched = await update_ai_usage_event_metadata(
        uuid4(),
        metadata_patch={"index_publish_outcome": "published"},
        pool=None,
    )
    assert patched is False


@pytest.mark.anyio
async def test_metadata_patch_db_error_returns_false() -> None:
    # A broken pool must never raise to the business caller.
    patched = await update_ai_usage_event_metadata(
        uuid4(),
        metadata_patch={"index_publish_outcome": "published"},
        pool=_BrokenPool(),  # type: ignore[arg-type]
    )
    assert patched is False


@pytest.mark.anyio
async def test_metadata_patch_does_not_mutate_patch_dict(
    usage_pool: asyncpg.Pool,
) -> None:
    event_id, _ = await _recorded_event(usage_pool)
    patch: dict[str, Any] = {"index_publish_outcome": "published"}

    patched = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch=patch,
        pool=usage_pool,
    )
    assert patched is True
    assert patch == {"index_publish_outcome": "published"}


@pytest.mark.anyio
async def test_metadata_patch_uses_db_pool_fallback(
    usage_pool: asyncpg.Pool,
    monkeypatch,
) -> None:
    from app.services.ai_usage import service as usage_service

    monkeypatch.setattr(usage_service.db_connection, "DB_POOL", usage_pool)
    event_id, _ = await _recorded_event(usage_pool)

    patched = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch={"index_publish_outcome": "published"},
        pool=None,
    )
    assert patched is True


# ---------------------------------------------------------------------------
# R1: observation identity must not be overwritable via metadata patch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metadata_patch_rejects_reserved_observation_key(
    usage_pool: asyncpg.Pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.services.ai_usage.service")
    event_id, invocation_key = await _recorded_event(usage_pool)

    before = await usage_pool.fetchrow(
        "SELECT status, error_code, error_message, input_tokens,"
        " output_tokens, total_tokens, invocation_key, metadata_json"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )

    # A patch that tries to overwrite the frozen observation identity.
    patch: dict[str, Any] = {
        "usage_invocation_observation": {
            "schema_version": 999,
            "sha256": "forged",
        },
        "index_publish_outcome": "published",
    }
    patched = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch=patch,
        pool=usage_pool,
    )
    assert patched is False
    # The caller's patch dict is not modified.
    assert patch == {
        "usage_invocation_observation": {
            "schema_version": 999,
            "sha256": "forged",
        },
        "index_publish_outcome": "published",
    }

    after = await usage_pool.fetchrow(
        "SELECT status, error_code, error_message, input_tokens,"
        " output_tokens, total_tokens, invocation_key, metadata_json"
        " FROM ai_usage_events WHERE id = $1",
        event_id,
    )
    # The row is completely untouched: original observation identity,
    # other metadata, status, tokens and invocation_key all preserved
    # (no partial application of the non-reserved keys either).
    assert after["status"] == before["status"] == "succeeded"
    assert after["error_code"] == before["error_code"]
    assert after["error_message"] == before["error_message"]
    assert after["input_tokens"] == before["input_tokens"] == 60
    assert after["output_tokens"] == before["output_tokens"]
    assert after["total_tokens"] == before["total_tokens"]
    assert after["invocation_key"] == before["invocation_key"] == (invocation_key)
    assert after["metadata_json"] == before["metadata_json"]
    observation = after["metadata_json"]["usage_invocation_observation"]
    assert observation["schema_version"] == 1
    assert observation["sha256"] != "forged"

    # Rejection is logged as a fixed warning with no payload; no
    # persist-failure ERROR record may appear (the DB is never touched).
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records == []


# ---------------------------------------------------------------------------
# R1: persist-failure logs must never carry exception payload
# ---------------------------------------------------------------------------


def _assert_no_sentinel_leak(
    caplog: pytest.LogCaptureFixture,
    *,
    expect_error_category: str,
) -> None:
    assert caplog.records, "expected at least one log record"
    for record in caplog.records:
        message = record.getMessage()
        assert _SENTINEL_SECRET not in message
        # No exception info / traceback may be attached anywhere.
        assert record.exc_info is None
        assert record.exc_text is None
        assert record.stack_info is None
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "expected a persist-failure ERROR record"
    # The exception category (class name) IS allowed and expected.
    assert any(expect_error_category in r.getMessage() for r in error_records)


@pytest.mark.anyio
async def test_recorder_broken_pool_logs_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.services.ai_usage.service")
    broken = _BrokenPool(_SentinelPoolError(_SENTINEL_SECRET))

    invocation_key = f"reader:rag_embedding:{uuid4()}:1:1"
    event = _event()
    observation_hash = _observation_hash(event, invocation_key=invocation_key)

    # The sentinel-carrying exception must not escape to the caller.
    event_id, disposition = await record_invocation_keyed_usage_event(
        event,
        invocation_key=invocation_key,
        observation_hash=observation_hash,
        pool=broken,  # type: ignore[arg-type]
    )
    assert event_id is None
    assert disposition == "persist_failed"

    _assert_no_sentinel_leak(caplog, expect_error_category="_SentinelPoolError")
    # Allowed identity fields are still present for ops triage.
    assert any(invocation_key in r.getMessage() for r in caplog.records)
    assert any("rag_embedding" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_metadata_patch_broken_pool_logs_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.services.ai_usage.service")
    broken = _BrokenPool(_SentinelPoolError(_SENTINEL_SECRET))

    event_id = uuid4()
    patched = await update_ai_usage_event_metadata(
        event_id,
        metadata_patch={"index_publish_outcome": "published"},
        pool=broken,  # type: ignore[arg-type]
    )
    assert patched is False

    _assert_no_sentinel_leak(caplog, expect_error_category="_SentinelPoolError")
    assert any(str(event_id) in r.getMessage() for r in caplog.records)
