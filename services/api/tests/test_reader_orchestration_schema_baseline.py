from __future__ import annotations

import hashlib
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
_RAW_BASELINE_SQL = (
    # DATA-SCHEMA-BASELINE D2: the single fresh baseline replaces all
    # per-step migrations; the constraint contracts below are verified
    # against it.
    REPO_ROOT / "infra" / "migrations" / "0001_initial.sql"
).read_text(encoding="utf-8")
# DATA-D2-CLOSEOUT-R1: 0001 pins ``search_path`` to ``public`` for the
# fresh-init path; isolated-schema tests strip that pin and apply the DDL
# into their own schema instead.
BASELINE_SQL = re.sub(
    r"^\s*SET search_path = public, pg_catalog;\s*$",
    "",
    _RAW_BASELINE_SQL,
    flags=re.MULTILINE,
)


def test_model_execution_journal_logical_schema_is_declared_exactly() -> None:
    schema_check_sql = (
        REPO_ROOT / "infra" / "scripts" / "check_schema_baseline.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE ai_model_execution_journal (" in BASELINE_SQL
    assert "invocation_key text" in BASELINE_SQL
    assert "CONSTRAINT ai_model_execution_journal_state_matrix_check" in (
        BASELINE_SQL
    )
    assert (
        "CONSTRAINT ai_model_execution_journal_execution_slot_check "
        "CHECK ((execution_slot >= 1))"
    ) in BASELINE_SQL
    assert (
        "CREATE UNIQUE INDEX uq_ai_usage_events_legacy_grammar_request_id "
        "ON ai_usage_events USING btree (request_id)"
    ) in BASELINE_SQL
    assert (
        "CREATE UNIQUE INDEX uq_ai_usage_events_invocation_key "
        "ON ai_usage_events USING btree (invocation_key) "
        "WHERE (invocation_key IS NOT NULL);"
    ) in BASELINE_SQL
    assert "ai_model_execution_journal_state_matrix_check" in schema_check_sql
    assert "uq_ai_usage_events_legacy_grammar_request_id" in schema_check_sql


def _load_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    env_path = API_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or not line.startswith("DATABASE_URL="):
                continue
            return line.split("=", 1)[1].strip().strip('"').strip("'")

    return "postgresql://claread:claread_dev@127.0.0.1:5432/claread"


DATABASE_URL = _load_database_url()


async def _connect(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


async def _insert_user(conn: asyncpg.Connection) -> UUID:
    return await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")


async def _insert_reading_record(
    conn: asyncpg.Connection,
    user_id: UUID,
    *,
    generation: int = 1,
    title: str = "Reader Orchestration Test",
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO reading_records (user_id, source_type, title, language, generation)
        VALUES ($1, 'text', $2, 'en', $3)
        RETURNING id
        """,
        user_id,
        title,
        generation,
    )


async def _insert_reading_base(
    conn: asyncpg.Connection,
    record_id: UUID,
    *,
    base_version: int = 1,
    record_generation: int = 1,
    text: str = "Hello world.",
    status: str = "active",
) -> UUID:
    return await conn.fetchval(
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
        VALUES (
            $1,
            $2,
            $3,
            $4,
            $5,
            $6,
            'd3-p1-canonicalizer',
            'd3-p1-builder',
            'd3-p1-segmenter',
            'en',
            'Snapshot Title',
            '{"units":[]}'::jsonb,
            $7
        )
        RETURNING id
        """,
        record_id,
        base_version,
        record_generation,
        text,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        utf16_code_unit_length(text),
        status,
    )


async def _insert_reader_run(
    conn: asyncpg.Connection,
    record_id: UUID,
    user_id: UUID,
    *,
    record_generation: int = 1,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO reader_runs (
            reading_record_id,
            user_id,
            run_type,
            status,
            record_generation,
            envelope_json,
            policy_version,
            trigger_kind
        )
        VALUES ($1, $2, 'initial_build', 'queued', $3, '{}'::jsonb, 'd3-p1', 'user')
        RETURNING id
        """,
        record_id,
        user_id,
        record_generation,
    )


async def _insert_reading_unit(
    conn: asyncpg.Connection,
    record_id: UUID,
    base_id: UUID,
    *,
    unit_id: str = "u1",
    order_index: int = 1,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO reading_units (
            reading_record_id,
            base_id,
            unit_id,
            order_index,
            unit_type,
            boundary_quality,
            base_start_utf16,
            base_end_utf16,
            text_hash,
            metadata_json
        )
        VALUES ($1, $2, $3, $4, 'body', 'normal', 0, 12, '1a2b3c4d', '{}'::jsonb)
        RETURNING id
        """,
        record_id,
        base_id,
        unit_id,
        order_index,
    )


async def _insert_anchor_segment(
    conn: asyncpg.Connection,
    record_id: UUID,
    base_id: UUID,
    *,
    unit_id: str = "u1",
    anchor_segment_id: str = "s1",
    sentence_id: str | None = "s1",
    order_index: int = 1,
    unit_order_index: int = 1,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO anchor_segments (
            reading_record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            sentence_id,
            paragraph_id,
            order_index,
            unit_order_index,
            segment_type,
            base_start_utf16,
            base_end_utf16,
            unit_start_utf16,
            unit_end_utf16,
            text_hash,
            boundary_quality
        )
        VALUES (
            $1, $2, $3, $4, $5, 'p1', $6, $7, 'sentence', 0, 12, 0, 12, '1a2b3c4d', 'normal'
        )
        RETURNING id
        """,
        record_id,
        base_id,
        unit_id,
        anchor_segment_id,
        sentence_id,
        order_index,
        unit_order_index,
    )


@pytest.fixture(scope="module")
async def reader_schema() -> AsyncIterator[str]:
    schema_name = f"test_reader_orch_{uuid4().hex}"
    try:
        admin_conn = await _connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable for reader orchestration schema tests: {exc}")

    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)
        yield schema_name
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def test_reading_records_generation_must_be_positive(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reading_records (user_id, source_type, title, language, generation)
                VALUES ($1, 'text', 'Bad Generation', 'en', 0)
                """,
                user_id,
            )
    finally:
        await conn.close()


async def test_user_annotations_color_contract_matches_baseline_palette(
    reader_schema: str,
) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)

        default_color = await conn.fetchval(
            """
            INSERT INTO user_annotations (
                user_id, anchor_type, target_key, selected_text,
                reading_record_id, base_id, generation, unit_id,
                anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash
            )
            VALUES ($1, 'text_range', 'default-color', 'Default color',
                    $2, $2, 1, 'u1', 's1', 0, 4, 'hash-default')
            RETURNING color
            """,
            user_id,
            uuid4(),
        )
        assert default_color == "warm_yellow"

        color_comment = await conn.fetchval(
            """
            SELECT col_description('user_annotations'::regclass, attnum)
            FROM pg_attribute
            WHERE attrelid = 'user_annotations'::regclass
              AND attname = 'color'
            """
        )
        assert color_comment == (
            "用户高亮颜色，固定支持 warm_yellow、soft_mint、soft_rose。"
        )

        # DATA-SCHEMA-BASELINE D2: highlights are Reading Record anchor rows
        # only; the color contract is verified against that row shape.
        for color in ("warm_yellow", "soft_mint", "soft_rose"):
            stored_color = await conn.fetchval(
                """
                INSERT INTO user_annotations (
                    user_id, anchor_type, target_key, selected_text, color,
                    reading_record_id, base_id, generation, unit_id,
                    anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash
                )
                VALUES ($1, 'text_range', $2, $3, $4,
                        $5, $5, 1, 'u1', 's1', 0, 4, $6)
                RETURNING color
                """,
                user_id,
                f"supported-{color}",
                f"Supported {color}",
                color,
                uuid4(),
                f"hash-{color}",
            )
            assert stored_color == color

        for color in ("soft_green", "sage_green", "soft_blue", "soft_purple"):
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO user_annotations (
                        user_id, anchor_type, target_key, selected_text, color,
                        reading_record_id, base_id, generation, unit_id,
                        anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash
                    )
                    VALUES ($1, 'text_range', $2, $3, $4,
                            $5, $5, 1, 'u1', $6, 0, 4, $7)
                    """,
                    user_id,
                    f"rejected-{color}",
                    f"Rejected {color}",
                    color,
                    uuid4(),
                    f"seg-{color}",
                    f"hash-{color}",
                )
    finally:
        await conn.close()


async def test_reading_record_title_generation_state_is_fail_closed(
    reader_schema: str,
) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)

        row = await conn.fetchrow(
            """
            SELECT generated_title_zh, title_generation_status,
                   title_generation_error_code, title_generation_attempt_count
            FROM reading_records
            WHERE id = $1
            """,
            record_id,
        )
        assert row["generated_title_zh"] is None
        assert row["title_generation_status"] == "pending"
        assert row["title_generation_error_code"] is None
        assert row["title_generation_attempt_count"] == 0

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                UPDATE reading_records
                SET title_generation_status = 'succeeded',
                    generated_title_zh = NULL
                WHERE id = $1
                """,
                record_id,
            )

        await conn.execute(
            """
            UPDATE reading_records
            SET title_generation_status = 'succeeded',
                generated_title_zh = '城市补贴政策争议'
            WHERE id = $1
            """,
            record_id,
        )
    finally:
        await conn.close()


