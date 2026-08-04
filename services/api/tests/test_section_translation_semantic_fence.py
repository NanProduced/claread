# task-history: P2B (renamed from test_p2b_section_translation_semantic_fence.py)
"""P2B — Production Section Translation Semantic Fence Closure.

Proves ``SectionTranslationBootstrapService.request_section_translation()``
freezes the four semantic fence fields in ``input_json`` / ``envelope_json``
and that ``operation_fingerprint`` carries the semantic token. The batch
translation worker enforces the full fence end-to-end.

All tests call the real bootstrap (not manual INSERT) and use a counting
fake executor on real PostgreSQL ``reader_jobs`` / ``reading_units`` paths.
Negative tamper tests first call the real bootstrap to create a correct
job, then corrupt the persisted row to prove worker-level fail-closed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config.settings import Settings, get_settings
from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    SEMANTIC_FENCE_KEY_CONTRACT,
    SEMANTIC_FENCE_KEY_LAYER,
    SEMANTIC_FENCE_KEY_MODE,
    SEMANTIC_FENCE_KEY_RESOLVER,
    SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
    AutomaticLayerPolicy,
    compose_semantic_fingerprint_token,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_RUN_TYPE,
)
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime
from app.services.reader_orchestration.section_lane import (
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    SectionRequestTrigger,
)
from app.services.reader_orchestration.section_translation_bootstrap import (
    REASON_SEMANTIC_FENCE_INCONSISTENT,
    SectionBootstrapOutcome,
    SectionTranslationBootstrapService,
)
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1
from app.services.reader_orchestration.smoke_harness import (
    DevFakeTranslationBatchExecutor,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationWorkerService,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    DATABASE_URL,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_reader_orchestration, pytest.mark.seam_service_integration, pytest.mark.life_permanent_regression]



# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def fence_env(tmp_path_factory=None):
    schema_name = f"test_p2b_sec_{uuid4().hex}"
    admin_conn = await asyncpg.connect(DATABASE_URL)
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        await admin_conn.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    pool = await make_pool(schema_name)
    original_pool = db_connection.DB_POOL
    db_connection.DB_POOL = pool
    try:
        yield pool
    finally:
        await pool.close()
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


@contextmanager
def _policy_mode(mode: str) -> Iterator[None]:
    get_settings.cache_clear()
    settings = Settings(reader_automatic_layer_policy_mode=mode)  # type: ignore[arg-type]
    original = get_settings

    def _fake() -> Settings:
        return settings

    import app.config.settings as settings_mod

    settings_mod.get_settings = _fake  # type: ignore[assignment]
    try:
        yield
    finally:
        settings_mod.get_settings = original  # type: ignore[assignment]
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_unit_policy(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    unit_id: str,
    policy: dict[str, bool],
    contract_version: str = SEMANTIC_CONTRACT_V1,
    resolver_version: str = AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    content_role: str = "prose",
) -> None:
    meta = {
        "semantic": {
            "contract_version": contract_version,
            "content_role": content_role,
            "resolver_version": resolver_version,
            "automatic_layer_policy": policy,
        }
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE reading_units
            SET metadata_json = COALESCE(metadata_json, '{}'::jsonb) || $3::jsonb
            WHERE reading_record_id = $1 AND unit_id = $2
            """,
            record_id,
            unit_id,
            jsonb_param(meta),
        )


