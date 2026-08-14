"""Stable Document Freeze Persistence Transaction.

Consumes a ``StableDocumentFreezePlan`` and commits the stable
document + canonical text layer + (optional) candidate confirmation in
a single caller-managed DB transaction.

Transaction model:
    The caller opens the transaction (``async with conn.transaction():``)
    and passes the connection. This service executes its SQL within that
    transaction. Any exception raised here propagates to the caller and
    triggers a rollback, leaving no half-frozen document.

    The service validates ``conn.is_in_transaction()`` at entry and
    fails closed if the caller forgot to open a transaction.

In-transaction steps (in order):
    1. Idempotency check: query for an existing
       ``stable_reading_documents`` row with the same
       ``(reading_record_id, record_generation)``.
         * Same ``content_sha256`` -> first require the existing row's
           ``status='active'`` (a same-hash match against a
           'superseded'/'rejected' row is not idempotent). Then
           validate that the prior freeze completed ALL steps (active
           base row exists with matching record/generation/status,
           content_sha256 and content_utf16_length match
           plan.canonical_text, navigation_json.units is a non-empty
           list, reading_units and anchor_segments rows exist). If ANY
           check fails, fail closed WITHOUT confirming the candidate.
           Only a complete state allows returning
           ``idempotent_noop=True``. If ``candidate_document_id`` is
           provided and the state is complete, still confirms the
           candidate via the state-machine-safe helper.
         * Different ``content_sha256`` -> fail closed.
    2. Supersede prior active ``stable_reading_documents`` for the same
       record (set ``status='superseded'``).
    3. Insert ``stable_reading_documents`` row from ``plan.stable_document``.
    4. Insert ``stable_document_blocks`` rows from ``plan.blocks``.
       Each block's ``interpretation_policy_json`` is materialized via
       ``block.interpretation_policy.model_dump(mode="json")`` — the DB
       DEFAULT ``'{}'::jsonb`` is never relied on (see migration comment).
       ``parent_block_id`` is the document-local block_id string, NOT
       the row UUID.
    5. Supersede prior active ``reading_bases`` for the same record
       (set ``status='superseded'``). This MUST happen before the new
       INSERT to avoid violating ``uq_reading_bases_active_record``
       (only one active base per reading_record_id).
    6. Build Reading Units / Anchor Segments / navigation_json from
       ``plan.canonical_text`` via
       :func:`build_reading_base_from_canonical_text`. The text is
       NOT recanonicalized — block offsets are bound to the exact
       canonical text. Any validation failure raises
       :class:`StableDocumentFreezePersistenceError`.
    7. Insert ``reading_bases`` row as the V1 Canonical Text Layer
       carrier: ``text = plan.canonical_text``,
       ``content_sha256 = sha256(plan.canonical_text)`` (NOT
       ``plan.content_sha256``, which is the block-level hash), and
       ``navigation_json`` from the build result's ``navigation_units``
       (NOT ``{"units": []}``).
    8. Insert ``reading_units`` rows from the build result's ``units``.
    9. Insert ``anchor_segments`` rows from the build result's
       ``anchor_segments``.
    10. Set ``reading_records.active_base_id`` to the new base with a
        generation fence (``WHERE generation = $N``).
    11. If ``candidate_document_id`` is provided, confirm the candidate
        via ``_confirm_candidate_document`` with state-machine safety:
         * ``ready`` -> ``confirmed`` (UPDATE with ``AND status='ready'``).
         * ``confirmed`` -> idempotent success (no write).
         * ``rejected`` / ``superseded`` -> fail closed.
        Guarded by ``(reading_record_id, record_generation)`` and
        ``user_id`` (when provided).

Out of scope (follow-up):
    * API route / BFF / Web integration.
    * Reader event publication (the caller may publish a
      ``stable_document_frozen`` event after commit if desired).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.contracts.annotation import utf16_code_unit_length
from app.database.json_compat import jsonb_param
from app.schemas.reader_documents import StableDocumentBlock
from app.services.reader_orchestration.automatic_layer_policy import (
    AutomaticLayerPolicy,
    build_reading_unit_metadata_json,
    build_semantic_integrity_override,
)
from app.services.reader_orchestration.base_builder import (
    BuiltAnchorSegment,
    BuiltReadingUnit,
    ReadingBaseBuildResult,
    build_reading_base_from_canonical_text,
)
from app.services.reader_orchestration.document_freeze_plan import (
    StableDocumentFreezePlan,
)
from app.services.reader_orchestration.stable_annotation_analysis import (
    StableAnnotationPolicyOverride,
    StableBlockAnnotation,
    empty_diagnostics_payload,
)


class StableDocumentFreezePersistenceError(ValueError):
    """Raised when the freeze persistence cannot proceed.

    Concrete reasons: connection not in a transaction, same-generation
    content_sha256 mismatch (fail closed), generation fence violation
    on reading_records, interrupted prior freeze (NULL active_base_id
    in idempotent branch), candidate document not found, or candidate
    in a non-confirmable state.
    """


@dataclass(frozen=True, slots=True)
class StableDocumentFreezePersistenceResult:
    """Summary of the persisted freeze transaction.

    ``idempotent_noop=True`` means the service found an existing
    stable document with the same (record, generation, content_sha256)
    and performed NO writes to stable_reading_documents /
    stable_document_blocks / reading_bases. In that case
    ``stable_document_id`` is the existing row's id and ``base_id`` is
    the current ``reading_records.active_base_id`` (which must NOT be
    NULL — a NULL active_base_id in the idempotent branch is treated
    as an interrupted prior freeze and fails closed).
    """

    stable_document_id: UUID
    base_id: UUID | None
    reading_record_id: UUID
    record_generation: int
    document_version: int
    content_sha256: str
    canonical_text_sha256: str
    block_count: int
    candidate_confirmed: bool
    idempotent_noop: bool


def compute_canonical_text_sha256(canonical_text: str) -> str:
    """SHA-256 of the canonical text, for ``reading_bases.content_sha256``.

    This is distinct from ``plan.content_sha256`` (which is the
    block-level hash computed by
    ``compute_stable_document_content_sha256``). The reading_bases row
    hashes the canonical TEXT so that existing snapshot validation
    (which compares ``sha256(base.text)``) continues to work.
    """
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def compute_canonical_text_utf16_length(canonical_text: str) -> int:
    """UTF-16 code unit length of the canonical text.

    Uses the same ``utf16_code_unit_length`` helper as the rest of the
    codebase so emoji / surrogate pairs are counted correctly.
    """
    return utf16_code_unit_length(canonical_text)


async def persist_stable_document_freeze_plan(
    conn: asyncpg.Connection,
    *,
    plan: StableDocumentFreezePlan,
    canonicalizer_version: str,
    builder_version: str,
    segmenter_version: str,
    language: str | None = None,
    candidate_document_id: UUID | None = None,
    user_id: UUID | None = None,
    now: datetime | None = None,
) -> StableDocumentFreezePersistenceResult:
    """Persist a ``StableDocumentFreezePlan`` within a caller-managed
    transaction.

    The caller is responsible for ``async with conn.transaction():``.
    This function does NOT open its own transaction; it only executes
    SQL on the supplied connection. Any exception propagates and the
    caller's transaction context manager rolls back.

    Args:
        conn: An asyncpg connection that is already inside a
            transaction.
        plan: The freeze plan produced by
            ``build_stable_document_freeze_plan``. Not mutated.
        canonicalizer_version: Version label for the canonicalizer
            that produced the canonical text (written to
            ``reading_bases.canonicalizer_version``).
        builder_version: Version label for the reading base builder.
        segmenter_version: Version label for the segmenter.
        language: Optional language code for the reading base.
        candidate_document_id: If provided, the candidate document
            with this id will be confirmed via the state-machine-safe
            helper ``_confirm_candidate_document``.
        user_id: If provided (together with ``candidate_document_id``),
            the candidate lookup/update is additionally guarded by
            ``user_id``.
        now: Optional timestamp; defaults to ``datetime.now(UTC)``.

    Returns:
        A ``StableDocumentFreezePersistenceResult`` summarizing the
        persisted rows.

    Raises:
        StableDocumentFreezePersistenceError: On connection not in
            transaction, same-generation hash mismatch, generation
            fence violation, interrupted prior freeze (NULL
            active_base_id in idempotent branch), candidate not found,
            or candidate in a non-confirmable state.
    """
    # (0) Fail closed if the caller forgot to open a transaction.
    if not conn.is_in_transaction():
        raise StableDocumentFreezePersistenceError(
            "persist_stable_document_freeze_plan must be called within "
            "an active transaction. Refusing to execute outside a "
            "transaction to prevent half-frozen documents."
        )

    frozen_at = now or datetime.now(UTC)
    stable_doc = plan.stable_document
    reading_record_id = UUID(stable_doc.reading_record_id)
    record_generation = stable_doc.record_generation
    document_version = stable_doc.document_version
    content_sha256 = plan.content_sha256
    canonical_text = plan.canonical_text
    canonical_text_sha256 = compute_canonical_text_sha256(canonical_text)
    canonical_text_utf16_length = compute_canonical_text_utf16_length(canonical_text)

    # ------------------------------------------------------------------
    # (1) Idempotency / fail-closed check on same-generation stable doc.
    # ------------------------------------------------------------------
    existing_row = await conn.fetchrow(
        """
        SELECT id, content_sha256, status, document_version
        FROM stable_reading_documents
        WHERE reading_record_id = $1
          AND record_generation = $2
        """,
        reading_record_id,
        record_generation,
    )

    if existing_row is not None:
        existing_sha = str(existing_row["content_sha256"])
        if existing_sha == content_sha256:
            # Hardening: the existing stable document must be
            # in status='active'. A same-hash match against a
            # 'superseded' or 'rejected' row means the active document
            # for this generation was already replaced or discarded;
            # treating it as idempotent would be incorrect. Fail closed
            # BEFORE any completeness validation or candidate
            # confirmation.
            existing_status = str(existing_row["status"])
            if existing_status != "active":
                raise StableDocumentFreezePersistenceError(
                    f"Idempotent stable document found for "
                    f"reading_record_id={reading_record_id} "
                    f"record_generation={record_generation} with "
                    f"matching content_sha256, but its status is "
                    f"{existing_status!r} (expected 'active'). The "
                    "active document for this generation was "
                    "superseded or rejected; refusing to treat it as "
                    "idempotent."
                )

            # Idempotent no-op for the stable document. BEFORE returning
            # a no-op result, validate that the prior freeze completed
            # ALL steps: active base row exists with matching
            # record/generation/status, content_sha256 and
            # content_utf16_length match plan.canonical_text,
            # navigation_json.units is a non-empty list, and
            # reading_units / anchor_segments rows exist for the active
            # base. If ANY check fails, fail closed WITHOUT confirming
            # the candidate — the prior freeze was interrupted and the
            # state is incomplete.
            existing_base_id = await _validate_idempotent_freeze_completeness(
                conn,
                reading_record_id=reading_record_id,
                record_generation=record_generation,
                canonical_text_sha256=canonical_text_sha256,
                canonical_text_utf16_length=canonical_text_utf16_length,
            )

            # Completeness validation passed. Confirm candidate if
            # provided. The candidate may not have been confirmed in
            # the prior freeze.
            candidate_confirmed = False
            if candidate_document_id is not None:
                candidate_confirmed = await _confirm_candidate_document(
                    conn,
                    candidate_document_id=candidate_document_id,
                    reading_record_id=reading_record_id,
                    record_generation=record_generation,
                    user_id=user_id,
                    frozen_at=frozen_at,
                )

            return StableDocumentFreezePersistenceResult(
                stable_document_id=UUID(str(existing_row["id"])),
                base_id=existing_base_id,
                reading_record_id=reading_record_id,
                record_generation=record_generation,
                document_version=int(existing_row["document_version"]),
                content_sha256=existing_sha,
                canonical_text_sha256=canonical_text_sha256,
                block_count=len(plan.blocks),
                candidate_confirmed=candidate_confirmed,
                idempotent_noop=True,
            )
        raise StableDocumentFreezePersistenceError(
            f"Cannot freeze stable document for reading_record_id="
            f"{reading_record_id} record_generation={record_generation}: "
            f"an existing stable document has content_sha256="
            f"{existing_sha!r} which differs from the plan's "
            f"content_sha256={content_sha256!r}. Same-generation "
            "re-freeze with different content is not allowed."
        )

    # ------------------------------------------------------------------
    # (2) Supersede prior active stable_reading_documents for this record.
    # ------------------------------------------------------------------
    await conn.execute(
        """
        UPDATE stable_reading_documents
        SET status = 'superseded'
        WHERE reading_record_id = $1
          AND status = 'active'
        """,
        reading_record_id,
    )

    # ------------------------------------------------------------------
    # (3) Insert stable_reading_documents row.
    # ------------------------------------------------------------------
    stable_document_id = uuid4()
    await conn.execute(
        """
        INSERT INTO stable_reading_documents (
            id,
            reading_record_id,
            record_generation,
            title,
            document_version,
            source_profile_json,
            content_sha256,
            status,
            frozen_at,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, 'active', $8, $8)
        """,
        stable_document_id,
        reading_record_id,
        record_generation,
        stable_doc.title,
        document_version,
        jsonb_param(stable_doc.source_profile_json),
        content_sha256,
        frozen_at,
    )

    # ------------------------------------------------------------------
    # (4) Insert stable_document_blocks rows.
    #
    # interpretation_policy_json is ALWAYS materialized from the
    # Python model — the DB DEFAULT '{}'::jsonb is never relied on.
    # parent_block_id is the document-local block_id string, NOT
    # the row UUID (the FK is composite on (stable_document_id,
    # parent_block_id)).
    # ------------------------------------------------------------------
    for block in plan.blocks:
        await _insert_stable_document_block(
            conn,
            stable_document_id=stable_document_id,
            block=block,
        )

    # ------------------------------------------------------------------
    # (5) Supersede prior active reading_bases for this record.
    #
    # This MUST happen before the INSERT below to avoid violating the
    # ``uq_reading_bases_active_record`` unique partial index:
    #
    #     CREATE UNIQUE INDEX uq_reading_bases_active_record
    #       ON reading_bases(reading_record_id)
    #       WHERE status = 'active';
    #
    # Without this UPDATE, the INSERT would collide with the prior
    # active base and the transaction would abort.
    # ------------------------------------------------------------------
    await conn.execute(
        """
        UPDATE reading_bases
        SET status = 'superseded'
        WHERE reading_record_id = $1
          AND status = 'active'
        """,
        reading_record_id,
    )

    # ------------------------------------------------------------------
    # (6) Build Reading Units / Anchor Segments / navigation_json from
    # the EXACT canonical text.
    #
    # The text is NOT recanonicalized — block offsets are bound to
    # plan.canonical_text. We reuse base_builder's private split /
    # segment / hash helpers via the public
    # ``build_reading_base_from_canonical_text`` entry point. Any
    # validation failure (empty units, non-round-tripping offsets,
    # hash mismatch, etc.) is wrapped in
    # StableDocumentFreezePersistenceError so the caller's
    # transaction rolls back cleanly.
    # ------------------------------------------------------------------
    base_id = uuid4()
    try:
        build_result = build_reading_base_from_canonical_text(
            reading_record_id=str(reading_record_id),
            base_id=str(base_id),
            canonical_text=canonical_text,
            title=stable_doc.title,
            language=language,
            builder_version=builder_version,
            segmenter_version=segmenter_version,
            canonicalizer_version=canonicalizer_version,
            stable_block_annotations=_stable_block_annotations_from_plan(plan),
        )
        # Keep the complete Stable Document row set alongside the base
        # carriers.  This is deliberately not projected into units: wrappers
        # and children without canonical ranges must survive fresh/reload
        # snapshot construction as first-class structure.
        build_result = replace(
            build_result,
            stable_document_blocks=tuple(plan.blocks),
        )
    except ValueError as exc:
        raise StableDocumentFreezePersistenceError(
            f"Failed to build reading base from canonical text for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}: {exc}"
        ) from exc

    navigation_json = _navigation_json_from_build_result(build_result)

    # ------------------------------------------------------------------
    # (7) Insert reading_bases row as the V1 Canonical Text Layer
    # carrier.
    #
    # reading_bases.content_sha256 hashes the canonical TEXT (not the
    # block-level hash), so existing snapshot validation that compares
    # sha256(base.text) continues to work.
    # base_version aligns with stable_document.document_version.
    # navigation_json comes from the build result's navigation_units
    # (NOT {"units": []}).
    # ------------------------------------------------------------------
    await conn.execute(
        """
        INSERT INTO reading_bases (
            id,
            reading_record_id,
            base_version,
            record_generation,
            text,
            content_sha256,
            content_utf16_length,
            canonicalizer_version,
            builder_version,
            segmenter_version,
            language,
            title_snapshot,
            navigation_json,
            diagnostics_json,
            status,
            frozen_at,
            created_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
            $13::jsonb, $15::jsonb, 'active', $14, $14
        )
        """,
        base_id,
        reading_record_id,
        document_version,
        record_generation,
        canonical_text,
        canonical_text_sha256,
        canonical_text_utf16_length,
        canonicalizer_version,
        builder_version,
        # Persist the RESOLVED segmenter identity (spaCy main
        # path vs named regex v2 fallback), not the requested auto
        # policy label, so reading_bases.segmenter_version reflects
        # the segmenter that actually ran. Caller-pinned labels are
        # resolved to themselves by the base builder.
        build_result.base.segmenter_version,
        language,
        stable_doc.title,
        jsonb_param(navigation_json),
        frozen_at,
        jsonb_param(
            build_result.annotation_analysis.diagnostics_payload()
            if build_result.annotation_analysis is not None
            else empty_diagnostics_payload()
        ),
    )

    # ------------------------------------------------------------------
    # (8) Insert reading_units rows.
    #
    # Order matters: anchor_segments have a FK to reading_units, so
    # units must be inserted first.
    # ------------------------------------------------------------------
    overrides_by_unit = (
        {
            override.unit_id: override
            for override in build_result.annotation_analysis.policy_overrides
        }
        if build_result.annotation_analysis is not None
        else {}
    )
    for unit in build_result.units:
        await _insert_reading_unit(
            conn,
            reading_record_id=reading_record_id,
            base_id=base_id,
            unit=unit,
            override=overrides_by_unit.get(unit.unit_id),
        )

    # ------------------------------------------------------------------
    # (9) Insert anchor_segments rows.
    # ------------------------------------------------------------------
    for segment in build_result.anchor_segments:
        await _insert_anchor_segment(
            conn,
            reading_record_id=reading_record_id,
            base_id=base_id,
            segment=segment,
        )

    # ------------------------------------------------------------------
    # (10) Set reading_records.active_base_id with generation fence.
    # ------------------------------------------------------------------
    fence_result = await conn.execute(
        """
        UPDATE reading_records
        SET active_base_id = $2,
            updated_at = $3
        WHERE id = $1
          AND generation = $4
        """,
        reading_record_id,
        base_id,
        frozen_at,
        record_generation,
    )
    if fence_result != "UPDATE 1":
        raise StableDocumentFreezePersistenceError(
            f"Generation fence violation: expected to update exactly one "
            f"reading_records row for id={reading_record_id} "
            f"generation={record_generation}, but got {fence_result!r}. "
            "The reading record may not exist or its generation may have "
            "changed."
        )

    # ------------------------------------------------------------------
    # (11) Optionally confirm candidate_reading_documents with
    # state-machine safety.
    # ------------------------------------------------------------------
    candidate_confirmed = False
    if candidate_document_id is not None:
        candidate_confirmed = await _confirm_candidate_document(
            conn,
            candidate_document_id=candidate_document_id,
            reading_record_id=reading_record_id,
            record_generation=record_generation,
            user_id=user_id,
            frozen_at=frozen_at,
        )

    return StableDocumentFreezePersistenceResult(
        stable_document_id=stable_document_id,
        base_id=base_id,
        reading_record_id=reading_record_id,
        record_generation=record_generation,
        document_version=document_version,
        content_sha256=content_sha256,
        canonical_text_sha256=canonical_text_sha256,
        block_count=len(plan.blocks),
        candidate_confirmed=candidate_confirmed,
        idempotent_noop=False,
    )


async def _confirm_candidate_document(
    conn: asyncpg.Connection,
    *,
    candidate_document_id: UUID,
    reading_record_id: UUID,
    record_generation: int,
    user_id: UUID | None,
    frozen_at: datetime,
) -> bool:
    """Confirm a candidate document with state-machine safety.

    The candidate can only transition from ``status='ready'`` to
    ``status='confirmed'``. If it is already ``confirmed``, the call
    is an idempotent success (no write). If it is ``rejected`` or
    ``superseded``, the call fails closed.

    The lookup is guarded by ``(reading_record_id, record_generation)``
    and ``user_id`` (when provided) so a candidate belonging to a
    different record / generation / user is treated as "not found"
    and fails closed.

    Returns:
        ``True`` if the candidate is confirmed (newly or already).

    Raises:
        StableDocumentFreezePersistenceError: If the candidate is not
            found, is in a non-confirmable state, or the UPDATE
            affects zero rows.
    """
    # Fetch current status with record / generation / user guards.
    if user_id is not None:
        row = await conn.fetchrow(
            """
            SELECT status
            FROM candidate_reading_documents
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $3
              AND user_id = $4
            """,
            candidate_document_id,
            reading_record_id,
            record_generation,
            user_id,
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT status
            FROM candidate_reading_documents
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $3
            """,
            candidate_document_id,
            reading_record_id,
            record_generation,
        )

    if row is None:
        raise StableDocumentFreezePersistenceError(
            f"Candidate document not found for id={candidate_document_id} "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}"
            + (f" user_id={user_id}" if user_id is not None else "")
            + ". The candidate may not exist or may belong to a different "
            "record/generation/user."
        )

    current_status = str(row["status"])

    if current_status == "confirmed":
        # Idempotent success: candidate was already confirmed in a
        # prior freeze attempt.
        return True

    if current_status in ("rejected", "superseded"):
        raise StableDocumentFreezePersistenceError(
            f"Candidate document {candidate_document_id} is in status="
            f"{current_status!r}, cannot confirm. Only candidates in "
            "status='ready' can transition to 'confirmed'."
        )

    if current_status != "ready":
        raise StableDocumentFreezePersistenceError(
            f"Candidate document {candidate_document_id} is in unexpected "
            f"status={current_status!r}. Expected 'ready'."
        )

    # UPDATE from ready -> confirmed with state-machine guard.
    if user_id is not None:
        result = await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET status = 'confirmed',
                confirmed_at = $3,
                updated_at = $3
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $4
              AND user_id = $5
              AND status = 'ready'
            """,
            candidate_document_id,
            reading_record_id,
            frozen_at,
            record_generation,
            user_id,
        )
    else:
        result = await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET status = 'confirmed',
                confirmed_at = $3,
                updated_at = $3
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $4
              AND status = 'ready'
            """,
            candidate_document_id,
            reading_record_id,
            frozen_at,
            record_generation,
        )

    if result != "UPDATE 1":
        raise StableDocumentFreezePersistenceError(
            f"Candidate document confirmation failed: expected to "
            f"update exactly one candidate_reading_documents row for "
            f"id={candidate_document_id} "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}"
            + (f" user_id={user_id}" if user_id is not None else "")
            + f", but got {result!r}."
        )

    return True


