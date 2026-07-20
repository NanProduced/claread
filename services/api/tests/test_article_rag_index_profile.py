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
import traceback
from dataclasses import FrozenInstanceError
from dataclasses import fields as dataclass_fields

import pytest

from app.services.reader_orchestration.article_rag_index_profile import (
    DEFAULT_ARTICLE_RAG_INDEX_VERSION,
    ArticleRagIndexProfile,
    ArticleRagIndexProfileResolution,
    ArticleRagIndexProfileResolutionError,
    compute_article_rag_index_profile_fingerprint,
    resolve_article_rag_index_evaluation_profile,
    resolve_article_rag_index_profile,
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


# ===========================================================================
# P1-B: Immutable V1 IndexProfile registry and resolver
# ===========================================================================
#
# These tests exercise the public resolver seam only.  They do NOT
# import the internal registry or any private resolver helper.  The
# resolver is the sole entry point for callers to obtain a profile
# identity (profile + fingerprint) from an ``index_version`` string.
#
# The registry:
#
#   * is built from a single frozen V1 resolution
#   * exposes a ``MappingProxyType``-backed, read-only mapping
#   * has no runtime register/override/mutation/environment hook
#   * never reads Settings, env vars, model registry, Zilliz, or any
#     bootstrap/plan/retrieval/vector-store module
#
# The resolver:
#
#   * returns a frozen ``ArticleRagIndexProfileResolution``
#   * fail-closes on any unregistered, blank, whitespace-padded,
#     non-string, or malicious ``index_version``
#   * never echoes, truncates, or persists the offending input


# ---------------------------------------------------------------------------
# Tracer bullet: resolver + DEFAULT constant + Resolution type exist
# ---------------------------------------------------------------------------


def test_tracer_bullet_resolver_default_returns_resolution():
    """RED-first tracer bullet for P1-B public resolver seam.

    The resolver MUST be importable and return an
    :class:`ArticleRagIndexProfileResolution` when given the default
    version constant.  RED before P1-B implementation: the symbols
    ``resolve_article_rag_index_profile``,
    ``DEFAULT_ARTICLE_RAG_INDEX_VERSION``, and
    ``ArticleRagIndexProfileResolution`` do not exist in the module.
    """
    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    assert isinstance(resolution, ArticleRagIndexProfileResolution)
    assert isinstance(resolution.profile, ArticleRagIndexProfile)
    assert isinstance(resolution.profile_fingerprint, str)


# ---------------------------------------------------------------------------
# DEFAULT constant points to V1
# ---------------------------------------------------------------------------


def test_default_article_rag_index_version_is_v1():
    assert DEFAULT_ARTICLE_RAG_INDEX_VERSION == "article_rag_index_v1"


# ---------------------------------------------------------------------------
# V1 mapping: 11 fields exactly match the frozen contract
# ---------------------------------------------------------------------------


def test_resolver_returns_v1_profile_with_exact_11_fields():
    """The resolved V1 profile MUST match the frozen V1 mapping
    field-for-field.  This is the canonical V1 identity."""
    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    p = resolution.profile
    assert p.index_version == "article_rag_index_v1"
    assert p.plan_version == "article_rag_index_plan_v1"
    assert p.chunker_version == "article_rag_index_plan_v1"
    assert p.document_embedding_model == "text-embedding-v4"
    assert p.document_embedding_dimension == 1024
    assert p.document_embedding_text_type == "provider_default"
    assert p.query_embedding_model == "text-embedding-v4"
    assert p.query_embedding_text_type == "provider_default"
    assert p.vector_namespace == "article_rag_index_v1"
    assert p.retrieval_schema_version == "article_rag_retrieval_v1"
    assert p.citation_mode_version == "article_rag_citation_v1"


# ---------------------------------------------------------------------------
# Fingerprint shape + matches compute helper
# ---------------------------------------------------------------------------


def test_resolver_fingerprint_is_sha256_hex_and_matches_compute():
    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    fp = resolution.profile_fingerprint
    assert isinstance(fp, str)
    assert len(fp) == 64
    assert fp == fp.lower()
    int(fp, 16)
    assert fp == compute_article_rag_index_profile_fingerprint(
        resolution.profile
    )


# ---------------------------------------------------------------------------
# V1 golden digest: precomputed fixed literal + independent cross-check
# ---------------------------------------------------------------------------


# Precomputed golden digest for the V1 profile.  Populated after the
# first GREEN run; left empty with an explicit pytest.fail guard until
# then.  If the V1 mapping, canonical payload, or schema identity
# changes, this digest will mismatch — which is the intended
# regression signal.
GOLDEN_DIGEST_V1_PROFILE = (
    "e443f581eb3e86aeb9dbcdcee806783186bd85da6c987c60357b61905ea86d6d"
)


def test_v1_golden_digest_matches_resolver_fingerprint():
    """The resolver's ``profile_fingerprint`` MUST equal the precomputed
    golden literal, AND must equal an independent SHA-256 recomputed
    from a hardcoded canonical payload (no production helper use)."""
    if not GOLDEN_DIGEST_V1_PROFILE:
        pytest.fail(
            "GOLDEN_DIGEST_V1_PROFILE not yet populated — run the "
            "production fingerprint once and paste the literal here."
        )
    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    assert resolution.profile_fingerprint == GOLDEN_DIGEST_V1_PROFILE

    # Independent cross-check: hardcode the canonical payload and hash
    # it directly, without using any production helper.
    expected_payload = {
        "$schema": "article_rag_index_profile_fingerprint_v1",
        "index_version": "article_rag_index_v1",
        "plan_version": "article_rag_index_plan_v1",
        "chunker_version": "article_rag_index_plan_v1",
        "document_embedding_model": "text-embedding-v4",
        "document_embedding_dimension": 1024,
        "document_embedding_text_type": "provider_default",
        "query_embedding_model": "text-embedding-v4",
        "query_embedding_text_type": "provider_default",
        "vector_namespace": "article_rag_index_v1",
        "retrieval_schema_version": "article_rag_retrieval_v1",
        "citation_mode_version": "article_rag_citation_v1",
    }
    raw = json.dumps(expected_payload, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert resolution.profile_fingerprint == expected


# ---------------------------------------------------------------------------
# Stability across repeated resolve calls
# ---------------------------------------------------------------------------


def test_repeated_resolve_returns_stable_result():
    a = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    b = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    assert a.profile_fingerprint == b.profile_fingerprint
    assert a.profile == b.profile


def test_repeated_resolve_returns_same_singleton_instance():
    """Characterize the current in-process registry singleton.

    Durable identity and correctness rely on profile-fingerprint
    equality, never Python object identity across processes, database
    reads, or task recovery.
    """
    a = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    b = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    assert a is b


# ---------------------------------------------------------------------------
# Resolution constructor invariant: fingerprint must match profile
# ---------------------------------------------------------------------------
#
# The public :class:`ArticleRagIndexProfileResolution` value object is
# part of the module's public API.  It MUST NOT be constructible with a
# ``profile_fingerprint`` that does not precisely equal
# :func:`compute_article_rag_index_profile_fingerprint` applied to its
# ``profile``.  Without this invariant, callers could build an
# "immutable-but-invalid" resolution and later migrations/workers
# could not trust ``resolution.profile_fingerprint``.


def test_resolution_constructor_accepts_valid_profile_and_fingerprint():
    """A correctly-paired (profile, fingerprint) MUST construct
    successfully.  This guards against an over-strict ``__post_init__``
    that rejects legitimate values.
    """
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    rebuilt = ArticleRagIndexProfileResolution(
        profile=valid.profile,
        profile_fingerprint=valid.profile_fingerprint,
    )
    assert rebuilt.profile_fingerprint == valid.profile_fingerprint
    assert rebuilt.profile == valid.profile


def test_resolution_constructor_rejects_mismatched_fingerprint():
    """RED-first tracer: a mismatched fingerprint MUST raise ValueError.

    Before the fix, ``ArticleRagIndexProfileResolution`` had no
    ``__post_init__`` invariant, so the following construction
    succeeded — producing an immutable-but-invalid resolution.
    """
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)

    with pytest.raises(ValueError):
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint="not-a-sha256",
        )


