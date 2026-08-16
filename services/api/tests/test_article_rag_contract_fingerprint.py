"""Tests for the Article RAG embedding contract fingerprint.

Covers the Wave 7 F1c contract-identity foundation:

  * same contract -> same fingerprint (deterministic, stable across
    calls and process restarts);
  * changing ANY of the 6 contract fields changes the fingerprint;
  * the fingerprint is a 64-char lowercase hex SHA-256;
  * serialisation is explicit and stable (no dataclass ``repr``).

Pure unit tests — no DB, no network, no provider.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.contracts.article_rag_contract import (
    ARTICLE_RAG_EMBEDDING_CONTRACT,
    ArticleRagEmbeddingContract,
    compute_embedding_contract_fingerprint,
)

pytestmark = [
    pytest.mark.chain_article_rag,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


def _make_contract(**overrides: object) -> ArticleRagEmbeddingContract:
    fields = {
        f.name: getattr(ARTICLE_RAG_EMBEDDING_CONTRACT, f.name)
        for f in dataclasses.fields(ArticleRagEmbeddingContract)
    }
    fields.update(overrides)
    return ArticleRagEmbeddingContract(**fields)  # type: ignore[arg-type]


def test_same_contract_produces_same_fingerprint() -> None:
    a = compute_embedding_contract_fingerprint(ARTICLE_RAG_EMBEDDING_CONTRACT)
    b = compute_embedding_contract_fingerprint(ARTICLE_RAG_EMBEDDING_CONTRACT)
    assert a == b


def test_fingerprint_is_64_char_lowercase_hex() -> None:
    fp = compute_embedding_contract_fingerprint(ARTICLE_RAG_EMBEDDING_CONTRACT)
    assert len(fp) == 64
    assert fp == fp.lower()
    int(fp, 16)  # must parse as hex


@pytest.mark.parametrize(
    "field_name,new_value",
    [
        ("document_embedding_model", "text-embedding-v5"),
        ("document_embedding_dimension", 768),
        ("document_embedding_text_type", "title_prefixed"),
        ("query_embedding_model", "text-embedding-v5"),
        ("query_embedding_text_type", "title_prefixed"),
        ("vector_collection", "article_rag_chunks_v2"),
    ],
)
def test_any_field_change_changes_fingerprint(
    field_name: str, new_value: object
) -> None:
    """Each of the 6 contract fields participates in the fingerprint."""
    base = compute_embedding_contract_fingerprint(ARTICLE_RAG_EMBEDDING_CONTRACT)
    mutated = compute_embedding_contract_fingerprint(
        _make_contract(**{field_name: new_value})
    )
    assert mutated != base, f"changing {field_name} must change the fingerprint"


def test_fingerprint_does_not_depend_on_dataclass_field_order() -> None:
    """Two contracts with identical field VALUES (constructed in
    different orders / from different instances) fingerprint equally —
    the serialisation order is fixed by the fingerprint function, not
    by the caller."""
    c1 = ARTICLE_RAG_EMBEDDING_CONTRACT
    c2 = _make_contract()  # same values, fresh instance
    assert compute_embedding_contract_fingerprint(c1) == (
        compute_embedding_contract_fingerprint(c2)
    )


def test_fingerprint_is_not_dataclass_repr() -> None:
    """The fingerprint must not equal a hash of ``repr`` — repr is an
    implementation detail and is explicitly forbidden as the
    serialisation base."""
    fp = compute_embedding_contract_fingerprint(ARTICLE_RAG_EMBEDDING_CONTRACT)
    import hashlib

    repr_hash = hashlib.sha256(
        repr(ARTICLE_RAG_EMBEDDING_CONTRACT).encode("utf-8")
    ).hexdigest()
    assert fp != repr_hash
