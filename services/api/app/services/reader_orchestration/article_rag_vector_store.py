"""D6-I4D: Article RAG Zilliz / Milvus Vector Writer Adapter.

Real-adapter foundation for :class:`ArticleRagVectorWriter` that
upserts Article RAG vectors into a Zilliz / Milvus collection.

Truth boundary
--------------

Zilliz is **only** an index replica.  Citation truth always returns to
Postgres's ``stable_document_blocks`` / ``reading_bases.text`` /
``reading_units`` / ``anchor_segments``.  Concretely:

* ``ArticleRagVectorChunk`` (D6-I4C) has NO ``text`` field.  This
  module reads only the dataclass's six defined fields plus the
  citation + metadata dicts.
* Vector payload never carries chunk text, Plate JSON, Markdown, DOM
  selections, Slate paths, or per-chunk raw content.  Defence in depth
  is layered:
  1. Type-level: the dataclass itself has no ``text``.
  2. Payload-shape guard: ``_build_article_rag_upsert_row`` asserts
     the serialised row's keys (and the JSON serialisation of nested
     citation/metadata dicts) do NOT intersect
     :data:`_FORBIDDEN_VECTOR_PAYLOAD_KEYS`.
  3. Citation sanitisation: the citation dict is built explicitly
     from the I4C-tested 9-key shape — ``dataclasses.asdict`` is
     intentionally avoided.  UUIDs are stringified, offsets are
     preserved as ``int | None``.
  4. Content-source guard: we never reach for ``chunk.text`` (it
     does not exist) and never reach into any projection / markdown
     / plate payload.

Contract
--------

* ``upsert_chunks`` is idempotent on ``chunk_id`` (the pymilvus primary
  key).  Re-upserting the same chunk overwrites prior vector; no error
  raised.
* ``upsert_count`` returned by pymilvus propagates **verbatim** to
  ``ArticleRagVectorWriteResult``.  Partial upserts (``upsert_count
  != len(chunks)``) are NOT silently coerced — D6-I4C's Phase-4 check
  surfaces them as retryable ``FAILURE_CODE_VECTOR_WRITE_FAILED``.
* Constructor does **not** open a network connection.  The first
  :meth:`upsert_chunks` call lazily constructs the
  :class:`pymilvus.MilvusClient` inside ``asyncio.to_thread`` so we
  don't block the event loop during the SDK handshake.

Default factory fail-closes: when ``reader_article_rag_vector_provider``
is empty/missing OR any of URI / token / collection / dim is missing,
the factory returns
:class:`app.services.reader_orchestration.article_rag_index_worker.UnconfiguredArticleRagVectorWriter`
— no network calls are ever made unless the deployment explicitly
opts in.

Security contract
-----------------

* The Zilliz token is **never** logged, **never** included in
  exception messages, **never** echoed in ``provider_metadata``.
* Chunk text and embedding vectors are **never** logged at INFO or
  higher; the token URI alone is logged at DEBUG level (useful for
  ops diagnostics but not a secret).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from .article_rag_index_bootstrap import ARTICLE_RAG_EMBEDDING_CONTRACT
from .article_rag_index_worker import (
    ArticleRagIndexWorkerError,
    ArticleRagVectorChunk,
    ArticleRagVectorWriteMetadata,
    ArticleRagVectorWriteResult,
    ArticleRagVectorWriter,
    FAILURE_CODE_VECTOR_WRITE_FAILED,
    FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
    UnconfiguredArticleRagVectorWriter,
)

# P1-G: Zilliz writer defence-in-depth failure codes.  All
# retryable=False, fixed safe message that does NOT echo the malicious
# model, vector content, chunk text, URI, token, collection name, or
# any caller-supplied value.
FAILURE_CODE_VECTOR_WRITER_COLLECTION_MISMATCH = (
    "vector_writer_collection_mismatch"
)
FAILURE_CODE_VECTOR_WRITER_DIMENSION_MISMATCH = (
    "vector_writer_dimension_mismatch"
)
FAILURE_CODE_VECTOR_WRITER_MODEL_MISMATCH = "vector_writer_model_mismatch"

# P1-G: fixed safe messages for writer defence-in-depth failures.
# Must NOT echo URI, token, collection name, dim value, model name,
# vector content, chunk text, or SDK error.
_P1G_MSG_WRITER_COLLECTION_MISMATCH = (
    "Article RAG vector writer collection does not match the configured "
    "writer collection or the metadata collection"
)
_P1G_MSG_WRITER_DIMENSION_MISMATCH = (
    "Article RAG vector writer dimension contract is violated across "
    "writer configuration, metadata, chunk embedding, or vector length"
)
_P1G_MSG_WRITER_MODEL_MISMATCH = (
    "Article RAG vector writer chunk embedding model does not match the "
    "metadata embedding model"
)

# P1-G-R1: fixed safe message for writer constructor dim rejection.
# Must NOT echo dim value, repr(dim), or any caller-supplied sentinel.
_P1G_R1_MSG_WRITER_UNCONFIGURED_DIM = (
    "ZillizArticleRagVectorWriter requires a positive integer dim"
)
# P1-G-R1: fixed safe message for writer contract metadata mismatch.
# Used when the 4 contract fields (collection, model, dim, text_type)
# do not match the frozen ARTICLE_RAG_EMBEDDING_CONTRACT.  Must NOT
# echo any caller-supplied value (model, text type, collection, dim).
_P1G_R1_MSG_WRITER_CONTRACT_MISMATCH = (
    "Article RAG vector writer metadata does not match the frozen "
    "embedding + vector-space contract"
)

# P1-G-R1: failure code for writer contract metadata mismatch.
FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH = (
    "vector_writer_contract_mismatch"
)

if TYPE_CHECKING:
    from app.config.settings import Settings
    from pymilvus import MilvusClient

logger = logging.getLogger(__name__)


# Provider name constant — kept module-local because the factory is the
# sanctioned entry point.
READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ = "zilliz"

# Default vector dimension when the caller does not supply one.  Picked
# to match DashScope ``text-embedding-v4`` at the registry default; the
# deployment can override via ``READER_ARTICLE_RAG_VECTOR_DIM``.
ZILLIZ_DEFAULT_VECTOR_DIM = 1024

# Hard caps on VARCHAR lengths; pymilvus requires positive lengths and
# rejects (length-0) for VARCHAR primary keys.
_ARTICLE_RAG_CHUNK_ID_MAX_LEN = 64
_ARTICLE_RAG_HASH_MAX_LEN = 64
_ARTICLE_RAG_MODEL_MAX_LEN = 64
_ARTICLE_RAG_UUID_MAX_LEN = 64
_ARTICLE_RAG_ID_LIST_MAX_LEN = 2048
_ARTICLE_RAG_JSON_MAX_LEN = 8192
_ARTICLE_RAG_VECTOR_FIELD_NAME = "vector"
_ARTICLE_RAG_VECTOR_INDEX_NAME = "article_rag_vector_autoin"
_ARTICLE_RAG_VECTOR_INDEX_TYPE = "AUTOINDEX"
_ARTICLE_RAG_VECTOR_METRIC_TYPE = "COSINE"

# Forbidden keys — sourced from D6-I4C test 3 (input_json denylist) +
# safety extensions for vector payload.  Any overlap between the row
# keys we build or the JSON-serialised citation/metadata dicts and this
# set is a truth-boundary violation and raises a non-retryable error.
_FORBIDDEN_VECTOR_PAYLOAD_KEYS = frozenset({
    # I4C test 3 keys (extended here for vector payload):
    "chunks",
    "chunk_text",
    "chunk_texts",
    "plate",
    "plate_json",
    "markdown",
    "markdown_syntax",
    "dom",
    "dom_selection",
    "slate",
    "slate_path",
    "ui",
    "ui_display_group",
    "render_profile",
    "render_snapshot",
    "citation_refs",
    # Extended safety (defence in depth):
    "text",
    "chunkText",
    "path",
    "selection",
    "value",
    "rich_text",
    "html",
    "innerText",
    "innerHTML",
})


class ZillizArticleRagVectorWriterError(ArticleRagIndexWorkerError):
    """Typed failure raised by :class:`ZillizArticleRagVectorWriter`.

    Inherits :class:`ArticleRagIndexWorkerError` so the I4C worker's
    exception handler (which only catches the worker base class) can
    route the failure to ``failed_retryable`` /
    ``failed_terminal`` correctly.

    ``failure_code`` is a stable, machine-readable label the worker
    uses to drive retry / terminal branching.  ``retryable=True`` for
    SDK / network / partial-upsert conditions; ``retryable=False`` for
    configuration errors or payload-sanitisation failures.

    Per Fix 5, the error message is a fixed diagnostic that excludes
    any verbatim SDK content (which may echo chunk text in a future
    regression).  The original exception remains reachable via
    ``__cause__``.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "vector_write",
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
            rationale_code=rationale_code,
        )


