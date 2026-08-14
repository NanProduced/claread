"""Runtime fixture identity (model-visible, deterministic).

Purpose
-------
Closes the audit gap: ``expected_envelope_fingerprint`` bound
the runtime envelope metadata (record_id / base_id / generation /
base_content_sha256 / visible_range) but did NOT bind the actual
model-visible baseline chunks. A BBC runtime record's envelope could
match while its baseline chunks contained a year (``2015``) the
dataset's ``allowed_temporal_claims`` did not declare — the evaluator
then misjudged a body-supported year as a hallucination.

The current contract replaces the envelope-only binding with a true
model-visible fixture identity: ``runtime_fixture_fingerprint`` — a
deterministic SHA-256 over

    baseline_status  +  is_complete  +  ordered (chunk_ordinal, chunk_text)

It EXCLUDES:

- random evidence handle_ids (``evh_<32 hex>`` — minted via
  ``secrets.token_hex(16)`` per assembly)
- absolute filesystem paths
- record UUIDs / base_ids / stable_document_ids
- timestamps / run_ids / session metadata

Two assemblies from the same snapshot (envelope + document_access)
produce the SAME hash because the assembler's chunk text and ordinal
are deterministic (derived from document scope + envelope). A change
to chunk content, order, truncation, or coverage (``is_complete``)
produces a DIFFERENT hash.

The fingerprint is computed in the harness preflight (before any
paid call) AND in the per-case run (after the assembler produces the
actual chunks). Both computations MUST agree because they operate on
the same deterministic inputs. The preflight computation catches
identity drift BEFORE the model is constructed; the per-case
computation persists the actual fingerprint on the artifact for
post-call audit.

This module is import-safe from both the harness
(``services/api/tests/``) and the aggregate
(``evals/scripts/``). It does NOT import from ``services/api/app/**``
— callers pass plain parameters (baseline_status, is_complete,
chunk tuples).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Framing version tag (stability fence)
# ---------------------------------------------------------------------------

#: Fixed framing version. Bumped only when the framing algorithm itself
#: changes. The tag is mixed into the hash FIRST so a fingerprint
#: computed under a different framing version never collides with one
#: computed under this version — even if the chunk content is identical.
#:
#: The trailing ``\x00`` separates the tag from the next field so a
#: hypothetical ``runtime_fixture_fingerprint/v10`` cannot prefix-match
#: ``runtime_fixture_fingerprint/v1``.
_FRAMING_VERSION_TAG: bytes = b"runtime_fixture_fingerprint/v1\x00"

# ---------------------------------------------------------------------------
# Public computation entrypoints
# ---------------------------------------------------------------------------

# Lightweight chunk view: (chunk_ordinal, chunk_text). The harness
# extracts these from ``BaselineAgentContext.model_context_chunks``
# (which carry random handle_ids we deliberately exclude).
RuntimeFixtureChunkView = tuple[int, str]


def compute_runtime_fixture_fingerprint(
    *,
    baseline_status: str,
    is_complete: bool,
    chunks: Sequence[RuntimeFixtureChunkView],
) -> str:
    """Compute the deterministic ``runtime_fixture_fingerprint``.

    Framing (length-prefixed, unambiguous):

    .. code-block:: text

        sha256(
            b"runtime_fixture_fingerprint/v1\\x00"
            || u64_be(len(baseline_status_utf8))  || baseline_status_utf8
            || u8(is_complete ? 1 : 0)
            || u64_be(chunk_count)
            for each chunk in chunk_ordinal order:
                || u64_be(chunk_ordinal)
                || u64_be(len(chunk_text_utf8))   || chunk_text_utf8
        )

    Excludes random evidence handle_ids, absolute paths, record UUIDs,
    stable_document_ids, base_ids, timestamps, and run_ids. Two
    assemblies from the same snapshot (envelope + document_access)
    produce the SAME hash because chunk text and ordinal are
    deterministic. A change to chunk content, order, truncation, or
    coverage (``is_complete``) produces a DIFFERENT hash.

    Args:
        baseline_status: One of ``"injected"``,
            ``"document_scope_unavailable"``, ``"envelope_mismatch"``,
            ``"no_units"``. The hash binds this string so a runtime
            that returns ``"envelope_mismatch"`` cannot silently pass
            as ``"injected"``.
        is_complete: ``True`` iff the full canonical article text
            entered the model without truncation. The hash binds this
            so a partial-baseline run cannot silently pass as
            complete.
        chunks: Ordered sequence of ``(chunk_ordinal, chunk_text)``.
            The function sorts by ``chunk_ordinal`` (defensive; the
            assembler already produces them in ordinal order) so a
            caller that passes them out of order still gets the same
            hash.

    Returns:
        Lowercase 64-char hex SHA-256 digest.
    """
    hasher = hashlib.sha256()
    hasher.update(_FRAMING_VERSION_TAG)

    # Baseline completeness/status.
    status_bytes = baseline_status.encode("utf-8")
    hasher.update(len(status_bytes).to_bytes(8, "big", signed=False))
    hasher.update(status_bytes)
    hasher.update(bytes([1 if is_complete else 0]))

    # Ordered model-visible chunk text (NO handle_id, NO
    # unit_id, NO base_id, NO record UUID, NO path, NO timestamp).
    sorted_chunks = sorted(chunks, key=lambda c: c[0])
    hasher.update(len(sorted_chunks).to_bytes(8, "big", signed=False))
    for ordinal, text in sorted_chunks:
        ordinal_bytes = int(ordinal).to_bytes(8, "big", signed=False)
        text_bytes = text.encode("utf-8")
        hasher.update(ordinal_bytes)
        hasher.update(len(text_bytes).to_bytes(8, "big", signed=False))
        hasher.update(text_bytes)

    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Semantic precheck: required atomic fact support
# ---------------------------------------------------------------------------

# Lightweight atomic-fact view: (fact_id, source_aliases, required).
# The harness extracts these from the case's expected atomic facts so
# this module does NOT import the schema.
RuntimeFixtureAtomicFactView = tuple[str, tuple[str, ...], bool]


def precheck_required_facts_support(
    *,
    atomic_facts: Sequence[RuntimeFixtureAtomicFactView],
    chunks: Sequence[RuntimeFixtureChunkView],
) -> list[str]:
    """Verify every required atomic fact is supported
    by ≥1 model-visible chunk BEFORE any paid provider call.

    For each ``required=True`` atomic fact with non-empty
    ``source_aliases``, at least one alias must be a case-insensitive
    substring of at least one chunk's text. A required fact with no
    supporting chunk makes the case an INVALID evaluation case — the
    dataset author declared a fact the fixture cannot ground, so the
    run would record ``fact_not_supported`` post-hoc instead of
    catching the error pre-call.

    Args:
        atomic_facts: Sequence of ``(fact_id, source_aliases, required)``.
            ``source_aliases`` is a tuple of canonical tokens from the
            article. ``required`` is True when the fact MUST be
            mentionable (False = informational only).
        chunks: Ordered sequence of ``(chunk_ordinal, chunk_text)`` —
            the actual model-visible chunks after the baseline
            assembler applied raw / serialized / chunk-count budgets.

    Returns:
        List of ``fact_id`` strings for required facts that are NOT
        supported by any chunk. Empty list when all required facts
        are supported (or when there are no required facts with
        non-empty source_aliases).

    The caller (harness preflight) treats a non-empty return as
    fail-closed: ``pytest.skip`` BEFORE the model is constructed,
    so provider calls = 0 and model builder calls = 0.

    Notes:
        - Case-insensitive substring match (matches the existing
          ``_compute_model_context_support`` semantics in the harness).
        - Empty / whitespace-only aliases are skipped (vacuously).
        - Facts with ``required=False`` are skipped (informational).
        - Facts with empty ``source_aliases`` are skipped (metadata-only).
        - When ``chunks`` is empty, EVERY required fact with non-empty
          aliases is unsupported — the caller fail-closes.
    """
    chunk_texts_lower = [text.lower() for _ordinal, text in chunks]

    unsupported: list[str] = []
    for fact_id, source_aliases, required in atomic_facts:
        if not required:
            continue
        aliases_lower = [a.lower() for a in source_aliases if a and a.strip()]
        if not aliases_lower:
            continue
        supported = False
        for chunk_text_lower in chunk_texts_lower:
            if any(alias in chunk_text_lower for alias in aliases_lower):
                supported = True
                break
        if not supported:
            unsupported.append(fact_id)
    return unsupported


# ---------------------------------------------------------------------------
# Validation helpers (used by aggregate three-layer check)
# ---------------------------------------------------------------------------

#: Strict SHA-256 lowercase hex pattern. Used to validate
#: ``expected_runtime_fixture_fingerprint`` on case schema and
#: ``runtime_fixture_fingerprint`` on artifact schema.
RUNTIME_FIXTURE_FINGERPRINT_PATTERN: str = r"^[0-9a-f]{64}$"


def is_valid_runtime_fixture_fingerprint(value: str | None) -> bool:
    """Return True iff ``value`` is a 64-char lowercase hex SHA-256.

    ``None`` / empty / wrong-length / uppercase / non-hex are all
    False. Used by the aggregate to validate persisted fingerprints
    before comparing them.
    """
    if not value or not isinstance(value, str):
        return False
    import re

    return bool(re.match(RUNTIME_FIXTURE_FINGERPRINT_PATTERN, value))