@pytest.mark.parametrize(
    "bad_fingerprint",
    [
        # 64-char lowercase hex but wrong content.
        "0" * 64,
        "a" * 64,
        "f" * 64,
        # Uppercase digest of the correct value — must still be rejected
        # because the canonical fingerprint is lowercase hex.
        "",
        " " * 64,
    ],
)
def test_resolution_constructor_rejects_wrong_fingerprint_content(
    bad_fingerprint: str,
):
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    with pytest.raises(ValueError):
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint=bad_fingerprint,
        )


def test_resolution_constructor_rejects_uppercase_digest():
    """The canonical fingerprint is lowercase hex; an uppercase digest
    MUST be rejected even if it would be the same value in a
    case-insensitive comparison.
    """
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    uppercase_fp = valid.profile_fingerprint.upper()
    # Sanity: uppercase is not equal to lowercase.
    assert uppercase_fp != valid.profile_fingerprint
    with pytest.raises(ValueError):
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint=uppercase_fp,
        )


@pytest.mark.parametrize(
    "non_string_fingerprint",
    [None, True, False, 123, 1.5, [], {}],
)
def test_resolution_constructor_rejects_non_string_fingerprint(
    non_string_fingerprint: object,
):
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    with pytest.raises(TypeError):
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint=non_string_fingerprint,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "non_profile",
    [None, object(), dict(), [], "not-a-profile", 123, True],
)
def test_resolution_constructor_rejects_non_article_rag_index_profile(
    non_profile: object,
):
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    with pytest.raises(TypeError):
        ArticleRagIndexProfileResolution(
            profile=non_profile,  # type: ignore[arg-type]
            profile_fingerprint=valid.profile_fingerprint,
        )