async def test_active_base_must_match_record_and_generation(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)
        other_record_id = await _insert_reading_record(conn, user_id, title="Other Record")
        other_base_id = await _insert_reading_base(conn, other_record_id)
        mismatch_generation_base_id = await _insert_reading_base(
            conn,
            record_id,
            base_version=2,
            record_generation=2,
            text="Generation 2 base.",
            status="superseded",
        )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
                record_id,
                other_base_id,
            )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "UPDATE reading_records SET active_base_id = $2 WHERE id = $1",
                record_id,
                mismatch_generation_base_id,
            )
    finally:
        await conn.close()


async def test_reading_bases_text_invariants(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    text = "A🙂B"
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
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
                    navigation_json,
                    status
                )
                VALUES (
                    $1, 1, 1, $2, $3, 3,
                    'd3-p1-canonicalizer', 'd3-p1-builder', 'd3-p1-segmenter',
                    'en', '{"units":[]}'::jsonb, 'active'
                )
                """,
                record_id,
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
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
                    navigation_json,
                    status
                )
                VALUES (
                    $1, 1, 1, $2, 'bad-sha', $3,
                    'd3-p1-canonicalizer', 'd3-p1-builder', 'd3-p1-segmenter',
                    'en', '{"units":[]}'::jsonb, 'active'
                )
                """,
                record_id,
                text,
                utf16_code_unit_length(text),
            )
    finally:
        await conn.close()


