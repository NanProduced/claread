from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

ALLOW_REAL_LLM_TESTS_ENV = "CLAREAD_ALLOW_REAL_LLM_TESTS"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_blocked_real_llm_attempts: list[BlockedRealLLMAttempt] = []


@dataclass(frozen=True)
class BlockedRealLLMAttempt:
    surface: str
    route: str
    profile_name: str
    provider: str
    model_name: str


class RealLLMCallBlockedError(RuntimeError):
    """Raised when a pytest run attempts to reach a real LLM provider."""


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY_ENV_VALUES


def is_pytest_active() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def real_llm_tests_allowed() -> bool:
    return _truthy_env(ALLOW_REAL_LLM_TESTS_ENV)


def _has_api_key(model_config: Any) -> bool:
    return bool(str(getattr(model_config, "api_key", "") or "").strip())


def assert_real_llm_allowed(surface: str, *, model_config: Any) -> None:
    """Fail closed for real provider calls during pytest.

    Unit tests should use fakes/mocks before reaching the provider runner. To run
    an intentional integration test against a real model, set
    ``CLAREAD_ALLOW_REAL_LLM_TESTS=1`` explicitly for that test command.
    """
    if not is_pytest_active() or real_llm_tests_allowed():
        return
    if model_config is None or not _has_api_key(model_config):
        return

    attempt = BlockedRealLLMAttempt(
        surface=surface,
        route=str(getattr(model_config, "route", "unknown")),
        profile_name=str(getattr(model_config, "profile_name", "unknown")),
        provider=str(getattr(model_config, "provider", "unknown")),
        model_name=str(getattr(model_config, "model_name", "unknown")),
    )
    _blocked_real_llm_attempts.append(attempt)
    raise RealLLMCallBlockedError(
        "Blocked real LLM call during pytest. "
        f"surface={attempt.surface}, route={attempt.route}, "
        f"profile={attempt.profile_name}, provider={attempt.provider}, "
        f"model={attempt.model_name}. "
        "Mock the LLM call in this test, or run intentional integration tests "
        f"with {ALLOW_REAL_LLM_TESTS_ENV}=1."
    )


def pop_blocked_real_llm_attempts() -> list[BlockedRealLLMAttempt]:
    attempts = list(_blocked_real_llm_attempts)
    _blocked_real_llm_attempts.clear()
    return attempts