_MALICIOUS_FINGERPRINTS = [
    "sk-1234567890abcdef",
    "https://malicious.example.com/path?token=secret",
    "<script>alert('xss')</script>",
    "密钥123",
    "' OR 1=1; --",
]


@pytest.mark.parametrize("malicious", _MALICIOUS_FINGERPRINTS)
def test_resolution_constructor_malicious_fingerprint_not_in_error(malicious: str):
    """A malicious ``profile_fingerprint`` MUST raise ValueError, and
    the offending input MUST NOT appear in ``str``, ``repr``,
    ``repr(vars)``, or ``traceback.format_exception``.
    """
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    with pytest.raises((ValueError, TypeError)) as exc_info:
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint=malicious,
        )
    err = exc_info.value
    for rendered in (str(err), repr(err), repr(vars(err))):
        assert malicious not in rendered, (
            f"malicious fingerprint leaked into error rendering: {rendered!r}"
        )
    tb_text = "".join(
        traceback.format_exception(type(err), err, err.__traceback__)
    )
    assert malicious not in tb_text, (
        f"malicious fingerprint leaked into traceback: {tb_text!r}"
    )


def test_resolution_constructor_error_messages_are_fixed_local_strings():
    """Error messages MUST be fixed local literals — no echo, no
    truncation, no interpolation of fingerprint or profile.
    """
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)

    # Mismatched fingerprint -> ValueError with fixed message.
    with pytest.raises(ValueError) as exc_info:
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint="not-a-sha256",
        )
    assert str(exc_info.value) == "profile_fingerprint must match profile"

    # Non-string fingerprint -> TypeError with fixed message.
    with pytest.raises(TypeError) as exc_info:
        ArticleRagIndexProfileResolution(
            profile=valid.profile,
            profile_fingerprint=123,  # type: ignore[arg-type]
        )
    assert str(exc_info.value) == "profile_fingerprint must be a str"

    # Non-profile -> TypeError with fixed message.
    with pytest.raises(TypeError) as exc_info:
        ArticleRagIndexProfileResolution(
            profile=object(),  # type: ignore[arg-type]
            profile_fingerprint=valid.profile_fingerprint,
        )
    assert str(exc_info.value) == "profile must be an ArticleRagIndexProfile"


