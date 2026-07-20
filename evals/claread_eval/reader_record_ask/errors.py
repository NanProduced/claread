"""Safe error projection for R4-A3 harness artifacts and reports.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: 安全错误投影（P1-2）.

The previous harness wrote ``f"{type(exc).__name__}: {str(exc)[:200]}"``
into ``RawArtifact.error``. Truncation is NOT sanitization — provider
exceptions routinely embed:

- API keys (``Authorization: Bearer sk-...``)
- URL query strings (``?key=...&model=...``)
- request bodies (article text, user prompt)
- ``reasoning_content`` from thinking-mode responses
- internal provider diagnostics

This module replaces that pattern with an allowlisted projection:

- ``safe_code``: a short allowlisted code (e.g. ``"runtime_exception"``,
  ``"db_connect_failed"``). Unknown hints fall back to
  ``runtime_exception`` — fail-closed.
- ``exception_type``: the exception's class name only (e.g.
  ``"TimeoutError"``). This is metadata about the exception's category,
  not its content.
- ``safe_summary``: a fixed, allowlisted human-readable summary. The
  summary is looked up from ``_SAFE_SUMMARIES`` by ``safe_code``;
  unknown codes get the generic ``runtime_exception`` summary.

The exception's ``str(exc)`` is NEVER read. The projection is therefore
safe to persist into artifacts and aggregate reports.

The harness / runner use :func:`project_exception` to build the
``RawArtifact.error`` string. The string format is fixed:

    ``safe_code=<code> exception_type=<type>``

The ``safe_summary`` is NOT included in the artifact string (it's
constant per ``safe_code`` and can be looked up by the report reader).
This keeps the artifact compact and avoids the appearance of leaking
context.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Allowlisted safe codes + fixed summaries
# ---------------------------------------------------------------------------

#: Generic fallback when the harness does not know what kind of exception
#: it caught. This is the fail-closed default.
_SAFE_CODE_RUNTIME_EXCEPTION: Final[str] = "runtime_exception"

# R4-A4-2R5R Task 4: Single source of truth for safe error codes.
# ``SafeErrorCode`` is a ``Literal`` of every legal safe-code value.
# ``_SAFE_SUMMARIES`` is keyed by this Literal, and
# ``_RECOGNIZED_SAFE_CODES`` is derived from it. Downstream consumers
# (``RawArtifact.safe_error_code``, harness classification) import this
# Literal so there is exactly ONE allowlist — no duplicated copies in
# artifact.py and the harness.
SafeErrorCode = Literal[
    "runtime_exception",
    "db_connect_failed",
    "db_record_load_failed",
    "db_record_stale",
    "model_route_unresolved",
    "model_request_failed",
    "envelope_build_failed",
    "document_scope_build_failed",
    "budget_exhausted",
    "preflight_failed",
    "case_load_failed",
    "artifact_write_failed",
    # R4-A4-2R5/R4-A4-2R5R failure taxonomy: separate model-fault output
    # failures from provider/network/generic runtime exceptions. The
    # three codes below are emitted ONLY when the harness classifies the
    # exception type (e.g. ``UnexpectedModelBehavior`` from pydantic_ai)
    # — never via free-text parsing of ``str(exc)``. The exception's
    # message body is NOT read; only ``type(exc).__name__`` is used.
    "output_retry_exhausted",
    "agent_output_invalid",
    # R4-A4-2R5R2 Task 2: conservative fallback when
    # ``UnexpectedModelBehavior`` is raised but the harness CANNOT prove
    # the output-validator retry budget was exhausted. Proof requires
    # BOTH ``output_validation_final_attempts`` AND
    # ``output_validation_retry_requests`` to EXACTLY equal
    # ``DEFAULT_OUTPUT_RETRIES + 1`` (3). Any missing/unequal/undersized/
    # oversized counter, or a counter inflated by partial-only calls,
    # falls back to this conservative code. This covers pydantic-ai
    # internal errors (malformed JSON from model, invalid tool call,
    # etc.) that raise ``UnexpectedModelBehavior`` WITHOUT exhausting
    # the output validator, AND the case where the validator was called
    # 3 times but only 2 raised ModelRetry (the 3rd passed, then a
    # subsequent non-validator UMB occurred). Conservative — does NOT
    # claim retry exhaustion.
    "unexpected_model_behavior",
]

#: Allowlist of recognized safe codes. A code must be in this mapping to
#: be emitted; unknown hints fall back to ``runtime_exception``.
_SAFE_SUMMARIES: Final[dict[SafeErrorCode, str]] = {
    "runtime_exception": "Runtime exception during agent execution.",
    "db_connect_failed": "Failed to connect to the configured database.",
    "db_record_load_failed": "Failed to load the requested record from the database.",
    "db_record_stale": "The requested record is stale or inactive.",
    "model_route_unresolved": "The configured model route could not be resolved.",
    "model_request_failed": "The model provider rejected or failed the request.",
    "envelope_build_failed": "Failed to construct the context envelope.",
    "document_scope_build_failed": "Failed to construct the document scope.",
    "budget_exhausted": "The configured request or token budget was exhausted.",
    "preflight_failed": "Pre-flight check failed before any model call.",
    "case_load_failed": "Failed to load the requested eval case.",
    "artifact_write_failed": "Failed to write the artifact to the run directory.",
    "output_retry_exhausted": (
        "Agent output retry budget exhausted; the model did not produce "
        "valid structured output within the configured retry budget."
    ),
    "agent_output_invalid": (
        "Agent output failed final validation after retries."
    ),
    "unexpected_model_behavior": (
        "pydantic-ai raised UnexpectedModelBehavior without proof of "
        "output-validator retry exhaustion. Conservative classification."
    ),
}

#: Set of recognized safe codes — used by :func:`project_exception` to
#: validate the caller's hint without leaking the hint itself when it
#: is not in the allowlist.
_RECOGNIZED_SAFE_CODES: Final[frozenset[SafeErrorCode]] = frozenset(_SAFE_SUMMARIES.keys())


# ---------------------------------------------------------------------------
# SafeErrorProjection
# ---------------------------------------------------------------------------


class SafeErrorProjection(BaseModel):
    """Allowlisted, sanitised projection of an exception.

    Field semantics:

    - ``safe_code``: allowlisted short code. Always one of
      :data:`_RECOGNIZED_SAFE_CODES` (typed via :data:`SafeErrorCode`).
    - ``exception_type``: the exception's class name. This is metadata
      (e.g. ``"TimeoutError"``, ``"ValueError"``), not content.
    - ``safe_summary``: fixed, allowlisted summary string looked up by
      ``safe_code``. Never derived from the exception's ``str()``.
    """

    model_config = {"extra": "forbid"}

    safe_code: SafeErrorCode
    exception_type: str
    safe_summary: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def project_exception(
    exc: BaseException,
    *,
    hint: SafeErrorCode | str = _SAFE_CODE_RUNTIME_EXCEPTION,
) -> SafeErrorProjection:
    """Project ``exc`` into an allowlisted :class:`SafeErrorProjection`.

    The exception's ``str(exc)`` is NEVER read. Only ``type(exc)`` is
    used to extract the class name. The ``hint`` is matched against the
    allowlist; unknown hints fall back to ``runtime_exception`` —
    fail-closed.

    Args:
        exc: the exception to project.
        hint: a caller-supplied safe-code hint. Must be one of
            :data:`_RECOGNIZED_SAFE_CODES`; otherwise the projection
            uses ``runtime_exception``.

    Returns:
        A :class:`SafeErrorProjection` carrying only allowlisted data.
    """
    safe_code: SafeErrorCode = (
        hint if hint in _RECOGNIZED_SAFE_CODES else _SAFE_CODE_RUNTIME_EXCEPTION
    )
    return SafeErrorProjection(
        safe_code=safe_code,
        exception_type=type(exc).__name__,
        safe_summary=_SAFE_SUMMARIES[safe_code],
    )


def safe_error_string(projection: SafeErrorProjection) -> str:
    """Render the projection as a compact, fixed-format string.

    Format: ``safe_code=<code> exception_type=<type>``

    The ``safe_summary`` is intentionally NOT included in the string
    because:

    - It's constant per ``safe_code`` and can be looked up by the
      report reader (no information loss).
    - Including it would make artifact JSON verbose without adding
      signal.
    - It avoids giving the appearance of leaking context to a casual
      reader of the artifact file.
    """
    return f"safe_code={projection.safe_code} exception_type={projection.exception_type}"


def project_exception_to_string(
    exc: BaseException,
    *,
    hint: SafeErrorCode | str = _SAFE_CODE_RUNTIME_EXCEPTION,
) -> str:
    """Convenience: project ``exc`` and render as a string.

    Equivalent to ``safe_error_string(project_exception(exc, hint=hint))``.
    """
    return safe_error_string(project_exception(exc, hint=hint))


def is_recognized_safe_code(code: str) -> bool:
    """Return ``True`` if ``code`` is in the allowlist."""
    return code in _RECOGNIZED_SAFE_CODES
