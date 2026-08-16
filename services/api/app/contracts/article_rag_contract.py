"""Frozen Article RAG embedding + vector contract.

Single source of truth for the Article RAG single-path embedding and
vector-space identity. Imported by:

  * ``app.config.settings`` — for the default Zilliz collection + dim
  * ``app.services.reader_orchestration.article_rag_index_bootstrap``
  * ``app.services.reader_orchestration.article_rag_index_worker``
  * ``app.services.reader_orchestration.article_rag_retrieval_service``
  * ``app.services.reader_orchestration.article_rag_vector_store``

Constraints:

  * Low-dependency — no imports from ``app.config`` or ``app.services``.
    This allows ``app.config.settings`` to import the contract without
    triggering service-level circular imports.
  * No version dimensions — no ``index_version`` / ``chunker_version`` /
    ``profile_fingerprint`` / registry / resolver / compatibility alias.
    The Article RAG index is a single path.
  * Frozen dataclass — callers MUST NOT mutate. The contract instance
    is treated as a process-wide constant.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArticleRagEmbeddingContract:
    """Frozen embedding + vector identity for the single Article RAG path.

    Captures the 6 fields downstream code (bootstrap / worker / vector
    writer / retrieval / settings) actually needs. No version / fingerprint
    fields — the Article RAG index is a single path.
    """

    document_embedding_model: str
    document_embedding_dimension: int
    document_embedding_text_type: str
    query_embedding_model: str
    query_embedding_text_type: str
    vector_collection: str


ARTICLE_RAG_EMBEDDING_CONTRACT = ArticleRagEmbeddingContract(
    document_embedding_model="text-embedding-v4",
    document_embedding_dimension=1024,
    document_embedding_text_type="provider_default",
    query_embedding_model="text-embedding-v4",
    query_embedding_text_type="provider_default",
    vector_collection="article_rag_chunks",
)


# Metadata key under which the contract fingerprint is persisted on
# ``reader_article_rag_index_runs.metadata_json``. Part of the durable
# contract identity — renaming it orphans every existing row.
EMBEDDING_CONTRACT_FINGERPRINT_METADATA_KEY = "embedding_contract_fingerprint"


def compute_embedding_contract_fingerprint(
    contract: ArticleRagEmbeddingContract,
) -> str:
    """Deterministic SHA-256 fingerprint over ALL 6 contract fields.

    The serialisation is explicit and stable: fields are joined in a
    fixed declaration order as ``key=value`` lines (no dataclass
    ``repr``, no dict ordering dependence, no runtime provider names).
    The same field values always produce the same fingerprint across
    calls, field-construction order, and process restarts; changing any
    single field changes the fingerprint.

    Used by the bootstrap (persist on new index runs), the idempotent
    no-op gate, and retrieval (fail-closed contract identity guard).
    """
    parts = (
        ("document_embedding_model", contract.document_embedding_model),
        (
            "document_embedding_dimension",
            str(contract.document_embedding_dimension),
        ),
        ("document_embedding_text_type", contract.document_embedding_text_type),
        ("query_embedding_model", contract.query_embedding_model),
        ("query_embedding_text_type", contract.query_embedding_text_type),
        ("vector_collection", contract.vector_collection),
    )
    raw = "\n".join(f"{key}={value}" for key, value in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