def test_resolution_constructor_does_not_overwrite_caller_fingerprint():
    """The constructor MUST NOT silently overwrite a caller-supplied
    fingerprint with the computed one.  A mismatch MUST raise rather
    than auto-correct.
    """
    valid = resolve_article_rag_index_profile(DEFAULT_ARTICLE_RAG_INDEX_VERSION)
    computed = compute_article_rag_index_profile_fingerprint(valid.profile)
    # Sanity: the resolver's fingerprint already equals the computed one.
    assert valid.profile_fingerprint == computed

    # A correct (profile, fingerprint) pair is preserved verbatim.
    rebuilt = ArticleRagIndexProfileResolution(
        profile=valid.profile,
        profile_fingerprint=computed,
    )
    assert rebuilt.profile_fingerprint == computed


# ---------------------------------------------------------------------------
# Immutability: profile and resolution are frozen
# ---------------------------------------------------------------------------


def test_resolution_is_frozen():
    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    with pytest.raises(FrozenInstanceError):
        resolution.profile_fingerprint = "tampered"  # type: ignore[misc]


def test_resolved_profile_is_still_frozen():
    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    with pytest.raises(FrozenInstanceError):
        resolution.profile.index_version = "v2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Registry has no V2; unknown versions do not fall back
# ---------------------------------------------------------------------------


def test_registry_has_no_v2():
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_profile("article_rag_index_v2")


def test_unknown_version_does_not_fallback_to_default():
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_profile("article_rag_index_v999")


# ---------------------------------------------------------------------------
# Fail-closed: unregistered / blank / whitespace-padded / non-string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_input",
    [
        "",
        "   ",
        "\t\n",
        " article_rag_index_v1",
        "article_rag_index_v1 ",
        " article_rag_index_v1 ",
        "\narticle_rag_index_v1",
        "article_rag_index_v1\n",
        123,
        None,
        True,
        False,
        1.5,
        ["article_rag_index_v1"],
    ],
)
def test_unregistered_or_malformed_index_version_fail_closed(bad_input: object):
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_profile(bad_input)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Malicious index_version never appears in error rendering / traceback
# ---------------------------------------------------------------------------


_MALICIOUS_INPUTS = [
    "sk-1234567890abcdef",
    "https://malicious.example.com/path?token=secret",
    "<script>alert('xss')</script>",
    "密钥123",
    "article_rag_index_v1\napi_key=sk-leak",
    "article_rag_index_v1\r\n<html>",
    "' OR 1=1; --",
]


@pytest.mark.parametrize("malicious", _MALICIOUS_INPUTS)
def test_malicious_index_version_not_in_error_rendering(malicious: str):
    with pytest.raises(ArticleRagIndexProfileResolutionError) as exc_info:
        resolve_article_rag_index_profile(malicious)
    err = exc_info.value
    # Fixed local message — no echo, no truncation, no interpolation.
    assert str(err) == "Article RAG index profile is not registered"
    for rendered in (str(err), repr(err), repr(vars(err))):
        assert malicious not in rendered, (
            f"malicious input leaked into error rendering: {rendered!r}"
        )
    tb_text = "".join(
        traceback.format_exception(type(err), err, err.__traceback__)
    )
    assert malicious not in tb_text, (
        f"malicious input leaked into traceback: {tb_text!r}"
    )


# ---------------------------------------------------------------------------
# __all__ completeness
# ---------------------------------------------------------------------------


def test_all_public_names_exported():
    from app.services.reader_orchestration import article_rag_index_profile as mod

    expected = {
        "ArticleRagIndexProfile",
        "compute_article_rag_index_profile_fingerprint",
        "DEFAULT_ARTICLE_RAG_INDEX_VERSION",
        "ArticleRagIndexProfileResolution",
        "ArticleRagIndexProfileResolutionError",
        "resolve_article_rag_index_profile",
        "resolve_article_rag_index_evaluation_profile",
    }
    assert set(mod.__all__) == expected


# ---------------------------------------------------------------------------
# V1 characterization parity: bootstrap / plan / retrieval
# ---------------------------------------------------------------------------
#
# These imports are TEST-SIDE characterization only.  The production
# profile module MUST NOT import bootstrap/plan/retrieval (no reverse
# dependency).  We import them here to assert that the V1 mapping
# registered in the profile module matches the existing identity
# constants used by bootstrap / retrieval / plan.


