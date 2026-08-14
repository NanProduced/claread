"""Tests for safe error projection.

Requirement: 安全错误投影.

Covers:
- ``project_exception`` returns allowlisted ``safe_code`` only.
- Unknown hints fall back to ``runtime_exception`` (fail-closed).
- Exception ``str(exc)`` is NEVER included in the projection.
- The rendered string does not leak: secret, URL query, API key,
  article body, ``reasoning_content``.
- ``exception_type`` is the class name (metadata, not content).
- ``safe_summary`` is fixed per ``safe_code`` (allowlisted).
"""

from __future__ import annotations

import pytest

from claread_eval.reader_record_ask.errors import (
    SafeErrorProjection,
    is_recognized_safe_code,
    project_exception,
    project_exception_to_string,
    safe_error_string,
)

# ---------------------------------------------------------------------------
# project_exception basics
# ---------------------------------------------------------------------------


def test_project_exception_returns_safe_code_for_known_hint() -> None:
    exc = RuntimeError("connection refused")
    projection = project_exception(exc, hint="db_connect_failed")
    assert projection.safe_code == "db_connect_failed"
    assert projection.exception_type == "RuntimeError"
    assert "Failed to connect to the configured database." == projection.safe_summary


def test_project_exception_unknown_hint_falls_back_to_runtime_exception() -> None:
    """Spec: "allowlisted safe error code" — unknown hints fail-closed."""
    exc = RuntimeError("connection refused")
    projection = project_exception(exc, hint="totally_unknown_hint")
    assert projection.safe_code == "runtime_exception"
    assert projection.exception_type == "RuntimeError"
    assert projection.safe_summary == "Runtime exception during agent execution."


def test_project_exception_default_hint_is_runtime_exception() -> None:
    exc = ValueError("bad input")
    projection = project_exception(exc)
    assert projection.safe_code == "runtime_exception"
    assert projection.exception_type == "ValueError"


def test_project_exception_returns_safe_error_projection_instance() -> None:
    exc = RuntimeError("x")
    projection = project_exception(exc)
    assert isinstance(projection, SafeErrorProjection)


# ---------------------------------------------------------------------------
# Exception str(exc) is NEVER included
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret_payload",
    [
        "api_key=sk-secret-abc123-with-many-characters",
        "Authorization: Bearer sk-abc123def456ghi789",
        "?key=sk-secret&model=deepseek-v4-pro",
        "reasoning_content=<very long internal chain of thought>",
        "BBC article body: The snowstorm affected Buffalo and surrounding areas...",
        "URL query: https://api.example.com/v1/chat?key=sk-secret-xyz",
        "Newline\ninjection\nattempt\nsk-secret",
        "Tab\tsk-secret\tinjection",
    ],
)
def test_project_exception_does_not_leak_secret_payload(secret_payload: str) -> None:
    """Spec: "不写异常原文; 日志也不得写 provider payload、query、正文、API key."

    The projection must NOT carry any of the exception's ``str(exc)``
    payload. Only the class name + allowlisted code + fixed summary.
    """
    exc = RuntimeError(secret_payload)
    projection = project_exception(exc, hint="model_request_failed")
    # The projection's three fields are all allowlisted / metadata.
    assert projection.safe_code == "model_request_failed"
    assert projection.exception_type == "RuntimeError"
    # safe_summary is from the allowlist, not from the exception text.
    assert projection.safe_summary == "The model provider rejected or failed the request."
    # The raw payload must not appear anywhere in the rendered string.
    rendered = safe_error_string(projection)
    assert secret_payload not in rendered
    # And the projection model_dump() must not carry the payload either.
    dumped = projection.model_dump()
    for field_value in dumped.values():
        assert secret_payload not in str(field_value)


def test_project_exception_does_not_read_str_exc() -> None:
    """Spec: "不写异常原文".

    A defensive test: even when ``str(exc)`` would raise (e.g. an
    exception whose ``__str__`` is broken), the projection must still
    succeed using only ``type(exc).__name__``.
    """

    class _BrokenStrException(Exception):
        def __str__(self) -> str:  # String conversion intentionally fails.
            raise RuntimeError("str(exc) is broken on purpose")

    exc = _BrokenStrException("never read this")
    projection = project_exception(exc, hint="runtime_exception")
    assert projection.exception_type == "_BrokenStrException"
    assert projection.safe_code == "runtime_exception"