async def _publish_semantic_outline(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    nodes: list[dict[str, Any]],
    generation: int = 1,
) -> None:
    output = {
        "status": "ready",
        "source_identity": {
            "base_id": str(base_id),
            "generation": generation,
        },
        "publication": {"outline_revision": "p2b-test"},
        "nodes": nodes,
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id, base_id, layer_type, target_scope, target_key,
                generation, status, operation_fingerprint, schema_version,
                output_json, published_at
            )
            VALUES (
                $1, $2, 'semantic_outline', 'record', 'record',
                $3, 'published', 'semantic_outline_p2b_fp', 1,
                $4::jsonb, NOW()
            )
            """,
            record_id,
            base_id,
            generation,
            jsonb_param(output),
        )


async def _insert_anchor_segment(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
    base_id: UUID,
    unit_id: str,
    anchor_segment_id: str,
    order_index: int = 1000,
    unit_order_index: int = 1000,
) -> None:
    """Insert a test anchor segment with high order indices to avoid
    conflicts with anchors auto-created by ``submit_article_ready``."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO anchor_segments (
                reading_record_id, base_id, unit_id, anchor_segment_id,
                sentence_id, paragraph_id, order_index, unit_order_index,
                segment_type, base_start_utf16, base_end_utf16,
                unit_start_utf16, unit_end_utf16, text_hash, boundary_quality
            )
            VALUES (
                $1, $2, $3, $4, $4, 'p1', $5, $6,
                'sentence', 0, 12, 0, 12, '1a2b3c4d', 'normal'
            )
            """,
            record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            order_index,
            unit_order_index,
        )


async def _query_job(pool: asyncpg.Pool, job_id: UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT job_type, target_type, target_key, operation_fingerprint,
                   input_json, status, rationale_code, failure_code
            FROM reader_jobs WHERE id = $1
            """,
            job_id,
        )
    assert row is not None
    input_json = row["input_json"]
    if hasattr(input_json, "keys"):
        input_json = dict(input_json)
    return {
        "job_type": row["job_type"],
        "target_type": row["target_type"],
        "target_key": row["target_key"],
        "operation_fingerprint": str(row["operation_fingerprint"]),
        "input_json": input_json,
        "status": row["status"],
        "rationale_code": row["rationale_code"],
        "failure_code": row["failure_code"],
    }


async def _assert_no_section_job_persisted(
    pool: asyncpg.Pool,
    *,
    record_id: UUID,
) -> None:
    """Assert bootstrap REJECT left zero reader_jobs and zero reader_runs."""
    async with pool.acquire() as conn:
        job_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_jobs
            WHERE reading_record_id = $1
              AND job_type = $2
            """,
            record_id,
            TRANSLATION_BATCH_JOB_TYPE,
        )
        run_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_runs
            WHERE reading_record_id = $1
              AND run_type = $2
            """,
            record_id,
            TRANSLATION_RUN_TYPE,
        )
    assert job_count == 0, (
        f"expected zero section jobs after REJECT, found {job_count}"
    )
    assert run_count == 0, (
        f"expected zero section runs after REJECT, found {run_count}"
    )


async def _query_envelope(pool: asyncpg.Pool, job_id: UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.envelope_json
            FROM reader_jobs j
            JOIN reader_runs r ON j.run_id = r.id
            WHERE j.id = $1
            """,
            job_id,
        )
    assert row is not None
    envelope = row["envelope_json"]
    if hasattr(envelope, "keys"):
        envelope = dict(envelope)
    return envelope


async def _tamper_input_json(
    pool: asyncpg.Pool,
    job_id: UUID,
    *,
    updates: dict[str, Any],
) -> None:
    """Corrupt a persisted job's input_json to simulate post-bootstrap tampering."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT input_json FROM reader_jobs WHERE id = $1",
            job_id,
        )
    assert row is not None
    current = row["input_json"]
    if hasattr(current, "keys"):
        current = dict(current)
    else:
        current = {}
    merged = {**current, **updates}
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET input_json = $2::jsonb WHERE id = $1",
            job_id,
            jsonb_param(merged),
        )


class _CountingDevBatchTranslator(DevFakeTranslationBatchExecutor):
    def __init__(self) -> None:
        self.calls = 0

    async def translate_batch(self, context):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().translate_batch(context)


async def _claim_and_process(
    pool: asyncpg.Pool,
    *,
    job_id: UUID,
    spy: _CountingDevBatchTranslator,
) -> Any:
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="p2b-section-fence",
        lease_duration=timedelta(seconds=30),
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    return await worker.process_claimed_translation_batch_job(claim=claim)


async def _make_multi_unit_article(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
) -> tuple[Any, list[str]]:
    """Create an article with 3+ units and return (article, unit_ids)."""
    article = await submit_article_ready(
        pool,
        user_id=user_id,
        plain_text=(
            "First paragraph about planning and timelines for the project.\n\n"
            "Second paragraph covers budget tradeoffs and team ownership.\n\n"
            "Third paragraph summarizes outcomes and next steps clearly."
        ),
    )
    async with pool.acquire() as conn:
        units = await conn.fetch(
            "SELECT unit_id FROM reading_units WHERE base_id = $1 ORDER BY order_index",
            article.base_id,
        )
    unit_ids = [str(u["unit_id"]) for u in units]
    assert len(unit_ids) >= 3, f"need >=3 units, got {len(unit_ids)}"
    return article, unit_ids