# ---------------------------------------------------------------------------
# Payload sanitiser
# ---------------------------------------------------------------------------


# Citation keys verified by D6-I4C test 14 (9 keys, exact set).  We
# build the citation dict explicitly from these to avoid leaking any
# extra key the caller might shove onto ``ArticleRagCitationRef``.
_ARTICLE_RAG_CITATION_KEYS = (
    "reading_record_id",
    "stable_document_id",
    "base_id",
    "record_generation",
    "block_ids",
    "unit_ids",
    "anchor_segment_ids",
    "canonical_text_start_utf16",
    "canonical_text_end_utf16",
)


def _serialise_dict_to_json(payload: dict[str, Any], *, max_len: int) -> str:
    """Serialise ``payload`` to a JSON string, ``default=str`` for safety.

    Refuses ``max_len`` overflow by raising
    :class:`ZillizArticleRagVectorWriterError` with
    ``failure_code="vector_payload_too_large"``.  Refuses forbidden
    payload keys via the denylist.
    """
    if not isinstance(payload, dict):
        raise ZillizArticleRagVectorWriterError(
            "vector payload expected a dict, got "
            f"{type(payload).__name__}",
            retryable=False,
            failure_class="vector_payload_invalid",
            failure_code="vector_payload_invalid",
        )
    _assert_no_forbidden_keys(payload, context="vector payload")
    text = json.dumps(payload, default=str, sort_keys=True, ensure_ascii=False)
    if len(text) > max_len:
        raise ZillizArticleRagVectorWriterError(
            "serialised vector payload exceeds the Zilliz VARCHAR limit "
            f"({len(text)} > {max_len})",
            retryable=False,
            failure_class="vector_payload_too_large",
            failure_code="vector_payload_too_large",
        )
    return text


def _assert_no_forbidden_keys(
    payload: dict[str, Any],
    *,
    context: str,
) -> None:
    """Refuse any top-level key in :data:`_FORBIDDEN_VECTOR_PAYLOAD_KEYS`."""
    overlap = _FORBIDDEN_VECTOR_PAYLOAD_KEYS.intersection(payload.keys())
    if overlap:
        raise ZillizArticleRagVectorWriterError(
            f"{context} contains forbidden key(s): {sorted(overlap)}; "
            "these fields must never reach the vector store",
            retryable=False,
            failure_class="vector_payload_leak",
            failure_code="vector_payload_leak",
        )