def test_v1_characterization_parity_with_bootstrap_plan_retrieval():
    from app.services.reader_orchestration.article_rag_index_bootstrap import (
        DEFAULT_INDEX_VERSION as bootstrap_default,
    )
    from app.services.reader_orchestration.article_rag_index_plan import (
        CHUNKER_VERSION,
    )
    from app.services.reader_orchestration.article_rag_retrieval_service import (
        DEFAULT_INDEX_VERSION as retrieval_default,
    )

    resolution = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    )
    p = resolution.profile
    assert p.index_version == bootstrap_default
    assert p.index_version == retrieval_default
    assert p.plan_version == CHUNKER_VERSION
    assert p.chunker_version == CHUNKER_VERSION


# ===========================================================================
# P2-A Group A: V2a offline evaluation profile resolver
# ===========================================================================
#
# This block establishes a read-only offline V2a evaluation profile seam
# that is distinct from the production resolver.  The production
# resolver MUST remain fail-closed on ``"article_rag_index_v2"``; the
# evaluation resolver accepts ONLY the explicit V2a identity and
# rejects every other input (including V1) with a fixed local error
# message and clean exception chain.
#
# Frozen V2a canonical payload (independent recomputation literal):
#   {
#     "$schema": "article_rag_index_profile_fingerprint_v1",
#     "chunker_version": "article_rag_index_plan_v2a",
#     "citation_mode_version": "article_rag_citation_v2a_contiguous",
#     "document_embedding_dimension": 1024,
#     "document_embedding_model": "text-embedding-v4",
#     "document_embedding_text_type": "provider_default",
#     "index_version": "article_rag_index_v2",
#     "plan_version": "article_rag_index_plan_v2a",
#     "query_embedding_model": "text-embedding-v4",
#     "query_embedding_text_type": "provider_default",
#     "retrieval_schema_version": "article_rag_retrieval_v2a",
#     "vector_namespace": "article_rag_index_v2"
#   }
#
# Golden fingerprint (independent recomputation via sha256 over the
# JSON serialisation with sort_keys=True and separators=(",", ":")):
#   09ea6677c3b031b95449f23d2d0751f6d1d02ef3bc74bb59299dc99390e10ef1

_V2A_INDEX_VERSION = "article_rag_index_v2"
_V2A_PLAN_VERSION = "article_rag_index_plan_v2a"
_V2A_CHUNKER_VERSION = "article_rag_index_plan_v2a"
_V2A_DOCUMENT_EMBEDDING_MODEL = "text-embedding-v4"
_V2A_DOCUMENT_EMBEDDING_DIMENSION = 1024
_V2A_DOCUMENT_EMBEDDING_TEXT_TYPE = "provider_default"
_V2A_QUERY_EMBEDDING_MODEL = "text-embedding-v4"
_V2A_QUERY_EMBEDDING_TEXT_TYPE = "provider_default"
_V2A_VECTOR_NAMESPACE = "article_rag_index_v2"
_V2A_RETRIEVAL_SCHEMA_VERSION = "article_rag_retrieval_v2a"
_V2A_CITATION_MODE_VERSION = "article_rag_citation_v2a_contiguous"

# Precomputed golden V2a fingerprint.  This is a FIXED LITERAL — the
# test does NOT recompute it via the production hash algorithm.  If any
# canonical payload field changes, this digest will mismatch, which is
# the intended regression signal.
GOLDEN_DIGEST_V2A_PROFILE = (
    "09ea6677c3b031b95449f23d2d0751f6d1d02ef3bc74bb59299dc99390e10ef1"
)


def test_v2a_tracer_bullet_evaluation_resolver_returns_resolution():
    """RED-first tracer bullet: the evaluation resolver seam exists and
    returns a frozen ``ArticleRagIndexProfileResolution`` for the
    explicit V2a identity string."""
    resolution = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    )
    assert isinstance(resolution, ArticleRagIndexProfileResolution)
    assert isinstance(resolution.profile, ArticleRagIndexProfile)
    assert isinstance(resolution.profile_fingerprint, str)