# ---------------------------------------------------------------------------
# 1. Bootstrap freezes four fence fields
# ---------------------------------------------------------------------------


async def test_section_bootstrap_freezes_four_fence_fields_in_input_json(
    fence_env: asyncpg.Pool,
) -> None:
    """After bootstrap, reader_jobs.input_json must carry all four fence keys."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n1",
                "start_unit_id": unit_id,
                "end_unit_id": unit_id,
                "title": "Section",
                "order_index": 0,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED
    assert result.job_id is not None

    job = await _query_job(pool, result.job_id)
    input_json = job["input_json"]
    # Four fence fields must be present.
    assert SEMANTIC_FENCE_KEY_CONTRACT in input_json
    assert SEMANTIC_FENCE_KEY_RESOLVER in input_json
    assert SEMANTIC_FENCE_KEY_LAYER in input_json
    assert SEMANTIC_FENCE_KEY_MODE in input_json
    # Values must be non-empty and correct.
    assert input_json[SEMANTIC_FENCE_KEY_CONTRACT] == SEMANTIC_CONTRACT_V1
    assert (
        input_json[SEMANTIC_FENCE_KEY_RESOLVER]
        == AUTOMATIC_LAYER_POLICY_RESOLVER_V1
    )
    assert input_json[SEMANTIC_FENCE_KEY_LAYER] == "translation"
    assert input_json[SEMANTIC_FENCE_KEY_MODE] in ("off", "shadow", "enforce")


async def test_section_bootstrap_freezes_four_fence_fields_in_envelope(
    fence_env: asyncpg.Pool,
) -> None:
    """The envelope_json on reader_runs must also carry the four fence keys."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n1",
                "start_unit_id": unit_id,
                "end_unit_id": unit_id,
                "title": "Section",
                "order_index": 0,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED

    envelope = await _query_envelope(pool, result.job_id)
    assert SEMANTIC_FENCE_KEY_CONTRACT in envelope
    assert SEMANTIC_FENCE_KEY_RESOLVER in envelope
    assert SEMANTIC_FENCE_KEY_LAYER in envelope
    assert SEMANTIC_FENCE_KEY_MODE in envelope
    assert envelope[SEMANTIC_FENCE_KEY_LAYER] == "translation"


# ---------------------------------------------------------------------------
# 2. Fingerprint contains semantic token
# ---------------------------------------------------------------------------


async def test_section_bootstrap_fingerprint_contains_semantic_token(
    fence_env: asyncpg.Pool,
) -> None:
    """operation_fingerprint must carry sem:{contract}:{resolver}:mode:{mode}."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
    )
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n1",
                "start_unit_id": unit_id,
                "end_unit_id": unit_id,
                "title": "Section",
                "order_index": 0,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED

    job = await _query_job(pool, result.job_id)
    fp = job["operation_fingerprint"]
    # Must start with the section fingerprint base.
    assert fp.startswith(TRANSLATION_SECTION_OPERATION_FINGERPRINT)
    # Must contain the semantic token in the same format as automatic jobs.
    expected_token = compose_semantic_fingerprint_token(
        {
            SEMANTIC_FENCE_KEY_CONTRACT: SEMANTIC_CONTRACT_V1,
            SEMANTIC_FENCE_KEY_RESOLVER: AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        },
    )
    assert expected_token in fp
    assert f":{expected_token}" in fp


# ---------------------------------------------------------------------------
# 3. USER_EXPLICIT translation=false → executor=1
# ---------------------------------------------------------------------------


async def test_section_bootstrap_trusted_explicit_executes_despite_allows_false(
    fence_env: asyncpg.Pool,
) -> None:
    """Trusted USER_EXPLICIT section with translation=false still executes (calls=1)."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=AutomaticLayerPolicy.all_off().as_dict(),
    )
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n1",
                "start_unit_id": unit_id,
                "end_unit_id": unit_id,
                "title": "Section",
                "order_index": 0,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED
    assert result.job_id is not None

    spy = _CountingDevBatchTranslator()
    process_result = await _claim_and_process(
        pool, job_id=result.job_id, spy=spy
    )
    assert spy.calls == 1
    assert process_result.status != "superseded"


