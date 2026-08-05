"""T5.3a: semantic outline bootstrap, bounded worker, record-level publisher."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.database import connection as db_connection
from app.schemas.reader_orchestration import ReaderPlateSnapshot
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    SEMANTIC_OUTLINE_OPERATION_FINGERPRINT,
    SEMANTIC_OUTLINE_TARGET_SCOPE,
    EnhancementJobBootstrapService,
    _fingerprint_matches_base,
    allow_semantic_outline_request_eligibility,
    default_semantic_outline_request_eligibility,
)
from app.services.reader_orchestration.job_runtime import (
    FenceViolationError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.semantic_outline_publisher import (
    SemanticOutlineCandidateNode,
    SemanticOutlineLayerPublisher,
    allocate_outline_revision,
    map_candidates_to_opaque_nodes,
    validate_mapped_outline,
    build_validation_context,
)
from app.services.reader_orchestration.semantic_outline_worker import (
    OUTLINE_MAX_ATTEMPTED_NODES,
    OUTLINE_MAX_TOTAL_PREVIEW_CHARS,
    OUTLINE_MAX_UNIT_PREVIEW_CHARS,
    FakeSemanticOutlineGenerator,
    SemanticOutlineWorkerService,
    build_bounded_worker_input,
    clamp_candidates,
)
from app.services.reader_orchestration.semantic_outline import SemanticOutlineUnit
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio



def _always_request(_state) -> bool:
    return True


@pytest.fixture
async def outline_env() -> asyncpg.Pool:
    schema_name = f"test_reader_semantic_outline_{uuid4().hex}"
    admin_conn = await connect_admin()
    original_pool = db_connection.DB_POOL
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        db_connection.DB_POOL = pool
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        db_connection.DB_POOL = original_pool
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Pure unit tests (no DB)
# ---------------------------------------------------------------------------


def test_bounded_input_caps_unit_and_total_preview() -> None:
    units = [
        {
            "unit_id": f"u{i}",
            "order_index": i,
            "unit_type": "body",
            "unit_text": "x" * 500,
        }
        for i in range(1, 80)
    ]
    built = build_bounded_worker_input(
        base_id="base_a",
        generation=1,
        unit_rows=units,
        max_unit_preview_chars=OUTLINE_MAX_UNIT_PREVIEW_CHARS,
        max_total_preview_chars=OUTLINE_MAX_TOTAL_PREVIEW_CHARS,
    )
    assert built.total_preview_chars <= OUTLINE_MAX_TOTAL_PREVIEW_CHARS
    assert all(len(u.preview) <= OUTLINE_MAX_UNIT_PREVIEW_CHARS for u in built.units)
    assert len(built.units) == 79
    # identity preserved even when previews empty after cap
    assert built.units[0].unit_id == "u1"


def test_clamp_candidates_respects_max_nodes() -> None:
    candidates = [
        SemanticOutlineCandidateNode(
            candidate_ref=f"c{i}",
            parent_candidate_ref=None,
            depth=1,
            title=f"T{i}",
            start_unit_id="u1",
            end_unit_id="u1",
        )
        for i in range(OUTLINE_MAX_ATTEMPTED_NODES + 10)
    ]
    clamped = clamp_candidates(candidates)
    assert len(clamped) == OUTLINE_MAX_ATTEMPTED_NODES


def test_opaque_map_preserves_parent_edges_and_differs_by_revision() -> None:
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c_parent",
            parent_candidate_ref=None,
            depth=1,
            title="Parent",
            start_unit_id="u1",
            end_unit_id="u3",
        ),
        SemanticOutlineCandidateNode(
            candidate_ref="c_child",
            parent_candidate_ref="c_parent",
            depth=2,
            title="Child",
            start_unit_id="u2",
            end_unit_id="u2",
        ),
    )
    mapped_a = map_candidates_to_opaque_nodes(candidates, outline_revision="rev_a")
    mapped_b = map_candidates_to_opaque_nodes(candidates, outline_revision="rev_b")
    assert mapped_a.outline_revision == "rev_a"
    parent_id = mapped_a.opaque_by_candidate["c_parent"]
    child_id = mapped_a.opaque_by_candidate["c_child"]
    assert mapped_a.attempted_nodes[0].node_id == parent_id
    assert mapped_a.attempted_nodes[1].node_id == child_id
    assert mapped_a.attempted_nodes[1].parent_node_id == parent_id
    # same candidate order/content, different revision → different opaque ids
    assert mapped_a.opaque_by_candidate["c_parent"] != mapped_b.opaque_by_candidate[
        "c_parent"
    ]
    assert not parent_id.startswith("base_")
    assert "order" not in parent_id


def test_validator_receives_final_opaque_ids() -> None:
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c1",
            parent_candidate_ref=None,
            depth=1,
            title="Chapter",
            start_unit_id="u1",
            end_unit_id="u2",
        ),
        SemanticOutlineCandidateNode(
            candidate_ref="c2",
            parent_candidate_ref="c1",
            depth=2,
            title="Section",
            start_unit_id="u1",
            end_unit_id="u1",
        ),
    )
    mapped = map_candidates_to_opaque_nodes(candidates)
    context = build_validation_context(
        base_id="base_a",
        generation=1,
        units=(
            SemanticOutlineUnit(unit_id="u1", order_index=1),
            SemanticOutlineUnit(unit_id="u2", order_index=2),
        ),
    )
    result = validate_mapped_outline(context=context, mapped=mapped)
    assert result.status == "ready"
    assert [n.node_id for n in result.nodes] == [
        mapped.opaque_by_candidate["c1"],
        mapped.opaque_by_candidate["c2"],
    ]
    assert result.nodes[1].parent_node_id == mapped.opaque_by_candidate["c1"]


def test_snapshot_semantic_outline_field_is_optional_default_none() -> None:
    """T5.4a: optional field exists; default None (not required, not always object)."""
    assert "semantic_outline" in ReaderPlateSnapshot.model_fields
    assert ReaderPlateSnapshot.model_fields["semantic_outline"].default is None


def test_default_request_eligibility_is_false() -> None:
    class _S:
        readiness_state = "article_ready"

    assert default_semantic_outline_request_eligibility(_S()) is False  # type: ignore[arg-type]


def test_explicit_allow_eligibility_is_true_for_controlled_di() -> None:
    class _S:
        readiness_state = "article_ready"

    assert allow_semantic_outline_request_eligibility(_S()) is True  # type: ignore[arg-type]


def test_bounded_worker_input_caps_preview_chars() -> None:
    unit_rows = [
        {
            "unit_id": f"u{i}",
            "order_index": i,
            "unit_type": "body",
            "unit_text": "x" * 500,
        }
        for i in range(1, 40)
    ]
    built = build_bounded_worker_input(
        base_id="base",
        generation=1,
        unit_rows=unit_rows,
        max_unit_preview_chars=50,
        max_total_preview_chars=200,
        max_units_for_preview=30,
    )
    assert built.total_preview_chars <= 200
    assert all(len(u.preview) <= 50 for u in built.units)
    # Unit identity always present even when preview emptied by budget.
    assert len(built.units) == 39
    assert all(u.unit_id.startswith("u") for u in built.units)


def test_clamp_candidates_respects_max_nodes() -> None:
    nodes = tuple(
        SemanticOutlineCandidateNode(
            candidate_ref=f"c{i}",
            parent_candidate_ref=None,
            depth=1,
            title=f"t{i}",
            start_unit_id="u1",
            end_unit_id="u1",
        )
        for i in range(OUTLINE_MAX_ATTEMPTED_NODES + 5)
    )
    clamped = clamp_candidates(nodes)
    assert len(clamped) == OUTLINE_MAX_ATTEMPTED_NODES


def test_allocate_outline_revision_is_opaque() -> None:
    a = allocate_outline_revision()
    b = allocate_outline_revision()
    assert a != b
    assert a.startswith("olrev_")


# ---------------------------------------------------------------------------
# Migration contract
# ---------------------------------------------------------------------------


async def test_migration_0020_extends_layer_job_worker_types() -> None:
    schema_name = f"test_migration_0020_{uuid4().hex}"
    admin_conn = await connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        user_id = await admin_conn.fetchval(
            "INSERT INTO users DEFAULT VALUES RETURNING id"
        )
        record_id = await admin_conn.fetchval(
            """
            INSERT INTO reading_records (user_id, title, source_type)
            VALUES ($1, 't', 'text')
            RETURNING id
            """,
            user_id,
        )
        import hashlib

        text = "hello"
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        base_id = await admin_conn.fetchval(
            """
            INSERT INTO reading_bases (
                reading_record_id, base_version, record_generation, status, text,
                content_utf16_length, content_sha256, language,
                canonicalizer_version, builder_version, segmenter_version,
                navigation_json
            )
            VALUES (
                $1, 1, 1, 'active', $2, 5, $3, 'en',
                'canon_v1', 'builder_v1', 'seg_v1', '{"units": []}'::jsonb
            )
            RETURNING id
            """,
            record_id,
            text,
            content_sha256,
        )
        await admin_conn.execute(
            "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
            record_id,
            base_id,
        )
        # layer_type semantic_outline accepted
        await admin_conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id, base_id, layer_type, target_scope, target_key,
                generation, status, operation_fingerprint, schema_version
            )
            VALUES ($1, $2, 'semantic_outline', 'record', 'document', 1,
                    'published', 'fp', 1)
            """,
            record_id,
            base_id,
        )
        # legacy layer types still accepted
        await admin_conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id, base_id, layer_type, target_scope, target_key,
                generation, status, operation_fingerprint, schema_version
            )
            VALUES ($1, $2, 'translation', 'unit', 'u1', 1,
                    'published', 'fp-t', 1)
            """,
            record_id,
            base_id,
        )
        # job_type accepted
        run_id = await admin_conn.fetchval(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status, record_generation,
                policy_version, trigger_kind
            )
            VALUES ($1, $2, 'semantic_outline_layer', 'queued', 1, 'v1', 'system')
            RETURNING id
            """,
            record_id,
            user_id,
        )
        await admin_conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id, job_type,
                target_type, target_key, expected_generation,
                operation_fingerprint, idempotency_key, input_hash
            )
            VALUES ($1, $2, $3, $4, 'build_semantic_outline', 'record', $5,
                    1, 'fp', 'idemp', 'hash')
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            str(record_id),
        )
        await admin_conn.execute(
            """
            INSERT INTO reader_runtime_spans (
                trace_id, span_kind, worker_type, status
            )
            VALUES ($1, 'worker_tick', 'semantic_outline', 'started')
            """,
            uuid4(),
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO reader_runtime_spans (
                    trace_id, span_kind, worker_type, status
                )
                VALUES ($1, 'worker_tick', 'bogus_worker', 'started')
                """,
                uuid4(),
            )
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


async def test_request_false_creates_no_job_or_layer(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=default_semantic_outline_request_eligibility,
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert result is None
    async with outline_env.acquire() as conn:
        job_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_jobs WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
        layer_count = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE layer_type = 'semantic_outline'"
        )
    assert job_count == 0
    assert layer_count == 0


async def test_bootstrap_only_after_article_ready(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(outline_env, user_id=user_id)
    bootstrap = EnhancementJobBootstrapService(
        pool=outline_env,
        semantic_outline_request_eligibility=_always_request,
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=article.record_id,
        user_id=user_id,
    )
    assert result is not None
    assert _fingerprint_matches_base(
        result.operation_fingerprint, SEMANTIC_OUTLINE_OPERATION_FINGERPRINT
    )
    async with outline_env.acquire() as conn:
        readiness = await conn.fetchval(
            "SELECT readiness_state FROM reading_records WHERE id = $1",
            article.record_id,
        )
        assert readiness == "article_ready"


async def _bootstrap_outline(pool, *, record_id, user_id):
    bootstrap = EnhancementJobBootstrapService(
        pool=pool,
        semantic_outline_request_eligibility=_always_request,
    )
    result = await bootstrap.bootstrap_semantic_outline_job(
        record_id=record_id,
        user_id=user_id,
    )
    assert result is not None
    return result


async def _bootstrap_and_claim_outline(
    pool: asyncpg.Pool,
    *,
    record_id,
    user_id,
    base_id,
    generation: int = 1,
    lease_owner: str = "outline-claim",
):
    """Bootstrap one outline job and claim it for publisher lease-fence tests."""
    boot = await _bootstrap_outline(pool, record_id=record_id, user_id=user_id)
    runtime = ReaderJobRuntime(pool=pool)
    claim = await runtime.claim_next_job(
        lease_owner=lease_owner,
        lease_duration=timedelta(seconds=60),
        job_type=SEMANTIC_OUTLINE_JOB_TYPE,
        target_type=SEMANTIC_OUTLINE_TARGET_SCOPE,
        operation_fingerprint=SEMANTIC_OUTLINE_OPERATION_FINGERPRINT,
        reading_record_id=record_id,
        base_id=base_id,
        expected_generation=generation,
    )
    assert claim is not None
    return boot, claim


def _nested_candidates() -> tuple[SemanticOutlineCandidateNode, ...]:
    return (
        SemanticOutlineCandidateNode(
            candidate_ref="c_root",
            parent_candidate_ref=None,
            depth=1,
            title="Chapter One",
            start_unit_id="u1",
            end_unit_id="u2",
        ),
        SemanticOutlineCandidateNode(
            candidate_ref="c_child",
            parent_candidate_ref="c_root",
            depth=2,
            title="Detail",
            start_unit_id="u1",
            end_unit_id="u1",
        ),
    )


async def test_ready_publish_with_nested_parent_edge(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env,
        user_id=user_id,
        plain_text="First paragraph.\n\nSecond paragraph.",
    )
    # Ensure unit ids exist for ranges — article_ready creates units.
    async with outline_env.acquire() as conn:
        units = await conn.fetch(
            """
            SELECT unit_id, order_index FROM reading_units
            WHERE reading_record_id = $1 ORDER BY order_index
            """,
            article.record_id,
        )
    assert len(units) >= 1
    u_ids = [u["unit_id"] for u in units]
    start = u_ids[0]
    end = u_ids[-1]
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="c_root",
            parent_candidate_ref=None,
            depth=1,
            title="Chapter One",
            start_unit_id=start,
            end_unit_id=end,
        ),
        SemanticOutlineCandidateNode(
            candidate_ref="c_child",
            parent_candidate_ref="c_root",
            depth=2,
            title="Detail",
            start_unit_id=start,
            end_unit_id=start,
        ),
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(candidates),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-1",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    assert result.publish_result is not None
    assert result.publish_result.outcome == "published"
    assert result.publish_result.event is not None
    assert result.publish_result.event.event_type == "layer_published"

    async with outline_env.acquire() as conn:
        layer = await conn.fetchrow(
            """
            SELECT id, status, output_json, operation_fingerprint
            FROM enhancement_layers
            WHERE layer_type = 'semantic_outline' AND status = 'published'
            """
        )
        events = await conn.fetch(
            """
            SELECT event_type, sequence
            FROM reader_events
            WHERE event_type = 'layer_published'
              AND payload_json->>'layer_type' = 'semantic_outline'
            ORDER BY sequence
            """
        )
    assert layer is not None
    nodes = layer["output_json"]["nodes"]
    assert len(nodes) == 2
    assert nodes[1]["parent_node_id"] == nodes[0]["node_id"]
    assert nodes[0]["node_id"].startswith("oln_")
    assert len(events) == 1

    snapshot = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    # T5.4a: trusted published ready|partial projects onto optional field.
    assert snapshot.semantic_outline is not None
    assert snapshot.semantic_outline.status in {"ready", "partial"}
    assert snapshot.semantic_outline.publication.layer_id == str(layer["id"])
    assert len(snapshot.semantic_outline.nodes) == 2
    progress_caps = {layer.capability for layer in snapshot.enhancement_progress.layers}
    assert "semantic_outline" not in progress_caps
    assert progress_caps <= {"translation", "vocabulary", "grammar"}


async def test_partial_publish_keeps_valid_nodes_only(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Only one unit."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="good",
            parent_candidate_ref=None,
            depth=1,
            title="Good",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
        SemanticOutlineCandidateNode(
            candidate_ref="bad",
            parent_candidate_ref=None,
            depth=1,
            title="   ",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(candidates),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-partial",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"
    assert result.publish_result is not None
    assert result.publish_result.status == "partial"
    async with outline_env.acquire() as conn:
        layer = await conn.fetchrow(
            """
            SELECT output_json FROM enhancement_layers
            WHERE layer_type = 'semantic_outline' AND status = 'published'
            """
        )
    assert len(layer["output_json"]["nodes"]) == 1
    assert layer["output_json"]["status"] == "partial"


async def test_version_zero_and_worker_failure_preserve_old_published(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Preserve me."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    good = (
        SemanticOutlineCandidateNode(
            candidate_ref="only",
            parent_candidate_ref=None,
            depth=1,
            title="Keep",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(good),
    )
    first = await worker.process_next_semantic_outline_job(
        lease_owner="outline-keep",
        lease_duration=timedelta(seconds=30),
    )
    assert first is not None and first.status == "succeeded"
    old_layer_id = first.publish_result.layer_id

    async with outline_env.acquire() as conn:
        seq_before = await conn.fetchval(
            """
            SELECT COALESCE(MAX(sequence), 0) FROM reader_events
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )

    # Validation failures never enter the lease transaction; dummy ids are fine.
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    units = (SemanticOutlineUnit(unit_id=unit_id, order_index=1),)
    dummy_job = uuid4()
    dummy_lease = uuid4()
    fail_result = await publisher.publish_from_candidates(
        job_id=dummy_job,
        lease_token=dummy_lease,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint="semantic_outline_document_v1:outline_input_v1:other",
        source_run_id=None,
        source_job_id=None,
        units=units,
        candidates=(
            SemanticOutlineCandidateNode(
                candidate_ref="bad",
                parent_candidate_ref=None,
                depth=1,
                title="  ",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
        ),
    )
    assert fail_result.outcome == "not_published"

    fail_worker = await publisher.publish_from_candidates(
        job_id=dummy_job,
        lease_token=dummy_lease,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint="semantic_outline_document_v1:outline_input_v1:other2",
        source_run_id=None,
        source_job_id=None,
        units=units,
        candidates=(),
        worker_failure=True,
    )
    assert fail_worker.outcome == "not_published"

    async with outline_env.acquire() as conn:
        published = await conn.fetch(
            """
            SELECT id, status FROM enhancement_layers
            WHERE layer_type = 'semantic_outline'
            ORDER BY created_at
            """
        )
        seq_after = await conn.fetchval(
            """
            SELECT COALESCE(MAX(sequence), 0) FROM reader_events
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
    assert len(published) == 1
    assert published[0]["status"] == "published"
    assert published[0]["id"] == old_layer_id
    assert seq_after == seq_before


async def test_idempotent_same_fingerprint_no_double_event(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Idempotent body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="n1",
            parent_candidate_ref=None,
            depth=1,
            title="Once",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    units = (SemanticOutlineUnit(unit_id=unit_id, order_index=1),)
    first = await publisher.publish_from_candidates(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint=boot.operation_fingerprint,
        source_run_id=claim.run_id,
        source_job_id=claim.job_id,
        units=units,
        candidates=candidates,
    )
    second = await publisher.publish_from_candidates(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint=boot.operation_fingerprint,
        source_run_id=claim.run_id,
        source_job_id=claim.job_id,
        units=units,
        candidates=candidates,
    )
    assert first.outcome == "published"
    assert second.outcome == "idempotent_reuse"
    assert second.reused_existing is True
    assert second.layer_id == first.layer_id
    assert second.event is None
    async with outline_env.acquire() as conn:
        event_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reader_events
            WHERE event_type = 'layer_published'
              AND payload_json->>'layer_type' = 'semantic_outline'
            """
        )
        published_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE layer_type = 'semantic_outline' AND status = 'published'
            """
        )
    assert event_count == 1
    assert published_count == 1


async def test_atomic_replace_on_new_fingerprint(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Replace body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    units = (SemanticOutlineUnit(unit_id=unit_id, order_index=1),)
    first = await publisher.publish_from_candidates(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint=boot.operation_fingerprint,
        source_run_id=claim.run_id,
        source_job_id=claim.job_id,
        units=units,
        candidates=(
            SemanticOutlineCandidateNode(
                candidate_ref="a",
                parent_candidate_ref=None,
                depth=1,
                title="Old",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
        ),
    )
    new_fp = f"{boot.operation_fingerprint}:replaced"
    async with outline_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET operation_fingerprint = $2
            WHERE id = $1 AND status = 'claimed'
            """,
            claim.job_id,
            new_fp,
        )
    second = await publisher.publish_from_candidates(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint=new_fp,
        source_run_id=claim.run_id,
        source_job_id=claim.job_id,
        units=units,
        candidates=(
            SemanticOutlineCandidateNode(
                candidate_ref="b",
                parent_candidate_ref=None,
                depth=1,
                title="New",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
        ),
    )
    assert first.outcome == "published"
    assert second.outcome == "published"
    assert second.layer_id != first.layer_id
    async with outline_env.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, operation_fingerprint
            FROM enhancement_layers
            WHERE layer_type = 'semantic_outline'
            ORDER BY created_at
            """
        )
        events = await conn.fetch(
            """
            SELECT sequence FROM reader_events
            WHERE event_type = 'layer_published'
              AND payload_json->>'layer_type' = 'semantic_outline'
            ORDER BY sequence
            """
        )
    assert len(rows) == 2
    assert rows[0]["status"] == "superseded"
    assert rows[1]["status"] == "published"
    assert rows[1]["operation_fingerprint"] == new_fp
    assert len(events) == 2


async def test_generation_fence_rejects_stale_publish(outline_env: asyncpg.Pool) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Fence body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
        seq_before = await conn.fetchval(
            "SELECT COALESCE(MAX(sequence), 0) FROM reader_events WHERE reading_record_id = $1",
            article.record_id,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    with pytest.raises(FenceViolationError):
        await publisher.publish_from_candidates(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            reading_record_id=article.record_id,
            base_id=article.base_id,
            generation=99,
            operation_fingerprint=boot.operation_fingerprint,
            source_run_id=claim.run_id,
            source_job_id=claim.job_id,
            units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
            candidates=(
                SemanticOutlineCandidateNode(
                    candidate_ref="x",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Stale",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            ),
        )
    async with outline_env.acquire() as conn:
        layer_count = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE layer_type = 'semantic_outline'"
        )
        seq_after = await conn.fetchval(
            "SELECT COALESCE(MAX(sequence), 0) FROM reader_events WHERE reading_record_id = $1",
            article.record_id,
        )
    assert layer_count == 0
    assert seq_after == seq_before


async def _seq_and_layer_counts(pool: asyncpg.Pool, record_id) -> tuple[int, int, int]:
    async with pool.acquire() as conn:
        seq = await conn.fetchval(
            "SELECT COALESCE(MAX(sequence), 0) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        )
        layers = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1 AND layer_type = 'semantic_outline'
            """,
            record_id,
        )
        published = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enhancement_layers
            WHERE reading_record_id = $1
              AND layer_type = 'semantic_outline'
              AND status = 'published'
            """,
            record_id,
        )
    return int(seq), int(layers), int(published)


async def test_stale_lease_token_cannot_overwrite_published(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Lease body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    # Establish a published layer via worker.
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(
            (
                SemanticOutlineCandidateNode(
                    candidate_ref="keep",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Keep",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            )
        ),
    )
    first = await worker.process_next_semantic_outline_job(
        lease_owner="lease-owner-a",
        lease_duration=timedelta(seconds=30),
    )
    assert first is not None and first.status == "succeeded"
    old_layer_id = first.publish_result.layer_id
    seq_before, _, published_before = await _seq_and_layer_counts(
        outline_env, article.record_id
    )
    assert published_before == 1

    # New job claimed under a different lease; attacker uses stale/wrong token.
    async with outline_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs SET status = 'superseded'
            WHERE reading_record_id = $1 AND job_type = $2
            """,
            article.record_id,
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
        lease_owner="lease-owner-b",
    )
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    with pytest.raises(FenceViolationError):
        await publisher.publish_from_candidates(
            job_id=claim.job_id,
            lease_token=uuid4(),  # wrong token
            reading_record_id=article.record_id,
            base_id=article.base_id,
            generation=1,
            operation_fingerprint=boot.operation_fingerprint,
            source_run_id=claim.run_id,
            source_job_id=claim.job_id,
            units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
            candidates=(
                SemanticOutlineCandidateNode(
                    candidate_ref="overwrite",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Overwrite",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            ),
        )
    seq_after, _, published_after = await _seq_and_layer_counts(
        outline_env, article.record_id
    )
    assert seq_after == seq_before
    assert published_after == 1
    async with outline_env.acquire() as conn:
        still = await conn.fetchval(
            "SELECT id FROM enhancement_layers WHERE id = $1 AND status = 'published'",
            old_layer_id,
        )
    assert still == old_layer_id


async def test_superseded_job_cannot_overwrite_published(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Supersede body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    # First successful publish under valid claim.
    first = await publisher.publish_from_candidates(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint=boot.operation_fingerprint,
        source_run_id=claim.run_id,
        source_job_id=claim.job_id,
        units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
        candidates=(
            SemanticOutlineCandidateNode(
                candidate_ref="ok",
                parent_candidate_ref=None,
                depth=1,
                title="Ok",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
        ),
    )
    assert first.outcome == "published"
    old_layer_id = first.layer_id
    seq_before, _, _ = await _seq_and_layer_counts(outline_env, article.record_id)

    async with outline_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET status = 'superseded',
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE id = $1
            """,
            claim.job_id,
        )

    with pytest.raises(FenceViolationError):
        await publisher.publish_from_candidates(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            reading_record_id=article.record_id,
            base_id=article.base_id,
            generation=1,
            operation_fingerprint=boot.operation_fingerprint,
            source_run_id=claim.run_id,
            source_job_id=claim.job_id,
            units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
            candidates=(
                SemanticOutlineCandidateNode(
                    candidate_ref="bad",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Bad",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            ),
        )
    seq_after, _, published_after = await _seq_and_layer_counts(
        outline_env, article.record_id
    )
    assert seq_after == seq_before
    assert published_after == 1
    async with outline_env.acquire() as conn:
        still = await conn.fetchval(
            "SELECT id FROM enhancement_layers WHERE id = $1 AND status = 'published'",
            old_layer_id,
        )
    assert still == old_layer_id


async def test_route_fence_mismatch_cannot_overwrite_published(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Route fence body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    first = await publisher.publish_from_candidates(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        reading_record_id=article.record_id,
        base_id=article.base_id,
        generation=1,
        operation_fingerprint=boot.operation_fingerprint,
        source_run_id=claim.run_id,
        source_job_id=claim.job_id,
        units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
        candidates=(
            SemanticOutlineCandidateNode(
                candidate_ref="ok",
                parent_candidate_ref=None,
                depth=1,
                title="Ok",
                start_unit_id=unit_id,
                end_unit_id=unit_id,
            ),
        ),
    )
    assert first.outcome == "published"
    old_layer_id = first.layer_id
    seq_before, _, _ = await _seq_and_layer_counts(outline_env, article.record_id)

    # Inject article_route mismatch: job says short_batch, run envelope structured.
    async with outline_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET input_json = COALESCE(input_json, '{}'::jsonb)
                || jsonb_build_object('article_route', 'short_batch')
            WHERE id = $1
            """,
            claim.job_id,
        )
        await conn.execute(
            """
            UPDATE reader_runs
            SET envelope_json = COALESCE(envelope_json, '{}'::jsonb)
                || jsonb_build_object('article_route', 'structured_batch')
            WHERE id = $1
            """,
            claim.run_id,
        )

    with pytest.raises(FenceViolationError):
        await publisher.publish_from_candidates(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            reading_record_id=article.record_id,
            base_id=article.base_id,
            generation=1,
            operation_fingerprint=boot.operation_fingerprint,
            source_run_id=claim.run_id,
            source_job_id=claim.job_id,
            units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
            candidates=(
                SemanticOutlineCandidateNode(
                    candidate_ref="route",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Route",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            ),
        )
    seq_after, _, published_after = await _seq_and_layer_counts(
        outline_env, article.record_id
    )
    assert seq_after == seq_before
    assert published_after == 1
    async with outline_env.acquire() as conn:
        still = await conn.fetchval(
            "SELECT id FROM enhancement_layers WHERE id = $1 AND status = 'published'",
            old_layer_id,
        )
    assert still == old_layer_id


async def test_article_ready_unaffected_by_outline_failure(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Still ready."
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(worker_failure=True),
    )
    # max_attempts default 3 — force terminal by setting max_attempts=1
    async with outline_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET max_attempts = 1 WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-fail",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "failed_terminal"
    snapshot = await ArticleReadyPersistenceService(pool=outline_env).load_snapshot(
        record_id=article.record_id, user_id=user_id
    )
    assert snapshot.record.readiness_state == "article_ready"
    assert snapshot.navigation.units  # L0 skeleton intact


async def test_target_key_mismatch_cannot_publish_layer_or_event(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Target key fence body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    async with outline_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET target_key = 'wrong-record' WHERE id = $1",
            claim.job_id,
        )
    before_publish = await _seq_and_layer_counts(outline_env, article.record_id)
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    with pytest.raises(FenceViolationError):
        await publisher.publish_from_candidates(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            reading_record_id=article.record_id,
            base_id=article.base_id,
            generation=1,
            operation_fingerprint=boot.operation_fingerprint,
            source_run_id=claim.run_id,
            source_job_id=claim.job_id,
            units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
            candidates=(
                SemanticOutlineCandidateNode(
                    candidate_ref="target-key",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Target key",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            ),
        )
    after_publish = await _seq_and_layer_counts(outline_env, article.record_id)
    assert after_publish == before_publish


async def test_mismatched_source_provenance_cannot_publish_layer_or_event(
    outline_env: asyncpg.Pool,
) -> None:
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Provenance fence body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    boot, claim = await _bootstrap_and_claim_outline(
        outline_env,
        record_id=article.record_id,
        user_id=user_id,
        base_id=article.base_id,
    )
    before_publish = await _seq_and_layer_counts(outline_env, article.record_id)
    publisher = SemanticOutlineLayerPublisher(pool=outline_env)
    with pytest.raises(FenceViolationError):
        await publisher.publish_from_candidates(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            reading_record_id=article.record_id,
            base_id=article.base_id,
            generation=1,
            operation_fingerprint=boot.operation_fingerprint,
            source_run_id=uuid4(),
            source_job_id=uuid4(),
            units=(SemanticOutlineUnit(unit_id=unit_id, order_index=1),),
            candidates=(
                SemanticOutlineCandidateNode(
                    candidate_ref="provenance",
                    parent_candidate_ref=None,
                    depth=1,
                    title="Provenance",
                    start_unit_id=unit_id,
                    end_unit_id=unit_id,
                ),
            ),
        )
    after_publish = await _seq_and_layer_counts(outline_env, article.record_id)
    assert after_publish == before_publish


# ---------------------------------------------------------------------------
# Phase 3: run state machine closure (TDD)
#   (a) generic Exception + max_attempts -> reader_runs.failed_terminal
#   (b) FenceViolation -> reader_runs.superseded
#   (c) success -> reader_runs.completed (safety net)
# ---------------------------------------------------------------------------


class _RaisingSemanticOutlineGenerator:
    """Test double: raises a generic Exception on generate (not FenceViolation,
    not SemanticOutlineGenerationError) to exercise the worker's outer
    except-Exception branch where reader_runs must transition to
    failed_terminal together with reader_jobs.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls: list[SemanticOutlineJobContext] = []

    async def generate(
        self, context: SemanticOutlineJobContext
    ) -> SemanticOutlineExecutionResult:
        self.calls.append(context)
        raise self._exc


class _FenceViolatingPublisher:
    """Test double: publish_from_candidates always raises FenceViolationError
    so the worker's FenceViolation handler is exercised end-to-end.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish_from_candidates(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))
        raise FenceViolationError("publish_fence_failed")


async def _latest_job_and_run_status(
    pool: asyncpg.Pool, job_type: str
) -> tuple[str | None, str | None]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT j.status AS job_status, r.status AS run_status
            FROM reader_jobs j
            JOIN reader_runs r ON r.id = j.run_id
            WHERE j.job_type = $1
            ORDER BY j.created_at DESC
            LIMIT 1
            """,
            job_type,
        )
    if row is None:
        return None, None
    return row["job_status"], row["run_status"]


async def test_run_transitions_to_failed_terminal_on_generic_exception_max_attempts(
    outline_env: asyncpg.Pool,
) -> None:
    """Generic Exception on the final attempt must terminalize both
    reader_jobs and reader_runs. RED point: run stays in 'running' today."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Generic exc body."
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    # Force attempt_count >= max_attempts so the generic Exception branch
    # takes the failed_terminal sub-path (not retry_later).
    async with outline_env.acquire() as conn:
        await conn.execute(
            "UPDATE reader_jobs SET max_attempts = 1 WHERE job_type = $1",
            SEMANTIC_OUTLINE_JOB_TYPE,
        )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=_RaisingSemanticOutlineGenerator(RuntimeError("boom")),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-generic-exc",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "failed_terminal"
    assert result.error_code == "RuntimeError"

    job_status, run_status = await _latest_job_and_run_status(
        outline_env, SEMANTIC_OUTLINE_JOB_TYPE
    )
    assert job_status == "failed_terminal"
    # RED: current code transitions the job but leaves the run in 'running'.
    assert run_status == "failed_terminal"


async def test_run_transitions_to_superseded_on_fence_violation(
    outline_env: asyncpg.Pool,
) -> None:
    """FenceViolation during publish must supersede both reader_jobs and
    reader_runs. RED point: run stays in 'running' today."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Fence violation body."
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(_nested_candidates()),
        publisher=_FenceViolatingPublisher(),
    )
    with pytest.raises(FenceViolationError):
        await worker.process_next_semantic_outline_job(
            lease_owner="outline-fence",
            lease_duration=timedelta(seconds=30),
        )

    job_status, run_status = await _latest_job_and_run_status(
        outline_env, SEMANTIC_OUTLINE_JOB_TYPE
    )
    assert job_status == "superseded"
    # RED: current code supersedes the job but leaves the run in 'running'.
    assert run_status == "superseded"


async def test_run_stays_completed_on_success(
    outline_env: asyncpg.Pool,
) -> None:
    """Success path must mark both reader_jobs and reader_runs as completed.
    Safety net: confirms the success path is not broken by the fix."""
    user_id = await insert_user(outline_env)
    article = await submit_article_ready(
        outline_env, user_id=user_id, plain_text="Success body."
    )
    async with outline_env.acquire() as conn:
        unit_id = await conn.fetchval(
            "SELECT unit_id FROM reading_units WHERE reading_record_id = $1 LIMIT 1",
            article.record_id,
        )
    candidates = (
        SemanticOutlineCandidateNode(
            candidate_ref="only",
            parent_candidate_ref=None,
            depth=1,
            title="Only",
            start_unit_id=unit_id,
            end_unit_id=unit_id,
        ),
    )
    await _bootstrap_outline(
        outline_env, record_id=article.record_id, user_id=user_id
    )
    worker = SemanticOutlineWorkerService(
        pool=outline_env,
        generator=FakeSemanticOutlineGenerator(candidates),
    )
    result = await worker.process_next_semantic_outline_job(
        lease_owner="outline-success",
        lease_duration=timedelta(seconds=30),
    )
    assert result is not None
    assert result.status == "succeeded"

    job_status, run_status = await _latest_job_and_run_status(
        outline_env, SEMANTIC_OUTLINE_JOB_TYPE
    )
    # reader_jobs uses STATUS_SUCCEEDED="succeeded"; reader_runs uses "completed".
    assert job_status == "succeeded"
    assert run_status == "completed"