async def _validate_idempotent_freeze_completeness(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    record_generation: int,
    canonical_text_sha256: str,
    canonical_text_utf16_length: int,
) -> UUID:
    """Validate that a prior same-hash freeze completed ALL steps.

    Called from the idempotent branch when an existing
    ``stable_reading_documents`` row has the same
    ``(reading_record_id, record_generation, content_sha256)`` as the
    plan. Before returning an idempotent no-op result, this helper
    verifies the full freeze pipeline completed:

        1. ``reading_records.active_base_id`` is non-NULL.
        2. An active ``reading_bases`` row exists for that base_id
           with matching ``(reading_record_id, record_generation,
           status='active')``.
        3. ``reading_bases.content_sha256`` equals
           ``sha256(plan.canonical_text)``.
        4. ``reading_bases.content_utf16_length`` equals the UTF-16
           length of ``plan.canonical_text``.
        5. ``reading_bases.navigation_json.units`` is non-empty.
        6. At least one ``reading_units`` row exists for the base_id.
        7. At least one ``anchor_segments`` row exists for the base_id.

    If ANY check fails, raises :class:`StableDocumentFreezePersistenceError`.
    The caller must NOT confirm the candidate when this raises — the
    prior freeze was interrupted and the state is incomplete.

    Args:
        conn: The asyncpg connection (inside a transaction).
        reading_record_id: The reading record id.
        record_generation: The record generation.
        canonical_text_sha256: ``sha256(plan.canonical_text)`` — the
            expected hash for the active reading_bases row.
        canonical_text_utf16_length: UTF-16 code unit length of
            ``plan.canonical_text``.

    Returns:
        The validated ``active_base_id`` (UUID) on success.

    Raises:
        StableDocumentFreezePersistenceError: If any completeness
            check fails.
    """
    # (1) Fetch reading_records.active_base_id (must be non-NULL).
    record_row = await conn.fetchrow(
        """
        SELECT active_base_id
        FROM reading_records
        WHERE id = $1 AND generation = $2
        """,
        reading_record_id,
        record_generation,
    )
    if record_row is None or record_row["active_base_id"] is None:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but "
            "reading_records.active_base_id is NULL. The prior "
            "freeze was interrupted before setting the active "
            "base; refusing to return a partial result."
        )
    active_base_id = UUID(str(record_row["active_base_id"]))

    # (2) Fetch the active reading_bases row. Must exist with matching
    # (reading_record_id, record_generation, status='active').
    base_row = await conn.fetchrow(
        """
        SELECT id, reading_record_id, record_generation, status,
               content_sha256, content_utf16_length, navigation_json
        FROM reading_bases
        WHERE id = $1
          AND reading_record_id = $2
          AND record_generation = $3
          AND status = 'active'
        """,
        active_base_id,
        reading_record_id,
        record_generation,
    )
    if base_row is None:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but the active "
            f"reading_bases row (id={active_base_id}) does not exist "
            "or does not match (record, generation, status='active'). "
            "The prior freeze was interrupted before inserting the "
            "reading base; refusing to return a partial result."
        )

    # (3) content_sha256 must match sha256(plan.canonical_text).
    base_content_sha256 = str(base_row["content_sha256"])
    if base_content_sha256 != canonical_text_sha256:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but the active "
            f"reading_bases row (id={active_base_id}) has "
            f"content_sha256={base_content_sha256!r} which differs "
            f"from sha256(plan.canonical_text)="
            f"{canonical_text_sha256!r}. The prior freeze may have "
            "used a different canonical text; refusing to return a "
            "partial result."
        )

    # (4) content_utf16_length must match.
    base_utf16_length = int(base_row["content_utf16_length"])
    if base_utf16_length != canonical_text_utf16_length:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but the active "
            f"reading_bases row (id={active_base_id}) has "
            f"content_utf16_length={base_utf16_length} which differs "
            f"from utf16 length of plan.canonical_text="
            f"{canonical_text_utf16_length}. The prior freeze may have "
            "used a different canonical text; refusing to return a "
            "partial result."
        )

    # (5) navigation_json.units must be a non-empty list. asyncpg's
    # JSONB codec returns a parsed dict; handle str fallback for
    # safety. hardening: a truthy but non-list value (dict,
    # string, object) must fail-closed — only a non-empty list is
    # acceptable. json.loads failures are wrapped as
    # StableDocumentFreezePersistenceError rather than leaking
    # JSONDecodeError.
    raw_navigation = base_row["navigation_json"]
    if isinstance(raw_navigation, str):
        try:
            navigation = json.loads(raw_navigation)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StableDocumentFreezePersistenceError(
                f"Idempotent stable document found for "
                f"reading_record_id={reading_record_id} "
                f"record_generation={record_generation}, but the "
                f"active reading_bases row (id={active_base_id}) has "
                f"navigation_json that is not valid JSON: "
                f"{raw_navigation!r}. Refusing to return a partial "
                f"result."
            ) from exc
    else:
        navigation = raw_navigation
    navigation_units = (
        navigation.get("units") if isinstance(navigation, dict) else None
    )
    if not isinstance(navigation_units, list) or len(navigation_units) == 0:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but the active "
            f"reading_bases row (id={active_base_id}) has "
            "navigation_json.units that is not a non-empty list "
            f"(got {type(navigation_units).__name__}). The prior "
            "freeze was interrupted before building navigation units; "
            "refusing to return a partial result."
        )

    # (6) At least one reading_units row must exist for the base_id.
    units_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM reading_units WHERE base_id = $1
        """,
        active_base_id,
    )
    if not units_count or int(units_count) == 0:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but the active "
            f"reading_bases row (id={active_base_id}) has 0 "
            "reading_units. The prior freeze was interrupted before "
            "inserting reading units; refusing to return a partial "
            "result."
        )

    # (7) At least one anchor_segments row must exist for the base_id.
    segments_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM anchor_segments WHERE base_id = $1
        """,
        active_base_id,
    )
    if not segments_count or int(segments_count) == 0:
        raise StableDocumentFreezePersistenceError(
            f"Idempotent stable document found for "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}, but the active "
            f"reading_bases row (id={active_base_id}) has 0 "
            "anchor_segments. The prior freeze was interrupted before "
            "inserting anchor segments; refusing to return a partial "
            "result."
        )

    return active_base_id


