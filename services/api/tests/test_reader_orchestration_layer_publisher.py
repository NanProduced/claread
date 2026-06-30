from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.contracts.annotation import compute_text_range_hash, slice_by_utf16_offsets
from app.schemas.reader_orchestration import TranslationGroup, TranslationLayerOutput
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_OPERATION_FINGERPRINT,
    TranslationJobBootstrapService,
)
from app.services.reader_orchestration.job_runtime import (
    FenceViolationError,
    LeaseTokenMismatchError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.layer_publisher import (
    TranslationLayerPublisher,
    TranslationPublishValidationError,
)
from app.services.reader_orchestration.orchestrator import (
    TRANSLATION_PARSED_POLICY_CODE,
    TRANSLATION_PARSED_RATIONALE_CODE,
)
from app.services.reader_orchestration.translation_parsed_decision import (
    TRANSLATION_PARSED_POLICY_VERSION,
    TRANSLATION_PARSED_TRIGGER,
)
from tests.reader_orchestration_test_support import (
    BASELINE_SQL,
    connect_admin,
    insert_user,
    make_pool,
    submit_article_ready,
)

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def layer_publisher_env() -> asyncpg.Pool:
    schema_name = f"test_reader_layer_publisher_{uuid4().hex}"
    admin_conn = await connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        pool = await make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _bootstrap_and_claim(
    pool: asyncpg.Pool,
    *,
    plain_text: str = "First sentence. Second sentence.",
) -> tuple[object, object, object]:
    user_id = await insert_user(pool)
    article = await submit_article_ready(pool, user_id=user_id, plain_text=plain_text)
    await TranslationJobBootstrapService(pool=pool).bootstrap_translation_run(
        record_id=article.record_id,
        user_id=user_id,
    )
    claim = await ReaderJobRuntime(pool=pool).claim_next_job(
        lease_owner="publisher-worker",
        lease_duration=timedelta(seconds=30),
    )
    assert claim is not None
    return user_id, article, claim


async def _load_claim_unit_state(
    pool: asyncpg.Pool,
    claim,
) -> tuple[str, str, list[asyncpg.Record]]:
    async with pool.acquire() as conn:
        unit_row = await conn.fetchrow(
            """
            SELECT unit.unit_id,
                   base.text AS base_text,
                   unit.base_start_utf16,
                   unit.base_end_utf16
            FROM reading_units unit
            JOIN reading_bases base
              ON base.id = unit.base_id
             AND base.reading_record_id = unit.reading_record_id
            WHERE unit.reading_record_id = $1
              AND unit.base_id = $2
              AND unit.unit_id = $3
            """,
            claim.reading_record_id,
            claim.base_id,
            claim.target_key,
        )
        assert unit_row is not None
        unit_text = slice_by_utf16_offsets(
            str(unit_row["base_text"]),
            int(unit_row["base_start_utf16"]),
            int(unit_row["base_end_utf16"]),
        )
        assert unit_text is not None
        segment_rows = await conn.fetch(
            """
            SELECT anchor_segment_id,
                   order_index,
                   unit_start_utf16,
                   unit_end_utf16,
                   text_hash
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            ORDER BY order_index ASC
            """,
            claim.reading_record_id,
            claim.base_id,
            claim.target_key,
        )
    assert segment_rows
    return str(unit_row["unit_id"]), unit_text, list(segment_rows)


async def _translation_output_for_claim(
    pool: asyncpg.Pool,
    claim,
    *,
    group_specs: list[dict[str, object]] | None = None,
) -> TranslationLayerOutput:
    unit_id, unit_text, segment_rows = await _load_claim_unit_state(pool, claim)
    segments_by_id = {
        str(row["anchor_segment_id"]): row
        for row in segment_rows
    }
    if group_specs is None:
        group_specs = [
            {
                "anchor_segment_ids": list(segments_by_id.keys()),
                "translated_text": "发布后的译文",
            }
        ]

    groups: list[TranslationGroup] = []
    for spec in group_specs:
        anchor_segment_ids = [str(value) for value in spec["anchor_segment_ids"]]
        resolved_rows = [
            segments_by_id[anchor_segment_id]
            for anchor_segment_id in anchor_segment_ids
            if anchor_segment_id in segments_by_id
        ]
        group_id = spec.get("group_id")
        if group_id is None:
            assert resolved_rows
            group_id = (
                f"{unit_id}_g"
                f"{int(resolved_rows[0]['order_index'])}_"
                f"{int(resolved_rows[-1]['order_index'])}"
            )
        source_text_hash = spec.get("source_text_hash")
        if source_text_hash is None:
            assert resolved_rows
            span_text = slice_by_utf16_offsets(
                unit_text,
                int(resolved_rows[0]["unit_start_utf16"]),
                int(resolved_rows[-1]["unit_end_utf16"]),
            )
            assert span_text is not None and span_text
            source_text_hash = compute_text_range_hash(span_text)
        groups.append(
            TranslationGroup(
                group_id=str(group_id),
                anchor_segment_ids=anchor_segment_ids,
                source_text_hash=str(source_text_hash),
                translated_text=str(spec.get("translated_text", "发布后的译文")),
            )
        )
    return TranslationLayerOutput(groups=groups)


async def _merge_claim_target_unit_with_next_unit(
    pool: asyncpg.Pool,
    claim,
) -> None:
    async with pool.acquire() as conn:
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, order_index, base_start_utf16, base_end_utf16
            FROM reading_units
            WHERE reading_record_id = $1
              AND base_id = $2
            ORDER BY order_index ASC
            """,
            claim.reading_record_id,
            claim.base_id,
        )
        assert len(unit_rows) >= 2
        first_unit = unit_rows[0]
        second_unit = unit_rows[1]
        assert str(first_unit["unit_id"]) == claim.target_key

        base_text = await conn.fetchval(
            """
            SELECT text
            FROM reading_bases
            WHERE reading_record_id = $1
              AND id = $2
            """,
            claim.reading_record_id,
            claim.base_id,
        )
        assert base_text is not None
        merged_text = slice_by_utf16_offsets(
            str(base_text),
            int(first_unit["base_start_utf16"]),
            int(second_unit["base_end_utf16"]),
        )
        assert merged_text is not None and merged_text

        await conn.execute(
            """
            UPDATE reading_units
            SET base_end_utf16 = $4,
                text_hash = $5
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            """,
            claim.reading_record_id,
            claim.base_id,
            first_unit["unit_id"],
            int(second_unit["base_end_utf16"]),
            compute_text_range_hash(merged_text),
        )

        second_segments = await conn.fetch(
            """
            SELECT anchor_segment_id, unit_order_index, unit_start_utf16, unit_end_utf16
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            ORDER BY order_index ASC
            """,
            claim.reading_record_id,
            claim.base_id,
            second_unit["unit_id"],
        )
        first_unit_segment_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM anchor_segments
            WHERE reading_record_id = $1
              AND base_id = $2
              AND unit_id = $3
            """,
            claim.reading_record_id,
            claim.base_id,
            first_unit["unit_id"],
        )
        assert first_unit_segment_count is not None

        for offset, segment in enumerate(second_segments, start=1):
            await conn.execute(
                """
                UPDATE anchor_segments
                SET unit_id = $4,
                    unit_order_index = $5,
                    unit_start_utf16 = $6,
                    unit_end_utf16 = $7
                WHERE reading_record_id = $1
                  AND base_id = $2
                  AND anchor_segment_id = $3
                """,
                claim.reading_record_id,
                claim.base_id,
                segment["anchor_segment_id"],
                first_unit["unit_id"],
                int(first_unit_segment_count) + offset,
                int(second_unit["base_start_utf16"]) + int(segment["unit_start_utf16"])
                - int(first_unit["base_start_utf16"]),
                int(second_unit["base_start_utf16"]) + int(segment["unit_end_utf16"])
                - int(first_unit["base_start_utf16"]),
            )


