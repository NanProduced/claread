"""Opt-in real LLM smoke tests for Ask Claread.

These tests are SKIPPED by default. To run them, ALL three conditions must be met:

    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_REAL_LLM_MODEL=qwen-plus \
        uv run pytest tests/test_reader_ask_real_llm_smoke.py -m real_llm -v

They call real LLM providers and incur cost. Only run when explicitly opted in.
The CLAREAD_REAL_LLM_MODEL variable must specify the authorized model name.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_ask_claread_real_llm_smoke_placeholder():
    """Placeholder: verify that the opt-in mechanism works.

    This test is skipped unless ALL of the following are set:
    - CLAREAD_ALLOW_REAL_LLM_TESTS=1
    - CLAREAD_REAL_LLM_MODEL=<authorized model name>
    - -m real_llm passed to pytest

    When the test runs, it confirms the triple gate is functional.

    To add real smoke scenarios:
    1. Read CLAREAD_REAL_LLM_MODEL to determine which model to use
    2. Build a ReaderAskAgentDeps with real callbacks
    3. Call get_reader_ask_agent() with the specified model
    4. Run agent.run() with a simple query
    5. Assert basic output properties (non-empty, has citations, etc.)

    IMPORTANT: Before adding real LLM calls, get explicit user authorization
    specifying: model, expected call count, and cost estimate.
    """
    # If we reach here, the triple gate is working.
    from app.llm.call_guard import real_llm_tests_allowed

    assert real_llm_tests_allowed(), "Should only run when CLAREAD_ALLOW_REAL_LLM_TESTS=1"
    assert os.environ.get("CLAREAD_REAL_LLM_MODEL"), (
        "Should only run when CLAREAD_REAL_LLM_MODEL is set"
    )