async def _insert_stable_document_block(
    conn: asyncpg.Connection,
    *,
    stable_document_id: UUID,
    block: StableDocumentBlock,
) -> None:
    """Insert one stable_document_blocks row.

    ``interpretation_policy_json`` is ALWAYS materialized from the
    Python model so the DB DEFAULT ``'{}'::jsonb`` is never used.
    ``parent_block_id`` is the document-local block_id string.
    """
    # Materialize the interpretation policy. This is the critical
    # step: an empty '{}' would silently route the block as
    # main_reading / main_reading_text and contradict the freeze plan
    # projection rules (tables / images / footnotes / code blocks
    # would leak into the main grammar pass).
    policy_json = block.interpretation_policy.model_dump(mode="json")

    await conn.execute(
        """
        INSERT INTO stable_document_blocks (
            id,
            stable_document_id,
            block_id,
            parent_block_id,
            order_index,
            block_type,
            text_content,
            payload_json,
            source_refs_json,
            canonical_text_start_utf16,
            canonical_text_end_utf16,
            interpretation_policy_json,
            quality_json
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11, $12::jsonb, $13::jsonb)
        """,
        uuid4(),
        stable_document_id,
        block.block_id,
        block.parent_block_id,
        block.order_index,
        block.block_type,
        block.text_content,
        jsonb_param(block.payload_json),
        jsonb_param(block.source_refs_json),
        block.canonical_text_start_utf16,
        block.canonical_text_end_utf16,
        jsonb_param(policy_json),
        jsonb_param(block.quality_json),
    )