# ---------------------------------------------------------------------------
# 4. Multi-unit + start/end anchors → executor=1
# ---------------------------------------------------------------------------


async def test_section_bootstrap_multi_unit_with_anchors_executes(
    fence_env: asyncpg.Pool,
) -> None:
    """Multi-unit section with start/end anchors: full pipeline, executor=1."""
    pool = fence_env
    user_id = await insert_user(pool)
    article, unit_ids = await _make_multi_unit_article(pool, user_id=user_id)
    u1, u2, u3 = unit_ids[0], unit_ids[1], unit_ids[2]

    for uid in unit_ids:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=uid,
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )

    # Use real anchor segments created by submit_article_ready for u1 and u3.
    async with pool.acquire() as conn:
        anchor_rows = await conn.fetch(
            """
            SELECT anchor_segment_id, unit_id
            FROM anchor_segments
            WHERE reading_record_id = $1 AND base_id = $2
              AND unit_id = ANY($3::text[])
            ORDER BY order_index ASC
            """,
            article.record_id,
            article.base_id,
            [u1, u3],
        )
    # Pick first anchor of u1 as start, last anchor of u3 as end.
    u1_anchors = [str(r["anchor_segment_id"]) for r in anchor_rows if str(r["unit_id"]) == u1]
    u3_anchors = [str(r["anchor_segment_id"]) for r in anchor_rows if str(r["unit_id"]) == u3]
    assert u1_anchors, "u1 must have at least one anchor segment"
    assert u3_anchors, "u3 must have at least one anchor segment"
    start_anchor = u1_anchors[0]
    end_anchor = u3_anchors[-1]

    # Publish outline covering u1..u3 with anchors.
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n-multi",
                "start_unit_id": u1,
                "end_unit_id": u3,
                "title": "Multi-unit section",
                "order_index": 0,
                "start_anchor_segment_id": start_anchor,
                "end_anchor_segment_id": end_anchor,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=u1,
            end_unit_id=u3,
            start_anchor_segment_id=start_anchor,
            end_anchor_segment_id=end_anchor,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED
    assert result.job_id is not None
    assert set(result.target_unit_ids) == {u1, u2, u3}

    # Verify fence fields are present on the multi-unit job.
    job = await _query_job(pool, result.job_id)
    input_json = job["input_json"]
    assert input_json[SEMANTIC_FENCE_KEY_LAYER] == "translation"
    assert input_json[SEMANTIC_FENCE_KEY_CONTRACT] == SEMANTIC_CONTRACT_V1

    spy = _CountingDevBatchTranslator()
    process_result = await _claim_and_process(
        pool, job_id=result.job_id, spy=spy
    )
    assert spy.calls == 1
    assert process_result.status != "superseded"


# ---------------------------------------------------------------------------
# 5. Contract/resolver mismatch across units → bootstrap fail closed
# ---------------------------------------------------------------------------


async def test_section_bootstrap_contract_mismatch_across_units_rejects(
    fence_env: asyncpg.Pool,
) -> None:
    """Multi-unit section with mixed contract versions → bootstrap rejects."""
    pool = fence_env
    user_id = await insert_user(pool)
    article, unit_ids = await _make_multi_unit_article(pool, user_id=user_id)
    u1, u2 = unit_ids[0], unit_ids[1]

    # Unit 1: valid contract.
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=u1,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
        contract_version=SEMANTIC_CONTRACT_V1,
    )
    # Unit 2: bogus contract version → mismatch.
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=u2,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
        contract_version="semantic_contract_v999_bogus",
    )

    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n-multi",
                "start_unit_id": u1,
                "end_unit_id": u2,
                "title": "Mixed",
                "order_index": 0,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=u1,
            end_unit_id=u2,
        ),
        authorized=True,
    )
    # Bootstrap must fail closed via the shared fence builder — no
    # half-legitimate job is persisted.
    assert result.outcome is SectionBootstrapOutcome.REJECT
    assert result.reason == REASON_SEMANTIC_FENCE_INCONSISTENT
    assert result.job_id is None
    await _assert_no_section_job_persisted(
        pool, record_id=article.record_id
    )


