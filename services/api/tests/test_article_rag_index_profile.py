"""Tests for P1-A Article RAG IndexProfile value object and fingerprint contract.

This module establishes the minimal deep-module foundation for the
Article RAG IndexProfile:

  * an immutable ``ArticleRagIndexProfile`` value object
  * a deterministic canonical fingerprint via
    ``compute_article_rag_index_profile_fingerprint``

The fingerprint contract is:

  * SHA-256 lowercase hex, length 64
  * computed over a fixed, versioned canonical JSON payload
  * stable across repeated calls and field-construction order
  * every profile field participates in the fingerprint
  * ``profile_fingerprint`` itself never recurses into the payload

No private helpers are imported.  Tests exercise only the public seam:
``ArticleRagIndexProfile`` construction and
``compute_article_rag_index_profile_fingerprint``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from dataclasses import fields as dataclass_fields

import pytest

from app.services.reader_orchestration.article_rag_index_profile import (
    ArticleRagIndexProfile,
    compute_article_rag_index_profile_fingerprint,
)


def _make_profile(**overrides: object) -> ArticleRagIndexProfile:
    """Build a profile with stable defaults; override via kwargs."""
    base: dict[str, object] = {
        "index_version": "v1",
        "plan_version": "plan-v1",
        "chunker_version": "chunker-v1",
        "document_embedding_model": "text-embedding-v4",
        "document_embedding_dimension": 1024,
        "document_embedding_text_type": "canonical_text",
        "query_embedding_model": "text-embedding-v4",
        "query_embedding_text_type": "raw_query",
        "vector_namespace": "article_rag_index_v1",
        "retrieval_schema_version": "retrieval-v1",
        "citation_mode_version": "citation-v1",
    }
    base.update(overrides)
    return ArticleRagIndexProfile(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tracer bullet: module imports + minimal fingerprint contract
# ---------------------------------------------------------------------------


def test_tracer_bullet_profile_constructs_and_fingerprint_is_sha256_hex():
    """RED-first tracer bullet: profile + fingerprint seam exists.

    Asserts the public seam is importable, the profile constructs with
    the 11 specified fields, and the fingerprint is a 64-character
    lowercase hex SHA-256 digest.
    """
    profile = _make_profile()
    fingerprint = compute_article_rag_index_profile_fingerprint(profile)

    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()
    # Must be valid hex.
    int(fingerprint, 16)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_profile_is_frozen_and_rejects_attribute_mutation():
    profile = _make_profile()
    with pytest.raises(FrozenInstanceError):
        profile.index_version = "v2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        profile.vector_namespace = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validation: empty / blank strings rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "index_version",
        "plan_version",
        "chunker_version",
        "document_embedding_model",
        "document_embedding_text_type",
        "query_embedding_model",
        "query_embedding_text_type",
        "vector_namespace",
        "retrieval_schema_version",
        "citation_mode_version",
    ],
)
@pytest.mark.parametrize("bad_value", ["", "   ", "\t\n"])
def test_profile_rejects_empty_or_blank_string_fields(
    field_name: str, bad_value: str
):
    with pytest.raises((ValueError, TypeError)):
        _make_profile(**{field_name: bad_value})


# ---------------------------------------------------------------------------
# Validation: dimension must be non-bool positive int
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_dim", [0, -1, -1024, True, False, 1.5, "1024", None])
def test_profile_rejects_invalid_dimension(bad_dim: object):
    with pytest.raises((ValueError, TypeError)):
        _make_profile(document_embedding_dimension=bad_dim)  # type: ignore[arg-type]


def test_profile_accepts_positive_int_dimension():
    for dim in (1, 768, 1024, 4096):
        profile = _make_profile(document_embedding_dimension=dim)
        assert profile.document_embedding_dimension == dim


# ---------------------------------------------------------------------------
# Fingerprint determinism
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_across_repeated_calls():
    profile = _make_profile()
    a = compute_article_rag_index_profile_fingerprint(profile)
    b = compute_article_rag_index_profile_fingerprint(profile)
    assert a == b


def test_fingerprint_is_stable_across_construction_order():
    """Two profiles with identical field VALUES must produce the same
    fingerprint, regardless of how the dataclass fields were ordered
    at construction time (dict insertion order must not matter)."""
    # Build the same profile twice via differently-ordered kwargs.
    profile_a = ArticleRagIndexProfile(
        index_version="v1",
        plan_version="plan-v1",
        chunker_version="chunker-v1",
        document_embedding_model="text-embedding-v4",
        document_embedding_dimension=1024,
        document_embedding_text_type="canonical_text",
        query_embedding_model="text-embedding-v4",
        query_embedding_text_type="raw_query",
        vector_namespace="article_rag_index_v1",
        retrieval_schema_version="retrieval-v1",
        citation_mode_version="citation-v1",
    )
    # Reverse kwargs order — dataclass construction is keyword-based,
    # so this stresses that the fingerprint does not depend on
    # ``__init__`` argument order.
    profile_b = ArticleRagIndexProfile(
        citation_mode_version="citation-v1",
        retrieval_schema_version="retrieval-v1",
        vector_namespace="article_rag_index_v1",
        query_embedding_text_type="raw_query",
        query_embedding_model="text-embedding-v4",
        document_embedding_text_type="canonical_text",
        document_embedding_dimension=1024,
        document_embedding_model="text-embedding-v4",
        chunker_version="chunker-v1",
        plan_version="plan-v1",
        index_version="v1",
    )
    a = compute_article_rag_index_profile_fingerprint(profile_a)
    b = compute_article_rag_index_profile_fingerprint(profile_b)
    assert a == b


# ---------------------------------------------------------------------------
# Every field participates in the fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_name", "alt_value"),
    [
        ("index_version", "v2"),
        ("plan_version", "plan-v2"),
        ("chunker_version", "chunker-v2"),
        ("document_embedding_model", "text-embedding-v3"),
        ("document_embedding_dimension", 768),
        ("document_embedding_text_type", "embedding_text"),
        ("query_embedding_model", "text-embedding-v3"),
        ("query_embedding_text_type", "rewritten_query"),
        ("vector_namespace", "article_rag_index_v2"),
        ("retrieval_schema_version", "retrieval-v2"),
        ("citation_mode_version", "citation-v2"),
    ],
)
def test_each_field_change_alters_fingerprint(
    field_name: str, alt_value: object
):
    base = _make_profile()
    altered = _make_profile(**{field_name: alt_value})
    base_fp = compute_article_rag_index_profile_fingerprint(base)
    altered_fp = compute_article_rag_index_profile_fingerprint(altered)
    assert base_fp != altered_fp, (
        f"field {field_name!r} did not affect fingerprint: "
        f"base={base_fp} altered={altered_fp}"
    )


# ---------------------------------------------------------------------------
# Canonical payload contract
# ---------------------------------------------------------------------------


def test_dataclass_fields_match_canonical_payload_fields():
    """Contract test: the dataclass field set MUST exactly equal the
    fields exposed by the public canonical payload.

    This catches drift between the dataclass declaration and canonical
    payload without coupling the test to private implementation details.
    """
    profile = _make_profile()
    dataclass_names = {
        field.name for field in dataclass_fields(ArticleRagIndexProfile)
    }
    payload_names = set(profile.canonical_payload()) - {"$schema"}

    assert payload_names == dataclass_names
    assert len(dataclass_names) == 11


def test_canonical_payload_is_versioned_detached_snapshot():
    """The canonical payload must:

      * include the fixed fingerprint schema identity under ``$schema``
      * NOT include ``profile_fingerprint`` (no recursion)
      * contain EXACTLY the 11 profile fields plus ``$schema`` — no
        more, no less (full dict equality, not subset)
    """
    profile = _make_profile()
    payload = profile.canonical_payload()

    expected_payload = {
        "$schema": "article_rag_index_profile_fingerprint_v1",
        "index_version": "v1",
        "plan_version": "plan-v1",
        "chunker_version": "chunker-v1",
        "document_embedding_model": "text-embedding-v4",
        "document_embedding_dimension": 1024,
        "document_embedding_text_type": "canonical_text",
        "query_embedding_model": "text-embedding-v4",
        "query_embedding_text_type": "raw_query",
        "vector_namespace": "article_rag_index_v1",
        "retrieval_schema_version": "retrieval-v1",
        "citation_mode_version": "citation-v1",
    }
    # Full dict equality — no subset, no extra keys.
    assert payload == expected_payload
    # profile_fingerprint itself is NOT in the payload (no recursion).
    assert "profile_fingerprint" not in payload
    # Payload must be JSON-serialisable with stable sort_keys.
    json.dumps(payload, sort_keys=True, separators=(",", ":"))


def test_canonical_payload_mutation_does_not_affect_profile_or_fingerprint():
    """The canonical payload is a detached snapshot.

    Mutating the returned dict must NOT change:
      * the profile's field values
      * subsequent fingerprint computations
      * subsequent ``canonical_payload()`` calls
    """
    profile = _make_profile()
    fingerprint_before = compute_article_rag_index_profile_fingerprint(profile)

    payload = profile.canonical_payload()
    # Mutate the returned dict.
    payload["$schema"] = "tampered"
    payload["index_version"] = "tampered"
    payload["bogus_key"] = "bogus"

    # Profile field values unchanged.
    assert profile.index_version == "v1"
    # Subsequent fingerprint unchanged.
    fingerprint_after = compute_article_rag_index_profile_fingerprint(profile)
    assert fingerprint_before == fingerprint_after
    # Subsequent canonical_payload() call returns a fresh, untampered dict.
    fresh_payload = profile.canonical_payload()
    assert fresh_payload["$schema"] == "article_rag_index_profile_fingerprint_v1"
    assert fresh_payload["index_version"] == "v1"
    assert "bogus_key" not in fresh_payload


# ---------------------------------------------------------------------------
# Golden digest: precomputed fixed expected digest
# ---------------------------------------------------------------------------


# Precomputed golden digest for the exact profile built by
# ``_make_profile()`` with the defaults shown above.  This is a
# FIXED LITERAL — the test does NOT recompute it via the production
# hash algorithm.  If the canonical payload or schema identity
# changes, this digest will mismatch and the test will fail, which
# is the intended regression signal.
#
# Generated once via:
#   python -c "
#   from app.services.reader_orchestration.article_rag_index_profile
#       import ArticleRagIndexProfile, compute_article_rag_index_profile_fingerprint
#   p = ArticleRagIndexProfile(
#       index_version='v1', plan_version='plan-v1',
#       chunker_version='chunker-v1',
#       document_embedding_model='text-embedding-v4',
#       document_embedding_dimension=1024,
#       document_embedding_text_type='canonical_text',
#       query_embedding_model='text-embedding-v4',
#       query_embedding_text_type='raw_query',
#       vector_namespace='article_rag_index_v1',
#       retrieval_schema_version='retrieval-v1',
#       citation_mode_version='citation-v1',
#   )
#   print(compute_article_rag_index_profile_fingerprint(p))
#   "
#
# Placeholder empty string until first GREEN run establishes it.
GOLDEN_DIGEST_DEFAULT_PROFILE = (
    "085f9002fedc8ae4c4fab03c8c85655bc9fd05cf9a953b0f56d757c81d4ea3a9"
)


def test_golden_digest_matches_default_profile_fingerprint():
    """The default profile's fingerprint must equal the precomputed
    golden digest.  This proves the canonical bytes are stable across
    environments and Python versions."""
    if not GOLDEN_DIGEST_DEFAULT_PROFILE:
        pytest.fail(
            "GOLDEN_DIGEST_DEFAULT_PROFILE not yet populated — run the "
            "production fingerprint once and paste the literal here."
        )
    profile = _make_profile()
    actual = compute_article_rag_index_profile_fingerprint(profile)
    assert actual == GOLDEN_DIGEST_DEFAULT_PROFILE
    # Cross-check: independently hash the canonical payload with
    # sort_keys=True, separators=(",", ":") to confirm the production
    # implementation uses that exact serialisation.
    payload = profile.canonical_payload()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert actual == expected


# ---------------------------------------------------------------------------
# No dedicated secret/content fields on the value object
# ---------------------------------------------------------------------------

# The value object has no dedicated secret/content fields.  This is NOT
# speculative sensitive-value detection — callers can pass arbitrary
# strings as profile identifiers.  The contract is simply that the
# dataclass declares no field whose NAME suggests a secret or content
# role (api_key, token, secret, uri, endpoint, text, body, etc.).
_NO_DEDICATED_SECRET_FIELDS = [
    "api_key",
    "token",
    "secret",
    "uri",
    "endpoint",
    "text",
    "body",
    "article_text",
    "sdk_object",
    "settings",
]


@pytest.mark.parametrize("field_name", _NO_DEDICATED_SECRET_FIELDS)
def test_profile_has_no_dedicated_secret_or_content_field(field_name: str):
    """The value object must not declare a field whose NAME suggests a
    secret or content role.  This is a structural contract on field
    names, not speculative detection of sensitive values — callers are
    responsible for passing only public profile identifiers."""
    profile = _make_profile()
    assert not hasattr(profile, field_name), (
        f"profile unexpectedly declares field: {field_name!r}"
    )
    # Repr check: the name must not appear as a field name pattern
    # (``name=``).  Substring matches inside legitimate field values
    # (e.g. ``canonical_text`` containing ``text``) are NOT field-name
    # leaks.
    rep = repr(profile)
    assert f"{field_name}=" not in rep, (
        f"profile repr exposes field name: {field_name!r}"
    )
    payload = profile.canonical_payload()
    assert field_name not in payload


def test_profile_repr_is_deterministic():
    profile = _make_profile()
    rep = repr(profile)
    # Dataclass repr identifies the value-object type and is deterministic.
    # Callers remain responsible for passing public identifiers only.
    assert "ArticleRagIndexProfile" in rep
    assert repr(profile) == rep


# ---------------------------------------------------------------------------
# Deterministic sanity: two unrelated profiles differ
# ---------------------------------------------------------------------------


def test_two_unrelated_profiles_have_different_fingerprints():
    a = _make_profile(index_version="v1")
    b = _make_profile(index_version="v1-alternate-fixed-string")
    assert compute_article_rag_index_profile_fingerprint(
        a
    ) != compute_article_rag_index_profile_fingerprint(b)