def _navigation_json_from_build_result(
    build_result: ReadingBaseBuildResult,
) -> dict[str, list[dict[str, Any]]]:
    """Project ``navigation_units`` into the ``reading_bases.navigation_json``
    shape.

    The shape matches the existing repository helper:
    ``{"units": [{unit_id, order_index, unit_type, boundary_quality,
    label, base_start_utf16, base_end_utf16}, ...]}``.
    """
    return {
        "units": [
            {
                "unit_id": unit.unit_id,
                "order_index": unit.order_index,
                "unit_type": unit.unit_type,
                "boundary_quality": unit.boundary_quality,
                "label": unit.label,
                "base_start_utf16": unit.base_start_utf16,
                "base_end_utf16": unit.base_end_utf16,
            }
            for unit in build_result.navigation_units
        ]
    }


def _stable_block_annotations_from_plan(
    plan: StableDocumentFreezePlan,
) -> list[StableBlockAnnotation]:
    """Derive ``StableBlockAnnotation`` intervals from a freeze plan.

    Each ``StableDocumentBlock`` whose ``canonical_text_start_utf16`` /
    ``canonical_text_end_utf16`` are both set (i.e. the block
    contributed text to the canonical text layer) becomes a
    ``StableBlockAnnotation``. Blocks without canonical-text offsets
    (table / table_row wrappers, image / image_ocr blocks, etc.) are
    skipped — they carry no unit-level text and so cannot match a
    built unit's UTF-16 range. Ranges are forwarded verbatim — the
    analyzer module owns all validity judgement.

    The annotation's ``payload_json`` is the block's ``payload_json``
    verbatim (carries ``level`` for headings, ``inline_marks`` for
    paragraphs, ``column_index`` / ``alignment`` / ``is_header`` for
    table cells, etc.) so the base builder can project the right
    fields onto the matching unit and into the snapshot
    ``reader_source_block`` payload.
    """
    annotations: list[StableBlockAnnotation] = []
    for block in plan.blocks:
        start = block.canonical_text_start_utf16
        end = block.canonical_text_end_utf16
        if start is None or end is None:
            continue
        # No range filtering here: valid AND invalid (empty / reversed /
        # out-of-bounds) ranges all enter the analyzer module, which owns
        # every exclusion decision plus its diagnostic.
        annotations.append(
            StableBlockAnnotation(
                start_utf16=start,
                end_utf16=end,
                block_type=block.block_type,
                block_id=block.block_id,
                parent_block_id=block.parent_block_id,
                payload_json=dict(block.payload_json) if block.payload_json else {},
            )
        )
    return annotations


