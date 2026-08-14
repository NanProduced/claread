"""Candidate Document -> Stable Document Freeze Plan (pure Python core).

This module implements the pure-Python freeze plan that turns a confirmed
Candidate / Stable block list into the durable payload that the persistence layer
persistence transaction will commit. It does NOT:

    * touch the DB / asyncpg / repository,
    * bind to an API route,
    * mutate caller-owned StableDocumentBlock instances,
    * use ``compose_stable_document_plain_text()`` as the Canonical Text
      Layer (that helper is preview-only; see ``document_blocks.py``).

Canonical Text Layer derivation rules (mirrors the projection rules in
``docs/initiatives/reader-agentic-orchestration/modules/plate-reader-projection.md``
and the per-block-type defaults materialized by
``app.schemas.reader_documents.default_interpretation_policy_for``):

    * Blocks whose ``interpretation_policy.default_route == "main_reading"``
      contribute their ``text_content`` to the canonical text.
    * paragraph / list_item / blockquote / caption / heading default to
      ``main_reading`` and therefore enter canonical text by default.
    * table / table_row / table_cell / code_block also default to
      ``main_reading`` (Markdown ecosystem refactor code/table are
      first-class reading content), so table_cell / code_block text
      enters canonical text by default.
    * image / unknown default to ``metadata_only`` and therefore do NOT
      enter canonical text by default.
    * image_ocr / footnote default to ``rag_ask_only`` and therefore do
      NOT enter canonical text by default.
    * A caller-supplied policy may promote an image_ocr / footnote to
      ``main_reading``; in that case the block MUST enter canonical
      text.
    * A caller-supplied policy may also demote a normally-main_reading
      block (e.g. paragraph / table_cell / code_block) to
      ``rag_ask_only`` / ``metadata_only`` / ``ignored``; in that case
      the block MUST NOT enter canonical text.
    * Each canonical-text block carries UTF-16 offsets into the final
      canonical text. Non-canonical blocks keep ``canonical_text_*_utf16``
      = ``None``.
    * Canonical text blocks are joined with a stable separator
      (``CANONICAL_TEXT_BLOCK_SEPARATOR``, pinned to ``"\\n\\n"``).
    * UTF-16 offsets are computed in JavaScript UTF-16 code units via
      ``app.contracts.annotation.utf16_code_unit_length``, NOT Python
      ``len``. This matters for emoji and surrogate pairs.
    * The structural wrapper blocks (``list`` / ``table`` /
      ``table_row``) are the one structural exception: their
      ``default_route`` defaults to ``"main_reading"`` (so the
      structure participates in the document tree) but
      ``text_content`` is ``None`` because the narrative text lives in
      the child ``list_item`` / ``table_cell`` blocks. The plan skips
      structural wrapper blocks when deriving canonical text — they
      carry no canonical offsets and never raise — while their
      children still contribute their ``text_content`` to canonical
      text normally.
    * For other ``main_reading`` blocks with empty ``text_content``
      (possible for structural blocks such as ``table_cell`` /
      ``code_block`` whose ``text_content`` may be ``None``), the
      plan fails closed with ``StableDocumentFreezePlanError``. A
      ``main_reading`` route with no text would produce an
      inconsistent block (main_reading policy but ``None`` canonical
      offsets).
    * If no main-reading text is produced, the plan fails closed with
      ``StableDocumentFreezePlanError``.
    * ``content_sha256`` is computed via
      ``compute_stable_document_content_sha256`` over the FINAL block
      list (with canonical offsets populated), so any policy / offset
      change changes the hash. The same hash is set on the returned
      ``StableReadingDocument.content_sha256``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.annotation import (
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.schemas.reader_documents import (
    StableDocumentBlock,
    StableReadingDocument,
)
from app.services.reader_orchestration.document_blocks import (
    StableDocumentValidationError,
    compute_stable_document_content_sha256,
    validate_stable_document_blocks,
)

# Stable separator between canonical-text block contributions. Tests pin
# this value; changing it invalidates already-frozen documents because
# UTF-16 offsets shift.
CANONICAL_TEXT_BLOCK_SEPARATOR = "\n\n"

# Structural wrapper block types: their ``default_route`` defaults to
# ``"main_reading"`` (so the structure participates in the document
# tree) but ``text_content`` is ``None`` because the narrative text
# lives in child blocks (``list_item`` for ``list``; ``table_cell``
# for ``table`` / ``table_row``). The freeze plan skips these wrappers
# when deriving canonical text instead of failing closed.
_STRUCTURAL_WRAPPER_BLOCK_TYPES = frozenset({"list", "table", "table_row"})


class StableDocumentFreezePlanError(ValueError):
    """Raised when a freeze plan cannot be built from the given inputs.

    Concrete reasons include: validator failure, no main-reading
    blocks contributing canonical text, or canonical text derivation
    producing an empty payload.
    """


class StableDocumentFreezePlanDiagnostics(BaseModel):
    """Diagnostics / warnings emitted by the freeze plan builder.

    ``block_routes`` maps ``block_id`` -> ``default_route`` for every
    block in the input (used by tests and downstream observability).
    ``warnings`` lists non-fatal issues a caller may surface to the user
    or log; fatal issues raise ``StableDocumentFreezePlanError``.
    """

    model_config = ConfigDict(extra="forbid")

    block_routes: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class StableDocumentFreezePlan(BaseModel):
    """Output of ``build_stable_document_freeze_plan``.

    ``blocks`` are NEW ``StableDocumentBlock`` instances (the builder
    never mutates caller-owned input). ``main_reading`` blocks carry
    ``canonical_text_start_utf16`` / ``canonical_text_end_utf16``; all
    other blocks keep canonical offsets = ``None``.

    ``content_sha256`` equals ``stable_document.content_sha256`` and is
    computed over the final block list with canonical offsets populated.
    """

    model_config = ConfigDict(extra="forbid")

    stable_document: StableReadingDocument
    blocks: list[StableDocumentBlock]
    canonical_text: str
    content_sha256: str
    diagnostics: StableDocumentFreezePlanDiagnostics = Field(
        default_factory=StableDocumentFreezePlanDiagnostics
    )


def _block_route(block: StableDocumentBlock) -> str:
    """Return the block's interpretation_policy.default_route, falling
    back to ``"main_reading"`` only if the policy is somehow missing.

    StableDocumentBlock's before-validator always materializes a
    per-block-type default policy, so the fallback is defensive and
    should never trigger in practice.
    """
    if block.interpretation_policy is None:  # pragma: no cover - defensive
        return "main_reading"
    return block.interpretation_policy.default_route


def _block_canonical_text(block: StableDocumentBlock) -> str:
    """Return the text the block contributes to canonical text.

    Returns an empty string when ``text_content`` is ``None`` or empty.
    The caller (``build_stable_document_freeze_plan``) treats an empty
    result for a ``main_reading``-routed block as a hard error and
    raises ``StableDocumentFreezePlanError`` — a ``main_reading`` block
    with no text would otherwise produce an inconsistent frozen block
    (main_reading policy but ``None`` canonical offsets).
    """
    return block.text_content or ""


def build_stable_document_freeze_plan(
    *,
    reading_record_id: str,
    record_generation: int,
    document_version: int,
    title: str | None,
    blocks: Iterable[StableDocumentBlock | dict[str, Any]],
    source_profile_json: dict[str, Any] | None = None,
) -> StableDocumentFreezePlan:
    """Build a Stable Document freeze plan from confirmed Candidate / Stable
    blocks.

    The function is pure: it does not touch the DB, does not call the
    API layer, and does not mutate the caller's input blocks. The
    returned ``blocks`` are NEW ``StableDocumentBlock`` instances
    produced via ``model_copy(deep=True, update=...)`` so caller-owned
    objects — including nested mutable fields (``payload_json`` /
    ``source_refs_json`` / ``quality_json`` /
    ``interpretation_policy.allowed_source_scope`` /
    ``interpretation_policy.notes``) — stay intact and are not aliased
    with the inputs.

    Raises:
        StableDocumentFreezePlanError: when validation fails, no
            main-reading text is produced, or canonical offset parity
            check fails (the last indicates a builder bug).
    """
    # (1) Run validator first. This guarantees block_id / order
    # parent / offset invariants before we touch canonical text.
    try:
        validated = validate_stable_document_blocks(blocks)
    except StableDocumentValidationError as exc:
        raise StableDocumentFreezePlanError(
            f"Stable document block validation failed: {exc}"
        ) from exc

    ordered = sorted(validated, key=lambda b: b.order_index)

    # Structure is represented by the Stable rows themselves.  A wrapper
    # with children must not be turned into a synthetic reading unit by
    # joining descendant text back into the parent: that would create a
    # second text truth and would make callout/list/table follow different
    # snapshot paths.  The generic rule below covers all structural wrappers,
    # including a source-callout blockquote which uses a single-space DB
    # placeholder because the current schema requires textual blockquotes to
    # be non-empty.  Only leaves with meaningful text contribute canonical
    # ranges; every wrapper remains present with NULL canonical offsets.
    children_by_parent: dict[str, list[StableDocumentBlock]] = {}
    for block in ordered:
        if block.parent_block_id is not None:
            children_by_parent.setdefault(block.parent_block_id, []).append(block)
    structural_wrapper_ids = {
        block.block_id
        for block in ordered
        if block.block_id in children_by_parent
        and not (block.text_content or "").strip()
    }

    # (2) Derive Canonical Text Layer. Only blocks whose
    # interpretation_policy.default_route == "main_reading" contribute.
    # Block-type defaults (paragraph / heading / list_item / blockquote
    # / caption / table / table_row / table_cell / code_block ->
    # main_reading; image_ocr / footnote -> rag_ask_only; image /
    # unknown -> metadata_only) are already materialized by
    # StableDocumentBlock's before-validator, so reading
    # interpretation_policy.default_route here is sufficient.
    canonical_chunks: list[str] = []
    # Map block_id -> (start, end) in canonical text UTF-16 code units.
    canonical_offsets: dict[str, tuple[int, int]] = {}
    block_routes: dict[str, str] = {}
    warnings: list[str] = []

    cursor_utf16 = 0
    separator_utf16 = utf16_code_unit_length(CANONICAL_TEXT_BLOCK_SEPARATOR)
    for block in ordered:
        route = _block_route(block)
        block_routes[block.block_id] = route

        if route != "main_reading":
            continue

        # Generic wrapper rule: list/table/callout containers participate in
        # the Stable tree but never contribute a second copy of child text.
        if block.block_id in structural_wrapper_ids:
            continue

        text = _block_canonical_text(block)
        if not text:
            # Structural wrapper blocks (``list`` / ``table`` /
            # ``table_row``) are the one structural exception: their
            # ``default_route`` defaults to ``"main_reading"`` (so the
            # structure participates in the document tree) but
            # ``text_content`` is ``None`` because the narrative text
            # lives in the child ``list_item`` / ``table_cell`` blocks.
            # Skip them rather than raising — the children still
            # contribute their ``text_content`` to canonical text
            # normally.
            #
            # For other ``main_reading`` blocks with empty
            # ``text_content`` (possible for structural blocks
            # such as ``table_cell`` / ``code_block`` whose
            # ``text_content`` may be ``None``), the plan fails closed
            # with ``StableDocumentFreezePlanError``. A ``main_reading``
            # route with no text would produce an inconsistent block
            # (main_reading policy but ``None`` canonical offsets).
            if block.block_type in _STRUCTURAL_WRAPPER_BLOCK_TYPES:
                continue
            raise StableDocumentFreezePlanError(
                f"block_id={block.block_id!r} "
                f"(block_type={block.block_type!r}) is routed to "
                "main_reading but has empty text_content; "
                "main_reading requires non-empty text_content."
            )

        if canonical_chunks:
            # Insert separator BEFORE the next chunk so canonical text
            # starts with the first block's text, not a separator.
            cursor_utf16 += separator_utf16

        start_utf16 = cursor_utf16
        chunk_utf16 = utf16_code_unit_length(text)
        end_utf16 = start_utf16 + chunk_utf16
        canonical_offsets[block.block_id] = (start_utf16, end_utf16)
        canonical_chunks.append(text)
        cursor_utf16 = end_utf16

    canonical_text = CANONICAL_TEXT_BLOCK_SEPARATOR.join(canonical_chunks)

    if not canonical_text:
        # (9) Fail closed: no main-reading text produced.
        raise StableDocumentFreezePlanError(
            "Cannot build Stable Document freeze plan: no main-reading "
            "blocks contributed canonical text. At least one block must "
            "have interpretation_policy.default_route == 'main_reading' "
            "with non-empty text_content."
        )

    # (3) Build new block list with canonical offsets populated. We
    # NEVER mutate the caller-owned input. Each output block is a deep
    # copy (``model_copy(deep=True, ...)``) so nested mutable fields
    # (payload_json / source_refs_json / quality_json /
    # interpretation_policy.allowed_source_scope /
    # interpretation_policy.notes) are not aliased with the caller's
    # instances. Blocks not in canonical text keep
    # canonical_text_start_utf16 = None / canonical_text_end_utf16 =
    # None, even if the caller passed pre-populated offsets.
    frozen_blocks: list[StableDocumentBlock] = []
    for block in ordered:
        offsets = canonical_offsets.get(block.block_id)
        if offsets is None:
            # Force canonical offsets to None for non-main_reading
            # blocks, regardless of what the caller passed in. This
            # guarantees the freeze plan's canonical mapping is derived
            # solely from the policy decision, not from caller-supplied
            # offsets that might be inconsistent with the policy.
            if (
                block.canonical_text_start_utf16 is None
                and block.canonical_text_end_utf16 is None
            ):
                frozen_blocks.append(block.model_copy(deep=True))
            else:
                frozen_blocks.append(
                    block.model_copy(
                        deep=True,
                        update={
                            "canonical_text_start_utf16": None,
                            "canonical_text_end_utf16": None,
                        },
                    )
                )
            continue

        start_utf16, end_utf16 = offsets
        update_fields: dict[str, Any] = {
            "canonical_text_start_utf16": start_utf16,
            "canonical_text_end_utf16": end_utf16,
        }
        frozen_blocks.append(
            block.model_copy(
                deep=True,
                update=update_fields,
            )
        )

    # (4) Defensive parity check: each main_reading block's canonical
    # offsets must slice back to the block's text_content in the final
    # canonical text. This catches any future builder regression where
    # offset accounting drifts from the canonical_text string.
    for block in frozen_blocks:
        if block.canonical_text_start_utf16 is None:
            continue
        start = block.canonical_text_start_utf16
        end = block.canonical_text_end_utf16
        sliced = slice_by_utf16_offsets(canonical_text, start, end)
        expected = _block_canonical_text(block)
        if sliced is None or sliced != expected:
            raise StableDocumentFreezePlanError(
                f"Canonical offset parity check failed for block_id="
                f"{block.block_id!r}: offsets ({start}, {end}) do not "
                f"slice back to the block's text_content. This indicates "
                "a bug in the freeze plan builder."
            )

    # (10) content_sha256 over final blocks.
    # compute_stable_document_content_sha256 includes canonical offsets
    # AND interpretation_policy in its hash input, so policy or offset
    # changes will change the hash.
    content_sha256 = compute_stable_document_content_sha256(frozen_blocks)

    stable_document = StableReadingDocument(
        reading_record_id=reading_record_id,
        record_generation=record_generation,
        title=title,
        document_version=document_version,
        source_profile_json=source_profile_json or {},
        content_sha256=content_sha256,
        status="active",
    )

    diagnostics = StableDocumentFreezePlanDiagnostics(
        block_routes=block_routes,
        warnings=warnings,
    )

    return StableDocumentFreezePlan(
        stable_document=stable_document,
        blocks=frozen_blocks,
        canonical_text=canonical_text,
        content_sha256=content_sha256,
        diagnostics=diagnostics,
    )