def test_v2a_evaluation_resolution_is_frozen_singleton():
    """The same frozen resolution instance MUST be returned for every
    successful V2a resolve.  The resolver MUST NOT construct a new
    object per call (no per-call fingerprint recomputation side-effect
    on identity)."""
    a = resolve_article_rag_index_evaluation_profile(_V2A_INDEX_VERSION)
    b = resolve_article_rag_index_evaluation_profile(_V2A_INDEX_VERSION)
    assert a is b
    assert a.profile is b.profile
    assert a.profile_fingerprint == b.profile_fingerprint


def test_v2a_profile_fields_match_spec_exactly():
    """The 11 frozen V2a profile fields MUST equal the spec literals."""
    p = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    ).profile
    assert p.index_version == _V2A_INDEX_VERSION
    assert p.plan_version == _V2A_PLAN_VERSION
    assert p.chunker_version == _V2A_CHUNKER_VERSION
    assert p.document_embedding_model == _V2A_DOCUMENT_EMBEDDING_MODEL
    assert p.document_embedding_dimension == _V2A_DOCUMENT_EMBEDDING_DIMENSION
    assert p.document_embedding_text_type == (
        _V2A_DOCUMENT_EMBEDDING_TEXT_TYPE
    )
    assert p.query_embedding_model == _V2A_QUERY_EMBEDDING_MODEL
    assert p.query_embedding_text_type == _V2A_QUERY_EMBEDDING_TEXT_TYPE
    assert p.vector_namespace == _V2A_VECTOR_NAMESPACE
    assert p.retrieval_schema_version == _V2A_RETRIEVAL_SCHEMA_VERSION
    assert p.citation_mode_version == _V2A_CITATION_MODE_VERSION


def test_v2a_golden_fingerprint_matches_precomputed_literal():
    """The V2a profile fingerprint MUST equal the precomputed golden
    digest.  This proves the canonical bytes are stable across
    environments and Python versions, and that the V2a payload was
    constructed independently of the V1 fingerprint."""
    resolution = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    )
    assert resolution.profile_fingerprint == GOLDEN_DIGEST_V2A_PROFILE
    # Independent re-derivation from the profile value object to assert
    # the production hash algorithm and the frozen literal agree.
    independent = compute_article_rag_index_profile_fingerprint(
        resolution.profile
    )
    assert independent == GOLDEN_DIGEST_V2A_PROFILE


def test_v2a_profile_is_frozen_and_rejects_attribute_mutation():
    """The V2a profile value object MUST be frozen."""
    p = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    ).profile
    with pytest.raises(FrozenInstanceError):
        p.index_version = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        p.vector_namespace = "mutated"  # type: ignore[misc]


def test_v2a_canonical_payload_includes_schema_identity_only_once():
    """The V2a canonical payload MUST include the same fingerprint
    schema identity as V1, contain exactly 11 profile fields plus
    ``$schema``, and never include ``profile_fingerprint`` (no
    recursion)."""
    p = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    ).profile
    payload = p.canonical_payload()
    assert payload["$schema"] == "article_rag_index_profile_fingerprint_v1"
    assert "profile_fingerprint" not in payload
    assert len(payload) == 12  # 11 fields + $schema


def test_v2a_profile_is_distinct_from_v1_profile():
    """The V2a profile MUST differ from V1 in at least the version
    identity fields (index_version, plan_version, chunker_version,
    vector_namespace, retrieval_schema_version, citation_mode_version).
    Embedding model / dimension / text_type may legitimately match V1
    — they are independent axes."""
    v1 = resolve_article_rag_index_profile(
        DEFAULT_ARTICLE_RAG_INDEX_VERSION
    ).profile
    v2a = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    ).profile
    assert v2a.index_version != v1.index_version
    assert v2a.plan_version != v1.plan_version
    assert v2a.chunker_version != v1.chunker_version
    assert v2a.vector_namespace != v1.vector_namespace
    assert v2a.retrieval_schema_version != v1.retrieval_schema_version
    assert v2a.citation_mode_version != v1.citation_mode_version
    # Fingerprints must differ.
    v1_fp = compute_article_rag_index_profile_fingerprint(v1)
    v2a_fp = compute_article_rag_index_profile_fingerprint(v2a)
    assert v1_fp != v2a_fp