async def _assert_no_translation_publish_side_effects(
    pool: asyncpg.Pool,
    claim,
) -> None:
    async with pool.acquire() as conn:
        layer_count = await conn.fetchval(
            "SELECT COUNT(*) FROM enhancement_layers WHERE source_job_id = $1",
            claim.job_id,
        )
        decision_count = await conn.fetchval(
            "SELECT COUNT(*) FROM parsed_decisions WHERE source_job_id = $1",
            claim.job_id,
        )
        event_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE source_job_id = $1",
            claim.job_id,
        )
        job_status = await conn.fetchval(
            "SELECT status FROM reader_jobs WHERE id = $1",
            claim.job_id,
        )
    assert layer_count == 0
    assert decision_count == 0
    assert event_count == 0
    assert job_status == "claimed"


async def test_publish_writes_group_native_translation_layer_and_group_native_coverage(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="Alpha. Beta.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(layer_publisher_env, claim)

    published = await publisher.publish_unit_translation(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        output=output,
        quality_json={"prompt_version": "publisher-test"},
    )

    assert published.reading_record_id == article.record_id
    assert published.base_id == article.base_id
    assert published.unit_id == article.snapshot.navigation.units[0].unit_id
    assert published.generation == 1
    assert published.event.sequence == 2

    async with layer_publisher_env.acquire() as conn:
        layer_row = await conn.fetchrow(
            """
            SELECT layer_type,
                   target_scope,
                   target_key,
                   generation,
                   status,
                   source_job_id,
                   schema_version,
                   output_json,
                   coverage_json,
                   quality_json
            FROM enhancement_layers
            WHERE id = $1
            """,
            published.layer_id,
        )
        decision_row = await conn.fetchrow(
            """
            SELECT policy_code,
                   parsed_state,
                   rationale_code,
                   coverage_json,
                   decision_json,
                   source_layer_id,
                   source_job_id
            FROM parsed_decisions
            WHERE reading_record_id = $1
            """,
            article.record_id,
        )
        event_row = await conn.fetchrow(
            """
            SELECT sequence, event_type, source_job_id, source_layer_id
            FROM reader_events
            WHERE id = $1
            """,
            published.event.event_id,
        )
        parsed_event_row = await conn.fetchrow(
            """
            SELECT sequence, event_type, source_job_id, source_layer_id
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'parsed_decision_updated'
            """,
            article.record_id,
        )

    assert layer_row is not None
    assert layer_row["layer_type"] == "translation"
    assert layer_row["target_scope"] == "unit"
    assert layer_row["target_key"] == published.unit_id
    assert layer_row["generation"] == 1
    assert layer_row["status"] == "published"
    assert layer_row["source_job_id"] == claim.job_id
    assert layer_row["schema_version"] == 1
    assert set(layer_row["output_json"].keys()) == {"groups"}
    assert layer_row["output_json"] == output.model_dump(mode="json")
    assert set(layer_row["output_json"]["groups"][0].keys()) == {
        "group_id",
        "anchor_segment_ids",
        "source_text_hash",
        "translated_text",
    }
    assert layer_row["output_json"]["groups"][0]["source_text_hash"] == compute_text_range_hash(
        "Alpha. Beta."
    )

    expected_coverage = {
        "translation_layer_id": str(published.layer_id),
        "coverage_status": "complete",
        "unit_id": published.unit_id,
        "generation": 1,
        "group_count": 1,
        "covered_anchor_segment_ids": ["s1", "s2"],
        "missing_anchor_segment_ids": [],
        "groups": [
            {
                "group_id": output.groups[0].group_id,
                "anchor_segment_ids": ["s1", "s2"],
                "source_text_hash": compute_text_range_hash("Alpha. Beta."),
                "translated_text_length": len("发布后的译文"),
            }
        ],
    }
    assert layer_row["coverage_json"] == expected_coverage
    assert layer_row["quality_json"] == {
        "prompt_version": "publisher-test",
        "group_count": 1,
        "covered_anchor_segment_count": 2,
    }
    for forbidden_key in ("target_language", "confidence", "notes", "source_text"):
        assert forbidden_key not in layer_row["coverage_json"]
        assert forbidden_key not in layer_row["quality_json"]
    assert "发布后的译文" not in str(layer_row["coverage_json"])
    assert "Alpha. Beta." not in str(layer_row["coverage_json"])
    assert "translated_text" not in layer_row["coverage_json"]["groups"][0]

    assert decision_row is not None
    assert decision_row["policy_code"] == TRANSLATION_PARSED_POLICY_CODE
    assert decision_row["parsed_state"] == "parsed"
    assert decision_row["rationale_code"] == TRANSLATION_PARSED_RATIONALE_CODE
    assert decision_row["source_layer_id"] == published.layer_id
    assert decision_row["source_job_id"] == claim.job_id
    assert decision_row["coverage_json"] == expected_coverage
    assert decision_row["decision_json"] == {
        "policy_version": TRANSLATION_PARSED_POLICY_VERSION,
        "trigger": TRANSLATION_PARSED_TRIGGER,
        **expected_coverage,
    }
    for forbidden_key in ("target_language", "confidence", "notes", "source_text"):
        assert forbidden_key not in decision_row["coverage_json"]
        assert forbidden_key not in decision_row["decision_json"]
    assert "发布后的译文" not in str(decision_row["coverage_json"])
    assert "发布后的译文" not in str(decision_row["decision_json"])
    assert "translated_text" not in decision_row["coverage_json"]["groups"][0]
    assert "translated_text" not in decision_row["decision_json"]["groups"][0]

    assert event_row is not None
    assert event_row["sequence"] == 2
    assert event_row["event_type"] == "layer_published"
    assert event_row["source_job_id"] == claim.job_id
    assert event_row["source_layer_id"] == published.layer_id

    assert parsed_event_row is not None
    assert parsed_event_row["sequence"] == 3
    assert parsed_event_row["event_type"] == "parsed_decision_updated"
    assert parsed_event_row["source_job_id"] == claim.job_id
    assert parsed_event_row["source_layer_id"] == published.layer_id


async def test_publish_rejects_unknown_anchor_segment_before_insert(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="Alpha. Beta.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = TranslationLayerOutput(
        groups=[
            TranslationGroup(
                group_id="u1_g1_1",
                anchor_segment_ids=["missing"],
                source_text_hash="deadbeef",
                translated_text="未知锚点译文",
            )
        ]
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_unknown_anchor_segment"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_hash_mismatch_for_space_separated_group_span(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="Alpha. Beta.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(
        layer_publisher_env,
        claim,
        group_specs=[
            {
                "anchor_segment_ids": ["s1", "s2"],
                "source_text_hash": compute_text_range_hash("Alpha.Beta."),
                "translated_text": "空格哈希错误",
            }
        ],
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_group_hash_mismatch"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_hash_mismatch_for_paragraph_break_group_span(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="First sentence.\n\nSecond sentence.",
    )
    await _merge_claim_target_unit_with_next_unit(layer_publisher_env, claim)
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(
        layer_publisher_env,
        claim,
        group_specs=[
            {
                "anchor_segment_ids": ["s1", "s2"],
                "source_text_hash": compute_text_range_hash(
                    "First sentence.Second sentence."
                ),
                "translated_text": "段落分隔哈希错误",
            }
        ],
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_group_hash_mismatch"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_non_contiguous_group_before_insert(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="One. Two. Three.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(
        layer_publisher_env,
        claim,
        group_specs=[
            {
                "anchor_segment_ids": ["s1", "s3"],
                "translated_text": "跳段译文",
            }
        ],
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_group_non_contiguous"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_overlapping_groups_before_insert(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="One. Two. Three.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(
        layer_publisher_env,
        claim,
        group_specs=[
            {
                "anchor_segment_ids": ["s1", "s2"],
                "translated_text": "第一组",
            },
            {
                "anchor_segment_ids": ["s2", "s3"],
                "translated_text": "第二组",
            },
        ],
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_group_overlap"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_missing_anchor_coverage_before_insert(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="Alpha. Beta.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(
        layer_publisher_env,
        claim,
        group_specs=[
            {
                "anchor_segment_ids": ["s1"],
                "translated_text": "只覆盖第一段",
            }
        ],
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_missing_anchor_coverage"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_empty_translated_text_before_insert(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="Alpha. Beta.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(
        layer_publisher_env,
        claim,
        group_specs=[
            {
                "anchor_segment_ids": ["s1", "s2"],
                "translated_text": "   \n\t  ",
            }
        ],
    )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_empty_translated_text"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_translation_fingerprint_mismatch_before_insert(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(
        layer_publisher_env,
        plain_text="Alpha. Beta.",
    )
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(layer_publisher_env, claim)

    async with layer_publisher_env.acquire() as conn:
        await conn.execute(
            """
            UPDATE reader_jobs
            SET operation_fingerprint = $2
            WHERE id = $1
            """,
            claim.job_id,
            "translation_unit_v2:deadbeef",
        )

    with pytest.raises(TranslationPublishValidationError) as exc_info:
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    assert exc_info.value.failure_code == "translation_fingerprint_mismatch"
    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


@pytest.mark.parametrize(
    ("case_name", "expected_reason"),
    [
        ("stale_generation", "stale_generation"),
        ("inactive_base", "inactive_base"),
        ("active_base_mismatch", "active_base_mismatch"),
    ],
)
async def test_publish_rejects_fence_failures(
    layer_publisher_env: asyncpg.Pool,
    case_name: str,
    expected_reason: str,
) -> None:
    _user_id, article, claim = await _bootstrap_and_claim(layer_publisher_env)
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(layer_publisher_env, claim)

    async with layer_publisher_env.acquire() as conn:
        async with conn.transaction():
            if case_name == "stale_generation":
                await conn.execute(
                    "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
                    article.base_id,
                )
                new_base_id = await conn.fetchval(
                    """
                    INSERT INTO reading_bases (
                        reading_record_id,
                        base_version,
                        record_generation,
                        text,
                        content_sha256,
                        content_utf16_length,
                        canonicalizer_version,
                        builder_version,
                        segmenter_version,
                        language,
                        title_snapshot,
                        navigation_json,
                        status
                    )
                    SELECT
                        reading_record_id,
                        base_version + 1,
                        2,
                        text,
                        content_sha256,
                        content_utf16_length,
                        canonicalizer_version,
                        builder_version,
                        segmenter_version,
                        language,
                        title_snapshot,
                        navigation_json,
                        'active'
                    FROM reading_bases
                    WHERE id = $1
                    RETURNING id
                    """,
                    article.base_id,
                )
                assert new_base_id is not None
                await conn.execute(
                    """
                    UPDATE reading_records
                    SET generation = 2,
                        active_base_id = $2
                    WHERE id = $1
                    """,
                    article.record_id,
                    new_base_id,
                )
            elif case_name == "inactive_base":
                await conn.execute(
                    "UPDATE reading_bases SET status = 'superseded' WHERE id = $1",
                    article.base_id,
                )
            elif case_name == "active_base_mismatch":
                await conn.execute(
                    "UPDATE reading_records SET active_base_id = NULL WHERE id = $1",
                    article.record_id,
                )
            else:
                raise AssertionError(f"unknown fence test case: {case_name}")

    with pytest.raises(FenceViolationError, match=expected_reason):
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            output=output,
        )

    await _assert_no_translation_publish_side_effects(layer_publisher_env, claim)


async def test_publish_rejects_lease_token_mismatch(
    layer_publisher_env: asyncpg.Pool,
) -> None:
    _user_id, _article, claim = await _bootstrap_and_claim(layer_publisher_env)
    publisher = TranslationLayerPublisher(pool=layer_publisher_env)
    output = await _translation_output_for_claim(layer_publisher_env, claim)

    with pytest.raises(LeaseTokenMismatchError):
        await publisher.publish_unit_translation(
            job_id=claim.job_id,
            lease_token=uuid4(),
            output=output,
        )


def test_layer_publisher_module_does_not_reference_render_scene_json() -> None:
    path = API_ROOT / "app" / "services" / "reader_orchestration" / "layer_publisher.py"
    assert "render_scene_json" not in path.read_text(encoding="utf-8")


def test_translation_job_bootstrap_uses_expected_fingerprint_base() -> None:
    assert TRANSLATION_OPERATION_FINGERPRINT == "translation_unit"
