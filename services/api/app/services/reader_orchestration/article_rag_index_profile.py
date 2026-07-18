"""P1-A + P1-B: Article RAG immutable IndexProfile, fingerprint, V1 registry/resolver.

This module is the minimal deep-module foundation for the Article RAG
IndexProfile.  It exposes three layers of public API:

P1-A — value object + fingerprint:

  * :class:`ArticleRagIndexProfile` — a frozen value object capturing
    the 11 identity fields that define a RAG index profile.
  * :func:`compute_article_rag_index_profile_fingerprint` — a
    deterministic SHA-256 fingerprint over a fixed, versioned
    canonical JSON payload.

P1-B — V1 registry + resolver:

  * :data:`DEFAULT_ARTICLE_RAG_INDEX_VERSION` — the single registered
    V1 index version string (``"article_rag_index_v1"``).
  * :class:`ArticleRagIndexProfileResolution` — a frozen
    ``(profile, profile_fingerprint)`` pair returned by the resolver.
  * :class:`ArticleRagIndexProfileResolutionError` — raised for any
    unregistered, blank, whitespace-padded, non-string, or malicious
    ``index_version``.
  * :func:`resolve_article_rag_index_profile` — the sole public entry
    point for callers to obtain a profile identity from an
    ``index_version`` string.

Design constraints (enforced by tests in
``tests/test_article_rag_index_profile.py``):

  * The value object is ``frozen=True`` and validates all fields at
    construction time.
  * String fields reject empty and pure-whitespace values.
  * ``document_embedding_dimension`` must be a non-bool positive ``int``.
  * The canonical payload uses a fixed fingerprint schema identity
    (``article_rag_index_profile_fingerprint_v1``) and stable JSON
    serialisation (``sort_keys=True``, ``separators=(",", ":")``).
  * Every profile field participates in the fingerprint.
  * ``profile_fingerprint`` itself never recurses into the payload.
  * The value object has no dedicated secret/content fields; callers
    must pass public profile identifiers only.
  * The registry is a ``MappingProxyType``-backed, read-only mapping
    of frozen resolutions; there is no runtime register, override,
    mutation, or environment hook.
  * The resolver fail-closes on any unregistered, blank,
    whitespace-padded, non-string, or malicious ``index_version``,
    and never echoes, truncates, or persists the offending input.

This module deliberately does NOT:

  * register any V2 profile
  * read Settings, environment variables, model registry, or Zilliz
  * import bootstrap, plan, retrieval, vector store, or worker (no
    reverse dependency, no future circular import)
  * implement schema migration
  * touch bootstrap, plan, worker, vector writer, or retrieval
  * add a profile table
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

# Fixed fingerprint schema identity.  Bumping this string (or any
# profile field) intentionally changes the resulting fingerprint,
# which is the desired regression signal when the canonical contract
# evolves.
_FINGERPRINT_SCHEMA_IDENTITY = "article_rag_index_profile_fingerprint_v1"

# Tuple of (field_name, expected_type) kept in a fixed, canonical order.
# This MUST be kept in sync with the dataclass field declarations below;
# ``test_dataclass_fields_match_canonical_payload_fields`` enforces that
# alignment through the public payload interface.  When adding a field:
# add it to the dataclass AND to this tuple, then regenerate the golden
# digest.
_PROFILE_FIELDS: tuple[tuple[str, type], ...] = (
    ("index_version", str),
    ("plan_version", str),
    ("chunker_version", str),
    ("document_embedding_model", str),
    ("document_embedding_dimension", int),
    ("document_embedding_text_type", str),
    ("query_embedding_model", str),
    ("query_embedding_text_type", str),
    ("vector_namespace", str),
    ("retrieval_schema_version", str),
    ("citation_mode_version", str),
)

# Field names that participate in the canonical payload, in the fixed
# order used for serialisation.  Mirrors ``_PROFILE_FIELDS`` minus the
# type annotation.
_CANONICAL_FIELD_ORDER: tuple[str, ...] = tuple(
    name for name, _ in _PROFILE_FIELDS
)


def _validate_non_blank_string(field_name: str, value: Any) -> str:
    """Reject non-str, empty, or whitespace-only values.

    The original value is never echoed in the error message to avoid
    leaking hostile content into logs.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a non-empty str, got {type(value).__name__}"
        )
    if not value or value.strip() != value or not value.strip():
        raise ValueError(
            f"{field_name} must be a non-empty, non-blank str"
        )
    return value


