from __future__ import annotations

from dataclasses import dataclass

import asyncpg

AI_USAGE_EVENTS_TABLE = "ai_usage_events"
USER_CREDIT_LEDGER_TABLE = "user_credit_ledger"
USER_ANNOTATIONS_TABLE = "user_annotations"
READER_NOTES_TABLE = "reader_notes"

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

READER_D6_ANCHOR_COLUMNS = (
    "reading_record_id",
    "base_id",
    "generation",
    "unit_id",
    "anchor_segment_id",
    "unit_start_utf16",
    "unit_end_utf16",
)

READER_D6_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    USER_ANNOTATIONS_TABLE: READER_D6_ANCHOR_COLUMNS,
    READER_NOTES_TABLE: READER_D6_ANCHOR_COLUMNS,
}

# DATA-SCHEMA-BASELINE D2: legacy dual-contract columns are dropped from the
# baseline, so there are no legacy columns left to require-nullable.
READER_D6_REQUIRED_NULLABLE_COLUMNS: dict[str, tuple[str, ...]] = {}

READER_D6_REQUIRED_INDEXES: dict[str, tuple[str, ...]] = {
    USER_ANNOTATIONS_TABLE: (
        "idx_user_annotations_reading_record",
        "uq_user_annotations_reading_record_anchor",
    ),
    READER_NOTES_TABLE: (
        "idx_reader_notes_reading_record",
        "uq_reader_notes_reading_record_anchor",
    ),
}

READER_D6_REQUIRED_CHECK_CONSTRAINT_SNIPPETS: dict[
    str, dict[str, tuple[str, ...]]
] = {
    USER_ANNOTATIONS_TABLE: {
        "user_annotations_text_anchor_payload_check": (
            "anchor_type = 'text_range'",
            "reading_record_id IS NOT NULL",
            "base_id IS NOT NULL",
            "generation IS NOT NULL",
            "unit_id IS NOT NULL",
            "anchor_segment_id IS NOT NULL",
            "unit_start_utf16 IS NOT NULL",
            "unit_end_utf16 IS NOT NULL",
            "paragraph_id IS NULL",
            "sentence_id IS NULL",
            "start_offset IS NULL",
            "end_offset IS NULL",
        ),
    },
}


@dataclass(frozen=True, slots=True)
class ReaderD5SchemaHealthReport:
    schema_name: str
    missing_columns: tuple[str, ...]
    missing_indexes: tuple[str, ...]
    missing_constraints: tuple[str, ...]
    invalid_constraints: tuple[str, ...] = ()
    non_nullable_columns: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (
            self.missing_columns
            or self.missing_indexes
            or self.missing_constraints
            or self.invalid_constraints
            or self.non_nullable_columns
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_name": self.schema_name,
            "ok": self.ok,
            "failure_codes": list(reader_schema_health_failure_codes(self)),
            "missing_columns": list(self.missing_columns),
            "missing_indexes": list(self.missing_indexes),
            "missing_constraints": list(self.missing_constraints),
            "invalid_constraints": list(self.invalid_constraints),
            "non_nullable_columns": list(self.non_nullable_columns),
        }


def _has_reader_d6_schema_drift(report: ReaderD5SchemaHealthReport) -> bool:
    d6_table_prefixes = (
        f"{USER_ANNOTATIONS_TABLE}.",
        f"{READER_NOTES_TABLE}.",
    )
    d6_constraint_names = {
        constraint_name
        for constraint_by_table in READER_D6_REQUIRED_CHECK_CONSTRAINT_SNIPPETS.values()
        for constraint_name in constraint_by_table
    }
    return any(
        name.startswith(d6_table_prefixes)
        for name in (
            *report.missing_columns,
            *report.invalid_constraints,
            *report.non_nullable_columns,
        )
    ) or any(
        index_name in index_names
        for index_names in READER_D6_REQUIRED_INDEXES.values()
        for index_name in report.missing_indexes
    ) or any(
        constraint_name in d6_constraint_names
        for constraint_name in report.missing_constraints
    )


def _has_reader_d5_schema_drift(report: ReaderD5SchemaHealthReport) -> bool:
    d5_table_prefixes = (
        f"{AI_USAGE_EVENTS_TABLE}.",
        f"{USER_CREDIT_LEDGER_TABLE}.",
    )
    d5_constraint_names = {
        constraint_name
        for constraint_names in READER_D5_REQUIRED_CONSTRAINTS.values()
        for constraint_name in constraint_names
    }
    return any(
        constraint_name in d5_constraint_names
        for constraint_name in report.missing_constraints
    ) or any(
        name.startswith(d5_table_prefixes) for name in report.missing_columns
    ) or any(
        index_name in index_names
        for index_names in READER_D5_REQUIRED_INDEXES.values()
        for index_name in report.missing_indexes
    )


def reader_schema_health_failure_codes(
    report: ReaderD5SchemaHealthReport,
) -> tuple[str, ...]:
    codes: list[str] = []
    if _has_reader_d5_schema_drift(report):
        codes.append("reader_d5_attribution_schema_drift")
    if _has_reader_d6_schema_drift(report):
        codes.append("reader_d6_anchor_migration_missing")
    return tuple(codes)


def _normalize_constraint_definition(value: str) -> str:
    return " ".join(value.upper().split())


