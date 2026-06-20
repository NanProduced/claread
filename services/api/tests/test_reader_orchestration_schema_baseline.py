from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.contracts.annotation import utf16_code_unit_length

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_SQL = (
    REPO_ROOT / "infra" / "migrations" / "0001_initial_schema.sql"
).read_text(encoding="utf-8")


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
    finally:
        await conn.close()


async def test_enhancement_layers_published_uniqueness(reader_schema: str) -> None:
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