def _validate_positive_non_bool_int(field_name: str, value: Any) -> int:
    """Reject bool, non-int, and non-positive values.

    ``bool`` is a subclass of ``int`` in Python, so it is rejected
    explicitly to prevent ``True``/``False`` masquerading as ``1``/``0``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{field_name} must be a non-bool int, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValueError(
            f"{field_name} must be a positive int, got {value}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ArticleRagIndexProfile:
    """Immutable Article RAG index profile value object.

    Captures the 11 identity fields that define a RAG index profile.
    Construction validates every field; the resulting object is frozen
    and hashable.  The value object has no dedicated secret/content
    fields; callers must pass public profile identifiers only.
    """

    index_version: str
    plan_version: str
    chunker_version: str
    document_embedding_model: str
    document_embedding_dimension: int
    document_embedding_text_type: str
    query_embedding_model: str
    query_embedding_text_type: str
    vector_namespace: str
    retrieval_schema_version: str
    citation_mode_version: str

    def __post_init__(self) -> None:
        # Validate every string field with the shared non-blank rule.
        for field_name in _CANONICAL_FIELD_ORDER:
            if field_name == "document_embedding_dimension":
                continue
            value = getattr(self, field_name)
            object.__setattr__(
                self, field_name, _validate_non_blank_string(field_name, value)
            )
        # Validate dimension separately (non-bool positive int).
        object.__setattr__(
            self,
            "document_embedding_dimension",
            _validate_positive_non_bool_int(
                "document_embedding_dimension",
                self.document_embedding_dimension,
            ),
        )

    def canonical_payload(self) -> dict[str, Any]:
        """Return a detached canonical snapshot for fingerprinting.

        The returned dict is a fresh copy on each call.  It is NOT a
        read-only view — callers can mutate it, but mutation has no
        effect on the profile or on subsequent fingerprint computations.

        The payload:

          * includes the fixed fingerprint schema identity under
            ``$schema``
          * includes all 11 profile fields
          * does NOT include ``profile_fingerprint`` (no recursion)
          * is JSON-serialisable with ``sort_keys=True`` and
            ``separators=(",", ":")``
        """
        payload: dict[str, Any] = {"$schema": _FINGERPRINT_SCHEMA_IDENTITY}
        for field_name in _CANONICAL_FIELD_ORDER:
            payload[field_name] = getattr(self, field_name)
        return payload


def compute_article_rag_index_profile_fingerprint(
    profile: ArticleRagIndexProfile,
) -> str:
    """Compute the deterministic SHA-256 fingerprint of a profile.

    The fingerprint is a 64-character lowercase hex string derived
    from the canonical JSON payload (``sort_keys=True``,
    ``separators=(",", ":")``) which includes the fixed fingerprint
    schema identity under ``$schema``.

    Properties enforced by tests:

      * stable across repeated calls
      * stable across profile construction order
      * every profile field participates
      * ``profile_fingerprint`` itself never recurses into the payload
    """
    payload = profile.canonical_payload()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# P1-B: V1 registry + resolver
# ---------------------------------------------------------------------------

# The single registered V1 index version.  This constant is the only
# ``index_version`` string the resolver will accept.  It is NOT a
# fallback: unknown versions fail-closed.
DEFAULT_ARTICLE_RAG_INDEX_VERSION = "article_rag_index_v1"

# Fixed local error message used for ALL resolution failures.  The
# offending input is never echoed, truncated, or interpolated.
_RESOLUTION_ERROR_MESSAGE = "Article RAG index profile is not registered"


class ArticleRagIndexProfileResolutionError(LookupError):
    """Raised when an ``index_version`` cannot be resolved.

    The error message is a fixed local string.  The offending input is
    never echoed, truncated, persisted, or otherwise exposed in the
    error's ``str``, ``repr``, ``vars``, or traceback.
    """


@dataclass(frozen=True, slots=True)
class ArticleRagIndexProfileResolution:
    """Immutable result of resolving an ``index_version``.

    Bundles the frozen :class:`ArticleRagIndexProfile` with its
    precomputed ``profile_fingerprint`` so callers do not need to call
    :func:`compute_article_rag_index_profile_fingerprint` separately.
    The same frozen resolution instance is returned for every successful
    resolve of the same ``index_version``.

    Construction invariant (enforced in ``__post_init__``):

      * ``profile`` MUST be an :class:`ArticleRagIndexProfile`.
      * ``profile_fingerprint`` MUST be a ``str``.
      * ``profile_fingerprint`` MUST precisely equal
        :func:`compute_article_rag_index_profile_fingerprint` applied
        to ``profile``.

    This invariant is the trust basis for downstream migration,
    bootstrap, worker, and retrieval code: they can rely on
    ``resolution.profile_fingerprint`` without re-computing it.  The
    constructor never auto-corrects or overwrites a caller-supplied
    fingerprint — a mismatch always raises.
    """

    profile: ArticleRagIndexProfile
    profile_fingerprint: str

    def __post_init__(self) -> None:
        # Type checks first — fixed local messages, no echo of value.
        if not isinstance(self.profile, ArticleRagIndexProfile):
            raise TypeError("profile must be an ArticleRagIndexProfile")
        if not isinstance(self.profile_fingerprint, str):
            raise TypeError("profile_fingerprint must be a str")
        # Precise equality with the canonical fingerprint of the
        # supplied profile.  No fallback, no auto-correction.  The
        # offending fingerprint is never echoed, truncated, or
        # persisted in the error message.
        expected = compute_article_rag_index_profile_fingerprint(self.profile)
        if self.profile_fingerprint != expected:
            raise ValueError("profile_fingerprint must match profile")


def _build_v1_resolution() -> ArticleRagIndexProfileResolution:
    """Build the frozen V1 resolution at module import time.

    The V1 mapping is the canonical Article RAG index identity.  It
    MUST match the existing identity constants used by bootstrap /
    retrieval / plan (see
    ``test_v1_characterization_parity_with_bootstrap_plan_retrieval``).

    Design notes:

      * ``document_embedding_text_type`` / ``query_embedding_text_type``
        are ``"provider_default"`` because V1 does not pass an explicit
        DashScope ``text_type`` for document or query embedding.
      * ``plan_version`` and ``chunker_version`` are intentionally
        equal in V1: there is only one existing identity
        (``CHUNKER_VERSION = "article_rag_index_plan_v1"``); the two
        profile fields are kept distinct so future versions can
        evolve them independently.
      * ``vector_namespace`` is the current default physical
        collection name.
    """
    profile = ArticleRagIndexProfile(
        index_version="article_rag_index_v1",
        plan_version="article_rag_index_plan_v1",
        chunker_version="article_rag_index_plan_v1",
        document_embedding_model="text-embedding-v4",
        document_embedding_dimension=1024,
        document_embedding_text_type="provider_default",
        query_embedding_model="text-embedding-v4",
        query_embedding_text_type="provider_default",
        vector_namespace="article_rag_index_v1",
        retrieval_schema_version="article_rag_retrieval_v1",
        citation_mode_version="article_rag_citation_v1",
    )
    fingerprint = compute_article_rag_index_profile_fingerprint(profile)
    return ArticleRagIndexProfileResolution(
        profile=profile,
        profile_fingerprint=fingerprint,
    )


# Single frozen V1 resolution, built once at module import.  The
# resolver returns this same instance on every successful V1 resolve.
_V1_RESOLUTION: ArticleRagIndexProfileResolution = _build_v1_resolution()

# Immutable registry: a ``MappingProxyType``-backed, read-only mapping
# from ``index_version`` to frozen resolution.  No runtime register,
# override, mutation, or environment hook is exposed.
_REGISTRY: Mapping[str, ArticleRagIndexProfileResolution] = MappingProxyType(
    {DEFAULT_ARTICLE_RAG_INDEX_VERSION: _V1_RESOLUTION}
)


def resolve_article_rag_index_profile(
    index_version: str,
) -> ArticleRagIndexProfileResolution:
    """Resolve an ``index_version`` to its frozen profile identity.

    This is the sole public entry point for callers to obtain a
    profile identity (profile + fingerprint) from an ``index_version``
    string.  Callers MUST NOT access the internal registry directly or
    recompute the mapping themselves.

    Args:
        index_version: The index version string to resolve.  Must be a
            non-empty, non-blank, exactly-matching registered version.

    Returns:
        The frozen :class:`ArticleRagIndexProfileResolution` for the
        requested version.  The same instance is returned for every
        successful resolve of the same version.

    Raises:
        ArticleRagIndexProfileResolutionError: If ``index_version`` is
            not a string, is empty/blank, has leading/trailing
            whitespace, contains newlines or other hostile content, or
            is not a registered version.  The offending input is never
            echoed, truncated, or persisted in the error.

    The fixed local error message is used for ALL failure cases.  No
    fallback to default occurs for unknown versions.
    """
    # Reject non-string, empty, blank, or whitespace-padded inputs
    # without echoing the value.  ``strip() != value`` catches leading
    # and trailing whitespace (including newlines and tabs).
    if not isinstance(index_version, str) or not index_version or (
        index_version.strip() != index_version
    ) or not index_version.strip():
        raise ArticleRagIndexProfileResolutionError(_RESOLUTION_ERROR_MESSAGE)

    resolution = _REGISTRY.get(index_version)
    if resolution is None:
        raise ArticleRagIndexProfileResolutionError(_RESOLUTION_ERROR_MESSAGE)
    return resolution


__all__ = [
    "ArticleRagIndexProfile",
    "compute_article_rag_index_profile_fingerprint",
    "DEFAULT_ARTICLE_RAG_INDEX_VERSION",
    "ArticleRagIndexProfileResolution",
    "ArticleRagIndexProfileResolutionError",
    "resolve_article_rag_index_profile",
]
