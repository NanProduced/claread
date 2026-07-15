"""Regression test for ``scripts/align_reader_events_event_type_check.py``.

The dev DB used to drift away from the canonical Python ``ReaderEventType``
literal set, causing the worker to crash with an asyncpg
``CheckViolationError`` on the first ``record_product_state_updated``
write. This test runs the alignment script in dry-run mode against a
fresh isolated schema built from the baseline migrations, then mutates
the constraint to drop ``record_product_state_updated`` and verifies the
alignment script restores it without touching unrelated constraints.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.database.connection import init_connection
from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL

pytestmark = pytest.mark.anyio

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
ALIGN_SCRIPT = API_ROOT / "scripts" / "align_reader_events_event_type_check.py"

CANONICAL_LITERAL_TYPES: tuple[str, ...] = (
    "article_ready",
    "record_product_state_updated",
    "layer_published",
    "layer_failed",
    "parsed_decision_updated",
    "record_state_changed",
    "action_required",
    "run_completed",
    "record_superseded",
    "projection_ops",
    "projection_reset_required",
)

def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for reader-events constraint integration tests")
    return database_url
def _run_script(*, schema_name: str, mode: str) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = env.get(
        "DATABASE_URL",
        _database_url(),
    )
    args = [
        sys.executable,
        str(ALIGN_SCRIPT),
        "--schema-name",
        schema_name,
    ]
    if mode == "dry":
        args.append("--dry-run")
    proc = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def _read_constraint_def(conn: asyncpg.Connection, name: str) -> str | None:
    return conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        WHERE conrelid = 'reader_events'::regclass
          AND contype = 'c'
          AND conname = $1
        """,
        name,
    )


def _strip_constraint_literals(defn: str) -> set[str]:
    return set(re.findall(r"'([a-z][a-z0-9_]*)'", defn))


@pytest.fixture
async def drifted_schema() -> str:
    schema_name = f"test_reader_events_align_{uuid4().hex}"
    admin = await asyncpg.connect(
        os.environ.get(
            "DATABASE_URL",
            _database_url(),
        )
    )
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        # Simulate the dev-DB drift: drop ``record_product_state_updated`` from
        # the canonical CHECK list and re-add a constraint that excludes it.
        await admin.execute("ALTER TABLE reader_events DROP CONSTRAINT reader_events_event_type_check")
        await admin.execute(
            """
            ALTER TABLE reader_events
            ADD CONSTRAINT reader_events_event_type_check
            CHECK (event_type = ANY (ARRAY[
              'article_ready'::text,
              'layer_published'::text,
              'layer_failed'::text,
              'parsed_decision_updated'::text,
              'record_state_changed'::text,
              'action_required'::text,
              'run_completed'::text,
              'record_superseded'::text,
              'projection_ops'::text,
              'projection_reset_required'::text
            ]))
            """
        )
        try:
            yield schema_name
        finally:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    finally:
        await admin.close()


async def test_alignment_script_dry_run_is_noop_when_constraint_already_matches(
    drifted_schema: str,
) -> None:
    # First run: should detect divergence and propose a fix (dry-run).
    rc, stdout, stderr = _run_script(schema_name=drifted_schema, mode="dry")
    assert rc == 0, stderr
    assert "missing in DB" in stdout
    assert "record_product_state_updated" in stdout


async def test_alignment_script_recovers_drifted_constraint(drifted_schema: str) -> None:
    # Apply alignment.
    rc, stdout, stderr = _run_script(schema_name=drifted_schema, mode="apply")
    assert rc == 0, stderr
    assert "Aligned constraint definition" in stdout

    # Verify against the canonical set derived from event_runtime.py.
    admin = await asyncpg.connect(
        os.environ.get(
            "DATABASE_URL",
            _database_url(),
        )
    )
    try:
        await admin.execute(f'SET search_path TO "{drifted_schema}", public')
        defn = await _read_constraint_def(admin, "reader_events_event_type_check")
        assert defn is not None
        assert _strip_constraint_literals(defn) == set(CANONICAL_LITERAL_TYPES)
        # And the sequence CHECK must still be intact (no collateral damage).
        seq_defn = await _read_constraint_def(admin, "reader_events_sequence_check")
        assert seq_defn is not None
        assert "sequence" in seq_defn
    finally:
        await admin.close()


async def test_alignment_script_leaves_matching_constraint_untouched() -> None:
    schema_name = f"test_reader_events_align_match_{uuid4().hex}"
    admin = await asyncpg.connect(
        os.environ.get(
            "DATABASE_URL",
            _database_url(),
        )
    )
    try:
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin.execute(f'SET search_path TO "{schema_name}", public')
        await admin.execute(BASELINE_SQL)
        rc, stdout, _ = _run_script(schema_name=schema_name, mode="apply")
        assert rc == 0
        assert "already matches" in stdout
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin.close()


async def test_canonical_extraction_matches_event_runtime_literal() -> None:
    """Defensive: ensure the script reads the same literals the code exposes."""
    runtime_path = API_ROOT / "app/services/reader_orchestration/event_runtime.py"
    src = runtime_path.read_text(encoding="utf-8")
    body_match = re.search(
        r"ReaderEventType\s*(?::\s*[^=]+)?=\s*Literal\[\s*([^\]]+)\]",
        src,
        flags=re.DOTALL,
    )
    assert body_match is not None, "ReaderEventType Literal not found"
    code_literals = tuple(re.findall(r'"([a-z][a-z0-9_]*)"', body_match.group(1)))
    assert set(code_literals) == set(CANONICAL_LITERAL_TYPES)
