from __future__ import annotations

from dataclasses import dataclass

import asyncpg

AI_USAGE_EVENTS_TABLE = "ai_usage_events"
USER_CREDIT_LEDGER_TABLE = "user_credit_ledger"

READER_D5_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    AI_USAGE_EVENTS_TABLE: (
        "reading_record_id",
        "reader_run_id",
        "reader_job_id",
        "enhancement_layer_id",
        "operation_fingerprint",
    ),
    USER_CREDIT_LEDGER_TABLE: (
        "subject_type",
        "subject_id",
        "reading_record_id",
        "reader_run_id",
        "reader_job_id",
    ),
}

READER_D5_REQUIRED_INDEXES: dict[str, tuple[str, ...]] = {
    AI_USAGE_EVENTS_TABLE: (
        "idx_ai_usage_events_reading_record",
        "idx_ai_usage_events_reader_run",
        "idx_ai_usage_events_reader_job",
        "idx_ai_usage_events_enhancement_layer",
        "idx_ai_usage_events_operation_fingerprint",
    ),
    USER_CREDIT_LEDGER_TABLE: (
        "idx_credit_ledger_subject",
        "idx_credit_ledger_reading_record",
        "idx_credit_ledger_reader_run",
        "idx_credit_ledger_reader_job",
    ),
}

READER_D5_REQUIRED_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    AI_USAGE_EVENTS_TABLE: (
        "fk_ai_usage_events_reading_record",
        "fk_ai_usage_events_reader_run",
        "fk_ai_usage_events_reader_job",
        "fk_ai_usage_events_enhancement_layer",
    ),
    USER_CREDIT_LEDGER_TABLE: (
        "fk_user_credit_ledger_reading_record",
        "fk_user_credit_ledger_reader_run",
        "fk_user_credit_ledger_reader_job",
    ),
}


@dataclass(frozen=True, slots=True)
class ReaderD5SchemaHealthReport:
    schema_name: str
    missing_columns: tuple[str, ...]
    missing_indexes: tuple[str, ...]
    missing_constraints: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.missing_columns or self.missing_indexes or self.missing_constraints
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_name": self.schema_name,
            "ok": self.ok,
            "missing_columns": list(self.missing_columns),
            "missing_indexes": list(self.missing_indexes),
            "missing_constraints": list(self.missing_constraints),
        }


def format_reader_d5_schema_health_failure(
    report: ReaderD5SchemaHealthReport,
) -> str:
    lines = [
        f"Reader D5 schema health check failed for schema '{report.schema_name}'.",
    ]
    lines.extend(f"- missing column: {name}" for name in report.missing_columns)
    lines.extend(f"- missing index: {name}" for name in report.missing_indexes)
    lines.extend(
        f"- missing constraint: {name}" for name in report.missing_constraints
    )
    lines.extend(
        (
            "The current repo baseline already includes these D5 attribution fields, "
            "indexes, and FKs in `infra/migrations/0001_initial_schema.sql`.",
            "This usually means your local database was created before the current "
            "fresh baseline or was only partially reset.",
            "Recommended fix:",
            "1. Reset or rebuild the local development database.",
            "2. Re-apply `infra/migrations/0001_initial_schema.sql`.",
            "3. Re-run `uv run python scripts/check_reader_schema_health.py`.",
        )
    )
    return "\n".join(lines)


async def check_reader_d5_schema_health(
    conn: asyncpg.Connection,
    *,
    schema_name: str = "public",
) -> ReaderD5SchemaHealthReport:
    required_tables = tuple(READER_D5_REQUIRED_COLUMNS.keys())

    column_rows = await conn.fetch(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = $1
          AND table_name = ANY($2::text[])
        """,
        schema_name,
        list(required_tables),
    )
    existing_columns = {
        (str(row["table_name"]), str(row["column_name"])) for row in column_rows
    }

    index_rows = await conn.fetch(
        """
        SELECT tablename, indexname
        FROM pg_indexes
        WHERE schemaname = $1
          AND tablename = ANY($2::text[])
        """,
        schema_name,
        list(required_tables),
    )
    existing_indexes = {
        (str(row["tablename"]), str(row["indexname"])) for row in index_rows
    }

    required_constraint_names = tuple(
        constraint_name
        for constraint_names in READER_D5_REQUIRED_CONSTRAINTS.values()
        for constraint_name in constraint_names
    )
    constraint_rows = await conn.fetch(
        """
        SELECT t.relname AS table_name, c.conname AS constraint_name
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = $1
          AND t.relname = ANY($2::text[])
          AND c.conname = ANY($3::text[])
        """,
        schema_name,
        list(required_tables),
        list(required_constraint_names),
    )
    existing_constraints = {
        (str(row["table_name"]), str(row["constraint_name"]))
        for row in constraint_rows
    }

    missing_columns = tuple(
        f"{table_name}.{column_name}"
        for table_name, column_names in READER_D5_REQUIRED_COLUMNS.items()
        for column_name in column_names
        if (table_name, column_name) not in existing_columns
    )
    missing_indexes = tuple(
        index_name
        for table_name, index_names in READER_D5_REQUIRED_INDEXES.items()
        for index_name in index_names
        if (table_name, index_name) not in existing_indexes
    )
    missing_constraints = tuple(
        constraint_name
        for table_name, constraint_names in READER_D5_REQUIRED_CONSTRAINTS.items()
        for constraint_name in constraint_names
        if (table_name, constraint_name) not in existing_constraints
    )

    return ReaderD5SchemaHealthReport(
        schema_name=schema_name,
        missing_columns=missing_columns,
        missing_indexes=missing_indexes,
        missing_constraints=missing_constraints,
    )
