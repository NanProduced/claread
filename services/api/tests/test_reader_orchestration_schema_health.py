from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.services.reader_orchestration.schema_health import (
    check_reader_d5_schema_health,
    format_reader_d5_schema_health_failure,
)
from tests.reader_orchestration_test_support import BASELINE_SQL, connect_admin

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
SCHEMA_CHECK_SQL = (
    REPO_ROOT / "infra" / "scripts" / "check_schema_baseline.sql"
).read_text(encoding="utf-8")


@pytest.fixture
async def schema_health_schema() -> asyncpg.Connection:
    schema_name = f"test_reader_schema_health_{uuid4().hex}"
    admin = await connect_admin()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        yield admin, schema_name
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin.close()


async def test_reader_schema_health_passes_on_fresh_baseline(
    schema_health_schema: tuple[asyncpg.Connection, str],
) -> None:
    admin, schema_name = schema_health_schema

    report = await check_reader_d5_schema_health(admin, schema_name=schema_name)

    assert report.ok is True
    assert report.missing_columns == ()
    assert report.missing_indexes == ()
    assert report.missing_constraints == ()
    assert report.invalid_constraints == ()
    assert report.non_nullable_columns == ()
    assert report.to_dict()["failure_codes"] == []


async def test_reader_schema_health_reports_drift_with_reset_guidance(
    schema_health_schema: tuple[asyncpg.Connection, str],
) -> None:
    admin, schema_name = schema_health_schema
    await admin.execute(
        f'ALTER TABLE "{schema_name}".ai_usage_events '
        "DROP COLUMN reader_job_id CASCADE"
    )

    report = await check_reader_d5_schema_health(admin, schema_name=schema_name)
    message = format_reader_d5_schema_health_failure(report)

    assert report.ok is False
    assert "ai_usage_events.reader_job_id" in report.missing_columns
    assert "idx_ai_usage_events_reader_job" in report.missing_indexes
    assert "fk_ai_usage_events_reader_job" in report.missing_constraints
    assert "Reset or rebuild the local development database" in message
    assert "infra/migrations/0001_initial.sql" in message


async def test_reader_schema_health_reports_missing_anchor_column_with_baseline_guidance(
    schema_health_schema: tuple[asyncpg.Connection, str],
) -> None:
    admin, schema_name = schema_health_schema
    await admin.execute(
        f'ALTER TABLE "{schema_name}".user_annotations '
        "DROP COLUMN reading_record_id CASCADE"
    )

    report = await check_reader_d5_schema_health(admin, schema_name=schema_name)
    message = format_reader_d5_schema_health_failure(report)

    assert report.ok is False
    assert "reader_d6_anchor_migration_missing" in report.to_dict()["failure_codes"]
    assert "user_annotations.reading_record_id" in report.missing_columns
    assert "idx_user_annotations_reading_record" in report.missing_indexes
    assert "uq_user_annotations_reading_record_anchor" in report.missing_indexes
    assert "infra/migrations/0001_initial.sql" in message
    assert "Old Docker volumes do not automatically re-run" in message
    assert "docker cp" in message
    assert "docker exec" in message


async def test_reader_schema_health_reports_missing_anchor_index(
    schema_health_schema: tuple[asyncpg.Connection, str],
) -> None:
    admin, schema_name = schema_health_schema
    await admin.execute(
        f'DROP INDEX "{schema_name}".uq_reader_notes_reading_record_anchor'
    )

    report = await check_reader_d5_schema_health(admin, schema_name=schema_name)
    message = format_reader_d5_schema_health_failure(report)

    assert report.ok is False
    assert "reader_d6_anchor_migration_missing" in report.to_dict()["failure_codes"]
    assert "uq_reader_notes_reading_record_anchor" in report.missing_indexes
    assert "infra/migrations/0001_initial.sql" in message


async def test_reader_schema_health_reports_old_user_annotation_check_constraint(
    schema_health_schema: tuple[asyncpg.Connection, str],
) -> None:
    admin, schema_name = schema_health_schema
    await admin.execute(
        f"""
        ALTER TABLE "{schema_name}".user_annotations
            DROP CONSTRAINT user_annotations_text_anchor_payload_check;

        ALTER TABLE "{schema_name}".user_annotations
            ADD CONSTRAINT user_annotations_text_anchor_payload_check
                CHECK (
                    anchor_type <> 'text_range'
                    OR (
                        sentence_id IS NOT NULL
                        AND start_offset IS NOT NULL
                        AND end_offset IS NOT NULL
                        AND start_offset >= 0
                        AND end_offset > start_offset
                        AND text_hash IS NOT NULL
                    )
                );
        """
    )

    report = await check_reader_d5_schema_health(admin, schema_name=schema_name)
    message = format_reader_d5_schema_health_failure(report)

    assert report.ok is False
    assert "reader_d6_anchor_migration_missing" in report.to_dict()["failure_codes"]
    assert (
        "user_annotations.user_annotations_text_anchor_payload_check"
        in report.invalid_constraints
    )
    assert (
        "invalid constraint: "
        "user_annotations.user_annotations_text_anchor_payload_check"
    ) in message
    assert "infra/migrations/0001_initial.sql" in message


def test_check_schema_baseline_sql_covers_reader_attribution_objects() -> None:
    required_markers = (
        "ai_usage_events.reader_run_id",
        "ai_usage_events.reader_job_id",
        "ai_usage_events.enhancement_layer_id",
        "ai_usage_events.operation_fingerprint",
        "user_credit_ledger.subject_id",
        "user_credit_ledger.reading_record_id",
        "user_credit_ledger.reader_run_id",
        "user_credit_ledger.reader_job_id",
        "idx_ai_usage_events_reader_run",
        "idx_ai_usage_events_reader_job",
        "idx_ai_usage_events_enhancement_layer",
        "idx_ai_usage_events_operation_fingerprint",
        "idx_credit_ledger_subject",
        "idx_credit_ledger_reading_record",
        "idx_credit_ledger_reader_run",
        "idx_credit_ledger_reader_job",
        "fk_ai_usage_events_reader_run",
        "fk_ai_usage_events_reader_job",
        "fk_ai_usage_events_enhancement_layer",
        "fk_user_credit_ledger_reading_record",
        "fk_user_credit_ledger_reader_run",
        "fk_user_credit_ledger_reader_job",
    )

    for marker in required_markers:
        assert marker in SCHEMA_CHECK_SQL
