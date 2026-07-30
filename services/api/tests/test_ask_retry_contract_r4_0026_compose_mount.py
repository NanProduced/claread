"""ASK-RETRY-CONTRACT-R4 P0: 0026 compose mount pin (task-scoped).

Does NOT assert full top-level migration coverage — residual 0020/0025
missing mounts are pre-existing and out of scope for this task.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "infra" / "docker" / "docker-compose.local.yml"
MIGRATION_0026 = (
    REPO_ROOT
    / "infra"
    / "migrations"
    / "0026_reader_ask_client_submission_idempotency.sql"
)


def test_compose_mounts_0026_after_0024() -> None:
    assert MIGRATION_0026.is_file(), "0026 migration file must exist"
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    dest_0024 = (
        "/docker-entrypoint-initdb.d/"
        "0024_reader_ask_turn_runs_reasoning_projection.sql"
    )
    dest_0026 = (
        "/docker-entrypoint-initdb.d/"
        "0026_reader_ask_client_submission_idempotency.sql"
    )
    assert dest_0026 in compose, (
        "docker-compose.local.yml must mount 0026 into initdb "
        "(ASK-RETRY-CONTRACT-R4 P0)"
    )
    assert dest_0024 in compose
    assert compose.index(dest_0024) < compose.index(dest_0026), (
        "0026 mount must appear after 0024 in compose"
    )


def test_0026_sql_defines_submission_table_and_lease() -> None:
    sql = MIGRATION_0026.read_text(encoding="utf-8")
    assert "reader_ask_client_submissions" in sql
    assert "PRIMARY KEY (thread_id, client_submission_id)" in sql
    assert "lease_expires_at" in sql
    assert "AUTHORED, NOT EXECUTED" in sql or "NOT EXECUTED" in sql
