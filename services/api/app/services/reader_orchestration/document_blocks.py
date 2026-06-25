"""D6-I1 Stable Document Block domain validator and plain-text composer.

Scope: domain-level helpers for StableReadingDocument / StableDocumentBlock.
This module does NOT depend on asyncpg, the DB connection pool, the web
framework, the orchestrator, the Candidate Document confirm flow, the
input adapters, or the RAG indexing layer. It exposes pure-Python helpers
that callers (Candidate confirm flow, Plate projection, RAG indexing) can
use to:

    * validate a set of stable document blocks (block_id uniqueness,
      parent integrity, order_index integrity, offset sanity, parent-id
      type discrimination),
    * compose a plain-text renderable preview from a sequence of
      textual-block text_content values without flattening table /
      image / footnote / code-block payloads.

Tables / images / footnotes / code blocks are intentionally NOT
collapsed into the plain-text output: the composer emits a stable
structural placeholder for each non-textual block and a literal block_id
marker, so callers can see where structural truth was elided. Raw
structural payloads must never be silently inlined as the canonical
text truth (see schema-and-domain-contract.md "rules" section).

Structural blocks ARE allowed as parents (e.g. `table -> table_row ->
table_cell`, `image -> image_ocr`, `image -> caption`). The validator
enforces unknown-parent / self-parent / cycle invariants only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from pydantic import ValidationError

from app.schemas.reader_documents import (
    StableDocumentBlock,
    StableDocumentBlockType,
)

# Block types whose payload is structural (table / image / code) and
# therefore MUST NOT be flattened into the plain-text renderable
# preview.
_STRUCTURAL_BLOCK_TYPES: frozenset[StableDocumentBlockType] = frozenset(
    {"table", "table_row", "table_cell", "image", "code_block", "unknown"}
)

# Placeholder emitted by the composer for a structural block. Callers
# should not parse the placeholder; they should treat the rendered text
# as preview-only and route structural content through the
# StableDocumentBlock.payload_json field instead.
_STRUCTURAL_PLACEHOLDER_PREFIX = "[[structural:"
_STRUCTURAL_PLACEHOLDER_SUFFIX = "]]"


class StableDocumentValidationError(ValueError):
    """Raised when a sequence of StableDocumentBlock rows fails
    domain-level validation. Wraps the underlying pydantic
    ValidationError or domain invariant message in a single, predictable
    surface for callers.
    """


def validate_stable_document_blocks(
    blocks: Iterable[StableDocumentBlock | dict],
) -> list[StableDocumentBlock]:
    """Validate a set of stable document blocks against domain invariants.

    Returns the validated, Pydantic-typed list. Raises
    StableDocumentValidationError on the first invariant violation.

    Invariants enforced (in order):

        1. Each block is parseable as a StableDocumentBlock. Pydantic
           field-level validation (text_content required for textual
           types, canonical_text offset sanity, parent != self) runs
           first via StableDocumentBlock.
        2. block_id is unique within the set.
        3. order_index is unique within the set.
        4. parent_block_id, when set, refers to another block_id in the
           same set. Structural blocks ARE valid parents (table rows
           may nest under a table, image_ocr / caption under an image).
        5. The combined set has no cycles in parent_block_id (depth-first
           walk from every node, so isolated strongly connected
           components are also detected).
    """
    parsed: list[StableDocumentBlock] = []
    for index, block in enumerate(blocks):
        if isinstance(block, StableDocumentBlock):
            parsed.append(block)
            continue
        try:
            parsed.append(StableDocumentBlock.model_validate(block))
        except ValidationError as exc:
            raise StableDocumentValidationError(
                f"block at index {index} failed pydantic validation: {exc}"
            ) from exc

    # (2) block_id uniqueness
    seen_block_ids: dict[str, StableDocumentBlock] = {}
    for block in parsed:
        if block.block_id in seen_block_ids:
            raise StableDocumentValidationError(
                f"duplicate block_id {block.block_id!r} "
                f"(also defined at order_index="
                f"{seen_block_ids[block.block_id].order_index})"
            )
        seen_block_ids[block.block_id] = block

    # (3) order_index uniqueness
    seen_order: dict[int, StableDocumentBlock] = {}
    for block in parsed:
        if block.order_index in seen_order:
            raise StableDocumentValidationError(
                f"duplicate order_index {block.order_index} on block_id "
                f"{block.block_id!r} (already used by block_id "
                f"{seen_order[block.order_index].block_id!r})"
            )
        seen_order[block.order_index] = block

    # (4) parent_block_id membership. Structural-block parents are
    # allowed by design (table -> table_row -> table_cell, image ->
    # image_ocr / caption). Only unknown or self-referencing parents
    # are rejected; self-parent is also enforced by Pydantic in the
    # StableDocumentBlock constructor.
    for block in parsed:
        if block.parent_block_id is None:
            continue
        if block.parent_block_id not in seen_block_ids:
            raise StableDocumentValidationError(
                f"block_id {block.block_id!r} references unknown "
                f"parent_block_id {block.parent_block_id!r}"
            )

    # (5) cycle detection. Walk from every node, so that an isolated
    # strongly connected component such as a<->b is still detected.
    visiting: set[str] = set()
    visited: set[str] = set()

    def _walk(block_id: str) -> None:
        if block_id in visited:
            return
        if block_id in visiting:
            raise StableDocumentValidationError(
                f"cycle detected at block_id {block_id!r}"
            )
        visiting.add(block_id)
        for child in parsed:
            if child.parent_block_id == block_id:
                _walk(child.block_id)
        visiting.remove(block_id)
        visited.add(block_id)

    for block in parsed:
        _walk(block.block_id)

    return parsed


def compose_stable_document_plain_text(
    blocks: Iterable[StableDocumentBlock | dict],
) -> str:
    """Compose a plain-text preview from a sequence of stable blocks.

    Textual blocks contribute their text_content verbatim, separated by
    single newlines. Structural blocks (table / image / code / unknown)
    contribute a stable placeholder marker that explicitly names the
    block_id and block_type, so the preview shows that structural truth
    was elided but never silently flattens the structural payload.

    The composed text is preview-only. It MUST NOT be used as the
    Canonical Text Layer (that remains reading_bases.text for V1).
    """
    validated = validate_stable_document_blocks(blocks)
    ordered = sorted(validated, key=lambda b: b.order_index)

    rendered: list[str] = []
    for block in ordered:
        if block.block_type in _STRUCTURAL_BLOCK_TYPES:
            rendered.append(
                f"{_STRUCTURAL_PLACEHOLDER_PREFIX}"
                f"block_id={block.block_id};type={block.block_type}"
                f"{_STRUCTURAL_PLACEHOLDER_SUFFIX}"
            )
            continue
        # Pydantic invariant guarantees text_content is non-None / non-empty
        # for textual block types.
        rendered.append(block.text_content or "")

    return "\n".join(rendered)


def _canonical_json(value: object) -> str:
    """Canonical JSON serialization for content_sha256 hashing.

    Key order MUST be stable across runs and across machines (sorted),
    whitespace MUST be minimized so two structurally identical payloads
    always serialize to the same bytes, and non-ASCII text MUST stay
    readable (ensure_ascii=False) so debug diffs of mismatched hashes
    are usable.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_stable_document_content_sha256(
    blocks: Iterable[StableDocumentBlock | dict],
) -> str:
    """Deterministic SHA-256 hash of the normalized block payload.

    The hash covers (block_id, order_index, block_type, text_content,
    payload_json, source_refs_json, canonical_text_*_utf16 when set,
    interpretation_policy). It excludes created_at / updated_at /
    quality_json noise so the hash stays stable across re-saves.

    JSON fields are serialized via a canonical form (sorted keys,
    compact separators, no NaN) so that semantically equal payloads
    that were constructed with different dict insertion orders (or
    different Python versions of dict ordering) always hash to the same
    value.

    Callers MUST use the same hash when persisting
    stable_reading_documents.content_sha256.
    """
    validated = validate_stable_document_blocks(blocks)
    ordered = sorted(validated, key=lambda b: b.order_index)

    digest = hashlib.sha256()
    for block in ordered:
        canonical = {
            "block_id": block.block_id,
            "order_index": block.order_index,
            "block_type": block.block_type,
            "parent_block_id": block.parent_block_id,
            "text_content": block.text_content,
            "payload_json": block.payload_json,
            "source_refs_json": block.source_refs_json,
            "canonical_text_start_utf16": block.canonical_text_start_utf16,
            "canonical_text_end_utf16": block.canonical_text_end_utf16,
            "interpretation_policy": block.interpretation_policy.model_dump(
                mode="json"
            ),
        }
        # Use a unit separator (US, 0x1f) that cannot appear inside the
        # canonical JSON, so block records never collide on concatenation.
        digest.update(b"|")
        digest.update(block.block_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(_canonical_json(canonical).encode("utf-8"))
    return digest.hexdigest()