async def _insert_reading_unit(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    unit: BuiltReadingUnit,
    override: StableAnnotationPolicyOverride | None = None,
) -> None:
    """Insert one ``reading_units`` row.

    The SQL column order mirrors the existing
    ``repository.insert_reading_units`` so behavior and params stay
    consistent. ``metadata_json`` carries the ``sentence_provider``
    tag and, for new generations with a semantic contract, the versioned
    automatic layer policy projection.
    """
    await conn.execute(
        """
        INSERT INTO reading_units (
            reading_record_id,
            base_id,
            unit_id,
            order_index,
            unit_type,
            boundary_quality,
            base_start_utf16,
            base_end_utf16,
            text_hash,
            metadata_json
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
        """,
        reading_record_id,
        base_id,
        unit.unit_id,
        unit.order_index,
        unit.unit_type,
        unit.boundary_quality,
        unit.base_start_utf16,
        unit.base_end_utf16,
        unit.text_hash,
        jsonb_param(_unit_metadata_json(unit, override)),
    )


def _unit_metadata_json(
    unit: BuiltReadingUnit,
    override: StableAnnotationPolicyOverride | None = None,
) -> dict[str, Any]:
    """Persist sentence_provider + versioned automatic-layer semantic policy.

    A structural integrity override (analyzer-attributed) is written as a
    top-level ``semantic_integrity_override`` object alongside — never
    inside the ``semantic`` subtree — in the same transaction.
    """
    policy = (
        AutomaticLayerPolicy.from_mapping(unit.automatic_layer_policy)
        if unit.automatic_layer_policy is not None
        else None
    )
    meta = build_reading_unit_metadata_json(
        sentence_provider=unit.sentence_provider,
        contract_version=unit.semantic_contract_version,
        content_role=unit.content_role,
        automatic_layer_policy=policy,
        resolver_version=unit.automatic_layer_policy_resolver_version,
    )
    if override is not None:
        meta["semantic_integrity_override"] = build_semantic_integrity_override(
            reason_code=override.reason_code,
        )
    return meta


async def _insert_anchor_segment(
    conn: asyncpg.Connection,
    *,
    reading_record_id: UUID,
    base_id: UUID,
    segment: BuiltAnchorSegment,
) -> None:
    """Insert one ``anchor_segments`` row.

    The SQL column order mirrors the existing
    ``repository.insert_anchor_segments``.
    """
    await conn.execute(
        """
        INSERT INTO anchor_segments (
            reading_record_id,
            base_id,
            unit_id,
            anchor_segment_id,
            sentence_id,
            paragraph_id,
            order_index,
            unit_order_index,
            segment_type,
            base_start_utf16,
            base_end_utf16,
            unit_start_utf16,
            unit_end_utf16,
            text_hash,
            boundary_quality
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
        reading_record_id,
        base_id,
        segment.unit_id,
        segment.anchor_segment_id,
        segment.sentence_id,
        segment.paragraph_id,
        segment.order_index,
        segment.unit_order_index,
        segment.segment_type,
        segment.base_start_utf16,
        segment.base_end_utf16,
        segment.unit_start_utf16,
        segment.unit_end_utf16,
        segment.text_hash,
        segment.boundary_quality,
    )