async def test_section_bootstrap_resolver_mismatch_across_units_rejects(
    fence_env: asyncpg.Pool,
) -> None:
    """Multi-unit section with mixed resolver versions → bootstrap rejects."""
    pool = fence_env
    user_id = await insert_user(pool)
    article, unit_ids = await _make_multi_unit_article(pool, user_id=user_id)
    u1, u2 = unit_ids[0], unit_ids[1]

    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=u1,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
        resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    )
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=u2,
        policy=AutomaticLayerPolicy.all_on().as_dict(),
        resolver_version="automatic_layer_policy_v999_bogus",
    )

    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n-multi",
                "start_unit_id": u1,
                "end_unit_id": u2,
                "title": "Mixed resolver",
                "order_index": 0,
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=u1,
            end_unit_id=u2,
        ),
        authorized=True,
    )
    # Bootstrap must fail closed via the shared fence builder — no
    # half-legitimate job is persisted.
    assert result.outcome is SectionBootstrapOutcome.REJECT
    assert result.reason == REASON_SEMANTIC_FENCE_INCONSISTENT
    assert result.job_id is None
    await _assert_no_section_job_persisted(
        pool, record_id=article.record_id
    )


# ---------------------------------------------------------------------------
# 6. Tampered fence → worker catches, executor=0
# ---------------------------------------------------------------------------


async def _bootstrap_single_unit_section(
    pool: asyncpg.Pool,
    *,
    policy: dict[str, bool] | None = None,
) -> tuple[Any, str, UUID]:
    """Create a single-unit section job via real bootstrap; return (article, unit_id, job_id)."""
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id
    await _set_unit_policy(
        pool,
        record_id=article.record_id,
        unit_id=unit_id,
        policy=policy or AutomaticLayerPolicy.all_on().as_dict(),
    )
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n1",
                "start_unit_id": unit_id,
                "end_unit_id": unit_id,
                "title": "Section",
                "order_index": 0,
            }
        ],
    )
    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED
    assert result.job_id is not None
    return article, unit_id, result.job_id