# ---------------------------------------------------------------------------
# safe_error_string format
# ---------------------------------------------------------------------------


def test_safe_error_string_format() -> None:
    projection = SafeErrorProjection(
        safe_code="db_connect_failed",
        exception_type="TimeoutError",
        safe_summary="Failed to connect to the configured database.",
    )
    rendered = safe_error_string(projection)
    assert rendered == "safe_code=db_connect_failed exception_type=TimeoutError"


def test_safe_error_string_does_not_include_summary() -> None:
    """Spec: artifact string stays compact; summary is constant per code."""
    projection = SafeErrorProjection(
        safe_code="runtime_exception",
        exception_type="ValueError",
        safe_summary="Runtime exception during agent execution.",
    )
    rendered = safe_error_string(projection)
    # The summary is NOT in the rendered string — it's looked up by code.
    assert "Runtime exception during agent execution." not in rendered
    assert "safe_code=runtime_exception" in rendered
    assert "exception_type=ValueError" in rendered


def test_project_exception_to_string_convenience() -> None:
    exc = RuntimeError("ignored text")
    rendered = project_exception_to_string(exc, hint="budget_exhausted")
    assert rendered == "safe_code=budget_exhausted exception_type=RuntimeError"
    assert "ignored text" not in rendered


# ---------------------------------------------------------------------------
# is_recognized_safe_code
# ---------------------------------------------------------------------------


def test_is_recognized_safe_code_for_known_codes() -> None:
    assert is_recognized_safe_code("runtime_exception")
    assert is_recognized_safe_code("db_connect_failed")
    assert is_recognized_safe_code("budget_exhausted")


def test_is_recognized_safe_code_for_unknown_code() -> None:
    assert not is_recognized_safe_code("totally_unknown")
    assert not is_recognized_safe_code("")


# ---------------------------------------------------------------------------
# Regression: secret-bearing exception round-trip
# ---------------------------------------------------------------------------


def test_secret_bearing_exception_round_trip_to_artifact_error_field() -> None:
    """Simulate the harness writing ``RawArtifact.error``.

    The harness previously wrote ``f"{type(exc).__name__}: {str(exc)[:200]}"``.
    Now it must use ``project_exception_to_string(exc, hint=...)`` and
    the resulting string must NOT contain any of the exception's text.
    """
    secret = "sk-secret-abc123def456ghi789"  # imagine this is a real API key
    article_snippet = "Buffalo received 36 inches of snow during the storm."
    url_query = "https://api.deepseek.com/v1/chat?api_key=sk-secret"
    reasoning = "reasoning_content=<long internal chain of thought>"

    exc = ValueError(
        f"provider returned 401: headers={ {'Authorization': secret} } "
        f"url={url_query} body={ {'messages': [{'content': article_snippet}]} } "
        f"{reasoning}"
    )

    error_str = project_exception_to_string(exc, hint="model_request_failed")

    # The rendered artifact error string must not leak any payload.
    assert secret not in error_str
    assert article_snippet not in error_str
    assert url_query not in error_str
    assert "reasoning_content" not in error_str
    assert "401" not in error_str
    # Only the safe_code and exception_type are present.
    assert error_str == "safe_code=model_request_failed exception_type=ValueError"


def test_exception_with_unicode_and_newlines_does_not_leak() -> None:
    """Even exotic exception payloads must not leak through."""
    payload = "BBC 正文：纽约州西部部分地区受到暴风雪影响\napi_key=sk-secret-中文"
    exc = RuntimeError(payload)
    error_str = project_exception_to_string(exc, hint="runtime_exception")
    assert "纽约州" not in error_str
    assert "api_key" not in error_str
    assert "sk-secret" not in error_str
    assert "中文" not in error_str
    assert "\n" not in error_str
