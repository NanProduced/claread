"""T5.8c — opt-in real-LLM smoke harness for semantic outline.

Skipped by default. Runs only when ALL three gates are open:

    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_REAL_LLM_MODEL=<model> \
        uv run pytest tests/test_reader_semantic_outline_t58c_real_llm.py \
            -m real_llm -v

Contract (see ``C:/tmp/TMP-t5.8c-r0-semantic-outline-opt-in-eval-gate-2026-07-18.md``
§2.1–§2.4):

1. Test carries ONLY ``@pytest.mark.real_llm``.
2. Default pytest / CI / non-``-m real_llm`` runs skip without constructing
   the model and without any outbound call (enforced by ``conftest.py``
   triple gate + ``fail_on_real_llm_attempts`` autouse fixture).
3. Before any outbound call, the test itself:
   - resolves the semantic-outline route/profile via ``build_model_for_route``;
   - requires ``CLAREAD_ALLOW_REAL_LLM_TESTS=1``;
   - requires ``CLAREAD_REAL_LLM_MODEL`` non-empty;
   - compares the resolved ``model_config.model_name`` against the env
     allowlisted model EXACTLY; mismatch → ``pytest.fail`` (fail-closed,
     zero provider call). NOT ``pytest.skip``.
4. Real adapter is DI-injected (``PydanticAISemanticOutlineGenerator``);
   production default stays ``UnconfiguredSemanticOutlineGenerator``.
5. Isolated, deletable test record/base/units fixture (per-test schema);
   input covers heading + body + at least one valid L2 candidate; obeys
   ``OUTLINE_MAX_UNIT_PREVIEW_CHARS=160`` / ``OUTLINE_MAX_TOTAL_PREVIEW_CHARS=8000``
   / ``OUTLINE_MAX_UNITS_FOR_PREVIEW=200``.
6. Single smoke = at most one provider call (policy
   ``DEFAULT_MAX_PROVIDER_CALLS_PER_JOB=1`` + PydanticAI ``output retries=0``
   + single ``generate`` path). No repair, no output retry, no rerun.
7. Success-path acceptance: job/run terminal state; published
   ``semantic_outline`` layer matches base/generation/source fence;
   snapshot top-level ``semantic_outline`` appears only when ready|partial
   and trusted; ``navigation.units`` unchanged before/after; healthy DB
   has one auditable usage event.
8. Invalid-output / timeout / usage-writer-failure paths are NOT executed
   in this round. They are covered by the existing T5.8b DB/unit seam
   (``test_reader_semantic_outline_t58b_adapter.py``) and referenced here
   only as documentation.
9. Never logs API key, endpoint, full prompt, or full provider payload.
   Smoke report fields: job_id, run_id, model_name, status, node_count,
   usage aggregate totals, functional_verdict, usage_audit_verdict.
10. Usage-writer failure is reported as ``observability_inconclusive``;
    never called a complete pass.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.llm.call_guard import real_llm_tests_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
from app.schemas.reader_orchestration import ReaderPlateSnapshot
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    EnhancementJobBootstrapService,
    allow_semantic_outline_request_eligibility,
)
from app.services.reader_orchestration.semantic_outline_execution_policy import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_PROVIDER_CALLS_PER_JOB,
    SemanticOutlineExecutionPolicy,
)
from app.services.reader_orchestration.semantic_outline_executor import (
    PydanticAISemanticOutlineGenerator,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    OUTLINE_MAX_TOTAL_PREVIEW_CHARS,
    OUTLINE_MAX_UNIT_PREVIEW_CHARS,
    OUTLINE_MAX_UNITS_FOR_PREVIEW,
    SemanticOutlineWorkerService,
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

_REAL_LLM_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"
_ALLOW_REAL_LLM_TESTS_ENV = "CLAREAD_ALLOW_REAL_LLM_TESTS"

# Smoke input: heading + body + at least one valid L2 candidate.
# Total chars ~700 — well under OUTLINE_MAX_TOTAL_PREVIEW_CHARS=8000.
# Each paragraph is under OUTLINE_MAX_UNIT_PREVIEW_CHARS=160 once segmented.
_SMOKE_TITLE = "Local Budget Hearings"
_SMOKE_PLAIN_TEXT = (
    "City Budget Hearings Wrap Up\n\n"
    "The city council held three days of budget hearings this week, "
    "focusing on transportation, parks, and public safety. Each session "
    "drew different audiences: business owners came for transportation, "
    "parents for parks, and neighborhood groups for public safety.\n\n"
    "Parks and Recreation\n\n"
    "The parks department requested funding for three playground "
    "renovations and a new splash pad. Council members questioned "
    "whether the renovations could be phased over two years to smooth "
    "the capital budget.\n\n"
    "Public Safety\n\n"
    "The police chief presented crime statistics showing a drop in "
    "property crimes but a rise in cyber-related offenses. She asked "
    "for additional digital forensics staff and a dedicated cyber "
    "outreach coordinator."
)

_TRUSTED_ENVELOPE_STATUSES = frozenset({"ready", "partial"})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def outline_env() -> asyncpg.Pool:
    """Per-test isolated schema; dropped on teardown — deletable fixture."""
    schema_name = f"test_reader_semantic_outline_t58c_{uuid4().hex}"
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


async def _bootstrap_outline(pool: asyncpg.Pool, *, record_id: UUID, user_id: UUID):
    """Bootstrap a single semantic_outline job using the DI test seam."""
    boot = await EnhancementJobBootstrapService(
        pool=pool,
        semantic_outline_request_eligibility=allow_semantic_outline_request_eligibility,
    ).bootstrap_semantic_outline_job(record_id=record_id, user_id=user_id)
    assert boot is not None, "bootstrap_semantic_outline_job returned None"
    return boot


async def _fetch_navigation_units(
    pool: asyncpg.Pool, *, record_id: UUID, base_id: UUID
) -> tuple[tuple[Any, ...], ...]:
    """Capture navigation.units source rows (order_index, unit_id, bounds, hash).

    The semantic_outline worker writes only to ``enhancement_layers``; it must
    never touch ``reading_units``. Comparing this snapshot before/after the
    call proves ``navigation.units`` is unchanged.

    This is an ADDITIONAL invariant — the primary acceptance seam is
    ``_load_record_snapshot`` below, which builds a full ``ReaderPlateSnapshot``
    via the production snapshot builder.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT unit_id, order_index, unit_type,
                   base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            """,
            record_id,
            base_id,
        )
    return tuple(
        (
            r["unit_id"],
            r["order_index"],
            r["unit_type"],
            r["base_start_utf16"],
            r["base_end_utf16"],
        )
        for r in rows
    )


async def _load_record_snapshot(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    user_id: UUID,
    base_id: UUID,
    generation: int,
) -> ReaderPlateSnapshot:
    """Build a ``ReaderPlateSnapshot`` via the production snapshot seam.

    Uses ``ArticleReadyPersistenceService.load_snapshot`` which internally
    calls ``ReaderOrchestrationRepository.load_snapshot_facts`` to load DB
    facts (record / base / units / anchor_segments / enhancement_layers /
    events / parsed_decisions / user_assets / ask_supplements) and then
    ``build_reader_plate_snapshot`` to assemble the snapshot, including
    ``project_semantic_outline_for_snapshot`` for the top-level
    ``semantic_outline`` field.

    This is the real acceptance seam — not a manual projection reimplementation
    and not a bare ``reading_units`` DB query.
    """
    service = ArticleReadyPersistenceService(pool=pool)
    return await service.load_snapshot(
        record_id=record_id,
        user_id=user_id,
        expected_base_id=base_id,
        expected_generation=generation,
    )


async def _fetch_published_outline_layer(
    pool: asyncpg.Pool, *, record_id: UUID, base_id: UUID
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, base_id, target_scope, target_key, generation, status,
                   source_run_id, source_job_id, published_at,
                   output_json
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND layer_type = 'semantic_outline'
              AND target_scope = 'record'
              AND target_key = 'document'
            ORDER BY published_at DESC
            LIMIT 1
            """,
            record_id,
            base_id,
        )