def test_production_resolver_remains_fail_closed_on_v2():
    """P2-A isolation contract: the production resolver MUST remain
    fail-closed on ``"article_rag_index_v2"``.  V1 is still the only
    registered production version.  This test will fail if V2 was
    accidentally added to the production ``_REGISTRY``."""
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_profile(_V2A_INDEX_VERSION)


def test_default_production_version_is_still_v1():
    """The default production index version MUST remain V1.  P2-A does
    not change ``DEFAULT_ARTICLE_RAG_INDEX_VERSION``."""
    assert DEFAULT_ARTICLE_RAG_INDEX_VERSION == "article_rag_index_v1"


@pytest.mark.parametrize(
    "bad_input",
    [
        None,
        True,
        False,
        0,
        1,
        -1,
        1.5,
        "",
        "   ",
        "\t\n",
        "article_rag_index_v1",
        "article_rag_index_v3",
        "article_rag_index",
        "v2",
        "article_rag_index_v2 ",
        " article_rag_index_v2",
        "ARTICLE_RAG_INDEX_V2",
        "article_rag_index_v2\n",
        "article_rag_index_v2\x00",
        "article_rag_index_v2; DROP TABLE users;",
        "article_rag_index_v2\x27--",
        b"article_rag_index_v2",
        ["article_rag_index_v2"],
        {"index_version": "article_rag_index_v2"},
        object(),
    ],
)
def test_evaluation_resolver_rejects_non_v2a_inputs(bad_input: object):
    """The evaluation resolver MUST fail-closed on every input that is
    not exactly ``"article_rag_index_v2"`` — including V1, unknown
    versions, non-string types, and malicious values."""
    with pytest.raises(ArticleRagIndexProfileResolutionError):
        resolve_article_rag_index_evaluation_profile(bad_input)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "malicious",
    [
        "article_rag_index_v2\x00",
        "article_rag_index_v2; DROP TABLE users;",
        "article_rag_index_v2\x27--",
        "article_rag_index_v2\nDROP TABLE",
        "article_rag_index_v2\x1b[31m",
        "<script>alert(1)</script>",
        "../../etc/passwd",
    ],
)
def test_evaluation_resolver_does_not_echo_malicious_input(malicious: str):
    """The evaluation resolver MUST never echo, truncate, or
    interpolate the offending input in the error's ``str``, ``repr``,
    ``args``, or traceback.  The fixed local error message is the only
    text surfaced."""
    err: ArticleRagIndexProfileResolutionError | None = None
    try:
        resolve_article_rag_index_evaluation_profile(malicious)
    except ArticleRagIndexProfileResolutionError as exc:
        err = exc
    assert err is not None, "resolver did not raise"
    assert malicious not in str(err)
    assert malicious not in repr(err)
    assert malicious not in " ".join(str(a) for a in err.args)
    tb_text = "".join(
        traceback.format_exception(type(err), err, err.__traceback__)
    )
    assert malicious not in tb_text


def test_evaluation_resolver_exception_has_no_cause_or_context():
    """When the evaluation resolver raises, ``__cause__`` and
    ``__context__`` MUST both be ``None``.  The fixed local error must
    not be raised ``from`` another exception, and must not be raised
    inside an ``except`` block that would leak a context chain."""
    err: ArticleRagIndexProfileResolutionError | None = None
    try:
        resolve_article_rag_index_evaluation_profile(
            "article_rag_index_v1"
        )
    except ArticleRagIndexProfileResolutionError as exc:
        err = exc
    assert err is not None
    assert err.__cause__ is None
    assert err.__context__ is None


def test_v2a_resolution_fingerprint_matches_profile():
    """The frozen ``profile_fingerprint`` on the V2a resolution MUST
    equal the canonical fingerprint of the V2a profile (the
    ``ArticleRagIndexProfileResolution`` post-init invariant holds)."""
    resolution = resolve_article_rag_index_evaluation_profile(
        _V2A_INDEX_VERSION
    )
    expected = compute_article_rag_index_profile_fingerprint(
        resolution.profile
    )
    assert resolution.profile_fingerprint == expected