async def test_reading_units_and_anchor_segments_constraints(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)
        base_id = await _insert_reading_base(conn, record_id)
        await _insert_reading_unit(conn, record_id, base_id, unit_id="u1", order_index=1)

        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_reading_unit(conn, record_id, base_id, unit_id="u1", order_index=2)

        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_reading_unit(conn, record_id, base_id, unit_id="u2", order_index=1)

        await _insert_anchor_segment(conn, record_id, base_id, unit_id="u1", anchor_segment_id="s1")

        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_anchor_segment(
                conn,
                record_id,
                base_id,
                unit_id="u1",
                anchor_segment_id="s2",
                sentence_id="s2",
                order_index=2,
                unit_order_index=1,
            )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _insert_anchor_segment(
                conn,
                record_id,
                base_id,
                unit_id="u-missing",
                anchor_segment_id="s3",
                sentence_id="s3",
                order_index=3,
                unit_order_index=2,
            )
    finally:
        await conn.close()


async def test_reader_event_sequences_start_at_one_and_rollback_has_no_gap(
    reader_schema: str,
) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)

        assert await conn.fetchval(
            "SELECT next_sequence FROM reader_event_sequences WHERE reading_record_id = $1",
            record_id,
        ) == 1

        tx = conn.transaction()
        await tx.start()
        sequence = await conn.fetchval(
            """
            UPDATE reader_event_sequences
            SET next_sequence = next_sequence + 1,
                updated_at = NOW()
            WHERE reading_record_id = $1
            RETURNING next_sequence - 1
            """,
            record_id,
        )
        assert sequence == 1
        await conn.execute(
            """
            INSERT INTO reader_events (reading_record_id, sequence, event_type, payload_json)
            VALUES ($1, $2, 'article_ready', '{}'::jsonb)
            """,
            record_id,
            sequence,
        )
        await tx.rollback()

        tx = conn.transaction()
        await tx.start()
        sequence = await conn.fetchval(
            """
            UPDATE reader_event_sequences
            SET next_sequence = next_sequence + 1,
                updated_at = NOW()
            WHERE reading_record_id = $1
            RETURNING next_sequence - 1
            """,
            record_id,
        )
        assert sequence == 1
        await conn.execute(
            """
            INSERT INTO reader_events (reading_record_id, sequence, event_type, payload_json)
            VALUES ($1, $2, 'article_ready', '{}'::jsonb)
            """,
            record_id,
            sequence,
        )
        await tx.commit()

        assert await conn.fetchval(
            "SELECT COUNT(*) FROM reader_events WHERE reading_record_id = $1",
            record_id,
        ) == 1
    finally:
        await conn.close()


