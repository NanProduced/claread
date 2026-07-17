"""P1-A: Article RAG immutable IndexProfile value object and fingerprint.

This module establishes the minimal deep-module foundation for the
Article RAG IndexProfile:

  * :class:`ArticleRagIndexProfile` — a frozen value object capturing
    the 11 identity fields that define a RAG index profile.
  * :func:`compute_article_rag_index_profile_fingerprint` — a
    deterministic SHA-256 fingerprint over a fixed, versioned
    canonical JSON payload.

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

This module deliberately does NOT:

  * register any V1/V2 profile
  * read Settings, environment variables, model registry, or Zilliz
  * implement a resolver
  * implement schema migration
  * touch bootstrap, plan, worker, vector writer, or retrieval
  * add a profile table
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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


__all__ = [
    "ArticleRagIndexProfile",
    "compute_article_rag_index_profile_fingerprint",
]
