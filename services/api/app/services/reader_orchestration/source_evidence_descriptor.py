"""SourceEvidenceDescriptor — server-only adapter for rag_ask_only blocks.

docs/architecture/ask-claread.md
v5 sections §3.2 (field definitions), §3.3 (expansion_text assembly rules
with fail-closed fallback), §3.5.1.2 (precise chunk filtering with frozen
field-read source), §5.4.4 (display label rules), and §5.4.1/§5.4.2
(sort key and hard cap 8).

This module is a thin adapter:
- Reads ``default_route`` / ``block_type`` from the same
  :class:`ArticleRagIndexChunk.metadata_json` (never re-queries the
  document; missing/wrong-type/out-of-allowlist → no descriptor).
- Builds :class:`SourceEvidenceDescriptor` (server-only, never serialized
  into ``ArticleRagCitationEvidence`` or any public schema).
- Converts descriptors to candidate :class:`ArticleMapEntrySource`
  (heading populated per §5.4.4, window_text = expansion_text).
  ``parent_context`` is digested after conversion — never enters
  turn state / DTO / SSE.

Does NOT invoke the expansion pointer ledger, the evidence registry,
the article-map assembly routine, or any DB / embedding / Zilliz seam.
Pure computation over the supplied plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.reader_orchestration.article_rag_index_plan import (
    ArticleRagIndexChunk,
    ArticleRagIndexPlan,
)
from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapEntrySource,
)

# ---------------------------------------------------------------------------
# Constants (frozen by contract)
# ---------------------------------------------------------------------------

#: §3.2 / §3.5.1.2 — allowlist of block_type values that may produce a
#: descriptor. ``image_ocr`` and other rag_ask_only block types are
#: intentionally excluded; future extensions require a new contract.
ALLOWED_DESCRIPTOR_BLOCK_TYPES: frozenset[str] = frozenset(
    {"table_cell", "code_block", "footnote"}
)

#: §3.5.1.2 — the only ``default_route`` value that qualifies a chunk for
#: descriptor generation. ``main_reading`` chunks must be ignored.
DESCRIPTOR_DEFAULT_ROUTE: str = "rag_ask_only"

#: §5.4.2 — hard cap on descriptor candidates produced by one plan.
DESCRIPTOR_HARD_CAP: int = 8

#: §5.4.4 — neutral labels used when structured parent_context is absent.
_TABLE_CELL_NEUTRAL_LABEL: str = "表格单元格"
_CODE_BLOCK_LABEL: str = "代码"
_FOOTNOTE_LABEL: str = "脚注"

#: §3.3 — neutral expansion prefix for table_cell without column_name.
_TABLE_CELL_NEUTRAL_PREFIX: str = "表格单元格: "


# ---------------------------------------------------------------------------
# Data models (§3.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescriptorParentContext:
    """Restricted, immutable descriptor assembly context (§3.2).

    Only used for §3.3 ``expansion_text`` assembly and §5.4.4 label
    derivation. After producing the final :class:`ArticleMapEntrySource`
    it must be digested — never retained in turn state, DTO, or SSE.

    All fields default to ``None``: the provider reads only
    ``default_route`` / ``block_type`` from
    :attr:`ArticleRagIndexChunk.metadata_json` (§3.5.1.2) and does not
    re-query the document to populate structured parent context. When
    a field is ``None`` the adapter uses the neutral fallback per §3.3
    / §5.4.4 (or omits the descriptor for footnote when structurally
    unsafe — see ``build_expansion_text``).
    """

    # table_cell: column name (only when parser gives cell→header relation)
    column_name: str | None = None
    # table_cell: row order (0-based)
    row_index: int | None = None
    # code_block: language identifier
    language: str | None = None
    # footnote: structured footnote identifier (only when parser preserves
    # structural relation; None means relation unavailable)
    footnote_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceEvidenceDescriptor:
    """Server-only descriptor for one rag_ask_only block (§3.2).

    Never serialized into :class:`ArticleRagCitationEvidence` or any
    public citation schema. Resolved and fence-validated in preflight
    (before the outer map assembly transaction); after validation it
    is converted to a pure :class:`ArticleMapEntrySource` and the
    descriptor itself is not retained in turn state.
    """

    # --- identity fence (symmetric with ArticleRagCitationEvidence) ---
    reading_record_id: str
    stable_document_id: str
    base_id: str
    record_generation: int
    # --- stable source content hash (replaces RAG index/run/plan provenance) ---
    # rag_ask_only does not go through the generic RAG index, so
    # index_run_id / plan_content_sha256 are intentionally excluded.
    # source_content_sha256 anchors the descriptor to the stable-document
    # content, serving as source-integrity fence (§3.4).
    source_content_sha256: str  # pattern: ^[0-9a-f]{64}$

    # --- block locator (server-only, never exposed to model) ---
    block_id: str
    block_type: Literal["table_cell", "code_block", "footnote"]

    # --- expansion material (pre-computed in preflight, server-side) ---
    # Assembly rules per §3.3; used directly as window_text in map source.
    expansion_text: str

    # --- parent context (restricted, immutable, server-only) ---
    # Digested after producing the final ArticleMapEntrySource.
    parent_context: DescriptorParentContext


# ---------------------------------------------------------------------------
# §3.5.1.2 — precise chunk filtering (4 AND conditions + field-read source)
# ---------------------------------------------------------------------------


def _read_metadata_str(
    metadata: dict[str, Any], key: str
) -> str | None:
    """Read a string field from chunk metadata, fail-closed on wrong type.

    Returns the string value when present and typed as ``str``; returns
    ``None`` when the key is missing or the value is not a string. Per
    §3.5.1.2 the caller must NOT re-query the document to fill the gap.
    """
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(key)
    if isinstance(raw, str):
        return raw
    return None


def _read_metadata_int(
    metadata: dict[str, Any], key: str
) -> int | None:
    """Read an int field from chunk metadata, fail-closed on wrong type.

    ``bool`` is explicitly rejected (Python ``bool`` is a subclass of
    ``int`` but is not a valid order_index).
    """
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(key)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


def chunk_qualifies_for_descriptor(chunk: ArticleRagIndexChunk) -> bool:
    """§3.5.1.2 — 4 AND conditions, all read from the chunk itself.

    1. ``metadata_json["default_route"] == "rag_ask_only"``
    2. ``metadata_json["block_type"] ∈ {table_cell, code_block, footnote}``
    3. ``citation.canonical_text_start_utf16`` and
       ``citation.canonical_text_end_utf16`` are both ``None``
    4. ``chunk.text`` is non-empty

    Field-read source is frozen: ``default_route`` / ``block_type`` MUST
    come from the same ``metadata_json`` dict. Missing key, wrong type,
    or value outside the allowlist → chunk does NOT qualify. The caller
    must NOT re-query the document to fill the gap (§3.5.1.2).
    """
    metadata = chunk.metadata_json
    if not isinstance(metadata, dict):
        return False

    # Condition 1: default_route == "rag_ask_only"
    default_route = _read_metadata_str(metadata, "default_route")
    if default_route != DESCRIPTOR_DEFAULT_ROUTE:
        return False

    # Condition 2: block_type ∈ allowlist
    block_type = _read_metadata_str(metadata, "block_type")
    if block_type not in ALLOWED_DESCRIPTOR_BLOCK_TYPES:
        return False

    # Condition 3: canonical UTF-16 range must both be None (rag_ask_only
    # blocks have no canonical range; non-None indicates data inconsistency
    # → §5.1 6(a) fail-closed).
    if chunk.citation.canonical_text_start_utf16 is not None:
        return False
    if chunk.citation.canonical_text_end_utf16 is not None:
        return False

    # Condition 4: chunk text non-empty (whitespace-only is treated as
    # empty — there is no expandable content for the descriptor).
    if not chunk.text or not chunk.text.strip():
        return False

    return True


# ---------------------------------------------------------------------------
# §3.3 — expansion_text assembly rules with fail-closed fallback
# ---------------------------------------------------------------------------


def build_expansion_text(
    *,
    block_type: Literal["table_cell", "code_block", "footnote"],
    chunk_text: str,
    parent_context: DescriptorParentContext,
) -> str | None:
    """§3.3 — assemble expansion_text for one descriptor.

    Returns ``None`` when fail-closed (descriptor must be omitted):
    - footnote without structured footnote relation (footnote_id is None)
      → omit descriptor, because the adapter cannot verify the marker was
      stripped and §3.3 forbids regex-guessing the marker.

    For table_cell / code_block the neutral fallback per §3.3 is used
    when structured parent context is absent — the descriptor is still
    produced with a neutral prefix / raw text.

    The adapter NEVER splices additional server-side metadata (locator,
    hash, range, block id, chunk id, plan hash, UTF-16 offset, generation,
    record id) into ``expansion_text``. Strings naturally present in the
    user's original content (UUIDs, years, numbers) are preserved.
    """
    if block_type == "table_cell":
        column_name = parent_context.column_name
        if column_name is not None and column_name.strip():
            return f"{column_name}: {chunk_text}"
        # Neutral fallback (§3.3): header relation unclear.
        return f"{_TABLE_CELL_NEUTRAL_PREFIX}{chunk_text}"

    if block_type == "code_block":
        # §3.3: raw code text (preserve language and newlines). Language
        # only affects the label (§5.4.4), not the expansion_text.
        return chunk_text

    if block_type == "footnote":
        # §3.3: only when parser preserved structured footnote relation
        # (footnote_id is not None) do we use the footnote body text.
        # Otherwise omit the descriptor — §3.3 forbids regex-guessing
        # the marker, and we cannot verify the marker was stripped.
        if parent_context.footnote_id is None:
            return None
        return chunk_text

    # Unreachable for Literal-typed block_type, but defensive.
    return None


# ---------------------------------------------------------------------------
# §5.4.4 — display label rules
# ---------------------------------------------------------------------------


def build_descriptor_label(
    *,
    block_type: Literal["table_cell", "code_block", "footnote"],
    parent_context: DescriptorParentContext,
) -> str:
    """§5.4.4 — derive the ``ArticleMapEntrySource.heading`` for a descriptor.

    - ``table_cell``: ``{column_name}`` or neutral ``表格单元格``.
    - ``code_block``: ``代码`` or ``代码: {language}``.
    - ``footnote``: always ``脚注`` (footnote_id never enters the label —
      it is a server-side internal id; "use-then-digest" cannot un-expose
      a leaked value).

    ``language`` / ``column_name`` are user-visible semantic info (not
    server-side ids), allowed in the label. After label derivation they
    are digested along with ``parent_context``.
    """
    if block_type == "table_cell":
        column_name = parent_context.column_name
        if column_name is not None and column_name.strip():
            return column_name.strip()
        return _TABLE_CELL_NEUTRAL_LABEL

    if block_type == "code_block":
        language = parent_context.language
        if language is not None and language.strip():
            return f"{_CODE_BLOCK_LABEL}: {language.strip()}"
        return _CODE_BLOCK_LABEL

    # footnote — v4 frozen: always "脚注", footnote_id never enters label.
    return _FOOTNOTE_LABEL


# ---------------------------------------------------------------------------
# §3.2 / §3.5.1.2 — descriptor construction from a plan chunk
# ---------------------------------------------------------------------------


def build_descriptor_from_chunk(
    *,
    chunk: ArticleRagIndexChunk,
    plan: ArticleRagIndexPlan,
) -> SourceEvidenceDescriptor | None:
    """Build a descriptor from one plan chunk, or ``None`` if fail-closed.

    Fail-closed paths (return ``None``):
    - §3.5.1.2: chunk does not qualify (default_route / block_type /
      canonical range / empty text).
    - §3.5.1.2: ``block_order_index`` missing or wrong type in
      ``metadata_json`` (needed for §5.4.1 sort key; without it the
      merge order is non-deterministic).
    - chunk has no ``block_ids`` (block locator invalid; §3.4 preflight
      check 2).
    - §3.3: ``build_expansion_text`` returns ``None`` (footnote without
      structured relation).

    Does NOT re-query the document. Does NOT invoke the ledger, the
    registry, or the article-map assembly routine.
    """
    if not chunk_qualifies_for_descriptor(chunk):
        return None

    metadata = chunk.metadata_json
    block_type_str = _read_metadata_str(metadata, "block_type")
    # chunk_qualifies_for_descriptor already validated block_type ∈ allowlist.
    assert block_type_str in ALLOWED_DESCRIPTOR_BLOCK_TYPES
    block_type: Literal["table_cell", "code_block", "footnote"] = (
        block_type_str  # type: ignore[assignment]
    )

    # §5.4.1 sort key needs block_order_index from metadata_json.
    # If missing or wrong type, fail-closed (cannot sort deterministically).
    _block_order_index = _read_metadata_int(metadata, "block_order_index")
    if _block_order_index is None:
        return None

    # §3.4 preflight check 2: block locator must be valid.
    if not chunk.citation.block_ids:
        return None
    block_id = chunk.citation.block_ids[0]
    if not block_id:
        return None

    # parent_context: provider reads only default_route / block_type from
    # metadata_json (§3.5.1.2). Structured parent_context fields are not
    # available from the chunk and must NOT be filled by re-querying the
    # document. All default to None.
    parent_context = DescriptorParentContext()

    expansion_text = build_expansion_text(
        block_type=block_type,
        chunk_text=chunk.text,
        parent_context=parent_context,
    )
    if expansion_text is None:
        # §3.3 fail-closed (e.g. footnote without structured relation).
        return None

    return SourceEvidenceDescriptor(
        reading_record_id=str(plan.reading_record_id),
        stable_document_id=str(plan.stable_document_id),
        base_id=str(plan.base_id),
        record_generation=plan.record_generation,
        source_content_sha256=plan.content_sha256,
        block_id=block_id,
        block_type=block_type,
        expansion_text=expansion_text,
        parent_context=parent_context,
    )


def descriptor_to_candidate_source(
    descriptor: SourceEvidenceDescriptor,
) -> ArticleMapEntrySource:
    """Convert a descriptor to a candidate :class:`ArticleMapEntrySource`.

    Per §3.5.1.3 / §5.4.4:
    - ``heading`` populated per §5.4.4 label rules (column_name / language
      / 脚注 / 表格单元格 / 代码).
    - ``window_text`` = ``expansion_text`` (§3.3 assembly output).

    After this conversion the descriptor (including ``parent_context``)
    is digested — never retained in turn state, DTO, or SSE. The
    candidate is a pure :class:`ArticleMapEntrySource` with no server-side
    metadata leakage.
    """
    heading = build_descriptor_label(
        block_type=descriptor.block_type,
        parent_context=descriptor.parent_context,
    )
    return ArticleMapEntrySource(
        heading=heading,
        window_text=descriptor.expansion_text,
    )


# ---------------------------------------------------------------------------
# §5.4.1 / §5.4.2 — sort + hard cap 8 + candidate conversion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DescriptorCandidate:
    """Internal sortable candidate before conversion to ArticleMapEntrySource."""

    descriptor: SourceEvidenceDescriptor
    # §5.4.1 canonical_order_index (from metadata_json.block_order_index)
    canonical_order_index: int
    # §5.4.1 tie-breaker stable_block_id
    stable_block_id: str

    def sort_key(self) -> tuple[int, int, str]:
        """§5.4.1 — (source_kind_rank=1, canonical_order_index, stable_block_id).

        ``source_kind_rank`` is ``1`` for descriptor sources (正文 source
        has rank ``0``). Within descriptor kind the effective sort is by
        ``(canonical_order_index, stable_block_id)``.
        """
        return (1, self.canonical_order_index, self.stable_block_id)


def build_descriptor_candidates(
    *,
    plan: ArticleRagIndexPlan,
) -> tuple[ArticleMapEntrySource, ...]:
    """Build descriptor candidate sources from a plan (§3.5.1.2 / §5.4).

    Pipeline:
    1. Filter plan chunks per §3.5.1.2 (4 AND conditions, field-read
       source = ``metadata_json``; fail-closed per chunk).
    2. Build descriptors per §3.2 / §3.3 (fail-closed per chunk).
    3. Sort by §5.4.1 key ``(source_kind_rank=1, block_order_index,
       block_id)``.
    4. Apply §5.4.2 hard cap 8 (drop tail; no replacement from 正文).
    5. Convert to :class:`ArticleMapEntrySource` (heading per §5.4.4,
       window_text = expansion_text). ``parent_context`` is digested.

    Returns an empty tuple when no chunks qualify or the plan has no
    rag_ask_only chunks of the allowed block types.
    """
    candidates: list[_DescriptorCandidate] = []
    for chunk in plan.chunks:
        descriptor = build_descriptor_from_chunk(chunk=chunk, plan=plan)
        if descriptor is None:
            continue
        # block_order_index already validated non-None by
        # build_descriptor_from_chunk; re-read for sort key.
        block_order_index = _read_metadata_int(
            chunk.metadata_json, "block_order_index"
        )
        # Defensive: build_descriptor_from_chunk returns None when
        # block_order_index is missing, so this should never happen.
        if block_order_index is None:
            continue
        candidates.append(
            _DescriptorCandidate(
                descriptor=descriptor,
                canonical_order_index=block_order_index,
                stable_block_id=descriptor.block_id,
            )
        )

    # §5.4.1 deterministic sort.
    candidates.sort(key=lambda c: c.sort_key())

    # §5.4.2 hard cap 8 (drop tail; no replacement from 正文 source).
    if len(candidates) > DESCRIPTOR_HARD_CAP:
        candidates = candidates[:DESCRIPTOR_HARD_CAP]

    # Convert to ArticleMapEntrySource; parent_context is digested here.
    return tuple(
        descriptor_to_candidate_source(c.descriptor) for c in candidates
    )


__all__ = [
    "ALLOWED_DESCRIPTOR_BLOCK_TYPES",
    "DESCRIPTOR_DEFAULT_ROUTE",
    "DESCRIPTOR_HARD_CAP",
    "DescriptorParentContext",
    "SourceEvidenceDescriptor",
    "build_descriptor_candidates",
    "build_descriptor_from_chunk",
    "build_descriptor_label",
    "build_expansion_text",
    "chunk_qualifies_for_descriptor",
    "descriptor_to_candidate_source",
]