def _build_article_rag_upsert_row(
    chunk: ArticleRagVectorChunk,
    *,
    metadata: ArticleRagVectorWriteMetadata,
) -> dict[str, Any]:
    """Convert one :class:`ArticleRagVectorChunk` into a pymilvus row dict.

    The returned dict has EXACTLY the following keys (no more, no less):

      ``chunk_id, content_sha256, embedding_text_sha256, embedding_model,
      vector, reading_record_id, stable_document_id, base_id,
      record_generation, plan_content_sha256, block_ids, unit_ids,
      anchor_segment_ids, canonical_start_utf16, canonical_end_utf16,
      citation_metadata_json, metadata_json``

    The Article RAG index is a single path — no ``index_version`` or
    ``chunker_version`` identity tags travel on the row.  Collection
    name is a writer-construction concern; embedding identity is
    enforced by the frozen ``ARTICLE_RAG_EMBEDDING_CONTRACT``.

    Defence in depth:
      * The top-level row dict is denylist-checked.
      * The citation dict is built explicitly from the 9 I4C-tested
        citation keys (UUIDs stringified; lists copied; offsets
        preserved as ``int | None``); it is then JSON serialised into
        ``citation_metadata_json``; the JSON string is denylist-checked
        after sanitisation.
      * The metadata dict is copied verbatim (I4A guarantees its keys
        come from a fixed whitelist).  Even so, both the original
        metadata dict AND the JSON string are denylist-checked.
    """
    citation_src = chunk.citation or {}
    if not isinstance(citation_src, dict):
        raise ZillizArticleRagVectorWriterError(
            "ArticleRagVectorChunk.citation is not a dict",
            retryable=False,
            failure_class="vector_payload_invalid",
            failure_code="vector_payload_invalid",
        )
    # Build a sanitised citation dict by enumerating ONLY the 9 known
    # keys.  Anything else is dropped — even if present on the
    # caller's citation (it shouldn't be), it will not reach Zilliz.
    citation_dict: dict[str, Any] = {}
    for key in _ARTICLE_RAG_CITATION_KEYS:
        if key not in citation_src:
            continue
        val = citation_src[key]
        if key in {"block_ids", "unit_ids", "anchor_segment_ids"}:
            citation_dict[key] = [str(x) for x in (val or [])]
        elif key in {
            "reading_record_id",
            "stable_document_id",
            "base_id",
        }:
            citation_dict[key] = str(val) if val is not None else None
        else:
            # record_generation / canonical_*_utf16 — preserve as-is
            citation_dict[key] = val

    metadata_src = chunk.metadata or {}
    if not isinstance(metadata_src, dict):
        raise ZillizArticleRagVectorWriterError(
            "ArticleRagVectorChunk.metadata is not a dict",
            retryable=False,
            failure_class="vector_payload_invalid",
            failure_code="vector_payload_invalid",
        )

    # Serialise citation + metadata to VARCHAR JSON columns with size
    # caps.  After serialisation we re-scan the JSON STRING (cheap
    # substring check) for any forbidden key presence — pure defence
    # in depth for a future regression.
    citation_json = _serialise_dict_to_json(
        citation_dict, max_len=_ARTICLE_RAG_JSON_MAX_LEN
    )
    metadata_json = _serialise_dict_to_json(
        metadata_src, max_len=_ARTICLE_RAG_JSON_MAX_LEN
    )
    _scrub_serialised_text_for_forbidden_keys(
        citation_json, context="citation_metadata_json"
    )
    _scrub_serialised_text_for_forbidden_keys(
        metadata_json, context="metadata_json"
    )

    row: dict[str, Any] = {
        "chunk_id": _truncate_or_raise(
            str(chunk.chunk_id), _ARTICLE_RAG_CHUNK_ID_MAX_LEN, "chunk_id"
        ),
        "content_sha256": _truncate_or_raise(
            str(chunk.content_sha256), _ARTICLE_RAG_HASH_MAX_LEN, "content_sha256"
        ),
        "embedding_text_sha256": _truncate_or_raise(
            str(chunk.embedding_text_sha256),
            _ARTICLE_RAG_HASH_MAX_LEN,
            "embedding_text_sha256",
        ),
        "embedding_model": _truncate_or_raise(
            str(chunk.embedding.model), _ARTICLE_RAG_MODEL_MAX_LEN, "embedding_model"
        ),
        "vector": list(chunk.embedding.vector),
        "reading_record_id": _truncate_or_raise(
            str(metadata.reading_record_id),
            _ARTICLE_RAG_UUID_MAX_LEN,
            "reading_record_id",
        ),
        "stable_document_id": _truncate_or_raise(
            str(metadata.stable_document_id),
            _ARTICLE_RAG_UUID_MAX_LEN,
            "stable_document_id",
        ),
        "base_id": _truncate_or_raise(
            str(metadata.base_id), _ARTICLE_RAG_UUID_MAX_LEN, "base_id"
        ),
        "record_generation": int(metadata.record_generation),
        "plan_content_sha256": _truncate_or_raise(
            str(metadata.plan_content_sha256),
            _ARTICLE_RAG_HASH_MAX_LEN,
            "plan_content_sha256",
        ),
        "block_ids": _truncate_or_raise(
            json.dumps(citation_dict.get("block_ids") or [], ensure_ascii=False),
            _ARTICLE_RAG_ID_LIST_MAX_LEN,
            "block_ids",
        ),
        "unit_ids": _truncate_or_raise(
            json.dumps(citation_dict.get("unit_ids") or [], ensure_ascii=False),
            _ARTICLE_RAG_ID_LIST_MAX_LEN,
            "unit_ids",
        ),
        "anchor_segment_ids": _truncate_or_raise(
            json.dumps(citation_dict.get("anchor_segment_ids") or [], ensure_ascii=False),
            _ARTICLE_RAG_ID_LIST_MAX_LEN,
            "anchor_segment_ids",
        ),
        "canonical_start_utf16": _coerce_offset(
            citation_dict.get("canonical_text_start_utf16")
        ),
        "canonical_end_utf16": _coerce_offset(
            citation_dict.get("canonical_text_end_utf16")
        ),
        "citation_metadata_json": citation_json,
        "metadata_json": metadata_json,
    }

    # Final defence-in-depth assertion on top-level row keys.
    _assert_no_forbidden_keys(row, context="row payload")

    return row