def format_reader_d5_schema_health_failure(
    report: ReaderD5SchemaHealthReport,
) -> str:
    lines = [
        f"Reader schema health check failed for schema '{report.schema_name}'.",
    ]
    lines.extend(f"- missing column: {name}" for name in report.missing_columns)
    lines.extend(f"- missing index: {name}" for name in report.missing_indexes)
    lines.extend(
        f"- missing constraint: {name}" for name in report.missing_constraints
    )
    lines.extend(
        f"- invalid constraint: {name}" for name in report.invalid_constraints
    )
    lines.extend(
        f"- column must allow NULL: {name}" for name in report.non_nullable_columns
    )
    if _has_reader_d6_schema_drift(report):
        lines.extend(
            (
                "",
                "D6 Reading Record user asset schema is incomplete.",
                "Required baseline: `infra/migrations/0001_initial.sql`.",
                "Old Docker volumes do not automatically re-run "
                "`/docker-entrypoint-initdb.d/` when the baseline changes.",
                "To keep existing local data, reset or rebuild the local "
                "development database, or apply the baseline once manually:",
                "  docker cp "
                ".\\infra\\migrations\\0001_initial.sql "
                "claread-postgres:/tmp/0001_initial.sql",
                "  docker exec claread-postgres psql -v ON_ERROR_STOP=1 "
                "-U claread -d claread "
                "-f /tmp/0001_initial.sql",
            )
        )
    if _has_reader_d5_schema_drift(report):
        lines.extend(
            (
                "",
                "D5 attribution schema is incomplete.",
            )
        )
    lines.extend(
        (
            "The current repo baseline includes these D5 attribution fields, "
            "indexes, and FKs plus the D6 user asset anchor columns/indexes in "
            "`infra/migrations/0001_initial.sql`.",
            "This usually means your local database was created before the current "
            "fresh baseline or was only partially reset.",
            "Recommended fix:",
            "1. Reset or rebuild the local development database, or apply the "
            "baseline manually.",
            "2. Re-apply `infra/migrations/0001_initial.sql` as needed.",
            "3. Re-run `uv run python scripts/check_reader_schema_health.py`.",
        )
    )
    return "\n".join(lines)


async def check_reader_d5_schema_health(
    conn: asyncpg.Connection,
    *,
    schema_name: str = "public",
) -> ReaderD5SchemaHealthReport:
    required_columns = {
        **READER_D5_REQUIRED_COLUMNS,
        **READER_D6_REQUIRED_COLUMNS,
    }
    required_indexes = {
        **READER_D5_REQUIRED_INDEXES,
        **READER_D6_REQUIRED_INDEXES,
    }
    required_check_constraints = {
        table_name: tuple(constraint_snippets.keys())
        for table_name, constraint_snippets in (
            READER_D6_REQUIRED_CHECK_CONSTRAINT_SNIPPETS.items()
        )
    }
    required_constraints = {
        **READER_D5_REQUIRED_CONSTRAINTS,
        **required_check_constraints,
    }
    required_tables = tuple(required_columns.keys())

    column_rows = await conn.fetch(
        """
        SELECT table_name, column_name, is_nullable
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
    nullable_columns = {
        (str(row["table_name"]), str(row["column_name"]))
        for row in column_rows
        if str(row["is_nullable"]) == "YES"
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
        for constraint_names in required_constraints.values()
        for constraint_name in constraint_names
    )
    constraint_rows = await conn.fetch(
        """
        SELECT
            t.relname AS table_name,
            c.conname AS constraint_name,
            pg_get_constraintdef(c.oid) AS constraint_definition
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
    constraint_definitions = {
        (str(row["table_name"]), str(row["constraint_name"])): str(
            row["constraint_definition"]
        )
        for row in constraint_rows
    }

    missing_columns = tuple(
        f"{table_name}.{column_name}"
        for table_name, column_names in required_columns.items()
        for column_name in column_names
        if (table_name, column_name) not in existing_columns
    )
    missing_indexes = tuple(
        index_name
        for table_name, index_names in required_indexes.items()
        for index_name in index_names
        if (table_name, index_name) not in existing_indexes
    )
    missing_constraints = tuple(
        constraint_name
        for table_name, constraint_names in required_constraints.items()
        for constraint_name in constraint_names
        if (table_name, constraint_name) not in existing_constraints
    )
    invalid_constraints = tuple(
        f"{table_name}.{constraint_name}"
        for table_name, constraint_snippets in (
            READER_D6_REQUIRED_CHECK_CONSTRAINT_SNIPPETS.items()
        )
        for constraint_name, required_snippets in constraint_snippets.items()
        if (table_name, constraint_name) in existing_constraints
        and not all(
            _normalize_constraint_definition(snippet)
            in _normalize_constraint_definition(
                constraint_definitions[(table_name, constraint_name)]
            )
            for snippet in required_snippets
        )
    )
    non_nullable_columns = tuple(
        f"{table_name}.{column_name}"
        for table_name, column_names in READER_D6_REQUIRED_NULLABLE_COLUMNS.items()
        for column_name in column_names
        if (table_name, column_name) in existing_columns
        and (table_name, column_name) not in nullable_columns
    )

    return ReaderD5SchemaHealthReport(
        schema_name=schema_name,
        missing_columns=missing_columns,
        missing_indexes=missing_indexes,
        missing_constraints=missing_constraints,
        invalid_constraints=invalid_constraints,
        non_nullable_columns=non_nullable_columns,
    )