async def _fetch_usage_rows(
    pool: asyncpg.Pool, *, job_id: UUID
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT status, model_route, model_profile, model_name,
                   input_tokens, output_tokens, total_tokens,
                   reader_run_id, reading_record_id
            FROM ai_usage_events
            WHERE reader_job_id = $1
            """,
            job_id,
        )


def _classify_functional(result_status: str, error_code: str | None) -> str:
    """Functional/quality verdict per TMP §2.4.1."""
    if result_status == "succeeded":
        return "pass"
    if error_code == "model_output_invalid":
        return "fail_structured"
    return "fail_functional"


def _classify_usage_audit(
    *,
    functional_verdict: str,
    usage_rows: list[asyncpg.Record],
) -> str:
    """Usage-audit verdict per TMP §2.4.2.

    Queries the DB actual persisted count, NOT the seam return value —
    ``record_ai_usage_event`` is failure-tolerant and returns ``None`` on
    failure, so the only authoritative signal is the row count in
    ``ai_usage_events``.
    """
    if functional_verdict in {"fail_functional"}:
        # Timeout / zero-call paths expect zero usage events (TMP §1.1.3).
        # Usage audit is not applicable here.
        return "not_applicable"
    count = len(usage_rows)
    if count == 1:
        return "pass"
    if count == 0:
        # Provider call was made (functional pass or fail_structured) but
        # no usage row persisted → writer failed tolerantly.
        return "observability_inconclusive"
    return "fail"


def _emit_smoke_report(
    *,
    job_id: UUID,
    run_id: UUID,
    model_name: str,
    status: str,
    node_count: int,
    usage_totals: dict[str, int],
    functional_verdict: str,
    usage_audit_verdict: str,
    error_code: str | None = None,
) -> str:
    """Build a leak-safe smoke report string — only allowed fields.

    Never includes: API key, endpoint, full prompt, full provider payload.
    """
    parts = [
        "T5.8c semantic outline real-LLM smoke report",
        f"  job_id={job_id}",
        f"  run_id={run_id}",
        f"  model_name={model_name}",
        f"  status={status}",
        f"  node_count={node_count}",
        (
            "  usage_totals="
            f"input={usage_totals['input_tokens']} "
            f"output={usage_totals['output_tokens']} "
            f"total={usage_totals['total_tokens']}"
        ),
        f"  functional_verdict={functional_verdict}",
        f"  usage_audit_verdict={usage_audit_verdict}",
    ]
    if error_code:
        parts.append(f"  error_code={error_code}")
    return "\n".join(parts)


@pytest.mark.real_llm
async def test_t58c_semantic_outline_real_llm_smoke(
    outline_env: asyncpg.Pool,
) -> None:
    """Single real-LLM smoke against the explicitly authorized model.

    Default-skipped by ``conftest.py`` triple gate. When explicitly enabled
    via ``-m real_llm`` + ``CLAREAD_ALLOW_REAL_LLM_TESTS=1`` +
    ``CLAREAD_REAL_LLM_MODEL=<model>``, runs exactly one provider call
    against the resolved semantic-outline route, verifies the published
    layer + snapshot fence + usage audit, and emits a leak-safe report.
    """
    # ------------------------------------------------------------------
    # Contract point 3: pre-call fail-closed gate (defence-in-depth on top
    # of conftest.py triple gate). Mismatch → fail-closed, NOT skip.
    # ------------------------------------------------------------------
    assert real_llm_tests_allowed(), (
        f"{_ALLOW_REAL_LLM_TESTS_ENV}=1 is required for real-LLM smoke"
    )
    authorized_model = os.environ.get(_REAL_LLM_MODEL_ENV, "").strip()
    assert authorized_model, (
        f"{_REAL_LLM_MODEL_ENV} must be set to the authorized model name"
    )

    settings = get_settings()
    profile_name = str(settings.reader_semantic_outline_model_profile or "").strip()
    if not profile_name:
        pytest.fail(
            "fail-closed: reader_semantic_outline_model_profile is empty; "
            "set MODEL_PROFILES_JSON + READER_SEMANTIC_OUTLINE_MODEL_PROFILE "
            "to the authorized profile before running real-LLM smoke. "
            "Zero provider call."
        )

    model, model_config = build_model_for_route(
        settings, MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
    )
    if model is None or model_config is None:
        pytest.fail(
            "fail-closed: reader_layer_semantic_outline route did not resolve "
            "to a buildable model; check MODEL_PROFILES_JSON / "
            "READER_SEMANTIC_OUTLINE_MODEL_PROFILE. Zero provider call."
        )

    # EXACT model-name comparison — fail-closed on mismatch (NOT skip).
    resolved_model_name = str(model_config.model_name)
    if resolved_model_name != authorized_model:
        pytest.fail(
            "fail-closed: resolved semantic-outline model does not match "
            f"authorized model. resolved={resolved_model_name!r}, "
            f"authorized={authorized_model!r}. "
            "Set READER_SEMANTIC_OUTLINE_MODEL_PROFILE / MODEL_PROFILES_JSON "
            "to the profile that resolves to the authorized model. "
            "Zero provider call."
        )

    # ------------------------------------------------------------------
    # Contract point 5: isolated, deletable test record/base/units fixture.
    # Input covers heading + body + at least one valid L2 candidate.
    # Obeys 160 / 8000 / 200 caps (smoke text is ~700 chars total).
    # ------------------------------------------------------------------
    assert len(_SMOKE_PLAIN_TEXT) < OUTLINE_MAX_TOTAL_PREVIEW_CHARS, (
        "smoke fixture total text must stay under "
        f"OUTLINE_MAX_TOTAL_PREVIEW_CHARS={OUTLINE_MAX_TOTAL_PREVIEW_CHARS}"
    )
    assert OUTLINE_MAX_UNIT_PREVIEW_CHARS == 160
    assert OUTLINE_MAX_UNITS_FOR_PREVIEW == 200
    assert DEFAULT_MAX_PROVIDER_CALLS_PER_JOB == 1, (
        "policy contract: single smoke = at most 1 provider call"
    )
    assert DEFAULT_MAX_OUTPUT_TOKENS == 4096

    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text=_SMOKE_PLAIN_TEXT,
        title=_SMOKE_TITLE,
        language="en",
    )
    boot = await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )

    # ------------------------------------------------------------------
    # Contract point 7 (primary): build ReaderPlateSnapshot via the
    # production snapshot seam BEFORE the worker call. This is the real
    # acceptance seam — ``ArticleReadyPersistenceService.load_snapshot``
    # internally calls ``build_reader_plate_snapshot`` which assembles
    # navigation.units from ``ReadingBaseBuildResult`` and projects
    # top-level ``semantic_outline`` via ``project_semantic_outline_for_snapshot``.
    # ------------------------------------------------------------------
    record_generation = int(article.snapshot.record.generation)
    snapshot_before = await _load_record_snapshot(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        generation=record_generation,
    )

    # Additional invariant: capture navigation.units source rows BEFORE the
    # call. This is supplementary — the primary seam is ``snapshot_before``.
    nav_before = await _fetch_navigation_units(
        outline_env, record_id=article.record_id, base_id=article.base_id
    )

    # ------------------------------------------------------------------
    # Contract point 4 + 6: DI-injected real adapter; single provider call.
    # Policy caps at DEFAULT_MAX_PROVIDER_CALLS_PER_JOB=1; PydanticAI agent
    # is built with ``output retries=0``; ``generate`` runs exactly once.
    # ------------------------------------------------------------------
    policy = SemanticOutlineExecutionPolicy.for_tests(generation_enabled=True)
    generator = PydanticAISemanticOutlineGenerator(
        settings=settings, policy=policy
    )
    worker = SemanticOutlineWorkerService(pool=outline_env, generator=generator)

    result = await worker.process_next_semantic_outline_job(
        lease_owner="t58c-real-llm-smoke",
        lease_duration=timedelta(seconds=30),
    )

    # ------------------------------------------------------------------
    # Contract point 7 (primary): build ReaderPlateSnapshot via the
    # production snapshot seam AFTER the worker call. Assert
    # ``navigation.units`` is value-by-value identical to ``snapshot_before``.
    # The worker must not change or write navigation.units.
    # ------------------------------------------------------------------
    snapshot_after = await _load_record_snapshot(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        generation=record_generation,
    )
    assert snapshot_after.navigation.units == snapshot_before.navigation.units, (
        "snapshot.navigation.units changed during semantic_outline smoke; "
        "the worker must not touch reading_units or navigation projection"
    )
    # Generation must remain stable across the worker call.
    assert snapshot_after.record.generation == record_generation, (
        "snapshot.record.generation changed during semantic_outline smoke"
    )

    # Additional invariant: reading_units DB rows unchanged (supplementary).
    nav_after = await _fetch_navigation_units(
        outline_env, record_id=article.record_id, base_id=article.base_id
    )
    assert nav_after == nav_before, (
        "navigation.units DB rows changed during semantic_outline smoke; "
        "the worker must not touch reading_units"
    )

    # ------------------------------------------------------------------
    # Functional / quality verdict (TMP §2.4.1).
    # ------------------------------------------------------------------
    assert result is not None, "worker returned None for a bootstrapped job"
    functional_verdict = _classify_functional(
        result.status, result.error_code
    )

    # ------------------------------------------------------------------
    # Usage-audit verdict (TMP §2.4.2) — query DB actual persisted rows.
    # ------------------------------------------------------------------
    usage_rows = await _fetch_usage_rows(outline_env, job_id=boot.job_id)
    usage_audit_verdict = _classify_usage_audit(
        functional_verdict=functional_verdict, usage_rows=usage_rows
    )

    # Extract aggregate usage totals from the persisted row (if any) —
    # these are token counts only, never prompt text or provider payload.
    if usage_rows:
        persisted = usage_rows[0]
        usage_totals = {
            "input_tokens": int(persisted["input_tokens"] or 0),
            "output_tokens": int(persisted["output_tokens"] or 0),
            "total_tokens": int(persisted["total_tokens"] or 0),
        }
    else:
        usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # ------------------------------------------------------------------
    # Success-path acceptance (contract point 7): published layer fence +
    # snapshot top-level semantic_outline appears only when ready|partial.
    # ------------------------------------------------------------------
    node_count = 0
    published_layer = await _fetch_published_outline_layer(
        outline_env, record_id=article.record_id, base_id=article.base_id
    )

    if functional_verdict == "pass":
        # Published layer must exist and match the current base/generation
        # fence; this is exactly what ``project_semantic_outline_for_snapshot``
        # filters on before revalidating the envelope.
        assert published_layer is not None, (
            "functional=pass but no published semantic_outline layer row found"
        )
        assert published_layer["status"] == "published", (
            f"layer row status={published_layer['status']!r}, expected 'published'"
        )
        assert str(published_layer["base_id"]) == str(article.base_id), (
            "published layer base_id does not match article.base_id"
        )
        assert published_layer["target_scope"] == "record"
        assert published_layer["target_key"] == "document"

        # ------------------------------------------------------------------
        # Contract point B: layer provenance/fence — row-level source
        # identity must match the current record generation and the
        # bootstrapped job/run ids. No mixing of old values.
        # ------------------------------------------------------------------
        assert int(published_layer["generation"]) == record_generation, (
            f"layer row generation={published_layer['generation']!r}, "
            f"expected record generation={record_generation}"
        )
        assert published_layer["source_job_id"] == boot.job_id, (
            f"layer row source_job_id={published_layer['source_job_id']!r}, "
            f"expected boot.job_id={boot.job_id}"
        )
        assert published_layer["source_run_id"] == boot.run_id, (
            f"layer row source_run_id={published_layer['source_run_id']!r}, "
            f"expected boot.run_id={boot.run_id}"
        )

        envelope = published_layer["output_json"]
        if isinstance(envelope, str):
            import json

            envelope = json.loads(envelope)
        assert isinstance(envelope, dict), "envelope is not a dict"

        envelope_status = envelope.get("status")
        assert envelope_status in _TRUSTED_ENVELOPE_STATUSES, (
            f"envelope status={envelope_status!r}, expected ready|partial"
        )
        assert envelope.get("schema_kind") == "reader_semantic_outline"
        assert int(envelope.get("schema_version") or 0) == 1

        source_identity = envelope.get("source_identity") or {}
        assert str(source_identity.get("base_id")) == str(article.base_id), (
            "envelope source_identity.base_id mismatch"
        )
        # Envelope source_identity.generation must match the CURRENT
        # snapshot/record generation (not a stale old value).
        assert int(source_identity.get("generation") or 0) == int(
            snapshot_after.record.generation
        ), (
            f"envelope source_identity.generation="
            f"{source_identity.get('generation')!r}, "
            f"expected snapshot_after.record.generation="
            f"{snapshot_after.record.generation}"
        )

        nodes = envelope.get("nodes") or []
        node_count = len(nodes)
        assert node_count > 0, "published envelope has zero nodes"

        # Provenance model must match the resolved model_name (the one that
        # was actually called — never log api_key / endpoint).
        provenance = envelope.get("provenance") or {}
        provenance_model = str(provenance.get("model") or "")
        assert provenance_model == resolved_model_name, (
            f"provenance.model={provenance_model!r} != resolved={resolved_model_name!r}"
        )

        # ------------------------------------------------------------------
        # Contract point A: top-level ``snapshot.semantic_outline`` is the
        # real acceptance seam. After the worker publishes the layer, the
        # production snapshot builder must project it as a trusted
        # ``ReaderSemanticOutlineProjection`` (not None, ready|partial,
        # non-empty nodes, source identity matching current snapshot).
        # ------------------------------------------------------------------
        assert snapshot_after.semantic_outline is not None, (
            "snapshot_after.semantic_outline is None after successful publish; "
            "project_semantic_outline_for_snapshot must project ready|partial"
        )
        outline_projection = snapshot_after.semantic_outline
        assert outline_projection.status in _TRUSTED_ENVELOPE_STATUSES, (
            f"snapshot semantic_outline.status={outline_projection.status!r}, "
            "expected ready|partial"
        )
        assert len(outline_projection.nodes) > 0, (
            "snapshot semantic_outline.nodes is empty after successful publish"
        )
        assert outline_projection.source_identity.base_id == str(article.base_id), (
            f"snapshot semantic_outline.source_identity.base_id="
            f"{outline_projection.source_identity.base_id!r}, "
            f"expected {str(article.base_id)!r}"
        )
        assert outline_projection.source_identity.generation == int(
            snapshot_after.record.generation
        ), (
            f"snapshot semantic_outline.source_identity.generation="
            f"{outline_projection.source_identity.generation!r}, "
            f"expected snapshot_after.record.generation="
            f"{snapshot_after.record.generation}"
        )

        # Job / run terminal state (contract point 7).
        async with outline_env.acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT status, failure_code FROM reader_jobs WHERE id = $1",
                boot.job_id,
            )
            run_row = await conn.fetchrow(
                "SELECT status, finished_at FROM reader_runs WHERE id = $1",
                boot.run_id,
            )
        assert job_row is not None and job_row["status"] == "succeeded", (
            f"job status={job_row['status'] if job_row else None!r}, expected 'succeeded'"
        )
        assert run_row is not None and run_row["status"] == "completed", (
            f"run status={run_row['status'] if run_row else None!r}, expected 'completed'"
        )
        assert run_row["finished_at"] is not None, "run.finished_at is None"
    else:
        # Functional failure paths: layer may or may not exist (publisher
        # does not supersede on failure). Node count stays 0 for reporting.
        if published_layer is not None:
            # If a row exists at all on failure, it must NOT be a fresh
            # published row for this generation (publisher fail-closed).
            envelope = published_layer["output_json"]
            if isinstance(envelope, str):
                import json

                envelope = json.loads(envelope)
            if isinstance(envelope, dict):
                node_count = len(envelope.get("nodes") or [])

    # ------------------------------------------------------------------
    # Contract point 10: usage-writer failure → observability_inconclusive.
    # Must NOT be called a complete pass.
    # ------------------------------------------------------------------
    report = _emit_smoke_report(
        job_id=boot.job_id,
        run_id=boot.run_id,
        model_name=resolved_model_name,
        status=result.status,
        node_count=node_count,
        usage_totals=usage_totals,
        functional_verdict=functional_verdict,
        usage_audit_verdict=usage_audit_verdict,
        error_code=result.error_code,
    )

    if usage_audit_verdict == "observability_inconclusive":
        pytest.fail(
            "smoke INCONCLUSIVE: usage writer failed tolerantly; "
            "cannot confirm cost accounting or product enablement. "
            "Functional verdict may be pass, but observability is not "
            f"auditable.\n{report}"
        )

    if usage_audit_verdict == "fail":
        pytest.fail(
            f"smoke FAIL(usage): expected exactly 1 usage event, "
            f"got {len(usage_rows)}.\n{report}"
        )

    if functional_verdict != "pass":
        pytest.fail(
            f"smoke FAIL(functional): functional_verdict={functional_verdict}, "
            f"status={result.status}, error_code={result.error_code}.\n{report}"
        )

    # Success: functional=pass AND usage_audit=pass.
    # Print the report so the operator sees the leak-safe summary.
    print("\n" + report)