async def test_reader_jobs_base_scope_and_active_fingerprint(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)
        base_id = await _insert_reading_base(conn, record_id)
        other_base_id = await _insert_reading_base(
            conn,
            record_id,
            base_version=2,
            record_generation=2,
            text="Second base.",
            status="superseded",
        )
        run_id = await _insert_reader_run(conn, record_id, user_id)

        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                operation_fingerprint,
                idempotency_key
            )
            VALUES (
                $1, NULL, $2, $3, 'build_base', 'record', $4, 'queued', 1, 'fp-build', 'id-build'
            )
            """,
            record_id,
            run_id,
            user_id,
            str(record_id),
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, NULL, $2, $3, 'translate_unit', 'unit', 'u1', 'queued', 1,
                    'fp-missing-base', 'id-0'
                )
                """,
                record_id,
                run_id,
                user_id,
            )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, NULL, $2, $3, 'translate_unit', 'record', $4, 'queued', 1,
                    'fp-missing-base-record-target', 'id-record-no-base'
                )
                """,
                record_id,
                run_id,
                user_id,
                str(record_id),
            )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, NULL, $2, $3, 'build_vocabulary_layer', 'unit', 'u-vocab', 'queued', 1,
                    'fp-vocab-missing-base', 'id-vocab-0'
                )
                """,
                record_id,
                run_id,
                user_id,
            )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, NULL, $2, $3, 'build_grammar_bundle', 'unit', 'u-grammar', 'queued', 1,
                    'fp-grammar-missing-base', 'id-grammar-0'
                )
                """,
                record_id,
                run_id,
                user_id,
            )

        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                operation_fingerprint,
                idempotency_key
            )
            VALUES ($1, $2, $3, $4, 'translate_unit', 'unit', 'u1', 'queued', 1, 'fp-unit', 'id-1')
            """,
            record_id,
            base_id,
            run_id,
            user_id,
        )

        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                operation_fingerprint,
                idempotency_key
            )
            VALUES (
                $1, $2, $3, $4, 'build_vocabulary_layer', 'unit', 'u-vocab', 'queued', 1,
                'fp-vocab', 'id-vocab-1'
            )
            """,
            record_id,
            base_id,
            run_id,
            user_id,
        )

        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                operation_fingerprint,
                idempotency_key
            )
            VALUES (
                $1, $2, $3, $4, 'build_grammar_bundle', 'unit', 'u-grammar', 'queued', 1,
                'fp-grammar', 'id-grammar-1'
            )
            """,
            record_id,
            base_id,
            run_id,
            user_id,
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, $2, $3, $4, 'translate_unit', 'unit', 'u1', 'claimed', 1, 'fp-unit', 'id-2'
                )
                """,
                record_id,
                base_id,
                run_id,
                user_id,
            )

        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                operation_fingerprint,
                idempotency_key
            )
            VALUES ($1, $2, $3, $4, 'translate_unit', 'unit', 'u1', 'queued', 2, 'fp-unit', 'id-3')
            """,
            record_id,
            other_base_id,
            run_id,
            user_id,
        )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, $2, $3, $4, 'translate_unit', 'unit', 'u1', 'queued', 1, 'fp-unit', 'id-4'
                )
                """,
                record_id,
                other_base_id,
                run_id,
                user_id,
            )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
            VALUES ($1, $2, $3, $4, 'translate_unit', 'unit', 'u1', 'queued', 2, 'fp-unit', 'id-4b')
            """,
            record_id,
            base_id,
            run_id,
            user_id,
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id,
                    base_id,
                    run_id,
                    user_id,
                    job_type,
                    target_type,
                    target_key,
                    status,
                    expected_generation,
                    operation_fingerprint,
                    idempotency_key
                )
                VALUES (
                    $1, $2, $3, $4, 'translate_unit', 'unit', 'u2', 'retry_scheduled', 1,
                    'fp-bad-status', 'id-5'
                )
                """,
                record_id,
                base_id,
                run_id,
                user_id,
            )

        # article_rag_index_build (added in 0010) is base-scoped:
        # non-null base_id is accepted, null base_id is rejected by
        # ck_reader_jobs_base_scope's catch-all clause.
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key,
                status, expected_generation,
                operation_fingerprint, idempotency_key
            )
            VALUES (
                $1, $2, $3, $4, 'article_rag_index_build', 'record', $5,
                'queued', 1, 'fp-rag-index', 'id-rag-index-1'
            )
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            str(record_id),
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id, base_id, run_id, user_id,
                    job_type, target_type, target_key,
                    status, expected_generation,
                    operation_fingerprint, idempotency_key
                )
                VALUES (
                    $1, NULL, $2, $3, 'article_rag_index_build', 'record', $4,
                    'queued', 1, 'fp-rag-index-no-base', 'id-rag-index-no-base'
                )
                """,
                record_id,
                run_id,
                user_id,
                str(record_id),
            )

        # generate_display_title_zh (added in 0011) is also base-scoped:
        # it targets the record but must be fenced to the active base/generation.
        await conn.execute(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key,
                status, expected_generation,
                operation_fingerprint, idempotency_key
            )
            VALUES (
                $1, $2, $3, $4, 'generate_display_title_zh', 'record', $5,
                'queued', 1, 'display-title-fp', 'id-display-title-1'
            )
            """,
            record_id,
            base_id,
            run_id,
            user_id,
            str(record_id),
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO reader_jobs (
                    reading_record_id, base_id, run_id, user_id,
                    job_type, target_type, target_key,
                    status, expected_generation,
                    operation_fingerprint, idempotency_key
                )
                VALUES (
                    $1, NULL, $2, $3, 'generate_display_title_zh', 'record', $4,
                    'queued', 1, 'display-title-fp-no-base', 'id-display-title-no-base'
                )
                """,
                record_id,
                run_id,
                user_id,
                str(record_id),
            )
    finally:
        await conn.close()


