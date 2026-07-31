"""One-call, opt-in real-provider acceptance for the thread-memory compactor.

Run only with the repository's triple real-LLM gate and explicitly authorize
``deepseek-v4-flash``.  The test intentionally reports no transcript or model
output and disables the Host retry so one pytest case makes at most one model
request.
"""

from __future__ import annotations

import os

import pytest

from app.llm.call_guard import real_llm_tests_allowed
from app.services.reader_record_ask.execution_config import CompactorBudgetConfig
from app.services.reader_record_ask.thread_memory.compactor import (
    run_thread_memory_compactor,
)


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_deepseek_flash_compactor_one_call_smoke() -> None:
    assert real_llm_tests_allowed()
    assert os.environ.get("CLAREAD_REAL_LLM_MODEL") == "deepseek-v4-flash"

    outcome = await run_thread_memory_compactor(
        canonical_messages=[
            {
                "id": "user-smoke-1",
                "role": "user",
                "content_md": "I want to understand intrinsic motivation.",
            },
            {
                "id": "assistant-smoke-1",
                "role": "assistant",
                "content_md": (
                    "Intrinsic motivation comes from interest in the activity "
                    "itself rather than an external reward."
                ),
            },
        ],
        ok_turn_runs=[],
        turn_range=(1, 1),
        host_bindings={},
        budget=CompactorBudgetConfig(
            max_output_tokens=512,
            timeout_seconds=10.0,
            retry_count=0,
        ),
    )

    assert outcome.detail_code == "ok"
    assert outcome.attempt_count == 1
    assert outcome.episode is not None
    assert outcome.episode.compaction_model == "deepseek-v4-flash"
    assert outcome.episode.compaction_method == "model"
    assert outcome.episode.structured_facts