def _coerce_offset(value: Any) -> int | None:
    """Convert an offset to ``int`` or ``None``.

    The plan layer emits either ``int`` or ``None``.  We defend against
    arbitrary types by attempting ``int(value)`` and falling back to
    ``None`` on failure — the worker calls Phase-4 hash validation on
    the post-write result and treats unexpected types as a
    truth-boundary drift anyway.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate_or_raise(value: str, max_len: int, field_name: str) -> str:
    """Reject values that exceed ``max_len`` rather than silently truncate.

    Truncation would corrupt SHA-256 hex digests, UUIDs, and version
    strings, so we fail closed and surface a typed error.  Under
    realistic inputs (UUIDs are 36 chars; SHA-256 hex is 64; index
    versions are short), no truncation should be needed.
    """
    if len(value) > max_len:
        raise ZillizArticleRagVectorWriterError(
            f"vector payload field '{field_name}' length {len(value)} "
            f"exceeds hard cap {max_len}",
            retryable=False,
            failure_class="vector_payload_invalid",
            failure_code="vector_payload_invalid",
        )
    return value


def _scrub_serialised_text_for_forbidden_keys(text: str, *, context: str) -> None:
    """Fail if any forbidden key substring appears in a serialised JSON.

    Cheap string check — defence in depth.  Catches regressions where
    a caller shoves ``"text"`` / ``"value"`` / etc. into the citation
    or metadata dict.
    """
    for forbidden in _FORBIDDEN_VECTOR_PAYLOAD_KEYS:
        # Match "KEY": patterns (start of value) so that short words
        # like ``"path"`` don't false-positive on words inside longer
        # field names.  ``"path":`` / ``"path"`` followed by ``:`` or
        # ``}`` / ``]`` boundaries.
        token = f'"{forbidden}"'
        index = 0
        while True:
            found = text.find(token, index)
            if found < 0:
                break
            # Boundary check: next non-whitespace char must be ``:`` for
            # this to be a JSON key (and not, say, a value string).
            tail = text[found + len(token):].lstrip()
            if tail.startswith(":"):
                raise ZillizArticleRagVectorWriterError(
                    f"serialised {context} contains forbidden key "
                    f"'{forbidden}'; refusing to upsert",
                    retryable=False,
                    failure_class="vector_payload_leak",
                    failure_code="vector_payload_leak",
                )
            index = found + len(token)


# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------


def _build_article_rag_collection_schema(dim: int) -> dict[str, Any]:
    """Build the pymilvus ``create_collection`` schema for article RAG chunks.

    Field shape (in order):

    ============================  ===================  =======================
    Field                         pymilvus type        Notes
    ============================  ===================  =======================
    chunk_id                      VARCHAR(64) PK       pymilvus primary key
    content_sha256                VARCHAR(64)
    embedding_text_sha256         VARCHAR(64)
    embedding_model               VARCHAR(64)
    vector                        FLOAT_VECTOR(dim)
    reading_record_id             VARCHAR(64)
    stable_document_id            VARCHAR(64)
    base_id                       VARCHAR(64)
    record_generation             INT64
    plan_content_sha256           VARCHAR(64)
    block_ids                     VARCHAR(2048)        JSON list serialised
    unit_ids                      VARCHAR(2048)        JSON list serialised
    anchor_segment_ids            VARCHAR(2048)        JSON list serialised
    canonical_start_utf16         INT64                nullable
    canonical_end_utf16           INT64                nullable
    citation_metadata_json        VARCHAR(8192)        full citation dict
    metadata_json                 VARCHAR(8192)        per-chunk metadata dict
    ============================  ===================  =======================

    NOTE: Zilliz is an index replica only.  Postgres remains the
    citation truth source.
    """
    if dim <= 0:
        raise ZillizArticleRagVectorWriterError(
            f"vector dim must be a positive integer; got {dim}",
            retryable=False,
            failure_class="configuration",
            failure_code="vector_writer_unconfigured",
        )
    return {
        "primary_key": "chunk_id",
        "fields": [
            {"name": "chunk_id", "type": "VARCHAR", "max_length": _ARTICLE_RAG_CHUNK_ID_MAX_LEN, "is_primary": True},
            {"name": "content_sha256", "type": "VARCHAR", "max_length": _ARTICLE_RAG_HASH_MAX_LEN},
            {"name": "embedding_text_sha256", "type": "VARCHAR", "max_length": _ARTICLE_RAG_HASH_MAX_LEN},
            {"name": "embedding_model", "type": "VARCHAR", "max_length": _ARTICLE_RAG_MODEL_MAX_LEN},
            {
                "name": _ARTICLE_RAG_VECTOR_FIELD_NAME,
                "type": "FLOAT_VECTOR",
                "dim": int(dim),
            },
            {"name": "reading_record_id", "type": "VARCHAR", "max_length": _ARTICLE_RAG_UUID_MAX_LEN},
            {"name": "stable_document_id", "type": "VARCHAR", "max_length": _ARTICLE_RAG_UUID_MAX_LEN},
            {"name": "base_id", "type": "VARCHAR", "max_length": _ARTICLE_RAG_UUID_MAX_LEN},
            {"name": "record_generation", "type": "INT64"},
            {"name": "plan_content_sha256", "type": "VARCHAR", "max_length": _ARTICLE_RAG_HASH_MAX_LEN},
            {"name": "block_ids", "type": "VARCHAR", "max_length": _ARTICLE_RAG_ID_LIST_MAX_LEN},
            {"name": "unit_ids", "type": "VARCHAR", "max_length": _ARTICLE_RAG_ID_LIST_MAX_LEN},
            {"name": "anchor_segment_ids", "type": "VARCHAR", "max_length": _ARTICLE_RAG_ID_LIST_MAX_LEN},
            {"name": "canonical_start_utf16", "type": "INT64"},
            {"name": "canonical_end_utf16", "type": "INT64"},
            {"name": "citation_metadata_json", "type": "VARCHAR", "max_length": _ARTICLE_RAG_JSON_MAX_LEN},
            {"name": "metadata_json", "type": "VARCHAR", "max_length": _ARTICLE_RAG_JSON_MAX_LEN},
        ],
    }


def _build_pymilvus_collection_schema(dim: int) -> Any:
    """Build the real pymilvus 2.6.x ``CollectionSchema`` for Article RAG.

    Per Fix 2: ``MilvusClient.create_collection(schema=...)`` invokes
    ``schema.verify()`` on the supplied object, which fails for plain
    dicts.  This helper lazily imports the ORM-style ``CollectionSchema``
    / ``FieldSchema`` / ``DataType`` constructors from :mod:`pymilvus`
    and returns a real schema instance.  The field list mirrors the
    structural fingerprint returned by
    :func:`_build_article_rag_collection_schema` so the test that
    asserts on the dict shape remains valid as a "structural
    fingerprint" check.

    If :mod:`pymilvus` is not installed, raise the same
    :class:`ZillizArticleRagVectorWriterError` as ``_ensure_client``
    does — ``failure_code="vector_writer_sdk_missing"``.
    """
    if dim <= 0:
        raise ZillizArticleRagVectorWriterError(
            f"vector dim must be a positive integer; got {dim}",
            retryable=False,
            failure_class="configuration",
            failure_code="vector_writer_unconfigured",
        )
    try:
        from pymilvus import (  # type: ignore[import-untyped]
            CollectionSchema,
            DataType,
            FieldSchema,
        )
    except ImportError as exc:
        raise ZillizArticleRagVectorWriterError(
            "pymilvus SDK is not installed; cannot build the real "
            "CollectionSchema for the article RAG vector writer",
            retryable=False,
            failure_class="sdk_unavailable",
            failure_code="vector_writer_sdk_missing",
        ) from exc

    fields = [
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            is_primary=True,
            max_length=_ARTICLE_RAG_CHUNK_ID_MAX_LEN,
        ),
        FieldSchema(
            name="content_sha256",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_HASH_MAX_LEN,
        ),
        FieldSchema(
            name="embedding_text_sha256",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_HASH_MAX_LEN,
        ),
        FieldSchema(
            name="embedding_model",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_MODEL_MAX_LEN,
        ),
        FieldSchema(
            name=_ARTICLE_RAG_VECTOR_FIELD_NAME,
            dtype=DataType.FLOAT_VECTOR,
            dim=int(dim),
        ),
        FieldSchema(
            name="reading_record_id",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_UUID_MAX_LEN,
        ),
        FieldSchema(
            name="stable_document_id",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_UUID_MAX_LEN,
        ),
        FieldSchema(
            name="base_id",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_UUID_MAX_LEN,
        ),
        FieldSchema(name="record_generation", dtype=DataType.INT64),
        FieldSchema(
            name="plan_content_sha256",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_HASH_MAX_LEN,
        ),
        FieldSchema(
            name="block_ids",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_ID_LIST_MAX_LEN,
        ),
        FieldSchema(
            name="unit_ids",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_ID_LIST_MAX_LEN,
        ),
        FieldSchema(
            name="anchor_segment_ids",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_ID_LIST_MAX_LEN,
        ),
        FieldSchema(
            name="canonical_start_utf16",
            dtype=DataType.INT64,
            # I4A permits ``rag_ask_only`` chunks (table / image_ocr /
            # footnote / code RAG sources) to carry None offsets; the
            # corresponding row must therefore be storable as NULL.
            nullable=True,
        ),
        FieldSchema(
            name="canonical_end_utf16",
            dtype=DataType.INT64,
            nullable=True,
        ),
        FieldSchema(
            name="citation_metadata_json",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_JSON_MAX_LEN,
        ),
        FieldSchema(
            name="metadata_json",
            dtype=DataType.VARCHAR,
            max_length=_ARTICLE_RAG_JSON_MAX_LEN,
        ),
    ]
    return CollectionSchema(
        fields=fields,
        description=(
            "Article RAG index replica (citation truth → Postgres)"
        ),
    )


def _build_pymilvus_vector_index_params(client: Any) -> Any:
    """Build index params for the article RAG vector field.

    Zilliz Cloud requires a vector index before a collection can be
    loaded/searched.  AUTOINDEX lets Zilliz pick the concrete index
    implementation while the metric stays aligned with the embedding
    and search path's cosine contract.
    """
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name=_ARTICLE_RAG_VECTOR_FIELD_NAME,
        index_type=_ARTICLE_RAG_VECTOR_INDEX_TYPE,
        index_name=_ARTICLE_RAG_VECTOR_INDEX_NAME,
        metric_type=_ARTICLE_RAG_VECTOR_METRIC_TYPE,
    )
    return index_params


def _ensure_pymilvus_vector_index(client: Any, *, collection: str) -> None:
    """Ensure the article RAG vector field has a Milvus/Zilliz index.

    This runs for both newly created collections and pre-existing local
    collections.  Older dev collections may already exist without an
    index; those collections fail with "index not found" when loaded in
    Zilliz Cloud until the index is added.
    """
    existing_indexes = client.list_indexes(
        collection_name=collection,
        field_name=_ARTICLE_RAG_VECTOR_FIELD_NAME,
    )
    if existing_indexes:
        return

    client.create_index(
        collection_name=collection,
        index_params=_build_pymilvus_vector_index_params(client),
    )


# ---------------------------------------------------------------------------
# Real adapter
# ---------------------------------------------------------------------------


class ZillizArticleRagVectorWriter:
    """Real Zilliz / Milvus vector writer for the Article RAG worker.

    Implements the :class:`ArticleRagVectorWriter` Protocol defined in
    D6-I4C.  Lazy-imports :mod:`pymilvus` on the first
    :meth:`upsert_chunks` call so the deployment can opt-in to the
    real adapter only when the SDK is installed and credentials are
    present.

    The constructor takes ``uri``, ``token``, ``collection``, and
    ``dim``.  No network call happens until the first
    :meth:`upsert_chunks` — at which point the writer constructs
    :class:`pymilvus.MilvusClient` inside :func:`asyncio.to_thread`
    ensures the collection exists with
    :func:`_build_article_rag_collection_schema`, and ensures a vector
    index exists before writing.

    Idempotency: re-upserting the same ``chunk_id`` overwrites the
    prior vector — no error is raised.  pymilvus enforces uniqueness on
    the primary key.

    Partial upserts: the ``upsert_count`` returned by pymilvus is
    propagated **verbatim** to
    :class:`ArticleRagVectorWriteResult`.  The D6-I4C Phase-4 check
    surfaces any ``upserted_count != len(chunks)`` mismatch as a
    retryable error.
    """

    def __init__(
        self,
        *,
        uri: str,
        token: str,
        collection: str,
        dim: int,
    ) -> None:
        if not uri or not uri.strip():
            raise ZillizArticleRagVectorWriterError(
                "ZillizArticleRagVectorWriter constructed without a URI",
                retryable=False,
                failure_class="configuration",
                failure_code=FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
            )
        if not token or not token.strip():
            raise ZillizArticleRagVectorWriterError(
                "ZillizArticleRagVectorWriter constructed without a token",
                retryable=False,
                failure_class="configuration",
                failure_code=FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
            )
        if not collection or not collection.strip():
            raise ZillizArticleRagVectorWriterError(
                "ZillizArticleRagVectorWriter constructed without a collection name",
                retryable=False,
                failure_class="configuration",
                failure_code=FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
            )
        # P1-G-R1: explicitly reject bool — Python ``bool`` is a subclass
        # of ``int``, so ``isinstance(True, int)`` returns True.  The
        # legacy ``not isinstance(dim, int)`` check let ``dim=True``
        # through (True > 0).  We now reject bool explicitly.  The error
        # message is a fixed safe literal — it MUST NOT interpolate
        # ``dim``, ``repr(dim)``, or any caller-supplied value, so a
        # malicious sentinel cannot leak into error surfaces.
        if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
            raise ZillizArticleRagVectorWriterError(
                _P1G_R1_MSG_WRITER_UNCONFIGURED_DIM,
                retryable=False,
                failure_class="configuration",
                failure_code=FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED,
            )
        self._uri = uri.strip()
        self._token = token  # held only for SDK construction; never logged.
        self._collection = collection.strip()
        self._dim = int(dim)
        # The Article RAG index is a single path — no ``index_version``
        # or ``chunker_version`` identity tags exist.  Collection name
        # and embedding identity are enforced by the frozen
        # ``ARTICLE_RAG_EMBEDDING_CONTRACT`` at upsert time.
        self._client: "MilvusClient | None" = None

    @property
    def provider_name(self) -> str:
        return READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ

    @property
    def collection(self) -> str:
        return self._collection

    def _ensure_client(self) -> "MilvusClient":
        """Lazily construct the pymilvus client (idempotent)."""
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ZillizArticleRagVectorWriterError(
                "pymilvus SDK is not installed; cannot construct Zilliz "
                "vector writer",
                retryable=False,
                failure_class="sdk_unavailable",
                failure_code="vector_writer_sdk_missing",
            ) from exc
        logger.debug(
            "Constructing pymilvus MilvusClient for article RAG vector writer "
            "(uri=%s, collection=%s, dim=%d)",
            self._uri,
            self._collection,
            self._dim,
        )
        self._client = MilvusClient(uri=self._uri, token=self._token)
        return self._client

    async def upsert_chunks(
        self,
        *,
        collection: str,
        chunks_with_embeddings: list[ArticleRagVectorChunk],
        metadata: ArticleRagVectorWriteMetadata,
    ) -> ArticleRagVectorWriteResult:
        """Upsert chunks to the configured collection.

        Side effects:
          * ``pymilvus.MilvusClient`` constructed on first call (in
            ``asyncio.to_thread`` so the event loop isn't blocked).
          * The collection is created on first call if it does not
            exist (idempotent — re-running with the same schema is a
            no-op).
          * The vector field index is created when missing. This also
            repairs pre-existing dev collections created before index
            creation was wired.
          * ``upsert`` is called once with the full list of rows
            keyed on ``chunk_id`` (pymilvus primary key).

        ``upsert_count`` is propagated verbatim — partial upserts
        surface to the worker as retryable
        :data:`FAILURE_CODE_VECTOR_WRITE_FAILED` via D6-I4C's Phase-4
        check.

        P1-G defence-in-depth: BEFORE any client / network / upsert
        call, validate the full frozen vector-space contract:

          1. ``collection == self._collection == metadata.collection``
             (three-way collection identity check).
          2. ``metadata.embedding_dimension`` is a non-bool int and
             equals ``self._dim``.
          3. For each chunk: ``chunk.embedding.dim`` is a non-bool int
             and equals ``metadata.embedding_dimension``.
          4. For each chunk: ``len(chunk.embedding.vector)`` equals
             ``metadata.embedding_dimension``.
          5. For each chunk: ``chunk.embedding.model`` is a str and
             equals ``metadata.embedding_model``.

        Any failure raises :class:`ZillizArticleRagVectorWriterError`
        with a fixed safe message (no echo of caller-supplied values),
        ``retryable=False``, and the appropriate stable
        ``failure_code``.  ``client.upsert_calls`` stays empty.
        """
        # P1-G check 1: three-way collection identity.  No strip, no
        # case-normalisation, no fallback.  Fixed safe message; no
        # echo of collection name (caller or configured or metadata).
        if (
            collection != self._collection
            or metadata.collection != self._collection
            or collection != metadata.collection
        ):
            raise ZillizArticleRagVectorWriterError(
                _P1G_MSG_WRITER_COLLECTION_MISMATCH,
                retryable=False,
                failure_class="vector_writer_collection_mismatch",
                failure_code=FAILURE_CODE_VECTOR_WRITER_COLLECTION_MISMATCH,
            )

        # P1-G-R1 check 2: frozen embedding + vector-space contract.
        # Validate all 4 contract fields (collection, model, dim,
        # text_type) against the frozen ``ARTICLE_RAG_EMBEDDING_CONTRACT``.
        # This MUST happen BEFORE the empty-batch early return so that
        # invalid metadata fails closed even when
        # ``chunks_with_embeddings == []``.
        #
        # The contract is a module-level frozen dataclass — no resolver
        # call, no exception unwrapping, no caller-supplied version
        # string traverses the error chain.  All 4 fields are compared
        # by structural equality; any mismatch raises a fixed safe
        # message that does NOT echo the offending value.
        contract = ARTICLE_RAG_EMBEDDING_CONTRACT
        if (
            metadata.collection != contract.vector_collection
            or metadata.embedding_model != contract.document_embedding_model
            or metadata.embedding_dimension
            != contract.document_embedding_dimension
            or metadata.embedding_text_type
            != contract.document_embedding_text_type
        ):
            raise ZillizArticleRagVectorWriterError(
                _P1G_R1_MSG_WRITER_CONTRACT_MISMATCH,
                retryable=False,
                failure_class="vector_writer_contract_mismatch",
                failure_code=FAILURE_CODE_VECTOR_WRITER_CONTRACT_MISMATCH,
            )

        # P1-G check 2 (legacy): metadata.embedding_dimension must be a
        # non-bool int and equal the writer's configured dim.  This is
        # now redundant with the contract check above (which validates
        # dim == contract.document_embedding_dimension == 1024 ==
        # self._dim in normal operation), but kept for defence in depth
        # — if a future change decouples writer dim from contract dim,
        # this check still catches the mismatch.
        metadata_dim = metadata.embedding_dimension
        if isinstance(metadata_dim, bool) or not isinstance(metadata_dim, int):
            raise ZillizArticleRagVectorWriterError(
                _P1G_MSG_WRITER_DIMENSION_MISMATCH,
                retryable=False,
                failure_class="vector_writer_dimension_mismatch",
                failure_code=FAILURE_CODE_VECTOR_WRITER_DIMENSION_MISMATCH,
            )
        if metadata_dim != self._dim:
            raise ZillizArticleRagVectorWriterError(
                _P1G_MSG_WRITER_DIMENSION_MISMATCH,
                retryable=False,
                failure_class="vector_writer_dimension_mismatch",
                failure_code=FAILURE_CODE_VECTOR_WRITER_DIMENSION_MISMATCH,
            )

        # P1-G-R1: empty batch early return.  This MUST come AFTER the
        # metadata + profile validation so that invalid metadata fails
        # closed even when the batch is empty.  A valid empty batch
        # returns ``upserted_count=0`` without any client/network call.
        if not chunks_with_embeddings:
            return ArticleRagVectorWriteResult(
                collection=collection,
                upserted_count=0,
                provider_metadata={"provider": self.provider_name},
            )

        metadata_model = metadata.embedding_model
        # P1-G checks 3-5: per-chunk dim / vector-len / model
        # validation.  Any single bad chunk fails the whole batch
        # before any client call (no partial upsert).
        for chunk in chunks_with_embeddings:
            emb = chunk.embedding
            chunk_dim = emb.dim
            if isinstance(chunk_dim, bool) or not isinstance(chunk_dim, int):
                raise ZillizArticleRagVectorWriterError(
                    _P1G_MSG_WRITER_DIMENSION_MISMATCH,
                    retryable=False,
                    failure_class="vector_writer_dimension_mismatch",
                    failure_code=FAILURE_CODE_VECTOR_WRITER_DIMENSION_MISMATCH,
                )
            if chunk_dim != metadata_dim:
                raise ZillizArticleRagVectorWriterError(
                    _P1G_MSG_WRITER_DIMENSION_MISMATCH,
                    retryable=False,
                    failure_class="vector_writer_dimension_mismatch",
                    failure_code=FAILURE_CODE_VECTOR_WRITER_DIMENSION_MISMATCH,
                )
            if len(emb.vector) != metadata_dim:
                raise ZillizArticleRagVectorWriterError(
                    _P1G_MSG_WRITER_DIMENSION_MISMATCH,
                    retryable=False,
                    failure_class="vector_writer_dimension_mismatch",
                    failure_code=FAILURE_CODE_VECTOR_WRITER_DIMENSION_MISMATCH,
                )
            if not isinstance(emb.model, str) or emb.model != metadata_model:
                raise ZillizArticleRagVectorWriterError(
                    _P1G_MSG_WRITER_MODEL_MISMATCH,
                    retryable=False,
                    failure_class="vector_writer_model_mismatch",
                    failure_code=FAILURE_CODE_VECTOR_WRITER_MODEL_MISMATCH,
                )

        rows = [
            _build_article_rag_upsert_row(chunk, metadata=metadata)
            for chunk in chunks_with_embeddings
        ]

        def _sync_upsert() -> dict[str, Any]:
            client = self._ensure_client()
            # Idempotent collection creation.  We pass a real
            # ``pymilvus.CollectionSchema`` (Fix 2) — the SDK calls
            # ``schema.verify()`` on this argument and a plain dict
            # would fail.
            if not client.has_collection(collection_name=collection):
                schema = _build_pymilvus_collection_schema(self._dim)
                client.create_collection(
                    collection_name=collection,
                    schema=schema,
                )
            _ensure_pymilvus_vector_index(client, collection=collection)
            return client.upsert(collection_name=collection, data=rows)

        try:
            result_dict = await asyncio.to_thread(_sync_upsert)
        except ZillizArticleRagVectorWriterError:
            # Already typed — propagate verbatim.
            raise
        except Exception as exc:  # noqa: BLE001
            # Per Fix 5: never forward the original SDK message — it may
            # echo chunk text in a future regression.  Surface a fixed
            # diagnostic naming the SDK, the row count, and the SDK
            # exception class.  ``__cause__`` preserves the original.
            raise ZillizArticleRagVectorWriterError(
                "Zilliz upsert failed via pymilvus "
                f"(input_count={len(rows)}, "
                f"wrapper_exc={type(exc).__name__}); see __cause__ for "
                "upstream diagnostic",
                retryable=True,
                failure_class="vector_write",
                failure_code=FAILURE_CODE_VECTOR_WRITE_FAILED,
            ) from exc

        # Forward verbatim.  Never silently coerce.  pymilvus 2.6.x
        # returns ``upsert_count``; keep the legacy ``upserted_count``
        # fallback only for older fakes / SDK variants.
        if isinstance(result_dict, dict):
            upserted_count_raw = result_dict.get(
                "upsert_count",
                result_dict.get("upserted_count", 0),
            )
        else:
            upserted_count_raw = 0
        try:
            upserted_count = int(upserted_count_raw)
        except (TypeError, ValueError):
            upserted_count = 0

        return ArticleRagVectorWriteResult(
            collection=collection,
            upserted_count=upserted_count,
            provider_metadata={
                "provider": self.provider_name,
                "requested_count": len(rows),
            },
        )


def build_default_article_rag_vector_writer(
    settings: Settings,
) -> ArticleRagVectorWriter:
    """Factory for the default Article RAG vector writer.

    Returns:
      * :class:`ZillizArticleRagVectorWriter` only when
        ``settings.reader_article_rag_vector_provider == "zilliz"``
        AND resolved ``reader_article_rag_zilliz_uri`` is non-empty AND
        resolved ``reader_article_rag_zilliz_token`` is non-empty AND
        ``reader_article_rag_zilliz_collection`` is non-empty AND
        ``reader_article_rag_vector_dim`` is a positive integer;
      * otherwise :class:`UnconfiguredArticleRagVectorWriter`.

    The factory NEVER logs the token.  The factory NEVER raises on
    misconfiguration — it returns the unconfigured writer so the
    caller surfaces ``FAILURE_CODE_VECTOR_WRITER_UNCONFIGURED``
    through the worker's error handlers, not as a startup failure.
    """
    provider_name = (
        getattr(settings, "reader_article_rag_vector_provider", "") or ""
    ).strip().lower()
    if provider_name != READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ:
        logger.debug(
            "Article RAG vector provider not configured "
            "(reader_article_rag_vector_provider=%r); using "
            "UnconfiguredArticleRagVectorWriter",
            provider_name,
        )
        return UnconfiguredArticleRagVectorWriter()

    resolve_uri = getattr(settings, "resolve_reader_article_rag_zilliz_uri", None)
    uri = (
        resolve_uri()
        if callable(resolve_uri)
        else getattr(settings, "reader_article_rag_zilliz_uri", "")
    )
    uri = (uri or "").strip()

    resolve_token = getattr(
        settings, "resolve_reader_article_rag_zilliz_token", None
    )
    token = (
        resolve_token()
        if callable(resolve_token)
        else getattr(settings, "reader_article_rag_zilliz_token", "")
    )
    token = (token or "").strip()
    collection = (
        getattr(settings, "reader_article_rag_zilliz_collection", "") or ""
    ).strip()
    dim_raw = getattr(settings, "reader_article_rag_vector_dim", 0)
    try:
        dim = int(dim_raw)
    except (TypeError, ValueError):
        dim = 0

    if not uri or not token or not collection or dim <= 0:
        logger.debug(
            "Article RAG vector provider='zilliz' but configuration is "
            "incomplete (uri/empty=%s, token/empty=%s, "
            "collection/empty=%s, dim=%s); using "
            "UnconfiguredArticleRagVectorWriter",
            not uri,
            not token,
            not collection,
            dim,
        )
        return UnconfiguredArticleRagVectorWriter()

    # The Article RAG index is a single path — no ``index_version``
    # identity tag exists.  Collection name and embedding identity are
    # separate concerns, both enforced by the frozen
    # ``ARTICLE_RAG_EMBEDDING_CONTRACT`` at upsert time.
    return ZillizArticleRagVectorWriter(
        uri=uri,
        token=token,
        collection=collection,
        dim=dim,
    )


__all__ = [
    "READER_ARTICLE_RAG_VECTOR_PROVIDER_ZILLIZ",
    "ZILLIZ_DEFAULT_VECTOR_DIM",
    "ZillizArticleRagVectorWriter",
    "ZillizArticleRagVectorWriterError",
    "build_default_article_rag_vector_writer",
]