async def test_enhancement_layers_published_uniqueness(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)
        base_id = await _insert_reading_base(conn, record_id)
        run_id = await _insert_reader_run(conn, record_id, user_id)
        other_base_id = await _insert_reading_base(
            conn,
            record_id,
            base_version=2,
            record_generation=2,
            text="Second generation layer base.",
            status="superseded",
        )

        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id,
                base_id,
                layer_type,
                target_scope,
                target_key,
                generation,
                status,
                operation_fingerprint,
                schema_version
            )
            VALUES ($1, $2, 'translation', 'unit', 'u1', 1, 'published', 'fp-layer-1', 1)
            """,
            record_id,
            base_id,
        )

        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id,
                base_id,
                layer_type,
                target_scope,
                target_key,
                generation,
                status,
                operation_fingerprint,
                schema_version
            )
            VALUES ($1, $2, 'translation', 'unit', 'u2', 2, 'draft', 'fp-layer-gen2', 1)
            """,
            record_id,
            other_base_id,
        )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id,
                    base_id,
                    layer_type,
                    target_scope,
                    target_key,
                    generation,
                    status,
                    operation_fingerprint,
                    schema_version
                )
                VALUES ($1, $2, 'translation', 'unit', 'u3', 1, 'draft', 'fp-layer-stale', 1)
                """,
                record_id,
                other_base_id,
            )

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id,
                    base_id,
                    layer_type,
                    target_scope,
                    target_key,
                    generation,
                    status,
                    operation_fingerprint,
                    schema_version
                )
                VALUES ($1, $2, 'translation', 'unit', 'u1', 1, 'published', 'fp-layer-2', 1)
                """,
                record_id,
                base_id,
            )

        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id,
                base_id,
                layer_type,
                target_scope,
                target_key,
                generation,
                status,
                operation_fingerprint,
                schema_version
            )
            VALUES ($1, $2, 'translation', 'unit', 'u1', 1, 'draft', 'fp-layer-3', 1)
            """,
            record_id,
            base_id,
        )

        grammar_job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id,
                base_id,
                run_id,
                user_id,
                job_type,
                target_type,
                target_key,
                status,
                expected_generation,
                operation_fingerprint,
                idempotency_key
            )
            VALUES (
                $1, $2, $3, $4, 'build_grammar_bundle', 'unit', 'u-grammar', 'succeeded', 1,
                'grammar_bundle_unit_v1', 'grammar-job-1'
            )
            RETURNING id
            """,
            record_id,
            base_id,
            run_id,
            user_id,
        )

        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id,
                base_id,
                layer_type,
                target_scope,
                target_key,
                generation,
                status,
                operation_fingerprint,
                schema_version,
                source_job_id
            )
            VALUES (
                $1, $2, 'grammar_note', 'unit', 'u-grammar', 1, 'published',
                'grammar_note_unit_v1', 1, $3
            )
            """,
            record_id,
            base_id,
            grammar_job_id,
        )

        await conn.execute(
            """
            INSERT INTO enhancement_layers (
                reading_record_id,
                base_id,
                layer_type,
                target_scope,
                target_key,
                generation,
                status,
                operation_fingerprint,
                schema_version,
                source_job_id
            )
            VALUES (
                $1, $2, 'sentence_analysis', 'unit', 'u-grammar', 1, 'published',
                'sentence_analysis_unit_v1', 1, $3
            )
            """,
            record_id,
            base_id,
            grammar_job_id,
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO enhancement_layers (
                    reading_record_id,
                    base_id,
                    layer_type,
                    target_scope,
                    target_key,
                    generation,
                    status,
                    operation_fingerprint,
                    schema_version,
                    source_job_id
                )
                VALUES (
                    $1, $2, 'grammar_note', 'unit', 'u-grammar-copy', 1, 'published',
                    'grammar_note_unit_v1', 1, $3
                )
                """,
                record_id,
                base_id,
                grammar_job_id,
            )
    finally:
        await conn.close()