async def test_section_bootstrap_tampered_layer_name_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """Corrupt automatic_layer_name to 'vocabulary' → worker supersede, executor=0."""
    pool = fence_env
    _article, _unit_id, job_id = await _bootstrap_single_unit_section(pool)

    await _tamper_input_json(
        pool,
        job_id,
        updates={SEMANTIC_FENCE_KEY_LAYER: "vocabulary"},
    )

    spy = _CountingDevBatchTranslator()
    await _claim_and_process(pool, job_id=job_id, spy=spy)
    assert spy.calls == 0
    job = await _query_job(pool, job_id)
    assert job["status"] == "superseded"
    # Supersede transitions record stable rationale_code; failure_code may be null.
    assert (
        job["rationale_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE
        or job["failure_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE
    )


async def test_section_bootstrap_tampered_contract_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """Corrupt semantic_contract_version → worker supersede, executor=0."""
    pool = fence_env
    _article, _unit_id, job_id = await _bootstrap_single_unit_section(pool)

    await _tamper_input_json(
        pool,
        job_id,
        updates={SEMANTIC_FENCE_KEY_CONTRACT: "semantic_contract_v999_bogus"},
    )

    spy = _CountingDevBatchTranslator()
    await _claim_and_process(pool, job_id=job_id, spy=spy)
    assert spy.calls == 0
    job = await _query_job(pool, job_id)
    assert job["status"] == "superseded"
    assert (
        job["rationale_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE
        or job["failure_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE
    )


async def test_section_bootstrap_tampered_target_unit_ids_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """Corrupt target_unit_ids with a non-existent unit → worker supersede, executor=0."""
    pool = fence_env
    _article, unit_id, job_id = await _bootstrap_single_unit_section(pool)

    # Add a forged unit ID alongside the real one.
    await _tamper_input_json(
        pool,
        job_id,
        updates={"target_unit_ids": [unit_id, "forged-unit-id"]},
    )

    spy = _CountingDevBatchTranslator()
    await _claim_and_process(pool, job_id=job_id, spy=spy)
    assert spy.calls == 0


async def test_section_bootstrap_tampered_range_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """Corrupt section_identity start/end to mismatch target_key → executor=0."""
    pool = fence_env
    _article, _unit_id, job_id = await _bootstrap_single_unit_section(pool)

    job = await _query_job(pool, job_id)
    identity = job["input_json"].get("section_identity") or {}
    # Swap start_unit_id to a forged value that doesn't match the target_key.
    identity["start_unit_id"] = "forged-start-unit"
    await _tamper_input_json(
        pool,
        job_id,
        updates={"section_identity": identity},
    )

    spy = _CountingDevBatchTranslator()
    await _claim_and_process(pool, job_id=job_id, spy=spy)
    assert spy.calls == 0


async def test_section_bootstrap_tampered_anchor_ownership_worker_zero_executor(
    fence_env: asyncpg.Pool,
) -> None:
    """Corrupt section_identity anchors to non-existent segments → executor=0."""
    pool = fence_env
    user_id = await insert_user(pool)
    article, unit_ids = await _make_multi_unit_article(pool, user_id=user_id)
    u1, _u2, u3 = unit_ids[0], unit_ids[1], unit_ids[2]

    for uid in unit_ids:
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=uid,
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )
    await _insert_anchor_segment(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        unit_id=u1,
        anchor_segment_id="sa-1",
    )
    await _insert_anchor_segment(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        unit_id=u3,
        anchor_segment_id="sa-3",
        order_index=1001,
        unit_order_index=1001,
    )
    await _publish_semantic_outline(
        pool,
        record_id=article.record_id,
        base_id=article.base_id,
        nodes=[
            {
                "node_id": "n-multi",
                "start_unit_id": u1,
                "end_unit_id": u3,
                "title": "Multi",
                "order_index": 0,
                "start_anchor_segment_id": "sa-1",
                "end_anchor_segment_id": "sa-3",
            }
        ],
    )

    bootstrap = SectionTranslationBootstrapService(pool=pool)
    result = await bootstrap.request_section_translation(
        record_id=article.record_id,
        user_id=user_id,
        intent=ExplicitSectionIntent(
            trigger=SectionRequestTrigger.USER_EXPLICIT,
            layer_family="translation",
            start_unit_id=u1,
            end_unit_id=u3,
            start_anchor_segment_id="sa-1",
            end_anchor_segment_id="sa-3",
        ),
        authorized=True,
    )
    assert result.outcome is SectionBootstrapOutcome.ADMITTED
    job_id = result.job_id
    assert job_id is not None

    # Tamper: replace anchor IDs with ghost values.
    job = await _query_job(pool, job_id)
    identity = job["input_json"].get("section_identity") or {}
    identity["start_anchor_segment_id"] = "ghost-sa"
    identity["end_anchor_segment_id"] = "ghost-ea"
    await _tamper_input_json(
        pool,
        job_id,
        updates={"section_identity": identity},
    )

    spy = _CountingDevBatchTranslator()
    await _claim_and_process(pool, job_id=job_id, spy=spy)
    assert spy.calls == 0


# ---------------------------------------------------------------------------
# 7. Vocabulary/grammar cannot bypass via section identity
# ---------------------------------------------------------------------------


async def test_section_translation_job_cannot_be_processed_as_vocabulary(
    fence_env: asyncpg.Pool,
) -> None:
    """A production section translation job (layer=translation) cannot be
    processed by the vocabulary worker even if job_type is tampered.

    The semantic fence validator runs before any executor call and rejects
    the layer mismatch (job layer=translation ≠ worker expected vocabulary)
    with a stable ``semantic_policy_version_mismatch`` rationale code. The
    job transitions to ``superseded`` — never ``failed_terminal`` — because
    the rejection is a fence violation, not an unrelated execution failure.

    The test uses the vocabulary *batch* path (``build_vocabulary_layer_article``
    + ``unit_range`` target scope) because the section job's ``target_key`` is
    a section identity, not a unit id. The batch context loader resolves units
    via ``input_json.target_unit_ids`` — the same path a real vocabulary batch
    job would take — so the shared fence validator truly runs and catches the
    layer mismatch before any executor call. The single-unit vocabulary path
    (``build_vocabulary_layer`` + ``target_key == unit_id``) cannot reach the
    fence for a section job because its SQL JOIN assumes ``target_key`` is a
    unit id; using that path would test an unrelated lookup failure, not fence
    evidence.
    """
    pool = fence_env
    _article, _unit_id, job_id = await _bootstrap_single_unit_section(pool)

    # Tamper: change job_type to the vocabulary *batch* type so the section
    # job (target_type=unit_range) is claimed by the vocabulary batch worker.
    # The job's input_json still carries automatic_layer_name="translation",
    # which the fence validator must reject against the vocabulary worker's
    # expected layer.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET job_type = 'build_vocabulary_layer_article' "
            "WHERE id = $1",
            job_id,
        )

    from app.services.reader_orchestration.vocabulary_worker import (
        VocabularyBatchCandidateOutput,
        VocabularyBatchExecutionResult,
        VocabularyBatchUnitCandidateOutput,
        VocabularyWorkerService,
    )

    class _CountingVocabBatch:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_batch(self, context):  # type: ignore[no-untyped-def]
            self.calls += 1
            return VocabularyBatchExecutionResult(
                output=VocabularyBatchCandidateOutput(
                    units=[
                        VocabularyBatchUnitCandidateOutput(
                            unit_id=str(context.units[0].unit_id),
                            items=[],
                        )
                    ]
                ),
                usage_data={
                    "aggregate": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    }
                },
                prompt_version="p2b",
                model_profile="fake",
                model_provider="fake",
                model_name="fake",
            )

    spy = _CountingVocabBatch()
    worker = VocabularyWorkerService(pool=pool, batch_executor=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="p2b-vocab-cross",
        lease_duration=timedelta(seconds=30),
        job_type="build_vocabulary_layer_article",
    )
    # The tampered job_type matches the claim filter, so the claim MUST
    # succeed — proving the fence validator, not the claim layer, is the
    # fail-closed gate.
    assert claim is not None
    assert claim.job_id == job_id
    result = await worker.process_claimed_vocabulary_batch_job(claim=claim)
    # Semantic fence typed-supersede: layer mismatch is a fence violation,
    # not an execution failure.
    assert result.status == "superseded"
    job = await _query_job(pool, job_id)
    assert (
        job["rationale_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE
        or job["failure_code"] == SEMANTIC_POLICY_VERSION_MISMATCH_CODE
    )
    assert spy.calls == 0


# ---------------------------------------------------------------------------
# 8. Mode off/shadow/enforce frozen on the job
# ---------------------------------------------------------------------------


async def test_section_bootstrap_mode_off_frozen_in_fingerprint(
    fence_env: asyncpg.Pool,
) -> None:
    """Mode=off → fingerprint carries mode:off."""
    pool = fence_env
    with _policy_mode("off"):
        user_id = await insert_user(pool)
        article = await submit_article_ready(pool, user_id=user_id)
        unit_id = article.snapshot.navigation.units[0].unit_id
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=unit_id,
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )
        await _publish_semantic_outline(
            pool,
            record_id=article.record_id,
            base_id=article.base_id,
            nodes=[
                {
                    "node_id": "n1",
                    "start_unit_id": unit_id,
                    "end_unit_id": unit_id,
                    "title": "S",
                    "order_index": 0,
                }
            ],
        )
        bootstrap = SectionTranslationBootstrapService(pool=pool)
        result = await bootstrap.request_section_translation(
            record_id=article.record_id,
            user_id=user_id,
            intent=ExplicitSectionIntent(
                trigger=SectionRequestTrigger.USER_EXPLICIT,
                layer_family="translation",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
            authorized=True,
        )
        assert result.outcome is SectionBootstrapOutcome.ADMITTED
        job = await _query_job(pool, result.job_id)
        assert ":mode:off" in job["operation_fingerprint"]
        assert job["input_json"][SEMANTIC_FENCE_KEY_MODE] == "off"


async def test_section_bootstrap_mode_shadow_frozen_in_fingerprint(
    fence_env: asyncpg.Pool,
) -> None:
    """Mode=shadow → fingerprint carries mode:shadow."""
    pool = fence_env
    with _policy_mode("shadow"):
        user_id = await insert_user(pool)
        article = await submit_article_ready(pool, user_id=user_id)
        unit_id = article.snapshot.navigation.units[0].unit_id
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=unit_id,
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )
        await _publish_semantic_outline(
            pool,
            record_id=article.record_id,
            base_id=article.base_id,
            nodes=[
                {
                    "node_id": "n1",
                    "start_unit_id": unit_id,
                    "end_unit_id": unit_id,
                    "title": "S",
                    "order_index": 0,
                }
            ],
        )
        bootstrap = SectionTranslationBootstrapService(pool=pool)
        result = await bootstrap.request_section_translation(
            record_id=article.record_id,
            user_id=user_id,
            intent=ExplicitSectionIntent(
                trigger=SectionRequestTrigger.USER_EXPLICIT,
                layer_family="translation",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
            authorized=True,
        )
        assert result.outcome is SectionBootstrapOutcome.ADMITTED
        job = await _query_job(pool, result.job_id)
        assert ":mode:shadow" in job["operation_fingerprint"]
        assert job["input_json"][SEMANTIC_FENCE_KEY_MODE] == "shadow"


async def test_section_bootstrap_mode_enforce_frozen_in_fingerprint(
    fence_env: asyncpg.Pool,
) -> None:
    """Mode=enforce → fingerprint carries mode:enforce."""
    pool = fence_env
    with _policy_mode("enforce"):
        user_id = await insert_user(pool)
        article = await submit_article_ready(pool, user_id=user_id)
        unit_id = article.snapshot.navigation.units[0].unit_id
        await _set_unit_policy(
            pool,
            record_id=article.record_id,
            unit_id=unit_id,
            policy=AutomaticLayerPolicy.all_on().as_dict(),
        )
        await _publish_semantic_outline(
            pool,
            record_id=article.record_id,
            base_id=article.base_id,
            nodes=[
                {
                    "node_id": "n1",
                    "start_unit_id": unit_id,
                    "end_unit_id": unit_id,
                    "title": "S",
                    "order_index": 0,
                }
            ],
        )
        bootstrap = SectionTranslationBootstrapService(pool=pool)
        result = await bootstrap.request_section_translation(
            record_id=article.record_id,
            user_id=user_id,
            intent=ExplicitSectionIntent(
                trigger=SectionRequestTrigger.USER_EXPLICIT,
                layer_family="translation",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
            authorized=True,
        )
        assert result.outcome is SectionBootstrapOutcome.ADMITTED
        job = await _query_job(pool, result.job_id)
        assert ":mode:enforce" in job["operation_fingerprint"]
        assert job["input_json"][SEMANTIC_FENCE_KEY_MODE] == "enforce"


# ---------------------------------------------------------------------------
# 9. Legacy job (no fence) compatibility
# ---------------------------------------------------------------------------


async def test_legacy_job_without_fence_still_works(
    fence_env: asyncpg.Pool,
) -> None:
    """Jobs created before the fence feature (no fence keys) still execute."""
    pool = fence_env
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id)
    unit_id = article.snapshot.navigation.units[0].unit_id

    # Manually insert a legacy job with NO fence keys (pre-feature job).
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind
            )
            VALUES ($1, $2, 'translation_layer', 'queued', 1,
                    '{}'::jsonb, 'legacy', 'system')
            RETURNING id
            """,
            article.record_id,
            user_id,
        )
        from app.services.reader_orchestration.reading_strategy import (
            resolve_reader_variant_strategy,
        )
        strategy = resolve_reader_variant_strategy(
            "daily_reading", "intermediate_reading"
        )
        layer = strategy.layers["translation"]
        legacy_input = {
            "reading_goal": strategy.reading_goal,
            "reading_variant": strategy.reading_variant,
            "strategy_version": strategy.strategy_version,
            "strategy_hash": strategy.strategy_hash,
            "layer_policy_hash": layer.policy_hash,
            "base_language": "en",
            "target_language": "zh-CN",
            "target_scope": "unit_range",
            "target_unit_ids": [unit_id],
            # NO semantic_contract_version / resolver / layer / mode keys.
        }
        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_hash, input_json, max_attempts
            )
            VALUES (
                $1, $2, $3, $4,
                $5, 'unit_range', $6, 'queued',
                0, 1, $7,
                $8, 'legacy-hash', $9::jsonb, 3
            )
            RETURNING id
            """,
            article.record_id,
            article.base_id,
            run_id,
            user_id,
            TRANSLATION_BATCH_JOB_TYPE,
            str(article.record_id),
            f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:{strategy.strategy_hash}",
            f"legacy:{unit_id}",
            jsonb_param(legacy_input),
        )
    assert isinstance(job_id, UUID)

    spy = _CountingDevBatchTranslator()
    worker = TranslationWorkerService(pool=pool, batch_translator=spy)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner="p2b-legacy",
        lease_duration=timedelta(seconds=30),
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        operation_fingerprint=TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )
    assert claim is not None
    assert claim.job_id == job_id
    process_result = await worker.process_claimed_translation_batch_job(claim=claim)
    assert spy.calls == 1
    assert process_result.status != "superseded"
