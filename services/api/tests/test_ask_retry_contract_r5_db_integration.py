"""ASK-RETRY-CONTRACT-R5 DB integration — OPT-IN only.

These tests require:
1. Local Postgres with migration 0026 **applied by Owner** (not by this agent).
2. Env ``CLAREAD_RUN_SUBMISSION_DB_TESTS=1``.
3. Working ``DB_POOL`` (same as other integration tests).

Without those conditions the module is skipped — unit tests in
``test_ask_retry_contract_r5.py`` remain the default gate.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAREAD_RUN_SUBMISSION_DB_TESTS") != "1",
    reason=(
        "opt-in: set CLAREAD_RUN_SUBMISSION_DB_TESTS=1 after Owner applies "
        "migration 0026 to local DB"
    ),
)


@pytest.mark.asyncio
async def test_concurrent_same_submission_key_one_pair() -> None:
    """Two concurrent ensures: only one user/assistant pair."""
    from app.database import connection as db_connection
    from app.services.reader_record_ask.repository import (
        ReaderRecordAskRepository,
    )
    from app.services.reader_record_ask.submission_gateway import (
        build_retry_snapshot,
        ensure_submission_for_send,
    )

    if db_connection.DB_POOL is None:
        pytest.skip("DB pool not initialized")

    # Minimal setup: need a real thread owned by a test user.
    # Owner must seed fixture data; this is a structural opt-in harness.
    pytest.skip(
        "requires seeded thread fixture — structural opt-in placeholder; "
        "implement against local seeded RR Ask thread when Owner enables"
    )