async def test_parsed_decisions_unique_constraint(reader_schema: str) -> None:
    conn = await _connect(reader_schema)
    try:
        user_id = await _insert_user(conn)
        record_id = await _insert_reading_record(conn, user_id)
        base_id = await _insert_reading_base(conn, record_id)
        await _insert_reading_unit(conn, record_id, base_id, unit_id="u1", order_index=1)

        await conn.execute(
            """
            INSERT INTO parsed_decisions (
                reading_record_id,
                base_id,
                unit_id,
                policy_code,
                parsed_state
            )
            VALUES ($1, $2, 'u1', 'translation_published_v1', 'parsed')
            """,
            record_id,
            base_id,
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO parsed_decisions (
                    reading_record_id,
                    base_id,
                    unit_id,
                    policy_code,
                    parsed_state
                )
                VALUES ($1, $2, 'u1', 'translation_published_v1', 'partial')
                """,
                record_id,
                base_id,
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Migration 0016: reader_runtime_spans.worker_type CHECK includes
# 'grammar_bundle_window' (grammar-window Analysis Window observability).
# ---------------------------------------------------------------------------


async def test_migration_0016_adds_grammar_bundle_window_worker_type() -> None:
    """0016 extends ``reader_runtime_spans.worker_type`` CHECK so the grammar-window
    window worker can write ``worker_tick`` spans.

    Verifies:
      1. ``worker_type='grammar_bundle_window'`` is accepted after 0016.
      2. Legacy worker_types remain accepted (no regression).
      3. Unknown worker_types are still rejected.
    """
    schema_name = f"test_migration_0016_{uuid4().hex}"
    admin_conn = await _connect()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(BASELINE_SQL)

        # grammar_bundle_window is accepted (the whole point of 0016).
        await admin_conn.execute(
            """
            INSERT INTO reader_runtime_spans (
                trace_id, span_kind, worker_type, status
            )
            VALUES ($1, 'worker_tick', 'grammar_bundle_window', 'started')
            """,
            uuid4(),
        )

        # Legacy worker_types still accepted (no regression).
        for legacy_type in (
            "display_title",
            "translation",
            "vocabulary",
            "grammar_bundle",
            "article_rag_index",
            "artifact_extraction",
            "artifact_materialization",
        ):
            await admin_conn.execute(
                """
                INSERT INTO reader_runtime_spans (
                    trace_id, span_kind, worker_type, status
                )
                VALUES ($1, 'worker_tick', $2, 'started')
                """,
                uuid4(),
                legacy_type,
            )

        # Unknown worker_type is rejected by the CHECK constraint.
        with pytest.raises(asyncpg.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO reader_runtime_spans (
                    trace_id, span_kind, worker_type, status
                )
                VALUES ($1, 'worker_tick', 'bogus_worker_type', 'started')
                """,
                uuid4(),
            )
    finally:
        await admin_conn.execute(
            f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
        )
        await admin_conn.close()


