from __future__ import annotations

import pytest

from app.llm.call_guard import pop_blocked_real_llm_attempts


@pytest.fixture(autouse=True)
def fail_on_real_llm_attempts():
    pop_blocked_real_llm_attempts()
    yield
    attempts = pop_blocked_real_llm_attempts()
    if not attempts:
        return

    details = "; ".join(
        (
            f"{attempt.surface} route={attempt.route} "
            f"profile={attempt.profile_name} provider={attempt.provider} "
            f"model={attempt.model_name}"
        )
        for attempt in attempts
    )
    pytest.fail(
        "Test attempted to call a real LLM provider. "
        "Mock the LLM boundary or run an explicit integration test with "
        f"CLAREAD_ALLOW_REAL_LLM_TESTS=1. Attempts: {details}"
    )