async def test_model_execution_journal_schema_has_exact_orthogonal_contract(
    reader_schema: str,
) -> None:
    conn = await _connect(reader_schema)
    try:
        columns = {
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND table_name = 'ai_model_execution_journal'
                """,
                reader_schema,
            )
        }
        assert {
            "invocation_key",
            "invocation_kind",
            "reader_job_id",
            "reader_run_id",
            "attempt_ordinal",
            "execution_slot",
            "capture_state",
            "usage_delivery_state",
            "resume_payload_kind",
            "resume_payload_schema_version",
            "usage_event_draft_schema_version",
            "normalized_payload_json",
            "usage_event_draft_json",
            "capture_envelope_sha256",
            "resume_payload_bytes",
            "usage_event_draft_bytes",
            "ai_usage_event_id",
            "delivery_attempt_count",
            "delivery_next_attempt_at",
            "delivery_last_error_code",
            "delivery_last_error_message",
            "started_at",
            "captured_at",
            "ambiguous_at",
            "reconciled_at",
            "dead_lettered_at",
            "payload_purged_at",
            "business_terminal_at",
        } <= columns

        constraints = {
            row["conname"]: row["definition"]
            for row in await conn.fetch(
                """
                SELECT conname, pg_get_constraintdef(oid) AS definition
                FROM pg_constraint
                WHERE conrelid = 'ai_model_execution_journal'::regclass
                """
            )
        }
        assert "ai_model_execution_journal_capture_state_check" in constraints
        assert "ai_model_execution_journal_delivery_state_check" in constraints
        assert "ai_model_execution_journal_state_matrix_check" in constraints
        assert "ai_model_execution_journal_usage_event_link_check" in constraints

        indexes = {
            row["indexname"]: row["indexdef"]
            for row in await conn.fetch(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = $1
                  AND tablename IN (
                      'ai_usage_events',
                      'ai_model_execution_journal'
                  )
                """,
                reader_schema,
            )
        }
        assert "uq_ai_usage_events_legacy_grammar_request_id" in indexes
        assert "request_id" in indexes[
            "uq_ai_usage_events_legacy_grammar_request_id"
        ]
        assert "uq_ai_usage_events_invocation_key" in indexes
        assert "invocation_key" in indexes[
            "uq_ai_usage_events_invocation_key"
        ]
        assert "uq_ai_model_execution_journal_invocation_key" in indexes
    finally:
        await conn.close()


async def test_model_execution_journal_rejects_illegal_state_combinations(
    reader_schema: str,
) -> None:
    conn = await _connect(reader_schema)
    try:
        for capture_state, delivery_state in (
            ("started", "pending"),
            ("started", "reconciled"),
            ("started", "dead_letter"),
            ("ambiguous", "pending"),
            ("ambiguous", "reconciled"),
            ("ambiguous", "dead_letter"),
            ("captured", "not_ready"),
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO ai_model_execution_journal (
                        invocation_key,
                        invocation_kind,
                        attempt_ordinal,
                        execution_slot,
                        capture_state,
                        usage_delivery_state
                    ) VALUES ($1, 'reader.grammar_batch', 1, 1, $2, $3)
                    """,
                    f"reader:grammar_batch:{uuid4()}:1:1",
                    capture_state,
                    delivery_state,
                )

        usage_event_id = await conn.fetchval(
            """
            INSERT INTO ai_usage_events (
                usage_scope,
                capability_code,
                billing_mode,
                status
            ) VALUES ('system_internal', 'reader_grammar_bundle',
                      'internal_only', 'model_call_completed')
            RETURNING id
            """
        )
        captured_values_sql = """
            'reader.grammar_batch.result', 1, 1,
            '{}'::jsonb, '{}'::jsonb,
            repeat('a', 64), 2, 2, NOW()
        """
        await conn.execute(
            f"""
            INSERT INTO ai_model_execution_journal (
                invocation_key, invocation_kind, attempt_ordinal,
                execution_slot, capture_state, usage_delivery_state,
                resume_payload_kind, resume_payload_schema_version,
                usage_event_draft_schema_version, normalized_payload_json,
                usage_event_draft_json, capture_envelope_sha256,
                resume_payload_bytes, usage_event_draft_bytes, captured_at,
                ai_usage_event_id, reconciled_at
            ) VALUES (
                $1, 'reader.grammar_batch', 1, 1, 'captured', 'reconciled',
                {captured_values_sql}, $2, NOW()
            )
            """,
            f"reader:grammar_batch:{uuid4()}:1:1",
            usage_event_id,
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                f"""
                INSERT INTO ai_model_execution_journal (
                    invocation_key, invocation_kind, attempt_ordinal,
                    execution_slot, capture_state, usage_delivery_state,
                    resume_payload_kind, resume_payload_schema_version,
                    usage_event_draft_schema_version,
                    normalized_payload_json, usage_event_draft_json,
                    capture_envelope_sha256, resume_payload_bytes,
                    usage_event_draft_bytes, captured_at
                ) VALUES (
                    $1, 'reader.grammar_batch', 1, 1,
                    'captured', 'reconciled', {captured_values_sql}
                )
                """,
                f"reader:grammar_batch:{uuid4()}:1:1",
            )

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO ai_model_execution_journal (
                    invocation_key, invocation_kind, attempt_ordinal,
                    execution_slot
                ) VALUES ($1, 'reader.grammar_batch', 1, 0)
                """,
                f"reader:grammar_batch:{uuid4()}:1:0",
            )

        await conn.execute(
            """
            INSERT INTO ai_usage_events (
                usage_scope, capability_code, billing_mode, status,
                invocation_key
            ) VALUES (
                'system_internal', 'reader_grammar_bundle',
                'internal_only', 'succeeded', 'global:test:1'
            )
            """
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO ai_usage_events (
                    usage_scope, capability_code, billing_mode, status,
                    invocation_key
                ) VALUES (
                    'system_internal', 'reader_grammar_bundle',
                    'internal_only', 'succeeded', 'global:test:1'
                )
                """
            )
    finally:
        await conn.close()
